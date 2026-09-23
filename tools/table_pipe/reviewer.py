# -*- coding: utf-8 -*-
"""reviewer.py — 语义审核者(硅基流动): 中继面板「④ 语义审核」的后端

为什么有它: 门禁(seg_import / check_job)只判**机器判得了**的东西 —— 编号齐不齐、
字形/占位符有没有落点、⋮ 对不对。语义错译(半句丢失、否定颠倒、数字单位错位、
术语漂移)它一概够不到; 面板的「待看清单」也只给读数(长度比/一致性), 不是判断。
这类错目前只能靠用户通读整篇去发现 —— 本件把这一层补上。

分工(照 relay_spec.md 第 4 条红线):
  翻译者是豆包, 审核者**必须与它无关** —— 被翻译方不得自校。故审核走硅基流动
  (DeepSeek-V3.2, 付费, **手动触发**), 而不是把译文再喂回豆包问"你译得对吗"。
  校验权仍在本机: 门禁是硬门(不过不落盘), 本件只是**给用户的存疑清单**,
  不参与放行判定。

**只审不改**(采纳模式的底线): 审核者只报问题, 绝不产出改写稿 —— 外来定稿文本
再过一遍生成式 LLM 只会引入风险, 这是润色钩子退役时立下的规矩。改不改、怎么改
由用户裁决; 要返工再由用户把清单交给豆包执行。

范围: 逐段「原文↔译文」对照(漏译/错译/数字/术语/指代/表达)。**全文级**的跨段指代、
接缝断裂、文风突变是军师层(strategist.py)的活, 本件不重复, 也不报机械门禁已覆盖的项
(编号/占位符/⋮/标点) —— 那是噪音, 会把自己报出的真问题淹掉。

[v28.75] 责任划分: 审核者要说清"错在译文还是在原文"。老扫描件的文本层缺陷(形近字母数字
混淆/缺字/断字 —— `011`=on、`340/00`=34‰、`lao mellae`=lamellae)在**原文里就已存在**,
而译者被载荷规则硬性要求**逐字符照抄**原文数字与字母(机检按字形落点验收)。把这类报成
「数字」「错译」＝ 让用户拿着返工单去找翻译方改一个**改不动**的东西(改了反而破坏字形
回锚)。故单列 kind = 源文缺陷, 报告里与译文侧存疑分节呈现, 面板一键复制的返工单
**不含**它们(见 split_src)。

[v28.76] 术语入口: 本件只报不改,**也不负责裁决** —— 但裁决完得有个落点。④ 报出
「术语」存疑之后, 面板可就地填「原词 + 译名」写进术语表(见 append_terms): 两份表
(1.x 的 terms.csv 与 BabelDOC 的 terms.babeldoc.csv, 格式不同)一次写齐。此前这一步
全靠人工开 csv, 而"漏同步第二份"是**静默失效**(换引擎那个词就不生效, 无处报错)。

密钥: 环境变量 SILICON_API_KEY / SILICON_MODEL / SILICON_BASE_URL 优先,
否则读 server/config/config.json 的 translators[silicon].envs(与 strategist.py 同一口径)。
零依赖: urllib 直连 OpenAI 兼容端点, 不引 openai 包 —— 本件要被零依赖的 panel.py 直接 import。
"""
import concurrent.futures
import io
import json
import os
import re
import time
import urllib.error
import urllib.request

PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
SRV_CFG = os.environ.get("P2Z_SRV_CFG",
                         os.path.join(PROJ, "server", "config", "config.json"))
TERMS_CSV = os.path.join(PROJ, "server", "glossary", "terms.csv")
REVIEW_DIR = os.environ.get("P2Z_REVIEW_DIR",
                            os.path.join(PROJ, "server", "translated", "review"))

BASE_DEFAULT = "https://api.siliconflow.cn/v1"
MODEL_DEFAULT = "deepseek-ai/DeepSeek-V3.2"
CHUNK_CHARS = 14000      # 每块「原文+译文」字符预算: 整篇一次发会超上下文也难定位
WORKERS = 3              # 并发块数(硅基流动允许; 串行发一篇 8 块要好几分钟)
TIMEOUT = 300            # 单块超时(秒)
MAX_TOKENS = 8192
MAX_NOTE = 300           # 单条说明截断
MAX_QUOTE = 120          # 原文片段截断
MAX_ITEMS = 200          # 存疑条数上限(超过只留前 N 条, 免得面板被淹)

