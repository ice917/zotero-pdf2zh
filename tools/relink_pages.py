# -*- coding: utf-8 -*-
"""relink_pages.py — 译文页链接热区重定位 (方案甲)

原理: NAMED 链接的语义是"这段锚文本 -> 目标"。译文重排后文字挪位, 链接框仍钉在
原坐标。而锚文本绝大多数是数字/代码/拉丁名 —— 字形机制保证它们在译文中原样存在。
故: 从原版同页同矩形提取锚文本 -> 在译文页重搜 -> 把链接矩形搬到新位置。

规则:
  - 回填页(零可译段页)跳过 —— 页面即原版, 天然对齐。
  - 锚文本 <2 字符跳过; 未命中的链接保持原矩形并记入报告(不死链, 只是热区偏)。
  - 同页多处命中: 取与原矩形中心最近者; **碎片命中先并成"一次出现"的整框**(见下)。
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


def merge_fragments(hits, gap=0.6, ytol=1.0):
    """把 search_for 的"碎片命中"并成"一次出现"的整框。

    碎片排版的页面上, search_for('[5]') 会返回**每个字一个矩形** —— 实测译文页
    `[`=[411.60,414.92] / `5`=[414.92,419.90] / `]`=[419.90,423.22], 首尾严丝合缝
    (间隙 0.00), 数字基准线比括号低 ~3pt。只取"离原框中心最近者"的话, 热区就只剩
    3.32pt(一个括号宽) —— **点括号能跳、点数字点不到**。实测 145 条里 49 条中招,
    而原版同一批 0 条, 即这批窄热区全是本步引入的。

    判据: 同一行(y 区间重叠) 且 水平相邻(间隙 <= gap)。空格宽约 2.4pt, 故 gap=0.6
    只并"被拆开的同一个锚"(实测间隙恒为 0), 不会把隔一个空格的下一处引用吞进来。
    若两处引用真的紧挨着(如 '[5][6]'), 会被并成一框 —— 框变大但两处都覆盖得到, 可接受。
    """
    boxes = [pymupdf.Rect(h) for h in hits]
    changed = True
    while changed:                  # 并一轮后可能出现新的近邻, 迭代到不动点
        changed = False
        out = []
        for b in boxes:
            for i, o in enumerate(out):
                if (o.y0 - ytol <= b.y1 and b.y0 <= o.y1 + ytol
                        and b.x0 - o.x1 <= gap and o.x0 - b.x1 <= gap):
                    # 必须写回 out[i]: `o |= b` 只重绑循环变量, 并出来的框会丢
                    out[i] = o | b
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def pick_hit(hits, center, occupied):
    """挑"这一条链接该搬到的框": 先并碎片 -> 再排除已被占用的出现 -> 最后取离原框最近者。
    返回 None = 没得挑(未命中, 或候选全被同页前面的锚抢占)。"""
    cands = [h for h in merge_fragments(hits)
             if not any(h.intersects(o) for o in occupied)]
    if not cands:
        return None

    def dist2(h):
        return ((h.x0 + h.x1) / 2 - center[0]) ** 2 + ((h.y0 + h.y1) / 2 - center[1]) ** 2

    return min(cands, key=dist2)


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

    moved = unresolved = skipped = 0
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
            hits = page.search_for(anchor)
            c0 = ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            best = pick_hit(hits, c0, occupied) if hits else None
            if best is None:
                unresolved += 1
                misses.append((pno + 1, anchor[:40]))
                continue
            occupied.append(best)
            new = l.copy()
            new["from"] = pymupdf.Rect(best)
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
