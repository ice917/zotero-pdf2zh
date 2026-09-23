# -*- coding: utf-8 -*-
"""把译文底下那张**扫描底图**遮白 —— 扫描件源文「译叠原」画面紊乱的成品后处理。

症状与证据见 改动记录.md 9.9: 扫描件源 PDF 每页 = 一整页位图(英文墨迹) + 不可见
OCR 文本层。渲染链(pdf2zh 1.x 经典转换器)只删得掉**文本层**, 动不了位图里的墨迹,
于是中文被画在英文笔画上 —— 两套字挤在同一坐标, 看着就是乱的。

为什么是"后处理遮白"而不是"前处理遮白"(原拟方案 B 的形态):
  前处理(把白矩形写进**源** PDF 再进管线)会改变渲染出的**页面图像**, 而 pdf2zh 1.x
  正是拿这张图跑 ONNX 版面模型 (high_level.py:128-132), 用它剔除 figure/table/
  formula 三类区域(box=0)。文本区被抹白后若被判成 `abandon`, 该区文本**整段不译** ——
  静默丢译文, 比叠画更糟, 而且产物看着"干净", 最难发现。后处理不碰渲染, 零风险,
  也正是上游 BabelDOC `ocr_workaround` 的官方做法: "add white rectangular blocks
  below the translation to cover the original text content"。

遮哪儿(两路并集, 缺一不可):
  ① **源文文本行的框** —— 英文墨迹就在这些框里。只盖中文框不够: 中文比英文短,
     行尾会漏出英文。
  ② **成品里含中日韩字符的 span 框** —— 兜住"中文画到源文行框之外"的情形。
  ①只取**与②相交**的那些行 —— 图片(Fig.1)里的 OCR 碎片从不被翻译, 也就从不与中文
  相交, 于是天然被排除, 图注/刻度不会被白块吃掉。**这是本工具不误伤图的关键。**

局限: 遮白只作用于**底图**。若某处中文恰好压在图上(版面模型误判), 那一片的图会被
一并遮掉 —— 但那里原本就是叠画的, 遮掉只会更清楚。

用法(解释器同 tools/tests/run_all.py; 仓库无 venv 目录, 用绝对路径):
  PY = D:/Users/<user>/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  & $PY tools/whiten_scan_bg.py --pdf <成品 mono/dual> --src <原文 PDF> [--dry]
  缺省写 <成品同目录>/<stem>-clean.pdf。**双栏并排版(compare)不适用**: 它一页里放
  两页内容, 坐标与源文页不是同一套, 别传。
  --dry 只报数不落盘; 落盘后自动两条自检: ①渲染 72dpi 量"遮白前后区内墨迹占比"
  (应显著下降, 剩下的正是不该被遮的中文笔画); ②**底图逐像素比对**, **区外必须为 0**
  (证明遮白只动了遮白区, 文字层/图/页边一字未动 —— 这条为 0 才返回 0)。
"""
import argparse
import math
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import fitz  # noqa: E402

# 只需"中日韩统一表意文字"; 标点/数字由源文行框兜住, 不必管
CJK = re.compile(r"[\u3400-\u9fff]")
DARK = 160          # 灰度 < 此值算"有墨"
DPI = 72            # 自检渲染分辨率(粗看墨迹占比, 不必高)


def cjk_rects(page, inflate):
    """成品页里含中日韩字符的 span 框(外扩 inflate 磅)。"""
    out = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type", 0) != 0:              # 1 = 图片块
            continue
        for ln in blk.get("lines", []):
            for sp in ln.get("spans", []):
                if CJK.search(sp.get("text") or ""):
                    out.append(fitz.Rect(sp["bbox"]) + (-inflate, -inflate,
                                                        inflate, inflate))
    return out


def line_rects(page):
    """页里所有文本行的框(文字块的行)。"""
    out = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type", 0) != 0:
            continue
        for ln in blk.get("lines", []):
            r = fitz.Rect(ln["bbox"])
            if not r.is_empty:
                out.append(r)
    return out


def mask_rects(src_page, out_page, pad, inflate):
    """本页要盖白的矩形 = 与中文相交的源文行框 ∪ 中文框(各外扩 pad)。"""
    cjk = cjk_rects(out_page, inflate)
    rects = [r + (-pad, -pad, pad, pad) for r in cjk]
    for r in line_rects(src_page):
        if any(r.intersects(c) for c in cjk):
            rects.append(fitz.Rect(r) + (-pad, -pad, pad, pad))
    return rects


