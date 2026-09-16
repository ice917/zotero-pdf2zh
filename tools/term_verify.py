# -*- coding: utf-8 -*-
"""
tools/term_verify.py —— 存疑术语联网查证器（离线批处理，零侵入）

模块职责：
  读取 zotero-pdf2zh 产出的「存疑清单.md」，提取其中的存疑术语，
  直连多个零 Key 公开数据源检索该术语的真实用法上下文，输出一份
  「术语 + 证据」报告，辅助人工（或 LLM）裁决译法。

关键函数：
- parse_doubt_md(path)              解析存疑清单，提取 (术语, 段落号, 冲突描述)
- zotero_search(term, ...)          本地库源：命中自己收藏的 PDF 全文，给出**原文上下文**（无需网络）
- openalex_search(term, ...)        学术文献源：论文标题 + 被引数（用法证据）
- wikipedia_search(term, ...)       百科源：条目名 + 条目摘要（定义性语境）⚠️未验证
- wikidata_search(term, ...)        实体源：规范实体 + 别名 + **中文标签**（服务译名裁决）⚠️未验证
- search_multi(term, sources, ...)  多源分发与合并（跨源去重、单源失败不影响整体）
- preflight_sources(sources)        开跑前剔除不可达源，避免在死源上逐术语空转
- build_report(...)                 组装 Markdown 报告
- main()                            命令行入口

设计原则（针对 AI Butler 联动场景）：
1. **直连数据源，不经过 local_mcp_server(8788)**。
   Zotero 本地 API(23119) / OpenAlex / 维基百科 / 维基数据均为直连，无需走本地 MCP。
   这样可以完全规避三条耦合风险：
     - 共享缓存污染（8788 的 _caches 是进程级全局，会被按 LRU 挤占）
     - 熔断级联（8788 变慢 → qwen_proxy 超时重试 → CircuitBreaker 开闸）
     - 外部 API 限流竞争
   对 AI Butler 零影响。
2. **检索源可插拔**：每个源实现统一签名
       search(term, max_results, timeout, context, max_retries) -> 契约
   并注册进 SOURCES；用 --source 选择，逗号可多选，all 为全源。
   新增一个源 = 写一个遵守契约的函数 + 注册一行 + 补一句 SOURCE_DESC。
   契约：{'ok': bool,
          'papers': [{'title','year','cited','relevance','source'}...],
          'hints': [str],          # 可选, 源特有的补充线索(如维基数据的中文标签)
          'error': str|None}
   无被引概念的源把 year/cited 置 None，报告会自动省略该项不显示。
3. **优雅降级**：任一术语、任一源查询失败只跳过该项，不影响整体；
   多源模式下只要有一个源成功即算成功；网络不可用时不产出报告，退出码非 0。
4. **礼貌限流**：请求间隔可配（默认 2.0s），遵守各 API 的礼貌池约定。
5. **只读**：不修改任何翻译产物，不写入术语表，仅输出报告文件。
6. **中文标签不等于裁决结果**：维基数据的 zh 标签只作为证据呈现，
   其质量参差（繁简混用、部分概念无中文标签），写入术语表仍须人工确认。
7. **按术语形态选源/标注**（v26.15，来自 40 术语实战）：
   - 中文术语不走 Zotero（下限见 SKIP_CJK_REASON），并在报告里写明跳过了什么；
   - 短缩写（如 CM / TO）附"改用全称"提示；
   - 库内**译文** PDF 的命中标记为循环证据，不作为译名依据；
   - 存疑清单里的模板占位串（如「中文译名（英文原文）」）直接丢弃并计数。

用法：
  python tools/term_verify.py                             # 处理最新的存疑清单(默认 OpenAlex)
  python tools/term_verify.py <存疑清单.md>               # 指定文件
  python tools/term_verify.py <文件> --limit 10           # 只查前 10 个术语
  python tools/term_verify.py <文件> --source zotero      # 查自己 Zotero 库里的 PDF 全文(无需网络)
  python tools/term_verify.py <文件> --source zotero,openalex   # 本地库 + 学术库
  python tools/term_verify.py <文件> --source all         # 全源合并
  python tools/term_verify.py <文件> --source wikidata    # 维基数据(出中文名, 需代理)
"""

import argparse
import glob
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OPENALEX_API = "https://api.openalex.org/works"
# 礼貌池：带 mailto 进优先通道（可选，留空也能用）
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "").strip()
USER_AGENT = "zotero-pdf2zh-term-verify/1.0 (mailto:%s)" % (OPENALEX_EMAIL or "anonymous")

# 维基媒体 API：维基百科全文检索 / 维基数据实体检索。均零 Key、直连、无需部署。
MEDIAWIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
# 维基媒体基金会的机器人政策要求"能说明身份与联系方式"的 UA，否则可能被限流
WIKIMEDIA_UA = ("zotero-pdf2zh-term-verify/1.0 "
                "(+https://github.com/ice917/zotero-pdf2zh; mailto:%s)"
                % (OPENALEX_EMAIL or "anonymous"))

# Zotero 7 本地 API（本机回环，不需要网络，也不经过任何第三方）。
# 前置条件：Zotero 7 已运行，且 设置→高级 里勾选了
# 「允许本机其他应用与 Zotero 通信」（prefs: extensions.zotero.httpServer.localAPI.enabled）。
ZOTERO_API = os.getenv("ZOTERO_LOCAL_API", "http://127.0.0.1:23119/api").rstrip("/")
# users/0 即"本机登录用户自己的文库"（实测可正常返回；也可用 ZOTERO_LIBRARY 覆盖）
ZOTERO_LIBRARY = os.getenv("ZOTERO_LIBRARY", "users/0")

# 汉字（含扩展 A 区）。用来判断术语是不是中文（决定该不该走某些源），
# 以及命中的摘录是不是来自中文译文。
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

REVIEW_DIRNAME = "review"


