# -*- coding: utf-8 -*-
"""接缝体检: 列出侧车里所有"跨页接续"的段对, 供人工快速扫出断句与截断误译。

为什么需要它:
  pdf2zh 1.x 逐页流式解析 —— 一句话被页边界切开时, 前段渲染的那一刻后段还没
  解析出来, 所以页内前瞻 (v23.4) 覆盖不到; 缓存层又是"段本地字符串改写", 没有
  跨页重排的修复出口。这类问题只能人工发现后做缓存手术。本脚本把"发现"这一步
  压缩成一条命令, 不必等军师跑 25 分钟。

判据 (零成本, 数据全在侧车里):
  1. 只取"正文段"(剥掉 {vN} 后仍有实义字符), 表格/页脚/纯占位符段跳过;
     相邻两段正文若跨页, 即为一条接缝 —— 中间隔多少表格段都会跨越。实测
     Cactaceae 的 S35(p8) 与 S71(p16) 之间隔着 7 页表格, 按"物理页相邻"配对
     永远找不到, 按"正文段相邻"就现形了;
  2. 标记: 前段译文尾无句末标点 -> "疑断"; 后段原文首为小写字母且前段原文尾
     无句末标点 -> "疑续"; 两条同时成立标 [!] (强疑断句), 单条成立标 [~];
     前段原文尾若落在介词/连词/系动词上, 另标 [悬空词:in] —— 这是"句子在此处
     被硬切断"的证据, 比前两条都硬 (实测它捞出了模型漏判的 S81);
  3. 高噪接缝(图注/章节标题/文献条目/短段密集区)默认折叠, --all 展开 —— 实测
     它们的标记几乎全是 [~], 人工一眼可排除, 却占了清单的一半; 台账登记过的
     不折叠 (已确认是真接缝, 不因"段短/含拉丁学名"再被藏起来);
  4. --with-report 交叉军师报告; 台账里登记过的条目标 [已手术]。

手术台账 (~/.cache/pdf2zh/segflow/surgery_log.json):
  以"原文指纹"(只对 raw 取 md5)为键 —— 手术只改译文, 指纹不变, 所以台账在
  反复重渲染后依然有效; 换了文档则指纹不同, 不会误标。登记:
    & $PY tools/seams_report.py --mark 71 --note "S71 头: 雌花花柱长 -> 雄花具有长花柱,柱头退化"
    & $PY tools/seams_report.py --mark 78,80 --label "Cactaceae 2009" --note "S78+S80 双改"
  没有台账时, 已修好的条目会继续出现在 [!] 里 (原文确实仍被页边界切开), 只能靠人记。

用法 (解释器同 tools/tests/run_all.py; 仓库无 venv 目录, 用绝对路径):
  PY = D:/Users/97638/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  列出全部接缝:   & $PY tools/seams_report.py
  交叉军师报告:   & $PY tools/seams_report.py --with-report
  展开高噪项:     & $PY tools/seams_report.py --all
  写入文件:       & $PY tools/seams_report.py --out seams.txt
  指定侧车/长度:  --sidecar <path> --chars 120

拿到清单之后怎么处理 (详见 改动记录.md):
  - 前段译文语义被扭曲 (如被截断成 "three successful" 后猜成"三倍于") -> 截断误译, 必修;
  - 只是物理切开、两页译文拼起来读得通 -> 默认不修 (补字必然与邻段开头重复);
  - 原文本身就这么排版 (断在参考文献作者名 / 跨多页表格) -> 不动。
"""
import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategist import load_segments, TOKEN_RE   # 复用侧车解析与占位符正则

SIDECAR_DEFAULT = os.path.join(
    os.path.expanduser("~"), ".cache", "pdf2zh", "segflow", "latest.jsonl")
REPORT_DEFAULT = os.path.join(os.path.dirname(SIDECAR_DEFAULT), "strategist_report.json")
LOG_DEFAULT = os.path.join(os.path.dirname(SIDECAR_DEFAULT), "surgery_log.json")

