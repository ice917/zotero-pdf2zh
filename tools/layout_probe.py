# -*- coding: utf-8 -*-
"""layout_probe.py — 版面类别图取证（只读、零 API 成本、不改任何生产文件）

【解决什么问题】
  「词被切成两段」这类缺陷（`T|opology`、`TAB|LE I:`、`seco|nd-best`）**只看段表和
  产物 PDF 都定不了位** —— 看不到引擎为什么在这里断段。本工具把那一层打开：逐字符
  打出它在 YOLO 版面检测框图上采到的类别 `cls`，`cls` 跳变处就是生产里的断段处。

【根因（2026-09-24 取证，结论）】
  `converter.receive_layout` 里**唯一**的断段条件是
      if cls == xt_cls: ... else: sstk.append("")        # converter.py L469 / L475
  而 `cls` 由**字符 bbox 左下角一个像素**在版面检测框图上采样
      cx, cy = int(child.x0), int(child.y0);  cls = layout[cy, cx]     # L415-416
  若某个检测框的边缘落在**词内部**，该词就会因逐字符 `cls` 跳变而被切成两段 ——
  一段送翻、一段按公式/保留区留原文（或反过来）。**段边界 = 检测框的像素边缘，
  与词/句边界无关。** 实测两篇五处切点全部落在框边缘上。

【本脚本与生产的口径关系（改动上游时要同步看这里）】
  · 字符流：用**与生产同一套**解析器（`PDFPageInterpreterEx` + `PDFConverterEx`）；
  · 类别图：`build_box_map()` 是 `pdf2zh/high_level.py` L134-161 的**逐行复刻**
    （默认 1 背景 / 非保留类框 = i+2(≥2) / figure·table = -1 / 其余保留类 = 0）。
    该文件在 `test_mirrors_sync.py` 的镜像清单内，**它一改，这里要跟着核**。

用法（venv python；路径按需用环境变量覆盖）:
  python tools/layout_probe.py <pdf> <页码> [关键字]   # 逐字符 cls + 检测框清单
  python tools/layout_probe.py --compare <pdf> <页码>  # 三种采样口径对照（见下）
  python tools/layout_probe.py --cuts <pdf> [--pages 3,5-9] [--json]
                                                       # **整篇扫描「版面切点」**（见下）

【`--cuts` 是类②切点的唯一覆盖手段（v30.4）】
  `tools/seg_check.py` 的「接缝切点」读数只看**段表文本**，故只能看见**两半都成了文字段**
  的类①切点；类②（半词落进 `cls<=0` 保留区、那半被记成 `{vN}` 或干脆短到过不了 4 字母
  词面门槛）在段表里**不留痕**，只有本模式能从**原文坐标**上直接看见 —— 它按生产同一口径
  （`cls_single` = 字符 bbox 左下角一像素）算 `cls`，相邻两字母同基线、水平相接而无空白
  却 `cls` 不同即为切点，与侧车怎么记那两半完全无关。实测产物里的可见残留
  （Zhang p9 `seco`→`最佳与|seco每个类别…`、Padmaprabhan p5 页眉 `拓扑gy Optimization…`）
  正是这一类。

【`--compare` 是一次**已收口**的实验】用对照采样治词内切点（取 bbox 中心 / 五点多数）
  —— 实测**证伪**：既消不掉系统性偏移（Zhang p9 的 `table_caption` 框左边缘 x=67、文字
  左边界 x≈49，采样点怎么取都在框外），又会在框**另一侧**制造新切点（两篇各多 1 处）。
  故 `converter.py` 的采样口径**未改**（改它属 PROTOCOL 守则 #1，缓存键变 → 全量重译）。
  保留此模式只为「以后有人再想动采样」时有一把现成的尺子。

环境变量:
  PDF2ZH_LAYOUT_MODEL  版面模型 onnx 路径（缺省取 ~/.cache/babeldoc/models/ 下那份；
                       再找不到则由 OnnxModel.load_available() 自寻）
"""
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
from pdfminer.layout import LTPage, LTChar
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfinterp import PDFResourceManager