# 「源文缺陷」(v28.75): 错的**根不在译文**而在原文自己 —— 老扫描件的文本层形近混淆
# (`011`=on / `110`=no / `III`=in / `IS`=15 / `340/00`=34‰)、缺字(`nutnt~vely`)、
# 断字(`lao mellae`=lamellae)、双空格断词(`essen  tially`)。译者被载荷规则硬性要求
# **逐字符照抄**原文的数字与字母(机检按字形落点验收), 所以"译者没把原文的笔误改对"
# 不是译者的错; 把它报成「数字」「错译」会让用户拿着返工单去找翻译方改一个**改不动**
# 的东西(改了反而破坏字形回锚)。单列一类, 与译文侧的存疑分开呈现。
SRC_DEFECT = "源文缺陷"
KINDS = ("漏译", "错译", "数字", "术语", "指代", "表达", SRC_DEFECT)
# 源文缺陷的线索词排在最前: 模型常写「原文数字笔误」「源文漏字」这种复合标签, 若让
# ("数字",…) 先命中就会把它退回「译文侧数字错」—— 正是本类要治的错归因。
_KIND_HINT = (("源文", SRC_DEFECT), ("笔误", SRC_DEFECT),
              ("印刷", SRC_DEFECT), ("扫描", SRC_DEFECT), ("OCR", SRC_DEFECT),
              ("ocr", SRC_DEFECT),
              ("漏", "漏译"), ("错", "错译"), ("数字", "数字"), ("数值", "数字"),
              ("单位", "数字"), ("术语", "术语"), ("指代", "指代"), ("代词", "指代"),
              ("表达", "表达"), ("病句", "表达"), ("流畅", "表达"))

_RULES = """你是学术论文译文的独立审核者。下面按 #S 编号逐段给出「原文 -> 译文」。
任务: 找出**译文引入的**语义性错误。只报问题, **绝对不要改写或重译任何一句**。

[先分清责任: 错在译文, 还是错在原文]
本管线的译者被**硬性要求逐字符照抄**原文里的数字/字母/符号(机检按字形落点验收)。
所以"译文与原文一字不差、但这个错是原文自己就有的"**不是译文错误** —— 那是扫描/排版
留下的**源文文本层缺陷**: 形近字母数字混淆、缺字、断字。
- 判据只有一个: 把译文与原文逐字比 —— **这个错在原文里已经存在**就是源文缺陷;
  只有"原文正确而译文走样"才算译文的错。
- 这类归到 kind = 源文缺陷, 并在说明里写清你**推测的原意**(如某个数字串疑似应为
  哪个数值、某个字母串疑似应为哪个词), 供人在**原文侧**校勘。
- 不要因为"译者没把原文的笔误改对"而指责译者; 更不要建议按推测原意改写译文
  (那会破坏字形回锚, 是硬门禁不允许的)。

[审这些]
1. 漏译: 原文有完整语义而译文明显缺失(不是"更简略", 是半句/整句没落地)。
2. 错译: 语义与原文不符 —— 张冠李戴、否定颠倒、因果/条件/主客关系搞反。
3. 数字: 数值、比例、量纲、范围、上下标与原文不符(如原文 0.69 译文写成 0.63)。
4. 术语: 同一原文术语在本篇里译法不统一, 或与下面给出的术语表冲突。
5. 指代: 代词/连接词指错对象, 或原句主语被译没了导致歧义。
6. 表达: 病句/机翻腔严重到影响理解(只在这种程度才报)。
7. 源文缺陷: 错的根在原文自身(见上面一节)。这类只报给**人**做原文校勘,
   不会变成译者的返工单。

[不要报]
- 编号是否齐全、占位符/特殊字形是否原样、⋮ 断点在不在、半角还是全角标点 —— 一律不报,
  这些由本机机械门禁负责, 报了纯属噪音。
- **文后参考文献表**的条目: 按载荷规则整条照抄, 作者名/年份/刊名/卷期页码一律不汉化、
  不改拼写。因此"作者名照抄没音译"、"与正文里对同一文献的汉化称呼不一致"
  都**不算问题**。
- 原文用「作者(年份)」代指被提到的研究对象, 是该体例的固有写法(意为"据该作者某年报道,
  该对象是…"): 译文照搬这个体例不算错译, 别报主语错。
- "还可以更通顺/更优雅/建议加主语"这类改进意见一律不报。
- 不要凑数: 没问题的段不要出现在结果里; 整篇没问题就直接输出 []。

[输出] 只输出 JSON 数组, 不要解释文字、不要 markdown 代码围栏。每项形如:
{"id":"S12","kind":"漏译|错译|数字|术语|指代|表达|源文缺陷","level":"高|中","note":"一句话说清错在哪(中文)","quote":"原文中出问题的那一小段原文(照抄, 便于定位)"}
level: 高 = 事实错误或影响理解; 中 = 局部瑕疵。
"""


