# -*- coding: utf-8 -*-
"""按坐标把整页表格切成网格(TSV) + 对齐审计  v4

v1 失败: 行间空档阈值(4pt)切不开 —— 块内行距空白 0.9-1.5pt / 块间 3.4-3.7pt, 阈值无解。
v2 失败: ① x 投影找列 -> 单字符列(H/D/Nectar, 宽 3pt)被当槽吃掉, 12 列(应为 13);
         ② 按 y0 间隙并格 -> 同格换行 1.28pt vs 换行 1.76pt, 无解, 且跨页误并。
v3 失败: 判"格横跨多列"用了区间交集, 而列边缘是舍入值(406.30)、bbox 是原始浮点
         (406.2615), 差 0.04pt 就谎报重叠 -> T2 吃掉 381 个真格(5 列全空)、T1 吃掉 27 个。

v4 换两个正交的判据(全部实测):
  列 = 左边缘 x0 的聚簇。表格左对齐, 同列所有行共享左边缘(同一列 x0 偏差 <1.2pt);
       续行缩进约 7pt -> 距离 <COL_MERGE(10pt) 的簇并掉, 保留左者。
       实测 T2 = 73/144/165/205/241/289/341/397/443/458/473/499/553 -> 13 列。
  行 = 基线 y1。同行各格字号不同 -> 顶边 y0 可差 2pt, 但**基线 y1 一致**(实测偏差 <0.3pt);
       行距 18pt -> 分离度 80 倍。行首由最左列的行首行标出:
       最左列里 x0 落在最左簇的即行首(续行缩进 7pt, 天然分开)。

审计原则: 不许静默丢。噪声 / 跨列组表头 / 未落格 / 格数异常 全部逐条列出。
"""

import os
from collections import Counter

import pymupdf

SD = os.path.dirname(os.path.abspath(__file__))          # 脚本目录(本件所在)
D = os.environ.get("P2Z_TABLE_DIR") or SD                # 工作目录(数据所在), 缺省=脚本目录
EXC = os.path.join(D, "excerpt_table_pages.pdf")
OUTDIR = D

PROSE_W = 0.45     # 行宽 > 页宽此比例 -> 散文(表注)
TAIL_DY = 3.0      # 紧跟表注末行(与表注块底线间隙 < 此值) -> 也算表注
CLU_TOL = 2.0      # x0 聚簇容差
COL_MERGE = 10.0   # 相邻簇距离 < 此值 -> 并簇(续行缩进约 7pt)
ROW_TOL = 1.0      # 行带在基线上的容差
MIN_SUPPORT = 3    # 列落格数下限, 低于此判为残列(幻影列)
INNER = 3.0        # 判"横跨多列"时, 内列左边缘须落在单元格内部并留出的边距

# 单符号字体 AdvP4C4E74 的真身 = 正负号(与载荷侧 mk_payload.py 同源修复)
CHAR_FIX = {chr(1): "±"}


def norm(t):
    # 单符号字体 AdvP4C4E74(整篇只排正负号)按 ToUnicode 抽成 U+0001, 就地还原
    for bad, good in CHAR_FIX.items():
        t = t.replace(bad, good)
    return " ".join(t.split())


def load(idx):
    doc = pymupdf.open(EXC)
    pages = []
    for i in idx:
        page = doc[i]
        wards, furn = [], []
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                t = norm("".join(s["text"] for s in ln["spans"]))
                if not t:
                    continue
                bb, d = ln["bbox"], tuple(round(v, 2) for v in ln["dir"])
                r = dict(y0=bb[1], y1=bb[3], x0=bb[0], x1=bb[2], t=t, pg=i + 1)
                (wards if d == (1.0, 0.0) else furn).append(r)
        wards.sort(key=lambda r: (r["y1"], r["x0"]))
        pages.append(dict(w=page.rect.width, h=page.rect.height,
                          rows=wards, furn=furn))
    doc.close()
    return pages


