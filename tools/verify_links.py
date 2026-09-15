# -*- coding: utf-8 -*-
"""verify_links.py — 超链接"落点语义"校验 (乱跳体检)

为什么需要: 链接框一一对应 ≠ 落点正确。relink 只保证"锚文本找得到",
resolve_links 只保证"目标页+坐标写死"; 但坐标写错(如原点方向反了)会让
所有链接跳到同一页的镜像位置 —— 阅读器表现就是"乱跳"。
本工具做端到端语义校验: 拿锚的"作者+年份"去落点附近的文献条目里找。

锚文本的现实情况: 本书引文链接矩形只覆盖年份('1983'), 作者在矩形左侧
同一行 —— 故先取左侧邻近词作作者线索, 再要求落点 y±tol 内同时出现
"年份" 与 "作者"(任一), 二者皆中才算命中。失配时额外试探镜像位置
(h - y): 若镜像中, 即坐标系方向反了。

用法:
  python tools/verify_links.py --target <成品.pdf> [--tol 45] [--report <报告>]
"""
import argparse, io, re, sys, unicodedata, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

norm = lambda s: re.sub(r"\s+", "", s or "")


def fold(s):
    """去重音/去非字母: 作者名在两版间的排版差异归并。
    成品参考文献里连字符会被吞(Molina-Freaner -> MolinaFreaner), 重音也可能被合成,
    不归并就会把"落点正确"误判成"缺作者"。
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", s.lower())


NAMEW = re.compile(r"^[\(\[]?([A-Z][A-Za-z\u00C0-\u024F'\-]{2,})")
YEAR = re.compile(r"(1[6-9]\d\d|20\d\d)")


def left_author(page, r):
    """锚矩形左侧同一行的最近实词(引用格式: 'Waser 1983' / '(Waser 1983)')"""
    best = None
    for x0, y0, x1, y1, w, *_ in page.get_text("words"):
        if y1 <= r.y0 + 1 or y0 >= r.y1 - 1:      # 必须垂直重叠
            continue
        if x1 > r.x0 + 1 or r.x0 - x1 > 70:        # 必须在左侧且邻近
            continue
        m = NAMEW.match(w)
        if not m:
            continue
        ww = m.group(1)
        if ww.lower() in ("the", "and", "for", "with", "from", "table", "fig", "figure"):
            continue
        if best is None or x1 > best[1]:
            best = (ww, x1)
    return best[0] if best else None


def near_text(page, y, tol):
    r = pymupdf.Rect(0, max(0, y - tol), page.rect.width, min(page.rect.height, y + tol))
    return page.get_text(clip=r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--tol", type=float, default=45)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    doc = pymupdf.open(args.target)
    stat = collections.Counter()
    bad = []

    for pno in range(len(doc)):
        page = doc[pno]
        for l in page.get_links():
            if l["kind"] != pymupdf.LINK_GOTO:
                continue
            stat["links"] += 1
            dp = l.get("page", -1)
            if dp < 0 or dp >= len(doc):
                stat["page_out"] += 1
                bad.append((pno + 1, "(页码越界)", dp, ""))
                continue
            stat["in_page"] += 1
            anchor = norm(page.get_text(clip=l["from"]))
            ym = YEAR.search(anchor)
            # style_links 会把锚文本原位重绘为蓝色, 文本层因此出现重复(如 '19901990');
            # 去掉年份后若只剩年份本身, 仍是引文锚; 剩别的字符(表号/编号)才算非年份锚。
            rest = anchor.replace(ym.group(1), "") if ym else anchor
            if not ym or (rest and not re.fullmatch(r"(?:1[6-9]\d\d|20\d\d)+", rest)):
                stat["skip_symbol"] += 1
                continue
            year = ym.group(1)
            author = left_author(page, l["from"])
            stat["checked"] += 1
            y = l["to"].y
            page_h = doc[dp].rect.height
            if not (0 <= y <= page_h):
                stat["y_out"] += 1
                bad.append((pno + 1, "%s %s" % (author, year), dp + 1,
                            "落点 y=%.0f 越界(页高 %.0f)" % (y, page_h)))
                continue
            hay_raw = near_text(doc[dp], y, args.tol)
            hay = norm(hay_raw)
            fhay = fold(hay_raw)
            hit_year = year in hay
            hit_auth = bool(author) and (author in hay or fold(author) in fhay)
            if hit_year and (hit_auth or not author):
                stat["hit"] += 1
                continue
            mirror_y = page_h - y
            mraw = near_text(doc[dp], mirror_y, args.tol)
            mhay = norm(mraw)
            m_hit = (year in mhay) and ((not author) or author in mhay or fold(author) in fold(mraw))
            if m_hit:
                stat["mirror"] += 1
            stat["miss"] += 1
            bad.append((pno + 1, "%s %s" % (author or "?", year), dp + 1,
                        "y=%.0f %s%s%s" % (y, "缺年份" if not hit_year else "",
                                          "缺作者" if author and not hit_auth else "",
                                          " | 镜像处命中" if m_hit else "")))

    doc.close()
    n = stat["checked"] or 1
    print("链接 %d | 页内 %d | 语义可校验 %d | 命中 %d | 失配 %d(其中镜像命中 %d) | 非年份锚跳过 %d"
          % (stat["links"], stat["in_page"], stat["checked"], stat["hit"],
             stat["miss"], stat["mirror"], stat["skip_symbol"]))
    print("语义命中率: %.1f%%" % (100.0 * stat["hit"] / n))
    for b in bad[:25]:
        print("  失配 p%d %r -> p%d %s" % b)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            for b in bad:
                f.write("p%d\t%s\tp%d\t%s\n" % b)
        print("失配清单:", args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