def config():
    """-> {"key", "model", "base"}; 缺密钥时 key 为空(调用方据此报错)。"""
    key = (os.environ.get("SILICON_API_KEY") or "").strip()
    model = (os.environ.get("SILICON_MODEL") or "").strip()
    base = (os.environ.get("SILICON_BASE_URL") or "").strip()
    if not key and os.path.exists(SRV_CFG):
        try:
            with io.open(SRV_CFG, encoding="utf-8") as f:
                cfg = json.load(f)
            for t in cfg.get("translators", []):
                if t.get("name") == "silicon":
                    envs = t.get("envs", {})
                    key = (envs.get("SILICON_API_KEY") or "").strip()
                    model = model or (envs.get("SILICON_MODEL") or "").strip()
                    break
        except Exception:
            pass
    return {"key": key, "model": model or MODEL_DEFAULT,
            "base": (base or BASE_DEFAULT).rstrip("/")}


# ---- 同源守卫(v28.62): 翻译方换了, 审核方就得跟着查一遍 -------------------------------
# relay_spec §4.5「校验权不外放」的落点: **被翻译方不得自校**。剪贴板这条路既然能换家
# (豆包/ChatGPT/Claude/Kimi/…), 就存在"翻译方与审核者是同一家模型"的组合 —— 那时审核
# 只是自我确认, 必须拒审。判据刻意粗糙(按模型名认族), 因为宁可多拦一次(换个翻译方或
# 换个审核者就能继续), 也不能放过"自己给自己打分"。
_FAMILY_KEYS = (
    ("deepseek", ("deepseek", "深度求索")),
    ("doubao", ("doubao", "豆包", "seed-", "skylark")),
    ("chatgpt", ("gpt-", "chatgpt", "o1-", "o3-", "o4-")),
    ("claude", ("claude",)),
    ("kimi", ("moonshot", "kimi")),
    ("qwen", ("qwen", "通义")),
)


def family_of(name):
    """按模型名/厂商名认族(小写包含即认; 中文名也认)。认不出返回 ""。"""
    s = (name or "").lower()
    for fam, keys in _FAMILY_KEYS:
        if any(k in s for k in keys):
            return fam
    return ""


def conflicts(vendor_family, cfg=None):
    """翻译方族名与当前审核者族名是否**同源** -> 同源返回族名, 否则 ""。

    认不出族名(空)不算冲突 —— 不确定就不拦, 但审核报告里写明审核者模型, 用户能自己看出
    是不是"自己打自己"。
    """
    fam = (vendor_family or "").strip().lower()
    if not fam:
        return ""
    return fam if fam == family_of((cfg or config())["model"]) else ""


def load_terms(path=None):
    """术语表 -> [(en, zh)]。注释行不可见(与 seg_export.load_terms 同口径:
    `#` 开头跳过; 字段数 < 2 跳过 —— 故注释里不能带逗号)。表不在或读不动返回 []。"""
    out = []
    p = path or TERMS_CSV
    try:
        with io.open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                parts = [x.strip() for x in ln.split(",")]
                if len(parts) >= 2 and parts[0] and parts[1]:
                    out.append((parts[0], parts[1]))
    except OSError:
        return []
    return out


# ------------------------------------------------------- 术语裁决入表 (v28.76)
# ④ 报出「术语」存疑之后的落点。此前这一段**全是手工**: 跑 term_verify 取证 -> 人裁决 ->
# 打开 terms.csv 追加一行 -> **再记得同步 terms.babeldoc.csv**。最后一步是静默陷阱: 两表
# 格式不同(见 terms_files), 漏写那份不报任何错, 换个引擎那个词就不生效了(实测没人会记得)。
TERMS_BABELDOC = os.path.join(PROJ, "server", "glossary", "terms.babeldoc.csv")
TERM_EN_MAX = 120        # 表里存的是**词/短语**; 上限用来拦住"把整句原文粘进来" ——
TERM_ZH_MAX = 60         # 长的匹配不上, 写进去等于白记, 还会污染审核者的术语判据。


