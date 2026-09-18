# -*- coding: utf-8 -*-
"""
tools/parse_smoke.py —— 解析层沙盘（走产线同一条代码路径，零 API 成本）

模块职责：
  用 pdf2zh 产线**同一条解析路径**（pdf2zh.pdfinterp.PDFPageInterpreterEx +
  pdfminer 的字体构造）逐页试解析目标 PDF，把"会在生产环境炸掉整篇"的
  解析层地雷提前挖出来：哪一页、什么异常、崩在哪个文件哪一行、可疑字体是谁。

和 tools/pre_check.py 的分工（别搞反）：
  - pre_check.py = **便宜筛子**：PyMuPDF 读"风险特征"（Type3 / 缺 ToUnicode /
    dvips 指纹…）。MuPDF 是另一个解析器，它能读出结构**不代表 pdfminer 读得下来**
    —— 只当线索，不当保证。
  - 本工具 = **真判据**：走产线同一条代码路径，过了才是真的过。
    代价是慢得多（逐页解释内容流），所以只对"新论文 + 被筛子标红的件"跑。

覆盖范围（诚实边界）：
  - 覆盖：页面资源初始化（含**字体构造**，v26.19 的 Type3 崩溃就在这里）、
    内容流解释、Form XObject 递归。
  - 不覆盖：翻译（要 API）、版面合并（PyMuPDF 侧）。那两段崩了不叫解析崩。
  - 一个进程内跑完整个文档；某页抛错后**重建解释器继续跑**，不因第一颗雷停下
    —— 目的是把雷**列全**，而不是复刻产线"首崩即整篇退出"的行为。
  - device 用 pdfminer 自带的 PDFPageAggregator（不启用版面分析），
    只为把内容流走完；产线用的是 TranslateConverter，两者共用同一条字体加载路径。

两类"雷"（闸门3 上线后必须分开看）：
  - **崩**（❌）：解释器抛异常。闸门3 只兜字体构造，这里剩下的都是它兜不住的
    （内容流语法、Form XObject 递归、页面资源…），产线仍会整篇退出。
  - **降级**（⚠）：闸门3 把坏字体换成了 NullFont —— 不崩了，但那处文字没有
    译文。这是**静默的质量损失**，必须显式报出来，否则等于没修。
    台账由 pdfminer 侧写（pdfminer/pdffont.py: record_font_fallback），
    本工具按"处理第 N 页前后新增了几条台账"把降级点归到具体页。

用法：
  python tools/parse_smoke.py <PDF>                    # 单篇逐页沙盘
  python tools/parse_smoke.py <PDF> --pages 1-8,12     # 只跑指定页
  python tools/parse_smoke.py --dir <目录>             # 批量（--limit 限制篇数）
  python tools/parse_smoke.py --list <清单.txt>        # 批量（每行一个路径，"<TAB>#" 后可跟注释）
  python tools/parse_smoke.py --dir <目录> --quiet     # 只写报告

报告写入 server/translated/review/解析沙盘_*.md，与翻前体检/翻后质检同一目录。
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys
import tempfile
import traceback

REVIEW_DIRNAME = "review"

# 字体降级台账的环境变量名（与 pdfminer/pdffont.py 的 _font_ledger_path 约定一致）
LEDGER_ENV = "PDF2ZH_FONT_LEDGER"

# 产物文件（-mono/-dual/-compare…）不是原始上传件，批量时跳过
PRODUCED_RE = re.compile(
    r"-(mono|dual|compare|mono-cut|dual-cut|crop-compare|no_watermark[^.]*)\.pdf$",
    re.IGNORECASE)


def project_root():
    """项目根目录（tools/ 的上级）"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def review_dir():
    """审校报告目录：server/translated/review"""
    return os.path.join(project_root(), "server", "translated", REVIEW_DIRNAME)


