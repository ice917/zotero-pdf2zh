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

**第二条判据(落点页一致性, 需 --original)**: 只靠"作者+年份"会**空洞通过** ——
锚文本是交叉引用(Fig. 3 / Sec. 4.4 / 参考文献里的 page.N 页锚)时一条都验不了。
实测 GeoTLM 篇 145 条链接被整批跳过, 打印"失配 0"其实等于没验。给出 --original
后加一道与锚文本内容无关的判据: 成品与原版页数成整倍(mono 1:1 / dual 2:1)时,
原版里每个命名目标解析出的页码, 就是成品同名链接**必须**落到的页码(dual 落
2p 或 2p+1)。配对用"矩形就近"而不是"按序" —— relink 把矩形挪了 1pt, 同 y 的两条
在 get_links() 里就换了序, 按序配会假报失配。

**第三条判据(落点不跨侧, dual 专用)**: dual 每对页 = [原版页, 译文页], 两侧各自
成对(译文页点引用跳译文侧, 原版页跳原版侧)。"串侧"的表现是"点中文页跳到英文页",
而页码本身完全合法 —— 只有这条能抓。同时做两侧条数对账, 并在原版侧还残留 NAMED
(dual_links 没跑/没跑完)时点名。

用法:
  python tools/verify_links.py --target <成品.pdf> [--original <原版.pdf>] \
      [--tol 45] [--report <报告>]
