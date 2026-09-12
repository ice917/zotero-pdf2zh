# -*- coding: utf-8 -*-
"""按页拆分 PDF 成 <10MB 的块(每块<=8页起, 若超限再减)"""
import pymupdf, os

SRC = r"D:\文件对比\Proximity Modeling Rainfall 11.pdf"
OUTDIR = r"D:\解释token\literature_analysis\_split11"
os.makedirs(OUTDIR, exist_ok=True)

d = pymupdf.open(SRC)
n = d.page_count
# 先算每页大小
page_bytes = []
for i in range(n):
    # 用 xref 数据近似: 页含 xref 对象; 简单方式: 新建单页doc写临时内存
    tmp = pymupdf.open()
    tmp.insert_pdf(d, from_page=i, to_page=i)
    page_bytes.append(len(tmp.tobytes()))
    tmp.close()
total = sum(page_bytes)
print(f"总页数 {n}, 估算总量 {total/1048576:.1f}MB, 每页均 {total/n/1024:.0f}KB")

# 贪心分块, 每块 <= 8MB
chunks = []
cur, cursz = [], 0
for i, b in enumerate(page_bytes):
    if cur and cursz + b > 8 * 1048576:
        chunks.append(cur)
        cur, cursz = [], 0
    cur.append(i)
    cursz += b
if cur:
    chunks.append(cur)

paths = []
for k, pages in enumerate(chunks):
    out = os.path.join(OUTDIR, f"part{k+1:02d}.pdf")
    t = pymupdf.open()
    t.insert_pdf(d, from_page=pages[0], to_page=pages[-1])
    t.save(out)
    t.close()
    sz = os.path.getsize(out)
    paths.append(out)
    print(f"part{k+1}: 页{pages[0]+1}-{pages[-1]+1} ({len(pages)}页) {sz/1048576:.1f}MB")

print("SPLIT_OK")