def project_root_env():
    """项目根目录（tools/ 的上级），供 --glossary 默认值使用"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------
# 存疑清单里 LLM 有时写的是**模板占位串**而不是真术语。实测 40 个术语里有 2 个
# 是「中文译名（flow contrast）」「中文译名（英文原文）」—— 来自"首次出现应处理为
# 中文译名（英文原文）"这条规则的措辞被当成术语名提了出来。拿去检索只会白跑，
# 而且报告里会多出一节假术语，故直接丢弃并计数。
JUNK_TERM_RE = re.compile(r"中文译名|英文原文")


def parse_doubt_md(path, stats=None):
    """
    解析存疑清单，返回 [{'term': 术语, 'para': 段落号, 'note': 冲突描述}, ...]

    清单结构（由 pdf2zh 润色管线产出）：
        ## 存疑段落 3            ← 旧版格式
        ...
        **存疑点**
        1. 术语"topology optimization"
           - 冲突点：...

        ## 段落 3                ← v18 humanize 新版格式
        ...
        【存疑】
        1. 术语"Loewner order"：...

    模板占位串（JUNK_TERM_RE）会被丢弃；传入 stats 字典可拿到丢弃数量
    （stats["junk"]），供报告如实写明。
    """
    text = open(path, encoding="utf-8").read()

    # 按 "## 存疑段落 N"(旧) / "## 段落 N"(v18 新) 切块
    chunks = re.split(r"^##\s*(?:存疑段落|段落)\s*(\d+)\s*$", text, flags=re.M)
    # chunks = [前缀, 段号, 内容, 段号, 内容, ...]
    items = []
    seen = set()

    for i in range(1, len(chunks) - 1, 2):
        para_no = chunks[i]
        body = chunks[i + 1]

        # 只取 "**存疑点**"(旧) / "【存疑】"(新) 之后的部分,
        # 到下一个 --- 分隔线 / 下一个段落标题 / 文件尾为止
        m = re.search(
            r"(?:\*\*存疑点\*\*|【存疑】)(.*?)(?=\n---|\n##\s|\Z)",
            body, flags=re.S,
        )
        if not m:
            continue
        doubt_block = m.group(1)

        # 提取术语：支持 术语"xxx" / 术语“xxx” / 专有名词"xxx" /
        # v18 变体: 术语/表达："xxx" (关键词与引号间允许 ≤12 字符的修饰)
        for tm in re.finditer(
            r'(?:术语|专有名词|短语)[^"“\n]{0,12}["“"]([^"“”]{1,80})["”"]',
            doubt_block,
        ):
            term = tm.group(1).strip()
            # 含 {vN} 公式占位符的是占位符切分存疑, 非词汇表对象
            if not term or re.search(r"\{v\d+\}", term):
                continue
            # 模板占位串(如「中文译名（英文原文）」)不是术语, 丢弃并计数
            if JUNK_TERM_RE.search(term):
                if stats is not None:
                    stats["junk"] = stats.get("junk", 0) + 1
                continue
            if term in seen:
                continue
            seen.add(term)

            # 取该术语后面紧邻的一行作为冲突描述
            tail = doubt_block[tm.end():tm.end() + 300]
            note = tail.split("\n")[0].strip(" ：:-")
            if not note:
                # 若同行没内容, 取下一行
                lines = [x.strip() for x in tail.split("\n") if x.strip()]
                note = lines[1].strip(" ：:-") if len(lines) > 1 else ""
            items.append({
                "term": term,
                "para": para_no,
                "note": note[:160],
            })
    return items


# ----------------------------------------------------------------------
# 检索
# ----------------------------------------------------------------------
def _is_relevant(title, term, strict=True):
    """
    相关性过滤。OpenAlex 的 search 是全文检索，通用短词会召回大量无关文献：
      - "void states"  实测召回 Ferroptosis(细胞死亡)、Ruling The Void(政治学书籍)
      - "CM"           实测召回 DC-DC 变换器、电动车充电、跨膜蛋白预测（全无关）

    strict=True  : 标题需包含术语的**全部**实词（精确优先）
    strict=False : 标题包含术语的**任一**实词（用于补足，避免 0 结果）

    对缩写/短词（如 "CM"）特殊处理：用**词边界**匹配，避免 "cm" 作为子串
    命中无关词。2026-09-02 修复：此前无实词时直接 return True 导致完全不过滤。
    """
    title_l = title.lower()
    words = [w for w in re.findall(r"[a-z]{3,}", term.lower())]

    if not words:
        # 缩写或无英文实词: 用词边界匹配整串, 而非 return True
        t = re.escape(term.lower().strip())
        if not t:
            return False
        return bool(re.search(r"\b%s\b" % t, title_l))

    if strict:
        return all(w in title_l for w in words)
    return any(w in title_l for w in words)


def llm_judge_relevance(term, titles, context="", timeout=30):
    """
    [方案 3] 用 LLM 判断检索结果是否与术语真正相关，过滤领域偏移的噪音。

    依据: arXiv:2601.11238 (LLM-Assisted Pseudo-Relevance Feedback) ——
          在利用 top-k 结果前插入 LLM 过滤层，既利用 LLM 语义判断，
          又基于语料证据，避免纯生成式方法的幻觉。
    痛点: 实测 "CM" 召回 DC-DC 变换器/电动车充电/跨膜蛋白预测（全无关），
          纯字面/关键词过滤对此无能为力，需语义判断。

    配置(环境变量, 全部为空则该功能不可用, 自动跳过):
        TERM_VERIFY_LLM_BASE    OpenAI 兼容端点, 例 https://.../v1
        TERM_VERIFY_LLM_KEY     API Key
        TERM_VERIFY_LLM_MODEL   模型名, 默认 qwen-plus

    **重要: 直连 LLM 端点, 不经过 8787(qwen_proxy)/8788(local_mcp_server),
    因此对 AI Butler 零影响**（不占其缓存、不触发熔断、不消耗其配额）。

    保守策略(避免误杀正确证据):
        - 调用失败/超时/解析异常 → 返回 None, 调用方**保留全部结果**不剔除
        - 只剔除 LLM 明确判为不相关(relevant=false)的条目
        - 每个判断附带 reason, 写入报告供人工复核

    返回: {title: {'relevant': bool, 'reason': str}} 或 None(不可用/失败)
    """
    base = os.getenv("TERM_VERIFY_LLM_BASE", "").strip()
    key = os.getenv("TERM_VERIFY_LLM_KEY", "").strip()
    model = os.getenv("TERM_VERIFY_LLM_MODEL", "qwen-plus").strip()
    if not base or not key:
        return None

    numbered = "\n".join("%d. %s" % (i, t) for i, t in enumerate(titles))
    domain_line = ("本文领域: %s\n" % context) if context else ""
    prompt = (
        "你是学术术语辨析助手。\n"
        "%s"
        '待判断术语: "%s"\n\n'
        "以下检索到的文献标题:\n%s\n\n"
        "请逐条判断: 该文献是否确实在本文领域下讨论该术语(或该术语所指的概念)?\n"
        "注意: 术语在其他领域的同名用法视为不相关。"
        '例: 术语 "CM" 在拓扑优化领域指 compliant mechanism(柔顺机构), '
        "则 DC-DC 变换器、电动车充电、跨膜蛋白预测等标题均不相关。\n\n"
        '只输出 JSON 数组, 不要任何解释文字:\n'
        '[{"i": 0, "relevant": true, "reason": "简述理由"}, ...]'
        % (domain_line, term, numbered)
    )

    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
    except Exception:
        return None  # 保守: 失败不剔除

    # 解析 JSON(模型可能用 ```json 包裹)
    m = re.search(r"\[.*\]", content, flags=re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return None

    out = {}
    for item in arr:
        try:
            idx = int(item.get("i"))
            if 0 <= idx < len(titles):
                out[titles[idx]] = {
                    "relevant": bool(item.get("relevant")),
                    "reason": str(item.get("reason", ""))[:200],
                }
        except Exception:
            continue
    # 若一个都没解析出来, 视为失败(保守不剔除)
    return out if out else None


def _http_json(url, headers=None, timeout=8, max_retries=4):
    """
    带退避重试的 GET + JSON 解析，供所有检索源复用。

    返回 (data, error)：成功为 (dict, None)，失败为 (None, 错误描述)。

    重试覆盖两类可恢复错误（2026-09-02 实测 18 个术语有 5 个遭 HTTP 429）：
      - HTTP 429(限流) / 5xx(服务端错误)
      - 网络类错误 URLError / TimeoutError / OSError（连接重置、DNS 抖动）
    退避 2s / 4s / 8s；4xx 等客户端错误立即放弃（重试无意义）。
    这段逻辑原先内联在 openalex_search 里，抽出来是为了让新源不必重复实现。
    """
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            last_err = "HTTP %s: %s" % (e.code, e.reason)
            if e.code == 429 or 500 <= e.code < 600:
                if attempt < max_retries - 1:
                    time.sleep(2 ** (attempt + 1))      # 2s, 4s, 8s
                    continue
            return None, last_err
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = "%s: %s" % (type(e).__name__, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            return None, last_err
        except Exception as e:
            return None, "%s: %s" % (type(e).__name__, e)
    return None, last_err or "未知错误"


def _strip_html(s):
    """去掉 MediaWiki 摘要里的 <span class="searchmatch"> 等标签并还原 HTML 实体"""
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def openalex_search(term, max_results=5, timeout=8, context="", max_retries=4):
    """
    检索术语的学术上下文证据。
    返回 {'ok': bool, 'papers': [{'title','year','cited','relevance','source'}...],
          'error': str}

    context: 领域上下文词（如 "topology optimization"），会拼接到检索词后，
             显著提升通用短词的召回精度（实测必加，否则噪音很大）。

    max_retries: 429 限流的指数退避重试次数。
             2026-09-02 实测 18 个术语有 5 个因 HTTP 429 失败(28%)，
             故加入退避重试：等待 2s / 4s / 8s 后重试。
    """
    query = (term + " " + context).strip() if context else term
    params = {
        "search": query,
        "per-page": max(1, min(max_results * 3, 50)),  # 多取一些, 供相关性过滤
        "select": "title,publication_year,cited_by_count",
    }
    if OPENALEX_EMAIL:
        params["mailto"] = OPENALEX_EMAIL
    url = OPENALEX_API + "?" + urllib.parse.urlencode(params)

    data, err = _http_json(url, headers={"User-Agent": USER_AGENT},
                           timeout=timeout, max_retries=max_retries)
    if err:
        return {"ok": False, "papers": [], "error": err}

    # 分级过滤: 先严格(匹配全部实词), 不足再用宽松(匹配任一实词)补足。
    # 目的: 优先保证精确性, 同时避免严格过滤导致 0 结果(如 "void states"
    #       在论文标题里常写作 "void regions"/"void elements")。
    raw = []
    for w in data.get("results", []):
        title = (w.get("title") or "").strip()
        if not title:
            continue
        raw.append({
            "title": title,
            "year": w.get("publication_year"),
            "cited": w.get("cited_by_count", 0),
            "source": "openalex",
        })

    strict = [p for p in raw if _is_relevant(p["title"], term, strict=True)]
    papers = strict[:max_results]
    for p in papers:
        p["relevance"] = "strong"        # 标题含全部实词, 强证据
    if len(papers) < max_results:
        seen = {p["title"] for p in papers}
        loose = [p for p in raw
                 if p["title"] not in seen
                 and _is_relevant(p["title"], term, strict=False)]
        for p in loose:
            p["relevance"] = "weak"      # 仅含部分实词, 可能领域偏移
        papers += loose[:max_results - len(papers)]

    return {"ok": True, "papers": papers, "error": None}


def wikipedia_search(term, max_results=5, timeout=8, context="", max_retries=4):
    """
    ⚠️【未对真实响应验证】本机网络不可达，本函数从未跑过真实的维基百科响应。
    实测：en.wikipedia.org 遭 **DNS 污染**（解析到 Facebook 的 157.240.17.35），
    TCP 443 不通。解析代码按 MediaWiki 官方 API 文档编写，并已用同构假数据
    离线验证合并/渲染分支；**真实响应若有字段出入，需在代理可用时复核**。

    维基百科全文检索：给出术语的**定义性语境**。

    与 OpenAlex 的分工：OpenAlex 证明"这个术语在学术文献里被这样用"（靠标题），
    维基百科则直接说明"这个术语指什么"（靠条目摘要）。译名裁决时两者互补。

    context 在本源中**不参与检索**：MediaWiki 全文检索对拼接领域词很敏感，
    拼上后会把精确条目挤下去，故有意忽略。

    相关性分级（MediaWiki 是语义召回，会带出大量无关条目，必须过滤）：
      - 条目名含术语全部实词 -> strong（条目就是该术语本身）
      - 否则要求术语出现在摘要里 -> weak（条目在讲包含该术语的概念）
      - 两者都不满足 -> 丢弃
    """
    params = {
        "action": "query",
        "list": "search",
        "srsearch": term,
        "srlimit": max(1, min(max_results * 3, 50)),   # 多取一些, 供相关性过滤
        "srprop": "snippet",
        "format": "json",
        "formatversion": "2",
    }
    url = MEDIAWIKI_API + "?" + urllib.parse.urlencode(params)
    data, err = _http_json(url, headers={"User-Agent": WIKIMEDIA_UA},
                           timeout=timeout, max_retries=max_retries)
    if err:
        return {"ok": False, "papers": [], "error": err}

    papers = []
    for w in ((data.get("query") or {}).get("search") or []):
        title = (w.get("title") or "").strip()
        if not title:
            continue
        snippet = _strip_html(w.get("snippet") or "")
        if _is_relevant(title, term, strict=True):
            relevance = "strong"
        elif term.lower() in snippet.lower():
            relevance = "weak"
        else:
            continue
        papers.append({
            "title": title,
            "year": None,          # 百科条目无出版年/被引数, 报告会自动省略
            "cited": None,
            "source": "wikipedia",
            "snippet": snippet,
            "relevance": relevance,
        })
        if len(papers) >= max_results:
            break
    return {"ok": True, "papers": papers, "error": None}


def _pick_zh(labels):
    """从维基数据的 labels 里取中文标签。按 zh-hans / zh-cn / zh 顺序优先（简体优先）"""
    for k in ("zh-hans", "zh-cn", "zh"):
        v = (labels.get(k) or {}).get("value")
        if v and v.strip():
            return v.strip()
    return None


def wikidata_search(term, max_results=5, timeout=8, context="", max_retries=4):
    """
    ⚠️【未对真实响应验证】本机网络不可达，本函数从未跑过真实的维基数据响应。
    实测：www.wikidata.org 的 DNS 正常(103.102.166.224)、TCP 443 可连，但
    **TLS 握手被阻断**（SNI 层面）。解析代码按 Wikidata 官方 API 文档编写，
    并已用同构假数据离线验证合并/渲染分支；**真实响应若有字段出入，需在代理
    可用时复核** —— 尤其是 wbgetentities 的 labels/aliases/descriptions 嵌套结构。

    维基数据实体检索：为术语找到**规范实体**，并取其中文标签。

    这是最直接服务"译名裁决"的源——OpenAlex / 维基百科只给英文证据，
    维基数据能直接给出该概念的中文名（例：herkogamy -> 雌雄异位），
    并以 hints 形式回传中文标签 / 定义 / 别名，写入报告的"补充线索"。

    注意：中文标签只是**证据**不是**裁决结果**。维基数据的中文标签质量参差
    （繁简混用、地区词差异、冷门概念根本没有中文标签），写入术语表前须人工确认。

    两步调用：wbsearchentities 找 QID -> wbgetentities 批量取 en/zh 标签与别名。
    第二步失败时降级为只用第一步的 label/description，不让整源失败。

    相关性分级：命中 label 或 alias（即名字本身就相等）且标题含术语全部实词
    -> strong；其余（描述命中、模糊匹配）-> weak。
    """
    params = {
        "action": "wbsearchentities",
        "search": term,
        "language": "en",
        "uselang": "en",
        "type": "item",
        "limit": max(1, min(max_results, 50)),
        "format": "json",
    }
    url = WIKIDATA_API + "?" + urllib.parse.urlencode(params)
    data, err = _http_json(url, headers={"User-Agent": WIKIMEDIA_UA},
                           timeout=timeout, max_retries=max_retries)
    if err:
        return {"ok": False, "papers": [], "error": err}

    hits = data.get("search") or []
    if not hits:
        return {"ok": True, "papers": [], "hints": [], "error": None}

    # 第二步: 批量取标签。失败则 entities 留空, 退回第一步的 label/description
    entities = {}
    ids = [h.get("id") for h in hits if h.get("id")]
    if ids:
        p2 = {
            "action": "wbgetentities",
            "ids": "|".join(ids),
            "props": "labels|aliases|descriptions",
            "languages": "en|zh|zh-hans|zh-cn",
            "format": "json",
        }
        d2, err2 = _http_json(WIKIDATA_API + "?" + urllib.parse.urlencode(p2),
                              headers={"User-Agent": WIKIMEDIA_UA},
                              timeout=timeout, max_retries=max_retries)
        if not err2:
            entities = d2.get("entities") or {}

    papers, hints = [], []
    for h in hits:
        qid = h.get("id")
        if not qid:
            continue
        ent = entities.get(qid) or {}
        labels = ent.get("labels") or {}
        # 英文名优先取实体标签(规范名), 退化到检索返回的 label
        en = ((labels.get("en") or {}).get("value") or h.get("label") or "").strip()
        if not en:
            continue

        zh = _pick_zh(labels)
        desc = (((ent.get("descriptions") or {}).get("en") or {}).get("value")
                or h.get("description") or "").strip()
        alts = [(a.get("value") or "").strip()
                for a in ((ent.get("aliases") or {}).get("en") or [])]
        alts = [a for a in alts if a]

        match_type = (h.get("match") or {}).get("type")
        is_exact = match_type in ("label", "alias") and _is_relevant(en, term, strict=True)
        papers.append({
            "title": en,
            "year": None,
            "cited": None,
            "source": "wikidata",
            "relevance": "strong" if is_exact else "weak",
        })

        parts = ["中文标签「%s」" % zh if zh else "无中文标签"]
        parts.append("%s ｜ %s" % (qid, match_type or "模糊匹配"))
        if desc:
            parts.append(desc[:160])
        if alts:
            parts.append("别名: " + " / ".join(alts[:5]))
        hints.append("%s: %s" % (en, "；".join(parts)))
        if len(papers) >= max_results:
            break

    return {"ok": True, "papers": papers, "hints": hints, "error": None}


def _zotero_get(path, timeout=8, max_retries=4):
    """
    Zotero 本地 API GET，返回 (data, error)。

    与直接用 `_http_json` 的差别：连接被拒（Zotero 没开）是本源最常见的失败，
    所以单独换成一句能照着做的提示，而不是裸的 WinError 10061。
    """
    data, err = _http_json(ZOTERO_API + path, headers={"User-Agent": USER_AGENT},
                           timeout=timeout, max_retries=max_retries)
    if err:
        low = err.lower()
        if "10061" in err or "refused" in low or "积极拒绝" in err:
            err = ("Zotero 未运行，或未开启「允许本机其他应用与 Zotero 通信」"
                   "（%s 拒绝连接）" % ZOTERO_API)
        else:
            err = "Zotero 本地 API: " + err
    return data, err


def _context_snippet(text, term, width=150):
    """
    在返回的全文里截出术语前后各 width 个字符，作为"原句就在眼前"的证据。
    找不到术语返回 ''（调用方据此把该条降级为弱证据）。
    """
    i = text.lower().find(term.lower())
    if i < 0:
        return ""
    seg = text[max(0, i - width): i + len(term) + width]
    return re.sub(r"\s+", " ", seg).strip()


def zotero_search(term, max_results=5, timeout=8, context="", max_retries=4):
    """
    Zotero 本地库检索：在**你自己已收藏的文献（含 PDF 全文索引）**里找该术语。

    为什么单独一个源：这是唯一**不需要任何网络**的源，实测命中率也最高 ——
    qmode=everything 会直接命中本地 PDF 的**全文索引**，回答"这个术语出现在
    我库里的哪几篇文献"。译名裁决时，最相关的语料往往就在手边。

    实测要点（决定了本函数的三个处理）：
      1. 全文命中返回的常是 **attachment / note**，其 title 是**文件名**
         （如 "Mandujano 等 - 2010 - ... 4.pdf"）。故必须按 `parentItem` 回查父条目，
         换成真正的文献标题/年份 —— 否则报告里会是一堆 "xxx 3.pdf"。
      2. 同一篇文献的多个附件会同时命中（实测一本书的 5 个附件全在结果里），
         按父条目去重，一篇文献只留一条。
      3. 再用 `/items/<key>/fulltext` 取回全文索引（实测一本 52KB），截出术语
         前后各 150 字的**原文上下文** —— 这是本工具能给出的最强证据。

    context 不参与检索：本地库是字面全文索引，拼领域词只会缩小召回。

    相关性分级：术语**确实出现在取回的全文里** -> strong（已核验的字面出现）；
    取不到全文（条目无索引、或是笔记）-> weak（只有搜索引擎的间接证据）。

    前置条件：Zotero 7 已运行，且 设置→高级 里勾选了「允许本机其他应用与 Zotero 通信」。

    注：库里若同时存有 pdf2zh 产出的**中文译文 PDF**，附件全文就是译文本身。
    此时用英文术语仍能命中（术语往往被保留未译），截出的上下文却是中文句子 ——
    那是**循环证据**（拿旧译法验证旧译法），故给这类条目打 `translated` 标记，
    由报告明示，避免被当成原文依据。中文术语则根本不走本源（见 SKIP_CJK_REASON）。
    """
    params = {
        "q": term,
        "qmode": "everything",          # 含 PDF 全文索引, 不只是标题/作者
        "limit": max(1, min(max_results * 4, 50)),   # 多取一些, 供父条目去重
        "format": "json",
    }
    data, err = _zotero_get("/%s/items?%s" % (ZOTERO_LIBRARY,
                                              urllib.parse.urlencode(params)),
                            timeout=timeout, max_retries=max_retries)
    if err:
        return {"ok": False, "papers": [], "error": err}

    cands = []
    for it in (data if isinstance(data, list) else []):
        d = it.get("data") or {}
        att_key = it.get("key")         # 全文索引用附件自己的 key

        # 附件/笔记的标题是文件名 -> 回查父条目换成真实文献元数据
        if d.get("parentItem") and d.get("itemType") in ("attachment", "note", "annotation"):
            pdata, _perr = _zotero_get("/%s/items/%s?format=json"
                                       % (ZOTERO_LIBRARY, d["parentItem"]),
                                       timeout=timeout, max_retries=1)
            pd = (pdata or {}).get("data") if isinstance(pdata, dict) else None
            if pd:
                d = pd

        title = (d.get("title") or "").strip()
        if not title:
            continue
        ym = re.search(r"\d{4}", d.get("date") or "")
        year = int(ym.group(0)) if ym else None

        # 取全文索引, 截术语上下文; 失败只降级不中断
        snippet = ""
        translated = False
        if att_key:
            ft, _ferr = _zotero_get("/%s/items/%s/fulltext" % (ZOTERO_LIBRARY, att_key),
                                    timeout=timeout, max_retries=1)
            content = (ft or {}).get("content") if isinstance(ft, dict) else None
            if content:
                snippet = _context_snippet(content, term)
                # 上下文是中文 -> 这个附件是 pdf2zh 产出的译文, 命中属循环证据
                translated = bool(snippet) and bool(CJK_RE.search(snippet))

        cands.append({
            "title": title,
            "year": year,
            "cited": None,              # 本地库没有被引数, 报告只显示年份
            "source": "zotero",
            "snippet": snippet,
            "translated": translated,   # 摘录来自译文 PDF(循环证据), 供报告标注
            "relevance": "strong" if snippet else "weak",
        })

    # 同一篇文献在库里常存有多个副本/附件(实测一本书的 5 个附件全部命中, 父条目
    # 还各不相同), 逐条列出纯属噪音 -> 按标题归一化去重; 若某条带原文上下文,
    # 优先保留它(那才是有效证据)。
    papers, by_title = [], {}
    for p in cands:
        k = re.sub(r"[^a-z0-9]+", "", p["title"].lower())
        i = by_title.get(k)
        if i is None:
            by_title[k] = len(papers)
            papers.append(p)
        elif p["snippet"] and not papers[i]["snippet"]:
            papers[i] = p

    return {"ok": True, "papers": papers[:max_results], "error": None}


# ----------------------------------------------------------------------
# 检索源注册表 + 多源分发
# ----------------------------------------------------------------------
# 每个源实现统一签名: search(term, max_results, timeout, context, max_retries) -> 契约
# 新增源三步: 1) 写一个遵守契约的函数 2) 在此注册一行 3) 在 SOURCE_DESC 补一句说明
SOURCES = {
    "zotero": zotero_search,
    "openalex": openalex_search,
    "wikipedia": wikipedia_search,
    "wikidata": wikidata_search,
}

SOURCE_DESC = {
    "zotero": "Zotero 本地库，基于自己收藏的 PDF 全文索引给出原文上下文，无需网络",
    "openalex": "OpenAlex 学术文献库，零 Key 公开 API，标题级用法证据",
    "wikipedia": "维基百科 MediaWiki 全文检索，条目摘要给出定义性语境，需代理且未验证",
    "wikidata": "维基数据实体标签，可直接给出中文名，需代理且未验证",
}

# 哪些源不该收中文术语，以及为什么（v26.15，40 术语实战实测）。
# Zotero 的全文索引对汉字是**字符级松散匹配**：单独查 "拓扑优化" 返回 10 条，
# 其中 9 条完全无关（仙人掌科、卡尔曼滤波、Attention Is All You Need、降雨建模…），
# 而这 10 条要逐个回查父条目 + 取全文索引才判得出来，纯属空转。
# 更关键的是：中文术语在本地库里唯一的强命中往往来自**我们自己产出的译文 PDF**
# —— 那是循环证据，不能作为裁决依据（实测 7 个中文术语全无有效产出）。
# 取舍：库里若真收藏了中文文献，这类术语就查不到了 —— 报告会写明跳过了什么，
# 需要时改用其它源或人工在 Zotero 里检索。
SKIP_CJK_REASON = {
    "zotero": "Zotero 全文索引对汉字为字符级松散匹配，只会产出假召回；"
              "且本地库中的中文命中多来自 pdf2zh 生成的译文（循环证据）",
}


# 预检探针词：只用来判断"这个源是否可达"，不关心它返回什么
PROBE_TERM = "science"


def preflight_sources(sources, timeout=5):
    """
    逐个探测源是否可达，返回 (alive, dead)，dead 为 [(源名, 失败原因)]。

    为什么需要这一步：多源模式下若某个源被网络阻断，每个术语都要在它身上
    耗尽 timeout×max_retries + 退避（约 46s）。实测本机 en.wikipedia.org 遭
    DNS 污染、wikidata.org 的 TLS 握手被重置——20 个术语就是十几分钟纯空转。
    开跑前一次性探明并剔除不可达源，把"每术语一次静默超时"变成"开跑时一句提示"。

    探测 = 用固定探针词真实调用一次该源（缩短 timeout、不重试），ok 即视为可达。
    因为探测本身就是真实调用，所以不会出现"探测说通、实际不通"的误判。
    """
    alive, dead = [], []
    for name in sources:
        res = SOURCES[name](PROBE_TERM, max_results=1, timeout=timeout, max_retries=1)
        if res.get("ok"):
            alive.append(name)
        else:
            dead.append((name, res.get("error") or "未知错误"))
    return alive, dead


def resolve_sources(spec):
    """
    解析 --source 取值，返回源名列表（顺序即合并优先级）。

    'all' 展开为全部源；逗号可分隔多个；空值或 'openalex' 保持旧行为（向后兼容）。
    未知源名抛 ValueError，由 main() 转成退出码 2。
    """
    spec = (spec or "").strip().lower()
    if not spec or spec == "openalex":
        return ["openalex"]
    if spec == "all":
        return list(SOURCES)
    names = []
    for s in spec.split(","):
        s = s.strip()
        if s and s not in names:
            names.append(s)
    unknown = [s for s in names if s not in SOURCES]
    if unknown:
        raise ValueError("未知检索源 %s（可选：%s，或 all）"
                         % (", ".join(unknown), ", ".join(SOURCES)))
    return names or ["openalex"]


def search_multi(term, sources, max_results=5, timeout=8, context="", max_retries=4):
    """
    按 sources 顺序调用各源并合并结果。

    单源时行为与"直接调该源"一致（仅失败时的 error 多一个源名前缀），
    保证 --source openalex 与改造前向后兼容。

    合并规则：
      - 每个源各自取 max_results 条，跨源按标题归一化去重，先到先得（即 --source 顺序）
      - 只要有一个源成功就 ok=True（优雅降级）；error 汇总各源失败原因供排查
      - hints 汇总各源补充线索，加 [源名] 前缀以便区分来源
      - 中文术语跳过 SKIP_CJK_REASON 里列出的源，跳过了什么记在返回值的
        `skipped`（{源名: 原因}）里，供报告如实写明，不计作失败
    """
    papers, hints, errors, any_ok = [], [], [], False
    seen = set()
    skipped = {}
    is_cjk = bool(CJK_RE.search(term))
    for name in sources:
        if is_cjk and name in SKIP_CJK_REASON:
            skipped[name] = SKIP_CJK_REASON[name]
            continue
        res = SOURCES[name](term, max_results=max_results, timeout=timeout,
                            context=context, max_retries=max_retries)
        if res.get("ok"):
            any_ok = True
        else:
            errors.append("%s: %s" % (name, res.get("error") or "未知错误"))
        for h in (res.get("hints") or []):
            hints.append("[%s] %s" % (name, h))
        for p in (res.get("papers") or []):
            key = re.sub(r"[^a-z0-9]+", "", (p.get("title") or "").lower())
            if key and key in seen:
                continue          # 同一篇文献被多个源召回, 只保留先到的那个
            if key:
                seen.add(key)
            papers.append(p)
    if not any_ok and skipped and not errors:
        # 所有源都因术语是中文被跳过: 这不是失败, 但也确实没查
        return {
            "ok": False,
            "papers": [], "hints": [],
            "error": "中文术语，已跳过不适用于中文检索的源（%s）" % "、".join(skipped),
            "skipped": skipped,
        }
    return {
        "ok": any_ok,
        "papers": papers,
        "hints": hints,
        "error": "；".join(errors) if errors else None,
        "skipped": skipped,
    }


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------
def _is_short_abbrev(term):
    """
    是不是"短缩写"（≤3 字符、无空格、字母开头）—— 如 CM / TO / OCR。

    为什么要单独提示：实测 `CM` 在 OpenAlex 上召回了 ICD-9-CM 疾病编码（被引 10840）、
    cm⁻¹ 波数（被引 1179）这类完全无关的高被引条目，`TO` 也几乎全噪音。
    这类术语不是查不到，而是**必须换全称或加领域限定词**才有意义。
    """
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,2}", term.strip()))


def build_report(src_path, results, elapsed, sources=None, dropped=None, junk=0):
    sources = sources or ["openalex"]
    multi = len(sources) > 1

    n_ok = sum(1 for r in results if r["res"]["ok"])
    # 因术语是中文而整条跳过（没查，但也不算失败）
    n_skip = sum(1 for r in results
                 if not r["res"]["ok"] and r["res"].get("skipped"))
    skip_stat = {}          # 源名 -> [次数, 原因]
    for r in results:
        for name, why in (r["res"].get("skipped") or {}).items():
            skip_stat.setdefault(name, [0, why])[0] += 1

    lines = []
    lines.append("# 存疑术语查证报告")
    lines.append("")
    lines.append("- 源清单：`%s`" % os.path.basename(src_path))
    lines.append("- 查证数据源：%s" % " + ".join(
        "%s（%s）" % (s, SOURCE_DESC.get(s, "自定义源")) for s in sources))
    if dropped:
        lines.append("- ⚠️ 已剔除不可达源：%s（预检失败，本报告不含其证据）"
                     % "、".join("%s —— %s" % (n, e) for n, e in dropped))
    if n_skip:
        lines.append("- 术语总数：%d ｜ 查询成功：%d ｜ 跳过：%d ｜ 失败：%d ｜ 耗时：%.1fs"
                     % (len(results), n_ok, n_skip,
                        len(results) - n_ok - n_skip, elapsed))
    else:
        lines.append("- 术语总数：%d ｜ 查询成功：%d ｜ 失败：%d ｜ 耗时：%.1fs"
                     % (len(results), n_ok, len(results) - n_ok, elapsed))
    for name, (cnt, why) in skip_stat.items():
        lines.append("- ℹ️ 已对 %d 个中文术语跳过 `%s` 源：%s" % (cnt, name, why))
    if junk:
        lines.append("- ℹ️ 已丢弃 %d 个模板占位串（如「中文译名（英文原文）」），"
                     "它们不是术语" % junk)
    lines.append("")
    lines.append("> 本报告只提供**术语的真实用法证据**，不自动改译文。")
    lines.append("> 请据此人工裁决，确认后再写入术语表。")
    lines.append("")

    for idx, r in enumerate(results, 1):
        term, para, note, res = r["term"], r["para"], r["note"], r["res"]
        lines.append("## %d. %s" % (idx, term))
        lines.append("")
        lines.append("- 存疑段落：%s" % para)
        if note:
            lines.append("- 冲突描述：%s" % note)
        if _is_short_abbrev(term):
            lines.append("- ⚠️ 该术语是**短缩写**，检索噪音极大（实测 `CM` 召回了 "
                         "ICD-9-CM 疾病编码、`cm⁻¹` 波数这类高被引无关条目）——"
                         "请改用全称，或用 `--context` 限定领域后重查")
        if not res["ok"]:
            if res.get("skipped"):
                # 术语是中文 -> 主动跳过该源, 不是查证失败
                lines.append("- 跳过：%s" % res["error"])
                lines.append("  （如需检索本地库中的中文文献，请改用 `--source openalex` "
                             "以外的途径，或直接在 Zotero 里检索）")
            else:
                lines.append("- **查证失败**：%s" % res["error"])
            lines.append("")
            continue
        if not res["papers"]:
            lines.append("- **未找到高相关条目**：%s 均未检索到高相关结果" % "、".join(sources))
            lines.append("  （可能术语过于具体、拼写特殊，或需补充 `--context` 领域上下文；"
                         "也可试 `--source all` 换更多源）")
            lines.append("")
            continue
        if res.get("llm_note"):
            lines.append("- %s" % res["llm_note"])
        lines.append("- 语境证据：")
        n_weak = sum(1 for p in res["papers"] if p.get("relevance") == "weak")
        if n_weak:
            lines.append("  - ⚠️ 其中 **%d 条为弱证据**（名称仅含部分词 / 模糊匹配 / "
                         "未能取到全文核验），请优先参考未标记的强证据" % n_weak)
        for p in res["papers"]:
            parts = []
            if multi and p.get("source"):
                parts.append("[%s]" % p["source"])
            if p.get("cited") is None:
                # 百科/实体/本地库类源没有被引数, 只给年份(有的话)
                if p.get("year"):
                    parts.append("(%s)" % p["year"])
            else:
                y = p["year"] if p["year"] else "n.d."
                parts.append("(%s, 被引 %d)" % (y, p["cited"]))
            if p.get("relevance") == "weak":
                parts.append("[弱]")
            lines.append("  - %s %s" % (" ".join(parts), p["title"]))
            if p.get("snippet"):
                # 上限 400: 本工具截的上下文是"术语前后各 150 字"(约 300+),
                # 截太短会把术语本身或后半句切掉, 那就失去"原句就在眼前"的意义了
                lines.append("    - 摘录：%s" % p["snippet"][:400])
            if p.get("translated"):
                lines.append("    - ⚠️ 此摘录来自库内的**译文** PDF —— 属循环证据"
                             "（拿旧译法验证旧译法），不可作为译名依据")
            if p.get("llm_reason"):
                lines.append("    - LLM 判据：%s" % p["llm_reason"])
        if res.get("hints"):
            lines.append("")
            lines.append("- 补充线索：")
            for h in res["hints"]:
                lines.append("  - %s" % h)
        if res.get("llm_dropped"):
            lines.append("")
            lines.append("  <details><summary>LLM 剔除的条目（可复核）</summary>")
            lines.append("")
            for t, why in res["llm_dropped"]:
                lines.append("  - %s —— %s" % (t, why))
            lines.append("")
            lines.append("  </details>")
        lines.append("")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def find_latest_doubt_md(base_dir):
    """在 server/translated/review 下找最新的存疑清单"""
    pattern = os.path.join(base_dir, "server", "translated", REVIEW_DIRNAME, "存疑清单*.md")
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


# ----------------------------------------------------------------------
# [方案 2] 术语表回写
# ----------------------------------------------------------------------
def propose_glossary_updates(results, context=""):
    """
    从查证结果中提出术语表新增/修订建议。

    生成规则(保守, 宁缺毋滥):
      - 只对"证据强"的术语提建议: 至少 1 条 strong 证据, 且 strong 数 >= weak 数
      - 短语术语(>=2 个实词)才建议; 单词/缩写歧义太大, 必须人工裁决
      - 建议译名 = 取被引最高的强证据文献标题(不自动定中文译名!)

    返回: [{'term', 'evidence_title', 'cited', 'year', 'suggested_by'}]
          —— 只含英文术语与证据, 不含中文译名; 中文译名由人工/LLM 裁决后写入。
    """
    proposals = []
    for r in results:
        res = r["res"]
        if not res.get("ok") or not res.get("papers"):
            continue
        strong = [p for p in res["papers"] if p.get("relevance") == "strong"]
        weak = [p for p in res["papers"] if p.get("relevance") == "weak"]
        if not strong or len(strong) < len(weak):
            continue
        # 至少两个实词(降低单词歧义误入术语表的风险)
        words = re.findall(r"[a-z]{3,}", r["term"].lower())
        if len(words) < 2:
            continue
        best = max(strong, key=lambda p: p.get("cited") or 0)
        proposals.append({
            "term": r["term"],
            "evidence_title": best["title"],
            "cited": best.get("cited", 0),
            "year": best.get("year"),
            "suggested_by": "term_verify(evidence-based)",
        })
    return proposals


def load_glossary(path):
    """读取现有术语表(csv, 无表头, 'english,chinese')。返回 dict 与原始行数。"""
    entries = {}
    n = 0
    if not os.path.isfile(path):
        return entries, n
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        n += 1
        if not line or "," not in line:
            continue
        en, _, zh = line.partition(",")
        entries[en.strip().lower()] = zh.strip()
    return entries, n


def write_glossary_updates(path, proposals, zh_by_term):
    """
    把人工/LLM 裁决后的译名写入术语表。
    zh_by_term: {term_lower: 中文译名} —— 必须已经过裁决, 不接受空值。
    返回 (added, skipped_exists, skipped_no_zh)
    """
    existing, _ = load_glossary(path)
    added, skip_exist, skip_nozh = [], [], []
    with open(path, "a", encoding="utf-8", newline="") as f:
        for p in proposals:
            key = p["term"].lower()
            if key in existing:
                skip_exist.append(p["term"])
                continue
            zh = zh_by_term.get(key, "").strip()
            if not zh:
                skip_nozh.append(p["term"])
                continue
            f.write("%s,%s\n" % (p["term"], zh))
            existing[key] = zh
            added.append((p["term"], zh))
    return added, skip_exist, skip_nozh


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)  # tools/ 的上级

    ap = argparse.ArgumentParser(description="存疑术语联网查证器")
    ap.add_argument("src", nargs="?", help="存疑清单 .md 路径（默认取最新一份）")
    ap.add_argument("--limit", type=int, default=0, help="只查前 N 个术语（0=全部）")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="请求间隔秒数（礼貌限流）。默认 2.0s；"
                         "2026-09-02 实测 1.0s 时 18 个术语有 5 个遭 HTTP 429")
    ap.add_argument("--per-term", type=int, default=5, help="每术语取多少篇文献")
    ap.add_argument("--context", default=os.getenv("TERM_CONTEXT", ""),
                    help="领域上下文词，会拼到检索词后以提升通用短词的召回精度 "
                         "(例: --context 'topology optimization')；"
                         "也可用环境变量 TERM_CONTEXT 设置")
    ap.add_argument("--llm-filter", action="store_true",
                    help="[方案 3] 用 LLM 过滤领域偏移的检索结果(默认关)。"
                         "需环境变量 TERM_VERIFY_LLM_BASE / TERM_VERIFY_LLM_KEY；"
                         "直连 LLM 端点, 不经 8787/8788, 对 AI Butler 零影响。"
                         "采用保守策略: LLM 调用失败时保留全部结果不剔除")
    ap.add_argument("--source", default=os.getenv("TERM_VERIFY_SOURCE", "openalex"),
                    help="检索源，逗号可多选，或 all（全部）。可选：%s。"
                         "默认 openalex（与改造前行为一致）。"
                         "zotero 查自己库里的 PDF 全文并给原文上下文（无需网络、命中率最高），"
                         "wikidata 能直接给出中文名，wikipedia 给出定义性语境（后两者需代理）。"
                         "也可用环境变量 TERM_VERIFY_SOURCE 设置"
                         % ", ".join(SOURCES))
    ap.add_argument("--out", help="输出报告路径（默认与源清单同目录）")
    ap.add_argument("--glossary", default=os.getenv("POLISH_GLOSSARY",
                    os.path.join(project_root_env(), "server", "glossary", "terms.csv")),
                    help="术语表 csv 路径(无表头, 'english,chinese')。"
                         "默认取 POLISH_GLOSSARY 环境变量, 其次 server/glossary/terms.csv")
    ap.add_argument("--propose", action="store_true",
                    help="[方案 2] 在报告末尾附上术语表新增建议(仅强证据短语, 不含中文译名)")
    args = ap.parse_args()

    try:
        sources = resolve_sources(args.source)
    except ValueError as e:
        print("[ERROR] %s" % e, file=sys.stderr)
        return 2

    src = args.src or find_latest_doubt_md(project_root)
    if not src or not os.path.isfile(src):
        print("[ERROR] 未找到存疑清单，请显式指定路径", file=sys.stderr)
        return 2

    print("[INFO] 源清单: %s" % src)
    print("[INFO] 检索源: %s" % " + ".join(sources))
    stats = {}
    items = parse_doubt_md(src, stats)
    print("[INFO] 提取到 %d 个存疑术语" % len(items))
    if stats.get("junk"):
        print("[INFO] 丢弃 %d 个模板占位串（如「中文译名（英文原文）」），它们不是术语"
              % stats["junk"])
    if not items:
        print("[WARN] 未提取到术语，检查清单格式是否变化", file=sys.stderr)
        return 1

    if args.limit > 0:
        items = items[:args.limit]
        print("[INFO] 按 --limit 仅处理前 %d 个" % len(items))

    if args.context:
        print("[INFO] 领域上下文: %s" % args.context)

    # [方案 3] LLM 过滤: 仅在显式开启且环境变量齐备时启用。
    # 未配置/调用失败时 llm_judge_relevance 返回 None, 自动跳过, 不影响主流程。
    use_llm = args.llm_filter
    if use_llm:
        has_cfg = bool(os.getenv("TERM_VERIFY_LLM_BASE", "").strip()
                       and os.getenv("TERM_VERIFY_LLM_KEY", "").strip())
        if not has_cfg:
            print("[WARN] --llm-filter 已指定，但未配置 TERM_VERIFY_LLM_BASE / "
                  "TERM_VERIFY_LLM_KEY，自动跳过过滤（结果不受影响）")
            use_llm = False
        else:
            print("[INFO] LLM 过滤已启用 (model=%s)"
                  % os.getenv("TERM_VERIFY_LLM_MODEL", "qwen-plus"))

    # 预检：剔除不可达源，避免每个术语在死源上空转几十秒。
    # 放在解析完清单之后，这样输入文件有问题时不必白跑一次网络探测。
    sources, dropped = preflight_sources(sources)
    for name, why in dropped:
        print("[WARN] 源不可达，已剔除: %s —— %s" % (name, why), file=sys.stderr)
    if not sources:
        print("[ERROR] 所有检索源均不可达，不产出报告。", file=sys.stderr)
        print("        维基百科/维基数据在国内网络不可直连（DNS 污染 / TLS 阻断）；"
              "若需使用，请先启用代理并设置 $env:HTTPS_PROXY，urllib 会自动读取该变量。",
              file=sys.stderr)
        return 2
    if dropped:
        print("[INFO] 实际使用源: %s" % " + ".join(sources))

    results = []
    t0 = time.time()
    for i, it in enumerate(items, 1):
        print("[%d/%d] 查证: %s" % (i, len(items), it["term"]))
        res = search_multi(it["term"], sources, max_results=args.per_term,
                           context=args.context)

        if use_llm and res["ok"] and res["papers"]:
            verdict = llm_judge_relevance(
                it["term"],
                [p["title"] for p in res["papers"]],
                context=args.context,
            )
            if verdict is None:
                res["llm_note"] = "LLM 过滤不可用/失败，已保留全部结果"
            else:
                kept = []
                # 注意: 变量名不能叫 dropped —— 外层 dropped 是预检剔除的源清单,
                # 重名会把报告头那行"已剔除不可达源"污染成 LLM 剔除的条目。
                llm_dropped = []
                for p in res["papers"]:
                    v = verdict.get(p["title"])
                    if v is None:
                        kept.append(p)          # LLM 未覆盖 -> 保留(保守)
                        continue
                    if v["relevant"]:
                        p["llm_reason"] = v["reason"]
                        kept.append(p)
                    else:
                        llm_dropped.append((p["title"], v["reason"]))
                res["papers"] = kept
                res["llm_dropped"] = llm_dropped
                res["llm_note"] = ("LLM 过滤：保留 %d 条，剔除 %d 条"
                                   % (len(kept), len(llm_dropped)))
        elif use_llm:
            res["llm_note"] = "无检索结果，跳过 LLM 过滤"

        results.append({
            "term": it["term"], "para": it["para"],
            "note": it["note"], "res": res,
        })
        if i < len(items):
            time.sleep(args.delay)
    elapsed = time.time() - t0

    report = build_report(src, results, elapsed, sources, dropped, stats.get("junk", 0))

    # [方案 2] 术语表新增建议(只提英文术语+证据, 中文译名留人工/LLM 裁决)
    if args.propose:
        proposals = propose_glossary_updates(results, context=args.context)
        existing, total_rows = load_glossary(args.glossary)
        new_items = [p for p in proposals
                     if p["term"].lower() not in existing]
        report += "\n\n---\n\n## 术语表新增建议（供裁决，不自动写入）\n\n"
        report += ("- 依据：仅收录**强证据**(标题含全部实词)且强证据数不少于弱证据的**短语术语**；\n"
                   "- 单词/缩写歧义大，一律不自动建议；\n"
                   "- **不含中文译名**——请人工(或让 LLM 依据证据)裁决后，"
                   "手动追加到 `%s`（格式 `english,chinese`）。\n\n" % args.glossary)
        if not new_items:
            report += "（本轮无符合条件的新术语）\n"
        else:
            report += "| 英文术语 | 依据文献(被引最高) | 年份 | 被引 |\n|---|---|---|---|\n"
            for p in new_items:
                y = p["year"] if p["year"] else "-"
                report += "| %s | %s | %s | %d |\n" % (
                    p["term"], p["evidence_title"], y, p["cited"])
            report += ("\n当前术语表共 %d 行；本轮可新增 %d 条（已有 %d 条命中现有表，自动跳过）\n"
                       % (total_rows, len(new_items),
                          len(proposals) - len(new_items)))

    out = args.out or os.path.join(
        os.path.dirname(src),
        "术语查证报告_%s.md" % time.strftime("%Y%m%d_%H%M%S"),
    )
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)

    ok = sum(1 for r in results if r["res"]["ok"])
    n_skip = sum(1 for r in results
                 if not r["res"]["ok"] and r["res"].get("skipped"))
    print("[DONE] 报告已生成: %s" % out)
    if n_skip:
        print("[DONE] 成功 %d ｜ 跳过 %d ｜ 共 %d ｜ 耗时 %.1fs"
              % (ok, n_skip, len(results), elapsed))
    else:
        print("[DONE] 成功 %d / 共 %d ｜ 耗时 %.1fs" % (ok, len(results), elapsed))
    return 0 if (ok or n_skip == len(results)) else 1


if __name__ == "__main__":
    sys.exit(main())