def terms_files():
    """要同步写入的术语表 —— 两件**格式不同**, 各按自己的格式写(见 append_terms):
      terms.csv          'english,chinese' 无表头; '#' 注释行是来源/判据, 读取端一律跳过
      terms.babeldoc.csv 'source,target' **带表头**, 不能掺注释行(BabelDOC 的解析器)
    1.x 侧读前者(POLISH_GLOSSARY / seg_export 默认); next/BabelDOC 侧读后者。"""
    return [TERMS_CSV, TERMS_BABELDOC]


def _append_lines(path, lines):
    """按文件**自己**的换行与结尾形态追加 —— 混用 CRLF/LF 会让整文件 diff 变脏(实测
    terms.csv 是 LF、terms.babeldoc.csv 是 CRLF); 少个结尾换行会把两行粘成一条, 而术语表
    读取端按行解析, 粘了就**丢一条**(还不报错)。"""
    with open(path, "rb") as f:
        raw = f.read()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    head = "" if (not raw or raw.endswith((b"\n", b"\r"))) else nl
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(head + nl.join(lines) + nl)


def _same_source_tail(path, note, day):
    """terms.csv 里**最近一条注释**是否就是同来源同日的 `# 来源`(同一次审核连着加词只写一次)。

    看的是「最近一条注释」而不是「最后一行」: 注释是写在它那批词条**前面**的, 追加完
    第一条词之后最后一行就变成词条了 —— 若按最后一行判, 这条判据永远不成立, 每加一个词
    都会再写一遍来源注释(实测拦到)。
    """
    try:
        with io.open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return False
    last = next((ln for ln in reversed(lines) if ln.startswith("#")), None)
    return bool(last and last.startswith("# 来源:") and day in last and note in last)


def append_terms(en, zh, src="", day=None, paths=None):
    """录入一条术语裁决 -> (status, msg); status ∈ written / exists / error。

    [v28.76] 去重以**第一份表**(terms.csv)为准: en 已存在即拒绝, 并报出表里的译名 ——
    同一个原词给两个中文名, 会自己触发审核者的术语一致性判据(terms.csv 抬头那条)。
    比对按小写: `Ovigerous lamellae` 与 `ovigerous lamellae` 是同一个词。
    """
    en, zh = (en or "").strip(), (zh or "").strip()
    if not en or not zh:
        return "error", "原词与译名都要填。"
    if "," in en or "," in zh:
        return "error", "原词/译名里不能有半角逗号 —— csv 就是用逗号分列的。"
    if any(c in en + zh for c in "\r\n"):
        return "error", "原词/译名不能换行。"
    if len(en) > TERM_EN_MAX or len(zh) > TERM_ZH_MAX:
        return "error", ("太长了(原词 %d 字符 / 译名 %d 字符): 术语表存的是**词或短语**, "
                         "整句原文匹配不上 —— 写进去等于白记, 还会污染审核者的术语判据。"
                         % (len(en), len(zh)))
    files = list(paths or terms_files())
    have = next((z for e, z in load_terms(files[0]) if e.lower() == en.lower()), None)
    if have is not None:
        if have == zh:
            return "exists", "表里已有「%s = %s」, 未重复写入。" % (en, have)
        return "error", ("表里「%s」的译名是「%s」—— 一个原词两个中文名会自己触发术语"
                         "一致性判据; 要改请直接改表(或先删旧行)。" % (en, have))

    day = day or time.strftime("%Y-%m-%d")
    note = re.sub(r"[,，]", " ", (src or "").strip()) or "篇名未记"   # 注释里不能有逗号
    done, missed = [], []
    for p in files:
        if not os.path.exists(p):
            missed.append(os.path.basename(p))
            continue
        try:
            if os.path.basename(p).startswith("terms.babeldoc"):
                _append_lines(p, ["%s,%s" % (en, zh)])
            else:
                lines = []
                if not _same_source_tail(p, note, day):
                    lines.append("# 来源: %s —— %s ④ 语义审核术语裁决" % (note, day))
                lines.append("%s,%s" % (en, zh))
                _append_lines(p, lines)
            done.append(os.path.basename(p))
        except OSError as e:
            missed.append("%s(%s)" % (os.path.basename(p), e))
    if not done:
        return "error", "一个文件都没写成: %s。" % ", ".join(missed)
    msg = "已写入 %s: %s = %s。" % (" + ".join(done), en, zh)
    if missed:
        msg += "⚠️ 未写: %s —— 那份表读不到, 它那边的词不会生效。" % ", ".join(missed)
    msg += "含该词的段会失效重译; 走豆包这条路要把载荷重新导出(第 4 条术语表变了)再送那几段。"
    return "written", msg


