# -*- coding: utf-8 -*-
"""style_links.py — 链接可见性恢复: 锚文本原位重绘为蓝色(替代下划线)

背景: 译文重排后锚文本变黑(原版为蓝色), 链接框虽在, 但读者看不出可点。
曾经的做法是画 0.8pt 蓝色下划线 —— 观感差(像错别字标记)且用户不接受。
本工具改为"取原字体、在原 origin 原位叠绘蓝色字形": 与黑字完全重合覆盖,
视觉即原版的蓝色锚文本, 零额外线条、零遮挡。

实现要点:
  - 字体从成品页自身的字体资源里抽(extract_font), 保证字形与原文一致;
    抽不出来(非嵌入的 Base-14: Times/Helvetica/Courier)就用 PyMuPDF 内置同名体顶上,
    度量一致; 页面上两套字体**写法不同**时靠 fontkey() 归一化配对(见该函数);
  - 只重绘落进链接矩形的字符(通常就是年份/编号);
  - 抽不到字体或重绘失败 -> 跳过该处(保持黑字, 不破坏页面)。
回填页(p3, 9-15)为原版页面, 自带蓝字, 跳过。

[2026-09-23] 此前只有"两边同名的嵌入式字体"能重绘成功 —— 中文锚(LXGW Neo ZhiSong,
  basefont 带空格 = `LXGW Neo ZhiSong Regular`)与非嵌入的 Base-14 一律被跳过, 于是
  **中文链接锚在成品里一直是黑的**(实测 GeoTLM mono: 307 段全中, 跳过 0; 改前跳过 58)。
  用户自定义概念链接把这件事暴露了出来: 锚是他从本页点的中文词, 装上了却看不出能点。
  上面两条兜底就是为它加的。

已知代价: 重绘是"叠加"而非"替换" —— 视觉上蓝字完全盖住原黑字, 但 PDF 文本
层里锚文本会重复一次(复制该年份会得到 '19901990')。不用 redaction 替换, 是因为
apply_redactions 会清掉该页全部链接(实测 18 -> 0), 而本工具跑在挂链接之后。
这条代价现在覆盖到中文锚(以前中文锚被跳过, 反而没有重复) —— 换来的可见性值这个价。

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

# 未嵌入的 Base-14 字体 -> PyMuPDF 内置字体名(度量相同)。见主循环里"抽不出字体"那一段。
BASE14 = {"timesroman": "tiro", "timesnewroman": "tiro", "times": "tiro",
          "helvetica": "helv", "arial": "helv",
          "courier": "cour", "couriernew": "cour"}


def fontkey(s):
    """字体名的归一化键: 去子集前缀 -> 去空白/连字符/大小写 -> 去字重后缀。

    为什么需要它: 同一个字体在 PyMuPDF 的两套接口里写法不同 ——
    get_fonts 的 basefont 是 `LXGW Neo ZhiSong Regular`, 而 rawdict 的 span['font']
    是 `LXGWNeoZhiSong`。查不到就整段跳过, 于是**中文锚一律重绘不出来**
    (实测 2026-09-23: 用户自定义链接的锚选中文概念词时, 链接装上了但不变蓝,
    读者看不出能点 —— 功能等于没做)。拉丁字体没这问题(NimbusRomNo9L-Regu 两边同名),
    所以此前一直没暴露。
    """
    s = (s or "").split("+")[-1]
    s = re.sub(r"[^A-Za-z0-9]", "", s).lower()
    for w in ("regular", "bolditalic", "boldoblique", "italic", "oblique", "bold", "medium"):
        if s.endswith(w) and len(s) > len(w):
            return s[:-len(w)]
    return s


def page_font_xrefs(page):
    """basefont(去子集前缀 / 归一化) -> xref。

    三档键按可靠性递减, 用 setdefault 保证前两档不被归一化档顶掉。"""
    m = {}
    for f in page.get_fonts(full=True):
        xref, ext, ftype, basefont, name, enc = f[:6]
        m[basefont] = xref
        m.setdefault(basefont.split("+")[-1], xref)
        m.setdefault(fontkey(basefont), xref)
    return m


def in_link(cbbox, r):
    """这个字算不算"落在矩形 r 这条链接里": 字心在框内, 或字的**大半**在框内。

    只用 intersects 会把**邻行/邻格**那些只擦到边的字也算进来 —— 而重绘是按
    chars[0].origin 起笔的, 于是这串字被**整体搬到别处**画了一遍: 实测 2026-09-23
    一条 `[1]` 引文锚的框右下角擦到下一行的 '9v', 重绘出的蓝字落在下一行正文里,
    该处 IoU 从 0.996 掉到 0.51(肉眼可见的乱码)。字心判据把擦边字排除; 保留
    "大半在框内"这一档, 是为了边缘被框线切掉一角的锚字仍能重绘。
    """
    cb = pymupdf.Rect(cbbox)
    if r.contains(pymupdf.Point((cb.x0 + cb.x1) / 2, (cb.y0 + cb.y1) / 2)):
        return True
    return (cb & r).get_area() >= 0.5 * cb.get_area()


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
                                 if in_link(c["bbox"], r)]
                        if not chars:
                            continue
                        if tuple(round(v, 2) for v in ln.get("dir", (1, 0))) != (1, 0):
                            # 旋转/竖排行不重绘(实测: 页边竖排的 arXiv 戳就是这种) ——
                            # insert_text 只会**平着写**, 照它自己的 origin 重绘等于把
                            # 这串字横着铺过整页(实测把戳上的 '9v' 画进了正文, 该处
                            # IoU 0.996 -> 0.51)。这类锚保持原色, 不动它。
                            skipped += 1
                            continue
                        xref = fm.get(s["font"]) or fm.get(fontkey(s["font"]))
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
                                # 抽到 0 字节也算"没得用"(Base-14 在有些 PDF 里不报错,
                                # 只回一个空文件) —— 当失败处理才会走到下面的内置字体兜底。
                                fcache[xref] = (path, "UL%d" % len(fcache)) if content else None
                            except Exception:
                                fcache[xref] = None
                        if not fcache[xref]:
                            # 没嵌进文件的字体(Times/Helvetica/Courier 这类 Base-14)用
                            # extract_font 抽不出来 -> 此前一律跳过, 于是**正文里的拉丁词
                            # 当锚时也不变蓝**(实测 2026-09-23: 『GelSight Mini』装在成品上
                            # 了, 读起来仍是黑字)。改用 PyMuPDF 内置的同名字体顶上: 度量
                            # 一致(Times-Roman ↔ tiro), 叠在原字形上视觉就是蓝的。
                            b14 = BASE14.get(fontkey(s["font"]))
                            if not b14:
                                skipped += 1
                                continue
                            path, fname = None, b14
                        else:
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
    if skip:
        # 回填页是整页跳过的: 那页**新装**的锚也不会变蓝(原书自带的锚本来就蓝, 看不出差别,
        # 只有用户新加的会露馅)。把这句话放在这里说 —— 只有本工具知道哪些页被跳过。
        print("  注意: 上面这些页整页未重绘 —— 落在它们上面的新装锚仍是黑字, "
              "要看得见请把锚选在译文页上。")


if __name__ == "__main__":
    main()