def split_noise(pg):
    """标出表注(整幅宽散文)与表格标记; 其余为候补单元格"""
    for r in pg["rows"]:
        r["noise"] = None
    wide = [r for r in pg["rows"] if (r["x1"] - r["x0"]) > PROSE_W * pg["w"]]
    for r in wide:
        r["noise"] = "表注"
    # 表注末行: 紧跟在任一表注行下面(间隙 < TAIL_DY), 但不再链式传递
    bottoms = [r["y1"] for r in wide]
    for r in pg["rows"]:
        if r["noise"]:
            continue
        if any(0 <= r["y0"] - b < TAIL_DY for b in bottoms):
            r["noise"] = "表注末行"
        elif r["t"].lower().startswith("table 10.") or r["t"].strip() == "(continued)":
            r["noise"] = "表格标记"
    return pg["rows"]


def columns(cells):
    """列 = 左边缘 x0 聚簇(近者并) -> (列左边缘, 各列落格数, 最左簇的 x0 值)

    落格数用于剔除"残列": 表格内居中的跨列组表头(如 T1 的 Flower traits, x0=430)
    会自成孤簇, 若不剔就是一个只有 1 格的幻影列。残列不丢, 在审计里逐条报。
    """
    cnt = Counter(round(c["x0"], 1) for c in cells)
    cl = []
    for x in sorted(cnt):
        if cl and x - cl[-1][-1] < CLU_TOL:
            cl[-1].append(x)
        else:
            cl.append([x])
    edges, sizes = [], {}
    for c in cl:
        n = sum(cnt[x] for x in c)
        if edges and c[0] - edges[-1] < COL_MERGE:
            sizes[edges[-1]] += n
        else:
            edges.append(c[0])
            sizes[c[0]] = n
    return edges, sizes, set(cl[0])