def blank_pixmap(pix, rects_px):
    """返回一份把 rects_px 涂白的新 Pixmap(原 pixmap 不动)。

    只涂颜色分量, alpha 通道原样保留 —— 扫描件没有 alpha, 这条只是别把它写坏。
    """
    n = pix.n + (1 if pix.alpha else 0)
    buf = bytearray(pix.samples)
    for x0, y0, x1, y1 in rects_px:
        x0, x1 = max(0, min(pix.width, x0)), max(0, min(pix.width, x1))
        y0, y1 = max(0, min(pix.height, y0)), max(0, min(pix.height, y1))
        if x1 <= x0 or y1 <= y0:
            continue
        white = b"\xff" * (pix.n * (x1 - x0))
        for y in range(y0, y1):
            off = y * pix.stride + x0 * n
            buf[off:off + len(white)] = white
    return fitz.Pixmap(pix.colorspace, pix.width, pix.height, bytes(buf), pix.alpha)


def to_px(page_rect, img_rect, pix, r):
    """页坐标下的矩形 -> 位图像素矩形。位图按正常方向铺放, 行 0 = 顶边。"""
    sx = pix.width / img_rect.width
    sy = pix.height / img_rect.height
    return (int((r.x0 - img_rect.x0) * sx), int((r.y0 - img_rect.y0) * sy),
            int(math.ceil((r.x1 - img_rect.x0) * sx)),
            int(math.ceil((r.y1 - img_rect.y0) * sy)))


def dark_ratio(pix, rects):
    """rects 覆盖区内"有墨"像素占比(粗看遮白效果; 分母是这些矩形的面积, 不并集去重)。"""
    if not rects:
        return 0.0, 0
    k = DPI / 72.0
    n = pix.n
    tot = dark = 0
    for r in rects:
        x0, y0 = int(r.x0 * k), int(r.y0 * k)
        x1, y1 = int(math.ceil(r.x1 * k)), int(math.ceil(r.y1 * k))
        for y in range(max(0, y0), min(pix.height, y1)):
            base = y * pix.stride
            for x in range(max(0, x0), min(pix.width, x1)):
                off = base + x * n
                tot += 1
                if pix.samples[off] < DARK:      # 灰度/RGB 的首分量都够用
                    dark += 1
    return (dark / tot if tot else 0.0), tot


def pix_diff(a, b, rects_px):
    """两张同尺寸**解码位图**的差异像素数 -> (落在 rects_px 内, 落在 rects_px 外)。

    比在**底图**上而不是在**页面渲染**上, 是这条判据成立的前提: 页面 72dpi 渲染时
    2550px 的底图被缩到 612px, 边界的插值会让遮白区的改动往区外**渗一个像素**
    (实测渗出 9869 px, 全是边界色带, 与内容无关)。底图逐像素比对没有插值,
    "区外为 0" 才能当硬门禁用。行级先比, 整行没动就跳过(快路径)。

    若为同尺寸不同通道数, 返回 (-1, -1) 表示不可比。
    """
    if (a.width, a.height, a.n) != (b.width, b.height, b.n):
        return -1, -1
    w, h, n, st = a.width, a.height, a.n, a.stride
    ivs = [[] for _ in range(h)]
    for x0, y0, x1, y1 in rects_px:
        x0, x1 = max(0, x0), min(w, x1)
        y0, y1 = max(0, y0), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        for y in range(y0, y1):
            ivs[y].append((x0, x1))
    sa, sb = a.samples, b.samples
    ins = out = 0
    for y in range(h):
        o = y * st
        if sa[o:o + w * n] == sb[o:o + w * n]:
            continue
        row = ivs[y]
        for x in range(w):
            p = o + x * n
            if sa[p:p + n] == sb[p:p + n]:
                continue
            if row and any(lo <= x < hi for lo, hi in row):
                ins += 1
            else:
                out += 1
    return ins, out


def drawn_images(doc, pno):
    """该页所有"画面"位图 -> [(xref, Pixmap, filter 名)]; 跳过 mask/smask。"""
    out = []
    for x in doc[pno].get_images(full=True):
        try:
            px = fitz.Pixmap(doc, x[0])
        except Exception:                                 # noqa: BLE001
            continue
        if px.colorspace is None:
            continue
        out.append((x[0], px, doc.xref_get_key(x[0], "Filter")[1] or ""))
    return out