import pymupdf  # noqa: E402

from pdf2zh.converter import PDFConverterEx  # noqa: E402
from pdf2zh.pdfinterp import PDFPageInterpreterEx  # noqa: E402

MODEL_FILE = "doclayout_yolo_docstructbench_imgsz1024.onnx"

VCLS = ["abandon", "figure", "table", "isolate_formula", "formula_caption"]
GRAPHIC_CLS = ["figure", "table"]


def default_model():
    """版面模型路径：环境变量 → 本机 babeldoc 缓存（不存在则由 OnnxModel 自寻）。"""
    env = os.environ.get("PDF2ZH_LAYOUT_MODEL")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".cache", "babeldoc", "models", MODEL_FILE)


def load_model(path):
    from pdf2zh.doclayout import OnnxModel
    if os.path.isfile(path):
        return OnnxModel(path)
    return OnnxModel.load_available()


def build_box_map(page_layout, h, w):
    """完全复刻 high_level.py L134-161 的类别图构建（改上游要同步核这里）。"""
    box = np.ones((h, w))
    for i, d in enumerate(page_layout.boxes):
        if page_layout.names[int(d.cls)] not in VCLS:
            x0, y0, x1, y1 = d.xyxy.squeeze()
            x0, y0, x1, y1 = (
                np.clip(int(x0 - 1), 0, w - 1),
                np.clip(int(h - y1 - 1), 0, h - 1),
                np.clip(int(x1 + 1), 0, w - 1),
                np.clip(int(h - y0 + 1), 0, h - 1),
            )
            box[y0:y1, x0:x1] = i + 2
    for i, d in enumerate(page_layout.boxes):
        if page_layout.names[int(d.cls)] in VCLS:
            x0, y0, x1, y1 = d.xyxy.squeeze()
            x0, y0, x1, y1 = (
                np.clip(int(x0 - 1), 0, w - 1),
                np.clip(int(h - y1 - 1), 0, h - 1),
                np.clip(int(x1 + 1), 0, w - 1),
                np.clip(int(h - y0 + 1), 0, h - 1),
            )
            box[y0:y1, x0:x1] = -1 if page_layout.names[int(d.cls)] in GRAPHIC_CLS else 0
    return box


# ---------------------------------------------------------------- 采样方案对比
# 生产口径 = 单点（字符 bbox 左下角）。另两案是对照，用于判断「改采样能不能消掉词内
# 切点」—— 完全不碰 converter.py。结论：不能（见文件头 --compare 段）。
LETTER = re.compile(r"^[A-Za-z]$")


def _cls_at(box, x, y, h, w):
    return int(box[int(np.clip(int(y), 0, h - 1)), int(np.clip(int(x), 0, w - 1))])


def cls_single(box, ch, h, w):
    """生产口径: cls = layout[int(y0), int(x0)]  (converter.py L415-416)"""
    return _cls_at(box, ch.x0, ch.y0, h, w)


def cls_center(box, ch, h, w):
    return _cls_at(box, (ch.x0 + ch.x1) / 2.0, (ch.y0 + ch.y1) / 2.0, h, w)


def cls_multi(box, ch, h, w):
    """四点角 + 中心，取多数类。"""
    from collections import Counter
    pts = [(ch.x0, ch.y0), (ch.x1, ch.y0), (ch.x0, ch.y1), (ch.x1, ch.y1),
           ((ch.x0 + ch.x1) / 2.0, (ch.y0 + ch.y1) / 2.0)]
    c = Counter(_cls_at(box, x, y, h, w) for x, y in pts)
    return c.most_common(1)[0][0]


def midword_cuts(chars, cls_fn, box, h, w):
    """词内切点: 相邻两字符**都是字母**、**同基线**、**水平相接**（无空白），但 cls 不同。
    这正是生产里 `cls != xt_cls` 触发断段、且切点落在词内部的形态。"""
    out = []
    prev = None
    for ch in chars:
        if prev is not None:
            a, b = prev.get_text(), ch.get_text()
            if (LETTER.match(a) and LETTER.match(b)
                    and abs(prev.y0 - ch.y0) < 2.0
                    and -1.0 <= (ch.x0 - prev.x1) <= 4.0):
                ca, cb = cls_fn(box, prev, h, w), cls_fn(box, ch, h, w)
                if ca != cb:
                    out.append((prev, ch, ca, cb))
        prev = ch
    return out


