# -*- coding: utf-8 -*-
"""resolve_links.py — NAMED 链接 -> 显式 GOTO (阅读器兼容 + 落点语义重定位)

为什么: NAMED 链接(按名字跳转)依赖 PDF 内的命名目标树, Edge 等简易阅读器不支持;
且成品保存过程中部分目标条目会损坏(page=-1)。原版 PDF 的目标树是完整的, 且
成品与原版页码 1:1 —— 故直接拿原版目标表解析。

三层修复(缺一则"乱跳"):
 1) 坐标系: resolve_names() 返回的目标 y 是 PDF 底部原点(y 向上), insert_link
    需要 fitz 顶部原点(y 向下) —— 不转换则落点整体镜像。
 2) 排版漂移: 目标坐标来自"原版同一页", 但成品的文字被重排/翻译(参考文献页尤甚),
    同一个 y 在两版指向不同段落 —— 故用原版目标处的"行文本(作者姓+年份)"去成品
    目标页里重新定位同名行, 用其真实 y。
 3) 行距歧义: 文献页行距仅 ~10pt, 目标 y 常落在两行之间(±1 行不确定)。故在原版
    目标 y±26pt 内枚举候选条目, 取"唯一能在成品页精确匹配(作者姓+年份)"的那条;
    多解时取离原坐标最近者; 全都匹配不上才回退到坐标转换值。

用法(在 relink_pages.py 之后跑一次):
  python tools/resolve_links.py --target <成品.pdf> --original <原版.pdf> [--report <报告>]
"""
import io, re, sys, os, unicodedata, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

YEAR = re.compile(r"(1[6-9]\d\d|20\d\d)")
SURNAME = re.compile(r"^[\(\[]?([A-Z][A-Za-z\u00C0-\u024F'\-]{2,})")
STOP = {"the", "and", "for", "with", "from", "this", "that", "table", "figure", "fig", "in", "pp"}


def fold(s):
    """去重音/去非字母: 拉丁名与作者名在两版间的排版变体归并"""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", s.lower())


def lines_of(page):
    """页面所有文本行: [(y0, y1, text), ...] —— 用几何行, 不受阅读顺序影响"""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            txt = "".join(s["text"] for s in l["spans"])
            if txt.strip():
                out.append((l["bbox"][1], l["bbox"][3], txt))
    return out


def candidates_at(page, y, win=26):
    """原版目标 y 附近的候选条目行(含年份), 按距离排序
    窗口需略宽: 目标 y 与行顶常有 4~8pt 偏差, 文献页行距仅 10pt。
    (实测扩大到 40/8 反而更差: 相邻条目被误选)
    """
    rows = [(y0, txt) for y0, y1, txt in lines_of(page) if y - win <= y0 <= y + win]
    rows = [r for r in rows if YEAR.search(r[1][:70])]
    rows.sort(key=lambda r: abs(r[0] - y))
    return rows[:6]


def key_of(text):
    """从行文本提取 (作者姓, 年份) 作为语义键"""
    t = (text or "").strip()
    m = SURNAME.match(t)
    sur = m.group(1) if m and m.group(1).lower() not in STOP else None
    ym = YEAR.search(t[:70])
    return sur, (ym.group(1) if ym else None)


def find_row_y(page, sur, year, prefer):
    """在成品目标页定位"那条文献"的行。
    文献页被翻译后标题已中文化, 唯一稳定键 = 作者姓(+ 年份); 年份允许落在
    相邻 ±14pt 的行(条目跨行排版)。返回 (y, 等级): 2=姓+年, 1=仅年, 0=未命中
    """
    rows = lines_of(page)
    fs = fold(sur) if sur else ""

    def band(y0):
        return "".join(t for yy, _, t in rows if abs(yy - y0) <= 14)

    exact = []
    for y0, y1, txt in rows:
        if fs and fs not in fold(txt):
            continue
        if year and year not in txt and year not in band(y0):
            continue
        exact.append(y0)
    if exact:
        return min(exact, key=lambda y: abs(y - prefer)), 2
    if year:
        ys = [y0 for y0, y1, txt in rows if year in txt]
        if ys:
            return min(ys, key=lambda y: abs(y - prefer)), 1
    return None, 0


