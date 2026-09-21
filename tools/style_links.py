# -*- coding: utf-8 -*-
"""style_links.py — 链接可见性恢复: 锚文本原位重绘为蓝色(替代下划线)

背景: 译文重排后锚文本变黑(原版为蓝色), 链接框虽在, 但读者看不出可点。
曾经的做法是画 0.8pt 蓝色下划线 —— 观感差(像错别字标记)且用户不接受。
本工具改为"取原字体、在原 origin 原位叠绘蓝色字形": 与黑字完全重合覆盖,
视觉即原版的蓝色锚文本, 零额外线条、零遮挡。

实现要点:
  - 字体从成品页自身的字体资源里抽(extract_font), 保证字形与原文一致;
  - 只重绘落进链接矩形的字符(通常就是年份/编号);
  - 抽不到字体或重绘失败 -> 跳过该处(保持黑字, 不破坏页面)。
回填页(p3, 9-15)为原版页面, 自带蓝字, 跳过。

已知代价: 重绘是"叠加"而非"替换" —— 视觉上蓝字完全盖住原黑字, 但 PDF 文本
层里锚文本会重复一次(复制该年份会得到 '19901990')。不用 redaction 替换, 是因为
apply_redactions 会清掉该页全部链接(实测 18 -> 0), 而本工具跑在挂链接之后。

--dual(双语版): dual 每对页 = [原版页, 译文页], 原版页**本来就是蓝字** —— 对它
再叠绘一次既无收益, 又白白污染文本层(原版页的文本层原本是干净的), 故 --dual 时
只处理译文侧(0 基奇数页)。跑在 dual_links.py 之后。
--sidecar 的页码是 **mono 坐标**(1 基), --dual 时按 2(p-1)+2 换算成 dual 里同一页的
译文侧编号 —— 不换算就会去跳 mono 那一页在 dual 的编号, 回填页(自带蓝字)照样被叠绘。

用法: python tools/style_links.py --target <成品.pdf> [--sidecar <侧车>] [--dual]
"""
import io, os, re, sys, tempfile
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

BLUE = (0.0, 0.0, 0.75)


def page_font_xrefs(page):
    """basefont(去子集前缀) -> xref"""
    m = {}
    for f in page.get_fonts(full=True):
        xref, ext, ftype, basefont, name, enc = f[:6]
        m[basefont] = xref
        m.setdefault(basefont.split("+")[-1], xref)
    return m


def main():
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--sidecar", default="")
    ap.add_argument("--dual", action="store_true",
                    help="成品是双语版(每对页=[原版页,译文页]): 只重绘译文侧(0基奇数页)")
    args = ap.parse_args()

    skip = set()
    if args.sidecar:
        PURE = re.compile(r"^(?:\{v\d+\})+$")
        for line in open(args.sidecar, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if not any((s.get("raw") or "").strip() and not PURE.match((s.get("raw") or "").strip())
                       and re.search(r"[A-Za-z0-9]", s["raw"]) for s in o["segs"]):
                skip.add(o["page"])

    if args.dual and skip:
        # 侧车页码是 mono 坐标(1 基), dual 里同一页的**译文侧**是 2(p-1)+2(1 基) ——
        # 不换算就会去跳 mono 那一页在 dual 的编号, 回填页(自带蓝字)照样被叠绘一遍。
        skip = {2 * (p - 1) + 2 for p in skip}

    doc = pymupdf.open(args.target)
    tmpf = []
    fcache = {}          # (xref) -> (临时字体路径, 注册名)
    n_span = 0
    n_char = 0
    skipped = 0

    for pno in range(len(doc)):
        if pno + 1 in skip:
            continue
        if args.dual and pno % 2 == 0:      # dual 原版侧本来就是蓝字, 不许再叠
            continue
        page = doc[pno]
        links = page.get_links()
        if not links:
            continue
        fm = page_font_xrefs(page)
        for l in links:
            r = l["from"]
            for b in page.get_text("rawdict", clip=r)["blocks"]:
                for ln in b.get("lines", []):
                    for s in ln["spans"]:
                        chars = [c for c in s.get("chars", [])
                                 if pymupdf.Rect(c["bbox"]).intersects(r)]
                        if not chars:
                            continue
                        xref = fm.get(s["font"])
                        if xref is None:
                            skipped += 1
                            continue
                        if xref not in fcache:
                            try:
                                name, ext, sub, content = doc.extract_font(xref)
                                fd, path = tempfile.mkstemp(suffix="." + (ext or "ttf"))
                                with os.fdopen(fd, "wb") as fh:
                                    fh.write(content)
                                tmpf.append(path)
                                fcache[xref] = (path, "UL%d" % len(fcache))
                            except Exception:
                                fcache[xref] = None
                        if not fcache[xref]:
                            skipped += 1
                            continue
                        path, fname = fcache[xref]
                        txt = "".join(c["c"] for c in chars)
                        try:
                            page.insert_text(chars[0]["origin"], txt,
                                             fontsize=s["size"], fontname=fname,
                                             fontfile=path, color=BLUE)
                            n_span += 1
                            n_char += len(chars)
                        except Exception:
                            skipped += 1
    tmp = args.target + ".tmp"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, args.target)
    for p in tmpf:
        try:
            os.remove(p)
        except OSError:
            pass
    print("链接锚重绘为蓝色: %d 段/%d 字 | 跳过 %d (跳过回填页 %s)%s"
          % (n_span, n_char, skipped, sorted(skip) or "无",
             " | dual: 已跳过原版侧" if args.dual else ""))


if __name__ == "__main__":
    main()
