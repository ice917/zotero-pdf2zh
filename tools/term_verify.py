# -*- coding: utf-8 -*-
"""
tools/term_verify.py —— 存疑术语联网查证器（离线批处理，零侵入）

模块职责：
  读取 zotero-pdf2zh 产出的「存疑清单.md」，提取其中的存疑术语，
  直连 OpenAlex 学术 API 检索该术语的真实用法上下文，输出一份
  「术语 + 证据」报告，辅助人工（或 LLM）裁决译法。

关键函数：
- parse_doubt_md(path)              解析存疑清单，提取 (术语, 段落号, 冲突描述)
- openalex_search(term, n, timeout) 检索术语的学术上下文证据
- build_report(items)               组装 Markdown 报告
- main()                            命令行入口

设计原则（针对 AI Butler 联动场景）：
1. **直连 OpenAlex，不经过 local_mcp_server(8788)**。
   OpenAlex 是零 Key 公开 API（日配额 10 万次），无需走本地 MCP。
   这样可以完全规避三条耦合风险：
     - 共享缓存污染（8788 的 _caches 是进程级全局，会被按 LRU 挤占）
     - 熔断级联（8788 变慢 → qwen_proxy 超时重试 → CircuitBreaker 开闸）
     - 外部 API 限流竞争
   对 AI Butler 零影响。
2. **优雅降级**：任一术语查询失败只跳过该项，不影响整体；
   网络不可用时不产出报告，退出码非 0。
3. **礼貌限流**：请求间隔可配（默认 1.0s），遵守 OpenAlex 礼貌池约定。
4. **只读**：不修改任何翻译产物，不写入术语表，仅输出报告文件。

用法：
  python tools/term_verify.py                         # 处理最新的存疑清单
  python tools/term_verify.py <存疑清单.md>           # 指定文件
  python tools/term_verify.py <文件> --limit 10       # 只查前 10 个术语
  python tools/term_verify.py <文件> --delay 2.0      # 放慢请求间隔
"""

import argparse
import glob
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

REVIEW_DIRNAME = "review"