def seq_map_y(opage, oy, tpage):
    """行序比例映射: 源页中 oy 所处"第几行"按比例映射到成品页同序行。
    文献列表两版条目顺序完全一致, 只是中文压缩后行数变少 —— 绝对坐标失效,
    但"行序"仍然对应。文本匹配失败时用它兜底。
    """
    orows = sorted(y0 for y0, y1, t in lines_of(opage))
    trows = sorted(y0 for y0, y1, t in lines_of(tpage))
    if not orows or not trows:
        return None
    i = 0
    while i < len(orows) - 1 and orows[i + 1] <= oy:
        i += 1
    frac = i / float(max(1, len(orows) - 1))
    return trows[int(round(frac * (len(trows) - 1)))]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    orig = pymupdf.open(args.original)
    names = orig.resolve_names()
    doc = pymupdf.open(args.target)
    stat = collections.Counter()
    misses = []

    for pno in range(len(doc)):
        page = doc[pno]
        for l in page.get_links():
            if l["kind"] != pymupdf.LINK_NAMED:
                continue
            n = l.get("nameddest") or l.get("name") or ""
            d = names.get(n)
            if not d or d.get("page", -1) < 0 or d["page"] >= len(doc):
                stat["fail"] += 1
                misses.append((pno + 1, n, "目标树无该名字"))
                continue
            # --- 1) 坐标系转换: PDF 底部原点 -> fitz 顶部原点 ---
            dp = d["page"]
            to = d["to"]
            tx = to[0] if isinstance(to, (tuple, list)) else to.x
            ty = to[1] if isinstance(to, (tuple, list)) else to.y
            opage = orig[dp]
            oy = max(0.0, min(opage.rect.height, opage.rect.height - ty))

            # --- 2/3) 候选条目投票: 唯一能精确重定位者胜, 多解取最近 ---
            tpage = doc[dp]
            cands = candidates_at(opage, oy)
            best = None
            for y0o, tline in cands:
                sur, year = key_of(tline)
                ny, lvl = find_row_y(tpage, sur, year, oy) if (sur or year) else (None, 0)
                if lvl == 2:
                    dist = abs(y0o - oy)
                    if best is None or dist < best[0]:
                        best = (dist, ny, 2, sur, year)
            if best is None:                      # 兜底: 行序比例映射
                seqy = seq_map_y(opage, oy, tpage)
                if seqy is not None:
                    ny = seqy
                    stat["seqmap"] += 1
                    misses.append((pno + 1, n, "行序映射 y=%.0f -> %.0f (文本键未中)" % (oy, seqy)))
                else:
                    ny = oy
                    stat["fallback"] += 1
                    misses.append((pno + 1, n, "未重定位, 回退坐标 y=%.0f" % oy))
            else:
                ny = best[1]
                stat["reloc" if best[2] == 2 else "weak"] += 1
                if best[2] == 1:
                    misses.append((pno + 1, n, "仅年份(弱): %s %s" % (best[3] or "?", best[4])))
            ny = max(0.0, min(tpage.rect.height, ny))
            if tx != 0:            # x=0 是"左对齐"语义, 保持即可
                tx = min(max(tx, 0.0), tpage.rect.width)

            new = {k: v for k, v in l.items() if k not in ("nameddest", "name")}
            new["kind"] = pymupdf.LINK_GOTO
            new["page"] = dp
            new["to"] = pymupdf.Point(tx, ny)
            page.delete_link(l)
            page.insert_link(new)

    tmp = args.target + ".tmp"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, args.target)

    print("NAMED -> GOTO: %d | 行文本重定位: %d | 行序映射: %d | 回退坐标: %d | 目标树缺失: %d"
          % (stat["reloc"] + stat["seqmap"] + stat["fallback"] + stat["fail"],
             stat["reloc"], stat["seqmap"], stat["fallback"], stat["fail"]))
    for m in misses[:8]:
        print("  %s" % (m,))
    if args.report and misses:
        with open(args.report, "w", encoding="utf-8") as f:
            for m in misses:
                f.write("p%d\t%s\t%s\n" % m)
        print("清单:", args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