def page_boxes(page, model):
    """pymupdf 页 -> (page_layout, box, h, w)。与 `_page_box` 同一口径，整篇扫描复用。"""
    pix = page.get_pixmap()
    h, w = pix.height, pix.width
    image = np.frombuffer(pix.samples, np.uint8).reshape(h, w, 3)[:, :, ::-1]
    page_layout = model.predict(image, imgsz=int(h / 32) * 32)[0]
    return page_layout, build_box_map(page_layout, h, w), h, w


def _page_box(pdf, pageno, model):
    """原文 PDF 页 -> (pymupdf doc, page_layout, box, h, w)。"""
    doc = pymupdf.open(pdf)
    page = doc[pageno - 1]
    page_layout, box, h, w = page_boxes(page, model)
    return doc, page_layout, box, h, w


def blame_edge(page_layout, x_lo, x_hi):
    """切点两侧 x 区间 -> 落在其中的检测框边缘（`名#序号 左/右边 x=… → cls=…`）。

    只为了让读数能直接指认「是哪只框把词切开的」；找不到边缘返回 None（不是缺陷）。
    图坐标为 y 向下、x 与 pdf 点同刻度（get_pixmap 默认 72dpi），故 x 可直接比对。
    """
    best = None
    for i, d in enumerate(page_layout.boxes):
        name = page_layout.names[int(d.cls)]
        x0, y0, x1, y1 = [float(v) for v in d.xyxy.squeeze()]
        val = -1 if name in GRAPHIC_CLS else (0 if name in VCLS else i + 2)
        for side, ex in (("左", x0), ("右", x1)):
            if x_lo - 1.5 <= ex <= x_hi + 1.5:
                dist = min(abs(ex - x_lo), abs(ex - x_hi))
                if best is None or dist < best[0]:
                    best = (dist, "%s#%d %s边 x=%.0f → cls=%d" % (name, i, side, ex, val))
    return best[1] if best else None


def parse_pages_arg(spec, total):
    """`'3,5-9'` -> `[3,5,6,7,8,9]`（去重保序、越界丢弃）；空/无有效项 -> 全页。"""
    if not spec:
        return list(range(1, total + 1))
    out = []
    for part in str(spec).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        a, sep, b = part.partition("-")
        try:
            rng = range(int(a), int(b) + 1) if sep else [int(part)]
        except ValueError:
            continue
        for n in rng:
            if 1 <= n <= total and n not in out:
                out.append(n)
    return out or list(range(1, total + 1))


def scan_cuts(pdf, pages_spec="", model=None):
    """整篇扫描「版面切点」（生产采样口径 `cls_single`）-> 逐页读数列表。

    切点判据 = `midword_cuts`（相邻两**字母**、同基线、水平相接无空白、`cls` 不同）——
    与侧车无关，故类②（半词进保留区 / 短残片）也在这里现形。
    `keep_side` 标出哪一半落在 `cls<=0` 保留区：那半会按公式留原文 → 产物里就是
    「中英夹花」（`拓扑gy`）。两半都 >0 时该字段为 None（两半都会送翻）。
    """
    model = model or load_model(default_model())
    doc = pymupdf.open(pdf)
    chars_by_page = parse_chars(pdf)
    sel = parse_pages_arg(pages_spec, doc.page_count)
    out = []
    for pageno in sel:
        page_layout, box, h, w = page_boxes(doc[pageno - 1], model)
        chars = chars_by_page.get(pageno - 1, [])
        cuts = []
        for a, b, ca, cb in midword_cuts(chars, cls_single, box, h, w):
            cuts.append({
                "page": pageno,
                "left": a.get_text(), "right": b.get_text(),
                "x_left": round(a.x0, 1), "x_right": round(b.x0, 1),
                "y0": round(a.y0, 1),
                "cls_left": ca, "cls_right": cb,
                "keep_side": "左" if ca <= 0 else ("右" if cb <= 0 else None),
                "edge": blame_edge(page_layout, a.x1, b.x0),
            })
        out.append({"page": pageno, "chars": len(chars), "cuts": cuts})
    doc.close()
    return out