# ---------------------------------------------------------------- 页范围
def parse_pages_range(spec, total):
    """
    "1-8,12" → [0, 1, ..., 7, 11]（0 基页号列表，保持升序去重）
    解析失败抛 ValueError。
    """
    picked = set()
    for chunk in str(spec).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", chunk)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
        elif chunk.isdigit():
            lo = hi = int(chunk)
        else:
            raise ValueError("页范围写法不认识: %r（示例 1-8,12）" % chunk)
        if lo < 1 or hi < lo:
            raise ValueError("页范围不合法: %r" % chunk)
        picked.update(range(lo, hi + 1))
    return sorted(p - 1 for p in picked if p <= total)


# ---------------------------------------------------------------- 沙盘核心
def make_interpreter():
    """
    按产线同一路径构造解释器。
    产线见 pdf2zh/high_level.py: PDFPageInterpreterEx(rsrcmgr, device, obj_patch)。
    这里 device 换成不自带版面分析的聚合器：只为把内容流走完，省时间。
    """
    from pdfminer.pdfinterp import PDFResourceManager
    from pdfminer.converter import PDFPageAggregator
    from pdf2zh.pdfinterp import PDFPageInterpreterEx

    rsrcmgr = PDFResourceManager()
    device = PDFPageAggregator(rsrcmgr, laparams=None)
    interp = PDFPageInterpreterEx(rsrcmgr, device, {})
    return rsrcmgr, device, interp


def last_frame(exc):
    """取最深一层调用栈的『文件:行 (函数名)』——诊断要的是这个，不是最外层"""
    tb = traceback.extract_tb(exc.__traceback__)
    if not tb:
        return None
    f = tb[-1]
    return "%s:%d (%s)" % (os.path.basename(f.filename), f.lineno, f.name)