"""
import argparse, io, re, sys, unicodedata, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

norm = lambda s: re.sub(r"\s+", "", s or "")


def page_consistency(doc, orig, bad, stat):
    """落点页一致性 (见模块头"第二条判据")。返回一句结论供打印。"""
    names = orig.resolve_names()
    if not names:
        return "原版无命名目标, 本判据不适用"
    ratio = len(doc) // len(orig)
    if ratio < 1 or len(orig) * ratio != len(doc):
        return "页数比不整(原版%d:成品%d), 本判据不适用" % (len(orig), len(doc))
    unpairable = 0
    for i in range(len(orig)):
        tgt_pages = []
        for l in orig[i].get_links():
            if l["kind"] != pymupdf.LINK_NAMED:
                continue
            tgt = names.get(l.get("nameddest"), {}).get("page")
            if tgt is None:
                continue
            tgt_pages.append((pymupdf.Rect(l["from"]), tgt, l.get("nameddest")))
        if not tgt_pages:
            continue
        tp = ratio * i
        if tp >= len(doc):
            continue
        tl = [l for l in doc[tp].get_links() if l["kind"] == pymupdf.LINK_GOTO]
        if len(tl) != len(tgt_pages):
            # 条数不等就没法一一配对: 这本身是异常(成品缺链接/多链接), 记账不静默
            unpairable += 1
            bad.append((tp + 1, "条数不等", -1,
                        "原版命名链接 %d 条, 成品 GOTO %d 条" % (len(tgt_pages), len(tl))))
            continue
        used = set()
        for l in tl:
            r = l["from"]
            c = ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            best, bd = None, None
            for j, (ro, _t, _n) in enumerate(tgt_pages):
                if j in used:
                    continue
                co = ((ro.x0 + ro.x1) / 2, (ro.y0 + ro.y1) / 2)
                d = (co[0] - c[0]) ** 2 + (co[1] - c[1]) ** 2
                if bd is None or d < bd:
                    bd, best = d, j
            used.add(best)
            _ro, tgt, nm = tgt_pages[best]
            stat["pg_checked"] += 1
            if tgt * ratio <= l["page"] < tgt * ratio + ratio:
                stat["pg_hit"] += 1
            else:
                stat["pg_miss"] += 1
                bad.append((tp + 1, "%s(锚距%.1fpt)" % (nm or "?", bd ** 0.5), l["page"] + 1,
                            "原版目标 p%d" % (tgt + 1)))
    return ("可校验 %d | 命中 %d | 失配 %d | 条数不等跳过 %d 页"
            % (stat["pg_checked"], stat["pg_hit"], stat["pg_miss"], unpairable))


def side_consistency(doc, orig, bad, stat):
    """落点不跨侧 + 两侧条数对账 (见模块头"第三条判据")。返回一句结论。

    只在成品页数是原版整数倍(dual ratio=2)时有意义 —— ratio=1 时"同侧"是废话。
    判据一: 第 p 页的 GOTO 落点页必须与 p 同侧(p % ratio == 落点页 % ratio)。
    判据二: 同一对页的两侧承载同一份内容, 引用链接条数应相等(原版侧 = NAMED+GOTO,
            译文侧 = GOTO); 原版侧还残留 NAMED 时一并点名(dual_links 没跑)。
    """
    ratio = len(doc) // len(orig)
    cross = named_left = unpair = 0
    for pno in range(len(doc)):
        for l in doc[pno].get_links():
            if l["kind"] == pymupdf.LINK_NAMED:
                named_left += 1
                continue
            if l["kind"] != pymupdf.LINK_GOTO:
                continue
            if (l["page"] % ratio) != (pno % ratio):
                cross += 1
                bad.append((pno + 1, "跨侧落点", l["page"] + 1,
                            "%s侧页跳到%s侧页" % ("译" if pno % ratio else "原",
                                                "译" if l["page"] % ratio else "原")))
    for k in range(len(orig)):
        a = len([l for l in doc[ratio * k].get_links()
                 if l["kind"] in (pymupdf.LINK_NAMED, pymupdf.LINK_GOTO)])
        b = len([l for l in doc[ratio * k + 1].get_links() if l["kind"] == pymupdf.LINK_GOTO])
        if a != b:
            unpair += 1
            bad.append((ratio * k + 1, "两侧条数不等", -1,
                        "原版侧 %d 条, 译文侧 GOTO %d 条" % (a, b)))
    if named_left:
        bad.append((-1, "原版侧残留 NAMED", -1,
                    "%d 条未转 GOTO(Edge 等简易阅读器点不动) -> 先跑 dual_links.py" % named_left))
    return "跨侧 %d | 残留 NAMED %d | 两侧条数不等 %d 页" % (cross, named_left, unpair)


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


def cite_number_anchor(page, r, n_digits=4):
    """锚是"引文号"而不是"年份"么? —— '2020' 也可能是 '[20]' 被叠绘成的。

    实测陷阱: 原文引文号 [20] 的链接矩形只盖住数字不盖括号, style_links 原位叠绘
    蓝色后文本层成了两份 '20', norm() 去空白一拼就是 '2020', 看着像年份。判据用
    **矩形宽度**: 4 位数字按该处实际字号(数字宽约 0.5em)至少要占 18pt(10pt 字),
    而只装得下两位数字的矩形不可能真的是四位年份 —— 那就是拼接出来的。
    """
    size = 0.0
    for b in page.get_text("dict", clip=r)["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                size = max(size, s["size"])
    return bool(size) and r.width < n_digits * size * 0.5 * 0.9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--original", default="",
                    help="原版 PDF: 给出则加一道「落点页一致性」判据(与锚文本内容无关)")
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
            if cite_number_anchor(page, l["from"]):
                stat["skip_citenum"] += 1
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

    n = stat["checked"] or 1
    print("链接 %d | 页内 %d | 语义可校验 %d | 命中 %d | 失配 %d(其中镜像命中 %d) | 非年份锚跳过 %d | 疑似引文号锚跳过 %d"
          % (stat["links"], stat["in_page"], stat["checked"], stat["hit"],
             stat["miss"], stat["mirror"], stat["skip_symbol"], stat["skip_citenum"]))
    print("语义命中率: %.1f%%" % (100.0 * stat["hit"] / n))
    if stat["in_page"] and not stat["checked"]:
        # 一条都没验却打印"失配 0"是最危险的一种绿: 必须自己说破。
        print("NOTICE 本判据一条都没验(锚文本无年份), 不可当作「链接正确」 —— "
              "请用 --original 走落点页一致性")
    if args.original:
        orig = pymupdf.open(args.original)
        print("落点页一致性: %s" % page_consistency(doc, orig, bad, stat))
        if len(doc) != len(orig):
            print("落点侧不串: %s" % side_consistency(doc, orig, bad, stat))
        orig.close()
    elif stat["in_page"] and not stat["checked"]:
        print("落点页一致性: 未执行(缺 --original)")
    doc.close()
    for b in bad[:25]:
        print("  %s p%d %r -> p%d %s" % ("失配" if b[0] > 0 else "异常", b[0], b[1], b[2], b[3]))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            for b in bad:
                f.write("p%d\t%s\tp%d\t%s\n" % b)
        print("失配清单:", args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