def cuts_finding(pages):
    """逐页读数 -> (总切点数, 半词进保留区数, 样本字符串)。只读数，不判定。"""
    cuts = [c for p in pages for c in p["cuts"]]
    keep = [c for c in cuts if c["keep_side"]]
    sample = "；".join("p%d %s|%s" % (c["page"], c["left"], c["right"]) for c in cuts[:5])
    return len(cuts), len(keep), sample


def print_cuts(pages):
    n_tot, n_keep, _ = cuts_finding(pages)
    print("=" * 100)
    print("版面切点扫描：%d 页 / %d 处词内切点（其中 %d 处有半词落进保留区 → 中英夹花）"
          % (len(pages), n_tot, n_keep))
    print("口径 = 生产采样（字符 bbox 左下角一像素，cls_single）；与段表无关，覆盖类②")
    print("=" * 100)
    for p in pages:
        if not p["cuts"]:
            continue
        print("\n[p%d] 字符 %d 个 → 切点 %d 处" % (p["page"], p["chars"], len(p["cuts"])))
        for c in p["cuts"]:
            print("    %r|%r  x %.1f|%.1f y0=%.1f  cls %d→%d %s  %s"
                  % (c["left"], c["right"], c["x_left"], c["x_right"], c["y0"],
                     c["cls_left"], c["cls_right"],
                     ("切点在保留区(%s)" % c["keep_side"]) if c["keep_side"] else "",
                     c["edge"] or "(无框边缘落在切点处)"))


def compare(pdf, pageno, model):
    """对照 单点 / 中心点 / 五点多数 三种采样下的词内切点数量与位置。"""
    doc, page_layout, box, h, w = _page_box(pdf, pageno, model)
    chars = parse_chars(pdf).get(pageno - 1, [])
    print("=" * 100)
    print("%s  p%d   字符 %d 个   检测框 %d 个"
          % (os.path.basename(pdf)[:56], pageno, len(chars), len(page_layout.boxes)))
    print("=" * 100)
    variants = [("单点(生产)", cls_single), ("bbox中心", cls_center), ("五点多数", cls_multi)]
    res = {}
    for name, fn in variants:
        cuts = midword_cuts(chars, fn, box, h, w)
        res[name] = cuts
        print("\n[%s] 词内切点 %d 处" % (name, len(cuts)))
        for a, b, ca, cb in cuts[:12]:
            print("    %r→%r  x %.1f|%.1f  cls %d→%d  (y0=%.1f)"
                  % (a.get_text(), b.get_text(), a.x0, b.x0, ca, cb, a.y0))
    doc.close()
    return res


class Collector(PDFConverterEx):
    """只收集顶层 LTPage 的 LTChar（与生产 receive_layout 的遍历口径一致）。"""

    def __init__(self, rsrcmgr):
        PDFConverterEx.__init__(self, rsrcmgr)
        self.pages = {}

    def receive_layout(self, ltpage):
        if not isinstance(ltpage, LTPage):
            return ""
        chars = [c for c in ltpage if isinstance(c, LTChar)]
        self.pages[ltpage.pageid] = chars
        return ""


def parse_chars(pdf_path):
    with open(pdf_path, "rb") as f:
        parser = PDFParser(f)
        doc = PDFDocument(parser)
        rsrcmgr = PDFResourceManager()
        dev = Collector(rsrcmgr)
        interp = PDFPageInterpreterEx(rsrcmgr, dev, {})
        for pageno, page in enumerate(PDFPage.create_pages(doc)):
            page.pageno = pageno
            page.page_xref = pageno          # pdfinterp 用它当 obj_patch 的键
            interp.process_page(page)
        dev.close()
    return dev.pages