def suspect_fonts(pdf_path, page_no):
    """
    尽力标注该页的字体画像（PyMuPDF，独立的另一个解析器，读得动才有输出）。
    只为把报告从"第 8 页崩了"变成"第 8 页有 Type3，看着就是它"。
    失败一律返回 []，绝不因此中断沙盘。
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        try:
            out = []
            for f in doc[page_no - 1].get_fonts(full=True):
                ftype = str(f[2])
                if "Type3" in ftype:
                    out.append("Type3:%s" % f[3])
            return out
        finally:
            doc.close()
    except Exception:
        return []


def read_ledger(path, skip=0):
    """
    读字体降级台账（JSONL），返回第 skip 行之后的记录；文件不存在/读不动 → []
    台账由 pdfminer 侧（pdffont.record_font_fallback）追加，这里只读。
    """
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for i, ln in enumerate(fh):
                if i < skip or not ln.strip():
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def smoke_one(pdf_path, pages=None, ledger=None):
    """
    逐页试解析单个 PDF。返回
      {'page_count', 'error', 'pages': [{'page','ok','exc','where','fonts','fallbacks'}...]}
    打不开 / 文档级失败 → error 非空、pages 为空（这属于"文件级"问题，不是解析层地雷）。
    ledger: 字体降级台账路径；给了就按"每页处理前后新增几条"把降级点归到页。
    """
    result = {"page_count": 0, "error": None, "pages": []}

    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfpage import PDFPage

    try:
        fp = open(pdf_path, "rb")
    except OSError as exc:
        result["error"] = "无法打开: %s" % exc
        return result

    with fp:
        try:
            doc = PDFDocument(PDFParser(fp))
            all_pages = list(PDFPage.create_pages(doc))
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            return result

        result["page_count"] = len(all_pages)
        want = pages if pages else list(range(len(all_pages)))

        rsrcmgr, device, interp = make_interpreter()
        for idx in want:
            if idx >= len(all_pages):
                break
            rec = {"page": idx + 1, "ok": True, "exc": None, "where": None,
                   "fonts": [], "fallbacks": []}
            seen_before = len(read_ledger(ledger)) if ledger else 0
            try:
                # 产线在 high_level 里给每页挂了 xref 桩，解释器要用
                all_pages[idx].page_xref = 1000000 + idx
                interp.process_page(all_pages[idx])
            except Exception as exc:
                rec["ok"] = False
                rec["exc"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
                rec["where"] = last_frame(exc)
                rec["fonts"] = suspect_fonts(pdf_path, idx + 1)
                # 抛错后解释器状态可能已坏：重建再继续，把后面的页也走一遍
                rsrcmgr, device, interp = make_interpreter()
            if ledger:
                for r in read_ledger(ledger, skip=seen_before):
                    rec["fallbacks"].append("%s:%s" % (r.get("subtype", "?"),
                                                       r.get("basefont", "?")))
            result["pages"].append(rec)

    return result


# ---------------------------------------------------------------- 报告
def _verdict(result):
    """(级别, 一句话结论)"""
    if result["error"]:
        return "🔴", "文件级失败：%s" % result["error"]
    broken = [p for p in result["pages"] if not p["ok"]]
    if broken:
        pages = ",".join(str(p["page"]) for p in broken)
        return "🔴", ("解析层有雷：第 %s 页抛错。产线跑同一路径会在**第一颗雷**处整篇退出，"
                      "且进入翻译前就失败（产物一个都不会写）" % pages)
    fb = [p for p in result["pages"] if p["fallbacks"]]
    if fb:
        pages = ",".join(str(p["page"]) for p in fb)
        n = sum(len(p["fallbacks"]) for p in fb)
        return "🟡", ("解析层不崩，但有**字体降级**：第 %s 页共 %d 个字体被换成哑字体"
                      "（闸门3 兜住了）。这些字体覆盖的文字不会有译文，需人工核对这几页"
                      % (pages, n))
    return "🟢", "全部页面解析通过：产线跑同一路径不会在解析层翻车"


def build_report(rows, title="解析层沙盘报告"):
    """
    rows: [{'path','result','scope'}...]；单篇与批量共用一份组装逻辑
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["# %s" % title, "",
             "- 沙盘时间: %s" % now,
             "- 路径: **产线同一条**（pdf2zh.pdfinterp.PDFPageInterpreterEx + pdfminer 字体构造）",
             "- 覆盖: 解析层（页面资源/字体构造/内容流/Form XObject）；不含翻译与版面合并",
             "- 判读: ❌=解释器抛异常（产线会整篇退出）；"
             "⚠=字体降级成哑字体（不崩，但该处无译文）；✅=干净",
             ""]
    for row in rows:
        res = row["result"]
        mark, verdict = _verdict(res)
        lines += ["## %s %s" % (mark, os.path.basename(row["path"])),
                  "",
                  "- %s" % verdict,
                  "- 总页数: %s%s" % (res["page_count"], row["scope"]),
                  ""]
        if res["error"]:
            continue
        lines += ["| 页 | 结果 | 异常 | 位置 | 字体降级 | 该页 Type3 字体 |",
                  "|---|---|---|---|---|---|"]
        for p in res["pages"]:
            lines.append("| {page} | {r} | {e} | {w} | {fb} | {f} |".format(
                page=p["page"],
                r="✅" if p["ok"] else "❌",
                e=(p["exc"] or "").replace("|", "/"),
                w=p["where"] or "",
                fb="; ".join(p["fallbacks"]).replace("|", "/") or "",
                f=", ".join(p["fonts"]) or ""))
        lines.append("")

    if len(rows) > 1:
        lines += ["## 汇总", "",
                  "| 文件 | 页数 | 判读 | 首崩页 | 降级页 | 异常 |",
                  "|---|---|---|---|---|---|"]
        for row in rows:
            res = row["result"]
            broken = [p for p in res["pages"] if not p["ok"]]
            fb = [p for p in res["pages"] if p["fallbacks"]]
            mark, _v = _verdict(res)
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                os.path.basename(row["path"])[:44],
                res["page_count"], mark,
                broken[0]["page"] if broken else "-",
                ",".join(str(p["page"]) for p in fb) or "-",
                (res["error"] or (broken[0]["exc"] if broken else
                                  ("字体降级" if fb else "全通"))).replace("|", "/")[:60]))
        bad = sum(1 for r in rows if _verdict(r["result"])[0] == "🔴")
        deg = sum(1 for r in rows if _verdict(r["result"])[0] == "🟡")
        lines += ["", "**合计: %d 篇 —— ❌崩 %d 篇 · ⚠降级 %d 篇 · ✅干净 %d 篇**"
                  % (len(rows), bad, deg, len(rows) - bad - deg)]

    lines += ["", "---", "",
              "> 由 tools/parse_smoke.py 自动生成。它是解析层的**真判据**；",
              "> 便宜筛子（风险特征）见 tools/pre_check.py。"]
    return "\n".join(lines)