_SENT_END_RE = re.compile(r"[。！？…；]\s*$")        # 译文句末标点
_RAW_END_RE = re.compile(r"[.?!;:]\s*$")             # 原文句末标点
_LOWER_RE = re.compile(r"^[a-z]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# [v26.3] 悬空功能词: 前段原文尾落在介词/连词/冠词/系动词上 = 该句在页边界被切断的
# **硬证据**(这些词后面必须还有成分)。实测 Cactaceae 25 条接缝里有 3 条带此信号:
#   S81 `...fruit set in`      -> 模型漏判为"物理切开"的那条
#   S87 `...indirect estimations were` -> 同一类型, 已手术
#   S130 `...In`(In general)   -> 良性, 译文已正确合并
# 即精确率 2/3 且恰好覆盖漏判 —— 信号一直在数据里, 只是没告诉模型去看。
DANGLING_WORDS = frozenset((
    "in on of to and or for with at by as from into during while when "
    "that which the a an is are was were be been being").split())

SHORT_SEG_CHARS = 45        # "短段"阈值: 文献条目普遍短于正文段
DENSE_WINDOW = 3            # 短段密集区判定窗口 (前后各若干段)
DENSE_NEED = 2              # 窗口内至少还有几个短段
CJK_MIN_BODY = 8            # 汉字数下限: 低于此值的短段判为文献条目
TITLE_MAX_CHARS = 12        # 译文实义长度下限: 低于此值且无句末标点 -> 章节标题
# 注: 曾用"汉字占比 < 35%"判定, 会把含拉丁学名/人名的短正文段误折叠 —— 实测
# S71 (p16) 译文 "雄花具有长花柱, 柱头退化 del Castillo 和 GonzalezEspinoza"
# 占比 29% 被判"文献", 而它正是 S35→S71 跨 7 页截断误译的尾段。改用汉字绝对计数。


def body_text(raw: str) -> str:
    """剥掉 {vN} 后的实义文本: 用来识别表格/页脚/纯占位符段。"""
    return TOKEN_RE.sub("", raw or "").strip()


def dangling_word(seg) -> str:
    """前段原文尾的悬空功能词 (无则空串)。

    先把 {vN} 换成空格再取末词, 这样 `estimations were{v97}` 也能识别出 `were`。"""
    t = TOKEN_RE.sub(" ", seg.get("raw") or "").rstrip()
    words = t.split()
    if not words:
        return ""
    w = words[-1].strip(".,;:()[]\"'").lower()
    return w if w in DANGLING_WORDS else ""


def sidecar_fp(segs) -> str:
    """原文指纹 (只对 raw 取 md5): 手术只改译文, 指纹不变, 台账因此长期有效。"""
    h = hashlib.md5()
    for s in segs:
        h.update((s.get("raw") or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_report_idx(path: str):
    """{idx: '报告'|'修正'} —— 报告缺失或格式不符时返回空表 (交叉信息是可选的)。"""
    d = _load_json(path, None)
    if not isinstance(d, dict):
        return {}
    out = {}
    for key, tag in (("reports", "报告"), ("corrections", "修正")):
        for it in d.get(key) or []:
            if isinstance(it, dict) and "idx" in it:
                try:
                    out[int(it["idx"])] = tag
                except (TypeError, ValueError):
                    pass
    return out


def load_ledger(path: str, fp: str):
    """该文档已登记的 {idx: {date, note}}。"""
    d = _load_json(path, {})
    entry = (d.get("docs") or {}).get(fp) or {}
    out = {}
    for it in entry.get("entries") or []:
        if isinstance(it, dict) and "idx" in it:
            try:
                out[int(it["idx"])] = it
            except (TypeError, ValueError):
                pass
    return out


def mark_ledger(path: str, fp: str, idxs, note: str, label: str = "") -> int:
    d = _load_json(path, {})
    if not isinstance(d, dict):
        d = {}
    docs = d.setdefault("docs", {})
    doc = docs.setdefault(fp, {})
    if label:
        doc["label"] = label
    entries = doc.setdefault("entries", [])
    today = __import__("datetime").date.today().isoformat()
    n = 0
    for idx in idxs:
        for e in entries:
            if e.get("idx") == idx:
                e.update({"date": today, "note": note})
                break
        else:
            entries.append({"idx": idx, "date": today, "note": note})
            n += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    return n


def _noise_kind(seg, dense: bool) -> str:
    """高噪接缝类型: 图注 / 章节标题 / 文献条目 / 短段密集区 (宁多勿少, --all 可展开)。"""
    t = seg.get("trans") or ""
    if t.lstrip().startswith(("图", "表", "Figure", "Table")):
        return "图注"
    b = body_text(t)
    if len(b) < TITLE_MAX_CHARS and not _SENT_END_RE.search(b):
        return "标题"                       # "引言" / "致谢" 这类章节切换不是断句
    if len(b) >= 25 and len(_CJK_RE.findall(b)) < CJK_MIN_BODY:
        return "文献"
    return "短段密集" if dense else ""


def collect_seams(segs, min_chars: int = 2):
    """返回 (正文段列表, 接缝列表)。接缝项: flag/ a/ b/ gap/ noise。

    配对规则是"相邻正文段跨页", 不是"相邻物理页" —— 中间隔多少表格/页脚段
    都会跨越, 这正是跨 7 页那种接续能被捞出来的原因。"""
    body = [s for s in segs if len(body_text(s["raw"])) >= min_chars]
    n = len(body)
    lens = [len(body_text(s["trans"])) for s in body]
    dense = []
    for i in range(n):
        near = sum(1 for j in range(max(0, i - DENSE_WINDOW), min(n, i + DENSE_WINDOW + 1))
                   if j != i and lens[j] < SHORT_SEG_CHARS)
        dense.append(lens[i] < SHORT_SEG_CHARS and near >= DENSE_NEED)

    rows = []
    for i in range(1, n):
        a, b = body[i - 1], body[i]
        if a["page"] is None or b["page"] is None or a["page"] == b["page"]:
            continue
        ta, ra, rb = a["trans"].rstrip(), a["raw"].rstrip(), b["raw"].lstrip()
        cut = not _SENT_END_RE.search(ta)
        cont = bool(_LOWER_RE.match(rb)) and not _RAW_END_RE.search(ra)
        flag = "[!]" if (cut and cont) else ("[~]" if (cut or cont) else "[ ]")
        rows.append({"flag": flag, "a": a, "b": b, "gap": b["seq"] - a["seq"] - 1,
                     "dangling": dangling_word(a),
                     "noise": _noise_kind(a, dense[i - 1]) or _noise_kind(b, dense[i])})
    return body, rows


def kept_visible(row, ledger) -> bool:
    """默认只显示非高噪接缝; 但台账里登记过的不折叠 —— 人工已确认它是真接缝,
    不能因为"段短/含拉丁学名"这类表象再被藏起来。"""
    if not row["noise"]:
        return True
    return row["a"]["seq"] in ledger or row["b"]["seq"] in ledger


def main() -> int:
    ap = argparse.ArgumentParser(description="跨页接缝体检 (侧车 -> 人工清单)")
    ap.add_argument("--sidecar", default=SIDECAR_DEFAULT)
    ap.add_argument("--report", default=REPORT_DEFAULT)
    ap.add_argument("--log", default=LOG_DEFAULT)
    ap.add_argument("--out", default="")
    ap.add_argument("--with-report", action="store_true")
    ap.add_argument("--all", action="store_true", help="展开高噪(图注/文献)接缝")
    ap.add_argument("--mark", default="", help="登记已手术段号, 如 71 或 78,80")
    ap.add_argument("--note", default="", help="配合 --mark 的手术说明")
    ap.add_argument("--label", default="", help="给该文档起一个人可读的名字")
    ap.add_argument("--chars", type=int, default=100, help="接缝两侧显示字符数")
    ap.add_argument("--min-chars", type=int, default=2, help="正文段最少实义字符数")
    args = ap.parse_args()

    if not os.path.exists(args.sidecar):
        print("侧车不存在: " + args.sidecar)
        return 1
    allsegs = load_segments(args.sidecar)
    if not allsegs:
        print("侧车为空: " + args.sidecar)
        return 1
    pages = [s["page"] for s in allsegs if s["page"] is not None]
    if not pages:
        print("该侧车没有 page 字段 (v26-L1 之前导出的), 无法做接缝体检。")
        print("先重启服务并 force 重渲染一次, 让 converter 重新导出带 page 的侧车。")
        return 1

    fp = sidecar_fp(allsegs)
    if args.mark:
        idxs = [int(x) for x in re.split(r"[,\s]+", args.mark.strip()) if x.strip()]
        add = mark_ledger(args.log, fp, idxs, args.note, args.label)
        print(f"台账已登记 {len(idxs)} 条 (新增 {add}) -> {args.log}")
        print(f"  文档指纹 {fp}" + (f" / 名称 {args.label}" if args.label else ""))
        print()

    body, rows = collect_seams(allsegs, args.min_chars)
    ledger = load_ledger(args.log, fp)
    rep = load_report_idx(args.report) if args.with_report else {}
    shown = rows if args.all else [r for r in rows if kept_visible(r, ledger)]
    folded = len(rows) - len(shown)

    out = []
    out.append("侧车: " + args.sidecar)
    out.append(f"原文指纹 {fp} / 台账已登记 {len(ledger)} 条")
    out.append(f"总段 {len(allsegs)} / 正文段 {len(body)} / 跳过 {len(allsegs) - len(body)}"
               f" (表格·页脚·纯占位符) / 页 {min(pages)}..{max(pages)}")
    if args.with_report:
        out.append(f"军师报告: {args.report} (corrections "
                   f"{sum(1 for v in rep.values() if v == '修正')} / reports "
                   f"{sum(1 for v in rep.values() if v == '报告')})")
    strong = sum(1 for r in rows if r["flag"] == "[!]")
    weak = sum(1 for r in rows if r["flag"] == "[~]")
    out.append(f"跨页接缝 {len(rows)} 处 (强疑断句 {strong} / 弱 {weak})"
               + (f"; 高噪已折叠 {folded} 处 (--all 展开)" if folded else ""))
    out.append("")

    n = args.chars
    for r in shown:
        a, b = r["a"], r["b"]
        head = f"{r['flag']} S{a['seq']}(p{a['page']}) -> S{b['seq']}(p{b['page']})"
        if r["gap"]:
            head += f"   隔{r['gap']}段"
        if r["dangling"]:
            head += f"   [悬空词:{r['dangling']}]"
        if r["noise"]:
            head += f"   [{r['noise']}]"
        done = [ledger[i] for i in (a["seq"], b["seq"]) if i in ledger]
        if done:
            d = done[0]
            head += f"   [已手术 {d.get('date', '')}" + (f": {d.get('note', '')}" if d.get("note") else "") + "]"
        if rep:
            hit = [("上" if i == 0 else "下") + rep[seg["seq"]]
                   for i, seg in enumerate((a, b)) if seg["seq"] in rep]
            if hit:
                head += "   [军师: " + "/".join(hit) + "]"
        out.append(head)
        out.append("      A尾 …" + a["trans"].rstrip()[-n:])
        out.append("      B头 " + b["trans"].lstrip()[:n])

    out.append("")
    out.append("[!] = 前段译文无句末标点 且 后段原文以小写续接, 最可能是被切开的同一句话")
    out.append("[悬空词:x] = 前段原文尾落在 in/of/and/were 这类词上 —— 句子被硬切断的铁证")
    out.append("逐条判定: 译文语义被扭曲 -> 截断误译必修; 两页拼读通顺 -> 默认不修;")
    out.append("          原文本身如此排版(参考文献作者名 / 跨多页表格) -> 不动。")
    out.append("处理完登记台账, 下次运行即标 [已手术]: --mark <段号> --note <说明>")

    txt = "\n".join(out)
    print(txt)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
        print("\n已写入 " + args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
