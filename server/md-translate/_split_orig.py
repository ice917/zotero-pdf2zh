# -*- coding: utf-8 -*-
"""拆分原版 PDF 页1-9 供 MinerU 对比 OCR"""
import pymupdf, os

SRC = r"D:\zotero-pdf2zh\server\translated\Proximity Modeling Rainfall.pdf"
OUT = r"D:\解释token\literature_analysis\_orig_p1_9.pdf"

d = pymupdf.open(SRC)
t = pymupdf.open()
t.insert_pdf(d, from_page=0, to_page=8)
t.save(OUT)
t.close()
print("SAVED", os.path.getsize(OUT))
