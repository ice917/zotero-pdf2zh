# -*- coding: utf-8 -*-
"""relink_pages.py — 译文页链接热区重定位 (方案甲)

原理: NAMED 链接的语义是"这段锚文本 -> 目标"。译文重排后文字挪位, 链接框仍钉在
原坐标。而锚文本绝大多数是数字/代码/拉丁名 —— 字形机制保证它们在译文中原样存在。
故: 从原版同页同矩形提取锚文本 -> 在译文页重搜 -> 把链接矩形搬到新位置。

规则:
  - 回填页(零可译段页)跳过 —— 页面即原版, 天然对齐。
  - 锚文本 <2 字符跳过; 未命中的链接保持原矩形并记入报告(不死链, 只是热区偏)。
  - 同页多处命中: 取与原矩形中心最近者。
用法:
  python tools/relink_pages.py --target <成品.pdf> --original <原版.pdf> \
      [--sidecar <侧车>] [--report <报告路径>]
注意: 只可在"backfill -> table_zh 之后、尚未 relink 过"的成品上运行一次
     (锚文本取自原版同页同矩形, relink 过的成品矩形已搬移, 不可作输入)。
"""
import io, re, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

PURE_GLYPH = re.compile(r"^(?:\{v\d+\})+$")
norm = lambda s: re.sub(r"\s+", "", s or "")


def zero_pages(sidecar):
    out = set()
    for line in open(sidecar, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        o = __import__("json").loads(line)
        tr = any((s.get("raw") or "").strip() and not PURE_GLYPH.match((s.get("raw") or "").strip())
                 and re.search(r"[A-Za-z0-9]", s["raw"]) for s in o["segs"])
        if not tr:
            out.add(o["page"])
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--sidecar", default="")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    skip = zero_pages(args.sidecar) if args.sidecar else set()
    doc = pymupdf.open(args.target)
    orig = pymupdf.open(args.original)
    if len(doc) != len(orig):
        print("FAIL 页数不一致")
        return 1

    moved = kept = unresolved = skipped = 0
    misses = []
    for pno in range(len(doc)):
        if pno + 1 in skip:
            continue
        page, opage = doc[pno], orig[pno]
        occupied = []  # 本页已被前面链接占用的命中矩形(防碰撞: 两个锚同抢一个字形)
        for l in page.get_links():
            if l["kind"] != pymupdf.LINK_NAMED:
                continue
            r = l["from"]
            anchor = norm(opage.get_text(clip=r))
            if len(anchor) < 2:
                skipped += 1
                continue
            hits = [h for h in page.search_for(anchor)
                    if not any(h.intersects(o) for o in occupied)]
            if not hits:
                unresolved += 1
                misses.append((pno + 1, anchor[:40]))
                continue
            c0 = ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)

            def dist2(h, _c=c0):
                dx = (h.x0 + h.x1) / 2 - _c[0]
                dy = (h.y0 + h.y1) / 2 - _c[1]
                return dx * dx + dy * dy

            best = min(hits, key=dist2)
            occupied.append(best)
            new = l.copy()
            new["from"] = pymupdf.Rect(best.x0, best.y0, best.x1, best.y1)
            page.update_link(new)
            moved += 1
    tmp = args.target + ".tmp"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, args.target)

    print("跳过(回填页/短锚): %d | 重定位: %d | 未命中(保持原位): %d" % (skipped + len(skip), moved, unresolved))
    if misses and args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            for p, a in misses:
                f.write("p%d\t%r\n" % (p, a))
        print("未命中清单:", args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
