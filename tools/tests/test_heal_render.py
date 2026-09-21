# -*- coding: utf-8 -*-
"""heal_render 渲染残渣治伤单元测试 (v28.63, 2026-09-21)

被锁死的缺陷 —— "文字没翻错, 版面坏了"的两类伤, 只能把原版同区域盖回去:

  R) 旋转文本退化: 原版里旋转 90° 的图内标注(实测 GeoTLM 的 avocado/hammer/
     wrench/cylinder、arXiv 水印), 译文里丢了旋转被拆成一列单字。
     陷阱: 退化竖列 `a\\nrX\\niv…` 去空白后与原版 `arXiv:…` **逐字相同** ——
     用"文字相等"判会全部放过(实测假相等放过了 p1 整条水印)。判据必须看布局
     (`keeps_rotation`: 该区域还有没有 dir 非水平的行)。
  B) 文献区错位: 参考文献页的 [n] 编号被丢到行内/页顶(实测 GeoTLM p7: [7] 落在
     条目文字中间, [20]~[23] 落到 [1] 之上), 而条目本身是英文(禁汉化) ——
     正确形态就是原版那一页。

另锁两条工程不变量(没锁住就会静默回归):
  * **擦哪儿盖哪儿**: R 段 redact 外扩 1.5pt(退化单字会探出原版行框), 补盖必须
    同样外扩。曾经"擦 rp 盖 R", 那一圈就成了抹白的空环 —— 实测 GeoTLM p5 右侧
    1pt 带与原版差 3.23, 同尺寸补盖后 0.48。
  * **链接必须活下来**: `apply_redactions()` 会清掉与 redact 矩形相交的链接
    (实测 GeoTLM p7 一次吞 60 条), 且调用后 `page.get_links()` 会抛 PyMuPDF 内部
    错(链接表指向已消失的 xref)。故本工具先快照、按"相交即删"预测、盖完回插。
  这两条一起构成"治伤不得损坏链接"的门禁。

判定手法 (全部合成, 不依赖真实论文): 造 orig/tr 两版 PDF, 页 1 摆旋转标注(R),
页 2 摆 [1]~[6] 编号(B); 治伤后逐像素比对 + 链接计数比对。
  * 干跑即体检: 有伤未治 -> 退出码 1; 治完再跑 -> 0 且报 "体检 PASS"(幂等)。
  * dual 映射(pno//2): 合成 2 页 dual, 只治译文页。

运行: venv python test_heal_render.py, 退出码 0=全过
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOLS, "heal_render.py")

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

import pymupdf  # noqa: E402

FOOT = "Alpha Beta Gamma Delta Epsilon Zeta".split()
LABELS = ["[%d] %s entry text" % (i + 1, FOOT[i]) for i in range(6)]
ROT_A, ROT_B = "avocado", "hammer"


def make_pair(path, degenerate=True):
    """两页: p1 旋转标注(R 案), p2 文献编号(B 案)。

    degenerate=True 造"译文"侧: p1 把旋转行拆成一列单字; p2 把 [2]~[6] 塞进
    行中、并把 [23] 丢到 [1] 之上。False 造"原版"侧。
    """
    doc = pymupdf.open()
    p1 = doc.new_page(width=612, height=792)
    if degenerate:
        for i, ch in enumerate(ROT_A + ROT_B):          # 一列单字, 覆盖原版那一列
            p1.insert_text((55, 700 - i * 5.3), ch, fontsize=9)
        p1.insert_link({"kind": pymupdf.LINK_NAMED, "from": pymupdf.Rect(55, 630, 62, 700),
                        "nameddest": "cite.anchor"})
    else:
        p1.insert_text((60, 700), ROT_A, fontsize=9, rotate=90)
        p1.insert_text((60, 660), ROT_B, fontsize=9, rotate=90)
        p1.insert_link({"kind": pymupdf.LINK_NAMED, "from": pymupdf.Rect(50.3, 626, 62.7, 700),
                        "nameddest": "cite.anchor"})
    p2 = doc.new_page(width=612, height=792)
    if degenerate:
        p2.insert_text((72, 80), "[23] " + FOOT[0], fontsize=10)   # 散落编号(在 [1] 之上)
        p2.insert_text((72, 96), LABELS[0], fontsize=10)           # [1] 行
        for i in range(1, 6):                                      # 其余编号塞进行中
            p2.insert_text((72, 116 + (i - 1) * 20), "see " + LABELS[i], fontsize=10)
    else:
        p2.insert_text((72, 96), LABELS[0], fontsize=10)
        for i in range(1, 6):
            p2.insert_text((72, 116 + (i - 1) * 20), LABELS[i], fontsize=10)
    doc.save(path)
    doc.close()
    return path


def make_dual(path, orig_path):
    """2 页 dual: p1 = 原版页(旋转标注完好), p2 = 译文页(退化列)。"""
    orig = pymupdf.open(orig_path)
    doc = pymupdf.open()
    a = doc.new_page(width=612, height=792)
    a.show_pdf_page(a.rect, orig, 0)                     # 原版页照搬
    b = doc.new_page(width=612, height=792)
    for i, ch in enumerate(ROT_A + ROT_B):
        b.insert_text((55, 700 - i * 5.3), ch, fontsize=9)
    doc.save(path)
    doc.close()
    orig.close()
    return path


def run(root, *args):
    proc = subprocess.run([sys.executable, SCRIPT] + list(args),
                          cwd=root, capture_output=True)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def links(path, pno):
    d = pymupdf.open(path)
    out = [(l["kind"], l.get("nameddest")) for l in d[pno].get_links()]
    d.close()
    return out


def pixdiff(pa, pb, rect, dpi=200):
    a, b = pymupdf.open(pa), pymupdf.open(pb)
    x = a[0].get_pixmap(dpi=dpi, clip=rect)
    y = b[0].get_pixmap(dpi=dpi, clip=rect)
    d = 0.0
    if (x.width, x.height, x.n) == (y.width, y.height, y.n):
        d = sum(abs(u - v) for u, v in zip(x.samples, y.samples)) / len(x.samples)
    a.close()
    b.close()
    return d


CLUSTER = pymupdf.Rect(50.3, 626, 62.7, 700)   # 两条旋转行并成的整列


def main():
    tmp = tempfile.mkdtemp(prefix="pdf2zh_test_heal_")
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    orig = make_pair(os.path.join(tmp, "orig.pdf"), degenerate=False)
    tr = make_pair(os.path.join(tmp, "tr.pdf"), degenerate=True)
    tr_damaged = os.path.join(tmp, "tr_damaged.pdf")
    with open(tr, "rb") as f, open(tr_damaged, "wb") as g:
        g.write(f.read())

    # ---- ① 干跑即体检: 有伤 -> FAIL/退出码 1 ----
    rc, so, se = run(tmp, "--target", tr, "--original", orig, "--dry-run")
    check("① 干跑报 R 1 处 + B 1 页", "R 旋转文本 1 处" in so and "B 文献区 1 页" in so, so.strip()[-300:])
    check("① 有伤未治 -> 退出码 1", rc == 1, "rc=%d %s" % (rc, se[-200:]))
    check("① 报体检 FAIL", "体检 FAIL" in so, so.strip()[-200:])

    # ---- ② 落盘治伤: 链接必须活下来 ----
    before = links(tr, 0)
    rc, so, se = run(tmp, "--target", tr, "--original", orig)
    check("② 治伤退出码 0", rc == 0, "%d %s" % (rc, se[-300:]))
    check("② 治伤前页 1 有 1 条链接", len(before) == 1 and before[0][0] == pymupdf.LINK_NAMED, repr(before))
    after = links(tr, 0)
    check("② redact 吞掉的链接被补回(计数与命名目标不变)", after == before, repr(after))

    # ---- ③ 幂等: 治过再干跑 -> PASS/退出码 0 ----
    rc, so, se = run(tmp, "--target", tr, "--original", orig, "--dry-run")
    check("③ 治后再干跑退出码 0", rc == 0, "%d %s" % (rc, se[-200:]))
    check("③ 报体检 PASS", "体检 PASS" in so, so.strip()[-200:])

    # ---- ④ 像素: 旋转列已与原版逐通道相同(擦哪儿盖哪儿) ----
    check("④ 旋转列与原版逐像素相同(差 0.00)", pixdiff(tr, orig, CLUSTER) == 0.0,
          "差=%.2f" % pixdiff(tr, orig, CLUSTER))
    check("④ 治伤前该列确实不同(证明上面那条不是空过)",
          pixdiff(tr_damaged, orig, CLUSTER) > 5.0, "差=%.2f" % pixdiff(tr_damaged, orig, CLUSTER))

    # ---- ⑤ 文献区: 治后 [n] 行数与原版对齐 ----
    rc, so, se = run(tmp, "--target", tr, "--original", orig, "--dry-run")
    check("⑤ 文献区 [n] 行数与原版对齐", re.search(r"\[n\]行 (\d+)/(\d+)", so) is not None
          and len(set(re.findall(r"\[n\]行 (\d+)/(\d+)", so)[0])) == 1, so.strip()[-200:])

    # ---- ⑥ dual: 2 页对 1 页, 只治译文页(锁 pno//2 映射) ----
    dual = make_dual(os.path.join(tmp, "dual.pdf"), orig)
    rc, so, se = run(tmp, "--target", dual, "--original", orig, "--dual")
    check("⑥ dual 治伤退出码 0(不越界)", rc == 0 and "盖回失败" not in so, "%d %s" % (rc, se[-300:]))
    d = pymupdf.open(dual)
    txt = d[1].get_text()
    d.close()
    check("⑥ dual 译文页的退化列已被原版旋转行替换", "avocado" in txt.replace("\n", ""), repr(txt[:80]))
    rc, so, se = run(tmp, "--target", dual, "--original", orig, "--dual", "--dry-run")
    check("⑥ dual 治后再干跑退出码 0", rc == 0, "%d %s" % (rc, so.strip()[-200:]))

    print("\nheal_render 渲染残渣治伤单元测试: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