def main():
    report, tsvs = [], []
    for label, idx in (("table_10_1", [0]), ("table_10_2", range(1, 7))):
        pages = load(idx)
        noise, cells, furn = [], [], []
        for pg in pages:
            for r in split_noise(pg):
                (noise if r["noise"] else cells).append(r)
            furn += pg["furn"]

        edges_all, sizes, leftmost = columns(cells)
        edges = [e for e in edges_all if sizes[e] >= MIN_SUPPORT]
        stray = [(e, sizes[e]) for e in edges_all if sizes[e] < MIN_SUPPORT]
        mid = [(edges[i] + edges[i + 1]) / 2.0 for i in range(len(edges) - 1)]

        for c in cells:
            c["col"] = sum(1 for m in mid if c["x0"] > m)

        def fill(band):
            row = ["" for _ in edges]
            for c in band:
                row[c["col"]] = (row[c["col"]] + " " + c["t"]).strip()
            return row

        def spans_cols(c):
            """单元格横跨了另一列 —— 判据是"有别的列的左边缘落在它内部(留 3pt 边距)"

            不能用"与相邻列区间有交集"来判: 列边缘是舍入过的整数位, 而 bbox 是原始浮点,
            两者可差 0.04pt, 于是 406.2615 < 406.30 就谎报重叠(实测已在 T2 吃掉 381 个真格)。
            也不能对全表用: 长数据格(如 "germination, seedlings, seeds/fruit")会横跨多列。
            故只在表头带里判 —— 跨列组表头只可能出现在表头。
            """
            return any(c["x0"] + INNER < a < c["x1"] - INNER for a in edges)

        # 行首: 最左列里 x0 落在最左簇的行(续行缩进约 7pt, 天然排除)
        L = []
        out, seen_head, group_hdr, foot = [], False, [], []
        for pg in pages:
            pc = [c for c in cells if c["pg"] == pg["rows"][0]["pg"]] if pg["rows"] else []
            if not pc:
                continue
            anchors = sorted(round(c["y1"], 2) for c in pc
                             if round(c["x0"], 1) in leftmost and c["col"] == 0)
            if not anchors:
                L.append("  !! p%d 无行锚点" % pc[0]["pg"])
                continue
            bands = []
            b0 = anchors[0]
            head = [c for c in pc if c["y1"] < b0 - ROW_TOL]
            if head:
                bands.append((None, head))
            for i, b in enumerate(anchors):
                nxt = anchors[i + 1] - ROW_TOL if i + 1 < len(anchors) else 1e9
                bands.append((b, [c for c in pc if b - ROW_TOL <= c["y1"] < nxt]))
            for b, band in bands:
                row = fill(band)
                nz = [x for x in row if x]
                is_head = (len(nz) >= 8 and
                           any(x in ("Species", "Subfamily") or x.startswith("Table 10.")
                               for x in nz))
                if is_head:
                    gh = [c for c in band if spans_cols(c)]
                    if gh:
                        group_hdr += gh
                        gid = set(id(c) for c in gh)
                        row = fill([c for c in band if id(c) not in gid])
                    if seen_head:
                        L.append("  (续页表头已并, p%d, 去掉跨列组表头 %d 格)" % (pc[0]["pg"], len(gh)))
                        continue
                    seen_head = True
                elif len(nz) == 1 and row[0]:
                    # 表下脚注(如 T1 的 "aUndetermined"): 只有最左列一格, 不是数据行
                    foot.append((pc[0]["pg"], b, row[0]))
                    continue
                out.append((pc[0]["pg"], b, row, "表头" if is_head else ""))

        rows = [(pg, b, row) for pg, b, row, _ in out]

        tsv = os.path.join(OUTDIR, label + ".tsv")
        with open(tsv, "w", encoding="utf-8") as f:
            for pg, b, row, _ in out:
                f.write("\t".join(row) + "\n")
        tsvs.append(tsv)

        L.insert(0, "-" * 78)
        L.insert(0, "参照列锚点数 %d" % sum(1 for c in cells
                                          if round(c["x0"], 1) in leftmost and c["col"] == 0))
        L.insert(0, "跨列组表头 %d 条 / 噪声 %d 条 / 页级方向行 %d 条"
                 % (len(group_hdr), len(noise), len(furn)))
        L.insert(0, "%s : %d 列 / %d 行(含表头)" % (label, len(edges), len(out)))
        L.insert(0, "=" * 78)

        L.append("-" * 78)
        L.append("列左边缘: " + " ".join("%d" % e for e in edges))
        L.append("每列落格数: " + " ".join("%d" % sizes[e] for e in edges))
        L.append("残列(落格数 < %d, 已并入最近主列) %d 个: %s"
                 % (MIN_SUPPORT, len(stray), " ".join("%d(%d)" % s for s in stray) or "无"))
        L.append("-" * 78)
        if out:
            L.append("表头: " + " | ".join("%d:%s" % (i, x) for i, x in enumerate(out[0][2])))
        L.append("-" * 78)
        L.append("各数据行非空格数:")
        bad = 0
        for pg, b, row, _ in out[1:]:
            nz = sum(1 for x in row if x)
            flag = "" if nz >= len(edges) - 2 else "   <== 格数偏少"
            if flag:
                bad += 1
            L.append("  p%-2d y=%6.2f 非空 %2d/%d%s" % (pg, b, nz, len(edges), flag))
        L.append("  格数偏少的行: %d / %d" % (bad, len(out) - 1))
        L.append("-" * 78)
        L.append("跨列组表头 %d 条:" % len(group_hdr))
        for c in group_hdr:
            L.append("  p%-2d y=%6.2f x=%.1f-%.1f %s" % (c["pg"], c["y0"], c["x0"], c["x1"], c["t"]))
        L.append("-" * 78)
        L.append("表下脚注(单格行, 未进 TSV) %d 条:" % len(foot))
        for pg, b, t in foot:
            L.append("  p%-2d y=%6.2f %s" % (pg, b, t))
        L.append("-" * 78)
        L.append("噪声 %d 条:" % len(noise))
        for r in noise:
            L.append("  p%-2d y=%6.2f [%s] %s" % (r["pg"], r["y0"], r["noise"], r["t"][:72]))
        L.append("-" * 78)
        L.append("页级方向行(dir!=1,0, 已滤) %d 条:" % len(furn))
        for r in furn:
            L.append("  p%-2d y=%6.2f %s" % (r["pg"], r["y0"], r["t"][:60]))

        report.append("\n".join(L))
        print("[%s] %d 列 / %d 行 / 格少行 %d / 组表头 %d / 噪声 %d"
              % (label, len(edges), len(out), bad, len(group_hdr), len(noise)))
        for pg, b, row, _ in out[:4]:
            print("   p%-2d %s" % (pg, " | ".join(x[:22] for x in row)))
        xs = Counter(len(row) - sum(1 for x in row if not x) for _, _, row in rows)
        print("   每行非空格数分布:", dict(sorted(xs.items())))

    with open(os.path.join(OUTDIR, "grid_audit.txt"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(report) + "\n")
    print("审计:", os.path.join(OUTDIR, "grid_audit.txt"))
    for t in tsvs:
        print("TSV :", t)


main()