def process_one(inp, srcp, out, pad, inflate, dry):
    src = fitz.open(srcp)
    doc = fitz.open(inp)
    if src.page_count != doc.page_count:
        print("FAIL: 源文 %d 页 / 成品 %d 页 —— 不是同一篇, 拒收"
              % (src.page_count, doc.page_count))
        return 1
    print("成品: %s" % inp)
    print("源文: %s" % srcp)
    print("页数 %d | pad=%.1f 磅 | 相交判定外扩=%.1f 磅" % (doc.page_count, pad, inflate))
    tot_masked = 0
    marks = []                        # (页序, rects, 遮白前的区内墨迹占比, 区内像素数)
    painted = []                      # (页序, 遮白前的底图 Pixmap, 图上遮白矩形)
    for i in range(doc.page_count):
        page = doc[i]
        rs = mask_rects(src[i], page, pad, inflate)
        if not rs:
            print("  p%d: 无中文 -> 不动" % (i + 1))
            continue
        b, npx = dark_ratio(page.get_pixmap(dpi=DPI), rs)   # 改图之前先量
        imgs = page.get_images(full=True)
        painted_here = 0
        for x in imgs:
            xref = x[0]
            try:
                pix = fitz.Pixmap(doc, xref)
            except Exception as e:                        # noqa: BLE001
                print("    (p%d xref=%s 读不出位图: %s)" % (i + 1, xref, e))
                continue
            if pix.colorspace is None:                    # mask/smask, 不是画面
                continue
            rpx = []
            for rc in page.get_image_rects(xref):
                rpx = [to_px(page.rect, rc, pix, r) for r in rs]
                if not dry:
                    page.replace_image(xref, pixmap=blank_pixmap(pix, rpx))
                painted.append((i, pix, rpx))
                painted_here += 1
        print("  p%d: 中文 span 框 %d / 源文行框 %d -> 遮白矩形 %d / 位图 xref %d / "
              "涂白 %d 处 | 区内墨迹占比 遮前 %.1f%% (%.0f px)"
              % (i + 1, len(cjk_rects(page, inflate)), len(line_rects(src[i])), len(rs),
                 len(imgs), painted_here, b * 100, npx))
        tot_masked += len(rs)
        marks.append((i, rs, b, npx))
    if dry:
        print("\n[dry] 合计遮白矩形 %d —— 未落盘" % tot_masked)
        return 0
    if not tot_masked:
        print("\n没有任何可遮的区域(成品里没有中文?) —— 不落盘")
        return 1
    # garbage=4 + clean: replace_image 会另建一个同名内容的图像对象, 旧项会作为
    # 死引用留在 /Resources 里(garbage=3 删不掉 —— 它仍被资源字典指着)。
    # 实测 garbage=4+clean 后 p1 图像名由 ['Im0','fzImg0'] 回到 ['Im0'], 体积还小一点。
    doc.save(out, garbage=4, deflate=True, clean=True)
    print("\n落盘: %s" % out)
    # 自检两条: ① 区内墨迹显著下降(剩下的正是不该被遮的中文笔画);
    #           ② **底图**逐像素比对 —— 区外必须为 0, 证明遮白只动了遮白区。
    doc2 = fitz.open(out)
    stat = {}
    for i, pix0, rpx in painted:
        p2 = None
        for _x, px, _f in drawn_images(doc2, i):
            if (px.width, px.height, px.n) == (pix0.width, pix0.height, pix0.n):
                p2 = px
                break
        if p2 is None:
            print("  (p%d 比对跳过: 落盘后找不到同尺寸底图)" % (i + 1))
            continue
        ins, out_ = pix_diff(pix0, p2, rpx)
        a, bb = stat.get(i, (0, 0))
        stat[i] = (a + ins, bb + out_)
    bad = 0
    for i, rs, b0, npx in marks:
        b1, _ = dark_ratio(doc2[i].get_pixmap(dpi=DPI), rs)
        ins, out_ = stat.get(i, (0, 0))
        if out_:
            bad += 1
            tail = "  <== 区外被动过, 不可交付"
        else:
            tail = ""
        print("  自检 p%d: 区内墨迹 遮前 %.1f%% -> 遮后 %.1f%% (%d px) | 底图差异 "
              "区内 %d / 区外 %d%s" % (i + 1, b0 * 100, b1 * 100, npx, ins, out_, tail))
    doc2.close()
    if bad:
        print("\nFAIL: %d 页底图出现区外像素差异 —— 遮白越界" % bad)
        return 1
    print("\nPASS: 底图区外像素差异为 0 —— 遮白只动了该动的地方")
    return 0


def main():
    ap = argparse.ArgumentParser(description="把译文底下的扫描底图遮白(成品后处理)")
    ap.add_argument("--pdf", required=True, help="成品 PDF(mono 或 dual)")
    ap.add_argument("--src", required=True, help="该篇**原文** PDF(取英文墨迹的行框)")
    ap.add_argument("--out", default="", help="输出路径(缺省 <stem>-clean.pdf)")
    ap.add_argument("--pad", type=float, default=1.5, help="每个矩形的外扩磅数(缺省 1.5)")
    ap.add_argument("--inflate", type=float, default=3.0,
                    help="判'源文行与中文相交'时中文框的外扩磅数(缺省 3.0)")
    ap.add_argument("--dry", action="store_true", help="只报数不落盘")
    args = ap.parse_args()

    inp, srcp = os.path.abspath(args.pdf), os.path.abspath(args.src)
    for p in (inp, srcp):
        if not os.path.exists(p):
            print("找不到: " + p)
            return 1
    out = os.path.abspath(args.out) if args.out else \
        os.path.join(os.path.dirname(inp),
                     os.path.splitext(os.path.basename(inp))[0] + "-clean.pdf")
    if out in (inp, srcp):
        print("FAIL: 输出路径与输入同一份, 拒绝覆盖")
        return 1
    return process_one(inp, srcp, out, args.pad, args.inflate, args.dry)


if __name__ == "__main__":
    sys.exit(main())