def save_report(content, tag):
    os.makedirs(review_dir(), exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(review_dir(), "解析沙盘_%s_%s.md" % (stamp, tag))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------- 入口
def list_pdfs(folder):
    """目录下所有 PDF（递归），跳过产物文件"""
    out = []
    for p in glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True):
        if not PRODUCED_RE.search(p):
            out.append(p)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description="解析层沙盘（产线同路径，零 API 成本）")
    ap.add_argument("pdf", nargs="?", default=None, help="PDF 路径")
    ap.add_argument("--dir", default=None, help="批量：目录（递归找 PDF）")
    ap.add_argument("--list", dest="listfile", default=None,
                    help="批量：清单文件（每行一个 PDF 路径，# 开头为注释）")
    ap.add_argument("--limit", type=int, default=0, help="批量时最多跑几篇（0=不限）")
    ap.add_argument("--pages", default=None, help="只跑指定页，如 1-8,12")
    ap.add_argument("--quiet", action="store_true", help="只写报告，不打印逐页表")
    args = ap.parse_args()

    targets = []
    if args.listfile:
        with open(args.listfile, encoding="utf-8") as fh:
            targets = []
            for ln in fh:
                # 允许行尾用 "\t#" 跟一段注释（说明为什么收这篇）
                ln = ln.split("\t#")[0].strip()
                if ln and not ln.startswith("#"):
                    targets.append(ln)
    elif args.dir:
        targets = list_pdfs(args.dir)
        if args.limit:
            targets = targets[:args.limit]
    elif args.pdf:
        targets = [args.pdf]
    if not targets:
        print("❌ 没找到要跑的 PDF（给路径，或 --dir 给目录）", file=sys.stderr)
        sys.exit(2)

    rows = []
    # 字体降级台账：本工具自己开一份临时台账（按 env 覆盖 pdfminer 侧默认路径），
    # 免得跑沙盘把产线的真实台账搅浑。跑完删掉。
    ledger_fd, ledger = tempfile.mkstemp(prefix="pdf2zh_smoke_ledger_", suffix=".jsonl")
    os.close(ledger_fd)
    os.environ[LEDGER_ENV] = ledger

    for path in targets:
        if not os.path.isfile(path):
            print("⚠ 跳过（不存在）: %s" % path, file=sys.stderr)
            continue
        pages = None
        res = smoke_one(path, pages=None, ledger=ledger)
        scope = ""
        if args.pages and not res["error"]:
            try:
                pages = parse_pages_range(args.pages, res["page_count"])
            except ValueError as exc:
                print("❌ %s" % exc, file=sys.stderr)
                sys.exit(2)
            res = smoke_one(path, pages=pages, ledger=ledger)
            scope = "（只跑了第 %s 页）" % ",".join(str(p + 1) for p in pages)
        rows.append({"path": path, "result": res, "scope": scope})
        mark, verdict = _verdict(res)
        if not args.quiet:
            print("%s %s | %s" % (mark, os.path.basename(path)[:52], verdict))
            for p in res["pages"]:
                if not p["ok"]:
                    print("     ❌ 第 %d 页 %s @ %s %s" % (
                        p["page"], p["exc"], p["where"],
                        ("| " + ", ".join(p["fonts"])) if p["fonts"] else ""))
                elif p["fallbacks"]:
                    print("     ⚠ 第 %d 页 字体降级: %s" % (
                        p["page"], ", ".join(p["fallbacks"])))
    try:
        os.remove(ledger)
    except OSError:
        pass

    tag = re.sub(r"[^\w\-]+", "_", os.path.basename(targets[0]))[:40] if len(targets) == 1 else "batch%d" % len(targets)
    path = save_report(build_report(rows), tag)
    print("📝 报告: %s" % path)
    sys.exit(0)


if __name__ == "__main__":
    main()