def split_chunks(pairs, budget=CHUNK_CHARS):
    """按「原文+译文」字符预算切块, 保序不丢编号。单段超预算的自成一块
    (宁可超也不切段 —— 切了段, 审核者就看不到完整上下文了)。"""
    chunks, cur, n = [], [], 0
    for p in pairs:
        size = len(p[1]) + len(p[2])
        if cur and n + size > budget:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += size
    if cur:
        chunks.append(cur)
    return chunks


def _prompt(pairs, doc="", terms=None):
    out = [_RULES]
    if doc:
        out.append("[所在文档] %s\n" % doc)
    if terms:
        out.append("[术语表] (约束译法, 译文与它冲突即为问题)\n"
                   + "; ".join("%s -> %s" % (a, b) for a, b in terms) + "\n")
    out.append("[待审段落 %d 段]\n" % len(pairs))
    for uid, orig, zh in pairs:
        out.append("[#%s]\n原文: %s\n译文: %s" % (uid, orig, zh))
    return "\n".join(out)


def _chat(prompt, cfg, timeout=TIMEOUT):
    """一次 OpenAI 兼容调用, 返回正文。失败抛异常(带 HTTP 正文, 便于定位)。"""
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        # 审核只要结论, 不要推理过程: 思考 token 既慢又会让 JSON 被 max_tokens 截断
        "enable_thinking": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["key"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError("HTTP %s %s" % (e.code, detail))
    return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def _norm_kind(kind):
    k = str(kind or "")
    for hint, name in _KIND_HINT:
        if hint in k:
            return name
    return "其他"


def parse_items(content, valid_ids):
    """模型回包 -> 规范化条目列表。**宽容解析**: 围栏/前后废话都容忍; 编号不在本块
    范围内、或说明为空的条目直接丢弃(宁可少报, 不可把幻觉的段号写进清单)。"""
    m = re.search(r"\[.*\]", content or "", flags=re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    low = {str(v).lower(): str(v) for v in valid_ids}
    out, seen = [], set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        uid = low.get(str(it.get("id") or "").strip().lstrip("#").lower())
        note = str(it.get("note") or "").strip()[:MAX_NOTE]
        if not uid or not note:
            continue
        row = (uid, _norm_kind(it.get("kind")),
               "高" if str(it.get("level") or "").strip().startswith("高") else "中",
               note, str(it.get("quote") or "").strip()[:MAX_QUOTE])
        if row[:4] in seen:                      # 同段同类型同说明 = 重复报, 去掉
            continue
        seen.add(row[:4])
        out.append(row)
    return out


def _audit_chunk(chunk, doc, terms, cfg):
    """单块审核 -> (条目列表, 错误串)。异常不外抛: 一块失败不该拖垮整篇。"""
    ids = [p[0] for p in chunk]
    try:
        content = _chat(_prompt(chunk, doc, terms), cfg)
    except Exception as e:
        return [], "%s" % e
    return parse_items(content, ids), ""


def audit(pairs, doc="", terms=None, cfg=None, workers=WORKERS, log=None):
    """整篇审核。pairs = [(编号, 原文, 译文)]。

    -> {"ok", "items", "n_units", "n_chunks", "n_failed", "chars", "secs",
        "model", "report", "logs"} 或 {"ok": False, "err"}。
    单块失败只计入 n_failed 并在 logs 里点名, 其余块的结论照常返回 ——
    审核结果是给用户看的参考, 不该因为一次网络抖动整篇作废。
    """
    cfg = cfg or config()
    if not cfg.get("key"):
        return {"ok": False, "err": "未配置 SILICON_API_KEY: 请设环境变量, 或在 %s 的 "
                                    "translators[silicon].envs 里填。" % SRV_CFG}
    pairs = [(str(u), o or "", z or "") for u, o, z in (pairs or [])]
    if not pairs:
        return {"ok": False, "err": "没有可比对的段落(载荷里没有原文)。"}
    chunks = split_chunks(pairs)
    terms = list(terms or [])
    logs = ["审 %d 段 / %d 块, 模型 %s%s"
            % (len(pairs), len(chunks), cfg["model"],
               (", 注入术语表 %d 条" % len(terms)) if terms else ", 无术语表")]
    t0 = time.time()

    results = [None] * len(chunks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_audit_chunk, c, doc, terms, cfg): i
                for i, c in enumerate(chunks)}
        for fut in concurrent.futures.as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:                  # 理论上 _audit_chunk 不抛, 兜底
                results[i] = ([], "%s" % e)

    items, failed = [], 0
    for i, (rows, err) in enumerate(results):
        if err:
            failed += 1
            logs.append("块 %d/%d 未完成: %s" % (i + 1, len(chunks), err))
        else:
            logs.append("块 %d/%d 完成: %d 条" % (i + 1, len(chunks), len(rows)))
        items.extend(rows)
    if len(items) > MAX_ITEMS:
        logs.append("存疑 %d 条, 只保留前 %d 条。" % (len(items), MAX_ITEMS))
        items = items[:MAX_ITEMS]

    r = {"ok": True, "items": items, "n_units": len(pairs), "n_chunks": len(chunks),
         "n_failed": failed, "chars": sum(len(a) + len(b) for _, a, b in pairs),
         "secs": round(time.time() - t0, 1), "model": cfg["model"], "report": "",
         "logs": logs}
    r["items"].sort(key=lambda x: 0 if x[2] == "高" else 1)   # 高在前; 稳定排序保块序
    return r


def split_src(items):
    """(译文侧存疑, 源文缺陷) —— 两者呈现与去向都不同, 由这里统一切一次。

    译文侧 -> 可作返工单发给翻译方; 源文缺陷 -> 错的根在原文, 发给翻译方是要人改一个
    **改不动**的东西(改了破坏字形回锚), 只能在原文侧校勘。
    """
    items = list(items or [])
    return ([x for x in items if x[1] != SRC_DEFECT],
            [x for x in items if x[1] == SRC_DEFECT])


def report_md(r, doc="", stem=""):
    """存疑清单落盘文本(与体检/质检报告同目录, 便于事后翻查)。

    审核者与**翻译方**都写进抬头: 换家之后同一篇会有多份报告, 不写清"谁译的、谁审的",
    事后翻出来分不出是哪一版(r["vendor"] 由面板给, 单跑本模块时为空)。

    [v28.75] 两类分节落盘: 译文侧存疑 + 原文文本层缺陷(不由译者承担)。
    """
    trans, src = split_src(r["items"])
    n = len(trans) + len(src)
    concl = "无存疑" if not n else "存疑 %d 条%s" % (
        n, ("(其中源文缺陷 %d 条, 不由译者承担)" % len(src)) if src else "")
    head = ["# 语义审核 (硅基流动 %s)" % r["model"], "",
            "- 时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "- 任务: %s" % (stem or "(未标注)"),
            "- 翻译方: %s" % (r.get("vendor") or "(未标注)"),
            "- 范围: %d 段 / %d 块%s%s"
            % (r["n_units"], r["n_chunks"],
               ("(%d 块未完成)" % r["n_failed"]) if r["n_failed"] else "",
               (", %s" % doc) if doc else ""),
            "- 耗时: %s 秒" % r["secs"],
            "- 结论: %s" % concl,
            "", "> 只审不改: 本报告只列问题, 未产出任何改写稿。是否返工由人裁决。", ""]
    if not n:
        return "\n".join(head + ["(硅基流动未报出语义问题)"]) + "\n"

    def lines(rows):
        out = []
        for uid, kind, level, note, quote in rows:
            out.append("- **#%s** [%s·%s] %s" % (uid, kind, level, note))
            if quote:
                out.append("  - 原文片段: `%s`" % quote.replace("`", "'"))
        return out

    body = head + ["## 存疑清单(译文侧)"]
    body += lines(trans) if trans else ["(无 —— 译文侧没有报出语义问题)"]
    if src:
        body += ["", "## 原文文本层缺陷(不由译者承担, 供原文校勘)",
                 "> 这些「错」在**原文**里就存在(老扫描件的形近混淆/缺字/断字)。管线要求译者",
                 "> 逐字符照抄原文数字与字母(机检按字形落点验收), 故**不要**把本节当返工单",
                 "> 发给翻译方 —— 要改只能改原文, 或另作校勘注。"]
        body += lines(src)
    return "\n".join(body) + "\n"


def write_report(text, stem=""):
    """写报告 -> 路径; 目录不可写返回 ""(报告是辅产物, 不能因此让审核失败)。"""
    try:
        os.makedirs(REVIEW_DIR, exist_ok=True)
        safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", stem or "review")[:40]
        p = os.path.join(REVIEW_DIR, "语义审核_%s_%s.md"
                         % (time.strftime("%Y%m%d_%H%M%S"), safe))
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p
    except OSError:
        return ""
