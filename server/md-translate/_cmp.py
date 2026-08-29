# -*- coding: utf-8 -*-
"""原版 PDF 字体使用统计(判断正文 vs 公式字体)"""
import pymupdf, io
from collections import Counter

OUT = r"D:\解释token\literature_analysis\_cmp_report.txt"
ORIG = r"D:\zotero-pdf2zh\server\translated\Proximity Modeling Rainfall.pdf"

buf = io.StringIO()
def log(s): buf.write(str(s) + "\n")

o = pymupdf.open(ORIG)
fonts = Counter()
for i in range(o.page_count):
    for b in o[i].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                fonts[(s["font"], round(s["size"], 1))] += len(s["text"])

log("===== 原版全文档字体统计 (字体, 字号) -> 字符数 =====")
for (f, sz), n in fonts.most_common(15):
    log(f"  {f}  {sz}pt: {n}")

# 样例文本
log("\n===== 原版页9(index8) 各字体样例 =====")
for b in o[8].get_text("dict")["blocks"]:
    for l in b.get("lines", []):
        for s in l.get("spans", []):
            t = s["text"]
            if t.strip():
                log(f"  [{s['font']}] {t.strip()[:60]}")

open(OUT, "w", encoding="utf-8").write(buf.getvalue())
print("DONE")