def project_root_env():
    """项目根目录（tools/ 的上级），供 --glossary 默认值使用"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------
def parse_doubt_md(path):
    """
    解析存疑清单，返回 [{'term': 术语, 'para': 段落号, 'note': 冲突描述}, ...]

    清单结构（由 pdf2zh 润色管线产出）：
        ## 存疑段落 3
        **原文**
        ...
        **初译(保持未动)**
        ...
        **存疑点**
        1. 术语"topology optimization"
           - 冲突点：...
        ---
    """
    text = open(path, encoding="utf-8").read()

    # 按 "## 存疑段落 N" 切块
    chunks = re.split(r"^##\s*存疑段落\s*(\d+)\s*$", text, flags=re.M)
    # chunks = [前缀, 段号, 内容, 段号, 内容, ...]
    items = []
    seen = set()

    for i in range(1, len(chunks) - 1, 2):
        para_no = chunks[i]
        body = chunks[i + 1]

        # 只取 "**存疑点**" 之后的部分
        m = re.search(r"\*\*存疑点\*\*(.*?)(?=\n---|\Z)", body, flags=re.S)
        if not m:
            continue
        doubt_block = m.group(1)

        # 提取术语：支持 术语"xxx" / 术语“xxx” / 专有名词"xxx"
        for tm in re.finditer(
            r'(?:术语|专有名词|短语)\s*["“"]([^"“”]{1,80})["”"]', doubt_block
        ):
            term = tm.group(1).strip()
            if not term or term in seen:
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


def openalex_search(term, max_results=5, timeout=8, context="", max_retries=4):
    """
    检索术语的学术上下文证据。
    返回 {'ok': bool, 'papers': [{'title','year','cited'}...], 'error': str}

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

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break  # 成功, 跳出重试循环
        except urllib.error.HTTPError as e:
            last_err = "HTTP %s: %s" % (e.code, e.reason)
            # 仅对 429(限流) / 5xx(服务端错误) 重试, 4xx 客户端错误不重试
            if e.code == 429 or 500 <= e.code < 600:
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)      # 2s, 4s, 8s
                    time.sleep(wait)
                    continue
            return {"ok": False, "papers": [], "error": last_err}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # [自研补丁 2026-09-03] 超时/连接重置/网络抖动同样退避重试,
            # 旧实现只覆盖 HTTP 429/5xx, 网络类错误立即放弃导致整词漏检
            last_err = "%s: %s" % (type(e).__name__, e)
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                time.sleep(wait)
                continue
            return {"ok": False, "papers": [], "error": last_err}
        except Exception as e:
            return {"ok": False, "papers": [], "error": "%s: %s" % (type(e).__name__, e)}
    else:
        return {"ok": False, "papers": [], "error": last_err or "未知错误"}

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


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------
def build_report(src_path, results, elapsed):
    lines = []
    lines.append("# 存疑术语查证报告")
    lines.append("")
    lines.append("- 源清单：`%s`" % os.path.basename(src_path))
    lines.append("- 查证数据源：OpenAlex（零 Key 公开 API，直连，不经本地 MCP）")
    lines.append("- 术语总数：%d ｜ 查询成功：%d ｜ 失败：%d ｜ 耗时：%.1fs"
                 % (len(results),
                    sum(1 for r in results if r["res"]["ok"]),
                    sum(1 for r in results if not r["res"]["ok"]),
                    elapsed))
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
        if not res["ok"]:
            lines.append("- **查证失败**：%s" % res["error"])
            lines.append("")
            continue
        if not res["papers"]:
            lines.append("- **未找到高相关文献**：OpenAlex 未检索到标题含该术语的文献")
            lines.append("  （可能术语过于具体、拼写特殊，或需补充 `--context` 领域上下文）")
            lines.append("")
            continue
        if res.get("llm_note"):
            lines.append("- %s" % res["llm_note"])
        lines.append("- 文献语境证据：")
        n_weak = sum(1 for p in res["papers"] if p.get("relevance") == "weak")
        if n_weak:
            lines.append("  - ⚠️ 其中 **%d 条为弱证据**（标题仅含部分词，可能领域偏移），"
                         "请优先参考未标记的强证据" % n_weak)
        for p in res["papers"]:
            y = p["year"] if p["year"] else "n.d."
            tag = "  [弱]" if p.get("relevance") == "weak" else ""
            lines.append("  - (%s, 被引 %d)%s %s" % (y, p["cited"], tag, p["title"]))
            if p.get("llm_reason"):
                lines.append("    - LLM 判据：%s" % p["llm_reason"])
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
    ap.add_argument("--out", help="输出报告路径（默认与源清单同目录）")
    ap.add_argument("--glossary", default=os.getenv("POLISH_GLOSSARY",
                    os.path.join(project_root_env(), "server", "glossary", "terms.csv")),
                    help="术语表 csv 路径(无表头, 'english,chinese')。"
                         "默认取 POLISH_GLOSSARY 环境变量, 其次 server/glossary/terms.csv")
    ap.add_argument("--propose", action="store_true",
                    help="[方案 2] 在报告末尾附上术语表新增建议(仅强证据短语, 不含中文译名)")
    args = ap.parse_args()

    src = args.src or find_latest_doubt_md(project_root)
    if not src or not os.path.isfile(src):
        print("[ERROR] 未找到存疑清单，请显式指定路径", file=sys.stderr)
        return 2

    print("[INFO] 源清单: %s" % src)
    items = parse_doubt_md(src)
    print("[INFO] 提取到 %d 个存疑术语" % len(items))
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

    results = []
    t0 = time.time()
    for i, it in enumerate(items, 1):
        print("[%d/%d] 查证: %s" % (i, len(items), it["term"]))
        res = openalex_search(it["term"], max_results=args.per_term,
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
                dropped = []
                for p in res["papers"]:
                    v = verdict.get(p["title"])
                    if v is None:
                        kept.append(p)          # LLM 未覆盖 -> 保留(保守)
                        continue
                    if v["relevant"]:
                        p["llm_reason"] = v["reason"]
                        kept.append(p)
                    else:
                        dropped.append((p["title"], v["reason"]))
                res["papers"] = kept
                res["llm_dropped"] = dropped
                res["llm_note"] = ("LLM 过滤：保留 %d 条，剔除 %d 条"
                                   % (len(kept), len(dropped)))
        elif use_llm:
            res["llm_note"] = "无检索结果，跳过 LLM 过滤"

        results.append({
            "term": it["term"], "para": it["para"],
            "note": it["note"], "res": res,
        })
        if i < len(items):
            time.sleep(args.delay)
    elapsed = time.time() - t0

    report = build_report(src, results, elapsed)

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
    print("[DONE] 报告已生成: %s" % out)
    print("[DONE] 成功 %d / 共 %d ｜ 耗时 %.1fs" % (ok, len(results), elapsed))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