def _opt(flag, default=""):
    """`--flag value` 取值（本工具刻意不引 argparse，保持最小依赖）。"""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _cuts_main(argv):
    """`--cuts` 模式：整篇扫描版面切点。<pdf> 为唯一位置参数，其余都是开关。"""
    positional, skip = [], False
    for a in argv[1:]:
        if skip:
            skip = False
            continue
        if a == "--pages":
            skip = True
            continue
        if a.startswith("--"):
            continue
        positional.append(a)
    if not positional:
        print("❌ --cuts 需要给 <pdf>", file=sys.stderr)
        return 2
    pdf = positional[0]
    if not os.path.isfile(pdf):
        print("❌ 找不到 PDF: %s" % pdf, file=sys.stderr)
        return 2
    try:
        pages = scan_cuts(pdf, _opt("--pages"))
    except Exception as exc:                    # 模型缺失 / 页读崩都不该吐半截 JSON
        print("❌ 版面切点扫描失败: %s" % exc, file=sys.stderr)
        return 2
    if "--json" in argv:
        print(json.dumps(pages, ensure_ascii=False))
    else:
        print_cuts(pages)
    return 0


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--compare" and len(argv) > 2:
        compare(argv[1], int(argv[2]), load_model(default_model()))
        return 0
    if argv and argv[0] == "--cuts":
        return _cuts_main(argv)
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    pdf = sys.argv[1]
    pageno = int(sys.argv[2])
    kw = sys.argv[3] if len(sys.argv) > 3 else None

    model = load_model(default_model())
    doc, page_layout, box, h, w = _page_box(pdf, pageno, model)

    print("=" * 100)
    print("PDF : %s" % os.path.basename(pdf))
    print("页  : p%d   位图 %dx%d   检测框 %d 个" % (pageno, w, h, len(page_layout.boxes)))
    print("=" * 100)
    print("\n-- 检测框（图坐标 y 向下 → 类别值：>=2 文本框 / 1 默认背景 / 0 保留区 / -1 图·表区）--")
    for i, d in enumerate(page_layout.boxes):
        name = page_layout.names[int(d.cls)]
        x0, y0, x1, y1 = [float(v) for v in d.xyxy.squeeze()]
        if name in GRAPHIC_CLS:
            val = -1
        elif name in VCLS:
            val = 0
        else:
            val = i + 2
        print("  [%2d] %-18s -> cls=%-3d  img xyxy=(%.0f,%.0f,%.0f,%.0f)"
              % (i, name, val, x0, y0, x1, y1))

    chars = parse_chars(pdf).get(pageno - 1, [])
    print("\n-- 字符流 cls 分组（cls 跳变处 = 生产里的断段处）--")
    runs = []
    for ch in chars:
        cy = int(np.clip(int(ch.y0), 0, h - 1))
        cx = int(np.clip(int(ch.x0), 0, w - 1))
        cls = int(box[cy, cx])
        if ch.get_text() == "•":
            cls = 0
        if runs and runs[-1][0] == cls:
            runs[-1][1].append(ch.get_text())
        else:
            runs.append([cls, [ch.get_text()]])

    for cls, txt in runs:
        s = "".join(txt)
        if not s.strip():
            continue
        flag = "  <== 保留区(不译/按公式)" if cls <= 0 else ""
        print("  cls=%-3d %r%s" % (cls, s[:90], flag))

    if kw:
        print("\n-- 关键字 %r 逐字符 cls --" % kw)
        buf = "".join(c.get_text() for c in chars)
        pos = buf.find(kw)
        if pos < 0:
            print("  (本页未找到)")
        else:
            for ch in chars[max(0, pos - 6):pos + len(kw) + 6]:
                cy = int(np.clip(int(ch.y0), 0, h - 1))
                cx = int(np.clip(int(ch.x0), 0, w - 1))
                cls = int(box[cy, cx])
                print("    %r  x0=%-7.1f y0=%-7.1f cls=%-3d%s"
                      % (ch.get_text(), ch.x0, ch.y0, cls,
                         "  <== 保留区" if cls <= 0 else ""))
    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
