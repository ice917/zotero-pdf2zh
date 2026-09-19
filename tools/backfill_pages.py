# -*- coding: utf-8 -*-
"""backfill_pages.py — 成品回填: 把"零可译段"页(整页表格等)用原版 PDF 对应页替换

为什么需要:
  整页表格的段落全是字形占位符(无可译文字), 翻译管线不碰它们, 但重渲染仍会把
  这类页重新排版 —— 旋转表头/单元格被打碎成竖排散字。这些页在原版里是出版级
  排版, 回填原版页面即可: 表格保持原样, 正文页保持译文。

零可译段判定: 该页所有段落 raw 均为纯 {vN} 占位符, 或不含字母/数字。

用法:
  python tools/backfill_pages.py --mono <译版mono.pdf> --original <原版.pdf> \
      --out <输出.pdf> [--sidecar 侧车路径]
页码 1:1 对应; 两侧页数不一致时拒绝执行。
"""
import argparse, io, json, re, sys, warnings

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
warnings.filterwarnings("ignore")

from pypdf import PdfReader, PdfWriter

PURE_GLYPH = re.compile(r"^(?:\{v\d+\})+$")


def true_page(o):
    """侧车记录的**真实 PDF 页码**(1 基); 记录里没有 pageid 时回落 None。

    侧车的 `page` 是 receive_layout 的**回调计数**, 不是页码: 图形对象若含文字
    也会再写一行 (end_figure -> receive_layout(fig)), 同一页因此可能占多行,
    图多的论文整体漂移 (与 seg_export.py 同一口径)。真实页码只有 `pageid` 说得准。
    """
    pid = o.get("pageid")
    return None if pid is None else int(pid) + 1


def zero_pages_from_sidecar(path):
    """零可译段页 (真实页码 1 基, 升序)。

    判据按**页**聚合: 该页所有记录都不含可译段才算零可译段页 (整页表格典型)。
    逐条记录判定会把同页的文字回调误判成零页 (一页多行), 且 `page` 是回调计数,
    当页码用会点错页 —— 故先按页码归并。老侧车无 `pageid` 时回落用 `page` 当页码。
    """
    trans = {}   # 页码 -> 该页是否含可译段
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        pg = true_page(o)
        if pg is None:
            pg = o["page"]
        if trans.get(pg):
            continue
        translatable = False
        for s in o["segs"]:
            raw = (s.get("raw") or "").strip()
            if raw and not PURE_GLYPH.match(raw) and re.search(r"[A-Za-z0-9]", raw):
                translatable = True
                break
        trans[pg] = translatable
    return sorted(pg for pg, ok in trans.items() if not ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mono", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sidecar", default="", help="给出则自动判定零可译段页")
    ap.add_argument("--pages", default="", help="显式页码列表, 如 3,9-15 (1 基)")
    args = ap.parse_args()

    if args.sidecar:
        pages = zero_pages_from_sidecar(args.sidecar)
    elif args.pages:
        pages = []
        for tok in args.pages.split(","):
            if "-" in tok:
                a, b = (int(x) for x in tok.split("-"))
                pages.extend(range(a, b + 1))
            else:
                pages.append(int(tok))
    else:
        print("需要 --sidecar 或 --pages")
        return 1
    if not pages:
        print("没有需要回填的页")
        return 0

    mono = PdfReader(args.mono)
    orig = PdfReader(args.original)
    if len(mono.pages) != len(orig.pages):
        print("FAIL 页数不一致: mono=%d original=%d" % (len(mono.pages), len(orig.pages)))
        return 1

    writer = PdfWriter()
    n = 0
    for i in range(len(mono.pages)):
        src = orig if (i + 1) in pages else mono
        writer.add_page(src.pages[i])
        if (i + 1) in pages:
            n += 1
    with open(args.out, "wb") as f:
        writer.write(f)
    print("回填 %d 页 %s -> %s" % (n, sorted(pages), args.out))
    print("输出: %d 页" % len(writer.pages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
