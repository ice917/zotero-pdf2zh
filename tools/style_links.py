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

用法: python tools/style_links.py --target <成品.pdf> [--sidecar <侧车>]
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

    doc = pymupdf.open(args.target)
    tmpf = []
    fcache = {}          # (xref) -> (临时字体路径, 注册名)
    n_span = 0
    n_char = 0
    skipped = 0

    for pno in range(len(doc)):
        if pno + 1 in skip:
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
    print("链接锚重绘为蓝色: %d 段/%d 字 | 跳过 %d (跳过回填页 %s)"
          % (n_span, n_char, skipped, sorted(skip) or "无"))


if __name__ == "__main__":
    main()
