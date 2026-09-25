# -*- coding: utf-8 -*-
"""v29 扫描件清底单元测试: 判据 / 行框合并 / 清底范围 / 源码守卫

被测: pdf2zh.converter.TranslateConverter 的
  · _raster_covers_page   —— 「本页是否有覆盖式位图(扫描件)」判据
  · _push_ink             —— 逐字符并成「紧致墨迹行框」
  · _whiten_ops           —— 按行框生成白色填充矩形

为什么这么测(血的教训, 2026-09-24): 最初用**段落包围盒**当清底范围, 而 pstk 的
包围盒是逐字符取并集来的 —— 一张图里散布的几个文字层(OCR)字符会把盒子撑到覆盖
整张图, 清底时就把图一起抹白了(实测 Johnson p2 右侧图区 8600px 墨迹一次抹光,
墨迹密度 12.4% -> 0.8%)。故本套件把「不吞图」写死在三条口径上:
  ① 行框合并不得跨越换行与大间隙;
  ② 图/表区(cls<0)的字符根本不进 ink;
  ③ 该段没渲出东西时不清底(否则涂出空白)。

[v30] 补齐同一语义的另一半: 图/表区(cls<=-1)**整段不重绘**, 且整组落在图/表区的公式组
也不重绘(纯公式段落会把紧随的 -1 字符并进来, 那种组的宿主段落不是图区段落)。扫描页的可见
内容本来就在覆盖式位图里, 段内文字层只是隐形 OCR 复本; 原先它被 cls<=0 判成"公式"原样重绘
-> 按重排落位偏移(实测 x-192.3/y+114.5)、Tr 由 3 变 0 变成可见黑字, 即重影。
本套件为此把守: 判据单一来源、段落/组类别与各自栈平行、**分段口径不动**(守则 #1: 动 xt_cls
哨兵会改 sstk 分段 -> 前瞻上下文变 -> 缓存键变 -> 触发重译, 实测正文译文被改写)。

运行: venv python test_whiten_scan.py, 退出码 0=全过。不触网、不读真实 PDF。
"""
import os
import re
import sys

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

from pdf2zh.converter import TranslateConverter
from pdfminer.layout import LTFigure, LTImage, LTPage


class FakeChar:
    """pdfminer LTChar 的最小替身(只用到这四个几何量与 size)"""
    def __init__(self, x0, y0, x1, y1, size=10.0):
        self.x0, self.y0, self.x1, self.y1, self.size = x0, y0, x1, y1, size


class FakePage:
    """_raster_covers_page 只用 bbox + 可迭代, 不需要真是 LTPage"""
    def __init__(self, w, h, items=()):
        self.bbox = (0.0, 0.0, w, h)
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)


def conv():
    """绕过 __init__(要 rsrcmgr + translator), 只取需要的两个常量。"""
    c = object.__new__(TranslateConverter)
    return c


class _FakeStream:
    def get_any(self, keys, default=None):
        return 1 if default is None else default


def make_image(x0, y0, x1, y1):
    im = LTImage("Im1", _FakeStream(), (x0, y0, x1, y1))
    return im


def src_of(rel):
    return open(os.path.join(_VENV_SITE, "pdf2zh", rel), encoding="utf-8").read()


def main():
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS {name}")
        else:
            failed += 1
            print(f"  FAIL {name} {detail}")

    c = conv()

    # ---------- ① 判据: 覆盖式位图 ----------
    big = FakePage(612, 792, [make_image(0, 0, 612, 792)])           # 整页位图 = 扫描件
    small = FakePage(612, 792, [make_image(100, 100, 200, 200)])     # 小块插图
    half = FakePage(612, 792, [make_image(0, 0, 612, 400)])          # 约半页
    fig = LTFigure("F", (0, 0, 612, 792), (1, 0, 0, 1, 0, 0))
    fig.add(make_image(0, 0, 612, 792))                              # 位图埋在 LTFigure 里
    tied = FakePage(612, 792, [fig])
    none = FakePage(612, 792, [])
    check("① 整页位图判为扫描", c._raster_covers_page(big) is True)
    check("① 小块插图不判为扫描", c._raster_covers_page(small) is False)
    check("① 半页位图(>=0.5)判为扫描", c._raster_covers_page(half) is True)
    check("① 位图包在 LTFigure 内也认", c._raster_covers_page(tied) is True)
    check("① 无位图不判为扫描", c._raster_covers_page(none) is False)
    check("① 退化页(面积 0)不判为扫描",
          c._raster_covers_page(FakePage(0, 0, [make_image(0, 0, 1, 1)])) is False)

    # 旁路: PDF2ZH_WHITEN_SCAN=0 -> 一律 False(出问题可秒关)
    _old = os.environ.get("PDF2ZH_WHITEN_SCAN")
    os.environ["PDF2ZH_WHITEN_SCAN"] = "0"
    check("① 环境变量 0 关闭清底", c._raster_covers_page(big) is False)
    os.environ["PDF2ZH_WHITEN_SCAN"] = "1"
    check("① 环境变量 1 恢复清底", c._raster_covers_page(big) is True)
    if _old is None:
        os.environ.pop("PDF2ZH_WHITEN_SCAN", None)
    else:
        os.environ["PDF2ZH_WHITEN_SCAN"] = _old

    # ---------- ② 行框合并 ----------
    ink = [[]]
    # 同一行连续字符 -> 并成一框
    r = None
    for i in range(5):
        r = c._push_ink(ink, r, FakeChar(100 + i * 6, 700, 106 + i * 6, 710))
    check("② 同行连续字符并成一框", len(ink[0]) == 1, ink[0])
    check("② 并框范围=首末字符边界",
          ink[0][0] == [100, 700, 130, 710], ink[0][0])

    # 换行 -> 断框(纵向不重叠)
    r = c._push_ink(ink, r, FakeChar(100, 680, 130, 690))
    check("② 换行断框", len(ink[0]) == 2, ink[0])

    # 同一行但大间隙(跨列/跨图留白) -> 断框
    ink2 = [[]]
    r = None
    r = c._push_ink(ink2, r, FakeChar(50, 700, 60, 710))
    r = c._push_ink(ink2, r, FakeChar(500, 700, 510, 710))   # 间隙 440pt >> 2*em
    check("② 大间隙断框(不跨留白)", len(ink2[0]) == 2, ink2[0])

    # 行距恰好贴边: 纵向重叠刚好不足 40% -> 断框
    ink3 = [[]]
    r = None
    r = c._push_ink(ink3, r, FakeChar(100, 700, 130, 710))
    r = c._push_ink(ink3, r, FakeChar(100, 704, 130, 714))   # 重叠 6/10 = 60% -> 并
    check("② 纵向重叠 60% 并框", len(ink3[0]) == 1, ink3[0])
    r = c._push_ink(ink3, r, FakeChar(30, 720, 60, 730))     # 纵向完全不重叠(下一行) -> 断
    check("② 纵向不重叠断框", len(ink3[0]) == 2, ink3[0])

    # run=None(段落边界/图区后)首字符必开新框
    ink4 = [[]]
    c._push_ink(ink4, None, FakeChar(100, 700, 110, 710))
    c._push_ink(ink4, None, FakeChar(111, 700, 120, 710))
    check("② run=None 强制开新框", len(ink4[0]) == 2, ink4[0])

    # ---------- ③ 清底范围 ----------
    ink5 = [[[100.0, 700.0, 200.0, 710.0], [100.0, 680.0, 160.0, 690.0]]]
    ops = c._whiten_ops(ink5, ["译文"])
    check("③ 生成白色填充", ops.startswith("q 1 1 1 rg ") and ops.endswith("Q "), ops[:40])
    check("③ 每行框一个矩形", ops.count(" re f ") == 2, ops)
    check("③ 外扩 WHITEN_PAD 兜住溢出行框",
          ("%f" % (100.0 - c.WHITEN_PAD)) in ops, ops)

    # 该段没渲出东西 -> 不清底(否则源文被抹而译文没来, 涂出空白)
    check("③ 译文为空不清底", c._whiten_ops(ink5, [""]) == "")
    check("③ 译文空白不清底", c._whiten_ops(ink5, ["   "]) == "")
    check("③ news 短于 ink 时逐段对齐, 不越界", c._whiten_ops(ink5, []) == "")

    # ink 里有空段落(整段都在图/表区) -> 该段无框可清, 不报错
    check("③ 空墨迹段不报错且不产矩形",
          c._whiten_ops([[]], ["译文"]) == "")

    # 退化框(零宽/零高)必须跳过, 否则写出非法矩形
    check("③ 零面积框被跳过",
          c._whiten_ops([[[10.0, 10.0, 10.0, 20.0]]], ["t"]) == "")

    # ---------- ④ 源码守卫(接线正确性) ----------
    cv = src_of("converter.py")
    check("④ 行框记录受 cls>=0 门禁(图/表区不入框)",
          re.search(r"if ink and cls >= 0:.*?_push_ink", cv, re.S) is not None)
    check("④ 清底只在本页有覆盖式位图时发生",
          re.search(r"_scan_page: bool = isinstance\(ltpage, LTPage\) "
                    r"and self\._raster_covers_page\(ltpage\)", cv) is not None
          and "if _scan_page:" in cv)
    check("④ 清底用 ink 而非段落包围盒 pstk",
          "_whiten_ops(ink, news)" in cv and "_whiten_ops(pstk" not in cv)
    check("④ 保留区判定含 -1(cls<=0)",
          "cls <= 0" in cv)
    check("④ 哨兵 -2 不与图/表区 -1 相撞",
          "xt_cls: int = -2" in cv)

    # [v30] 扫描页图/表区不重绘: 段落级 + 组级两道
    check("④ 扫描页图/表区段落不重绘(_scan_page + pcls<=GRAPHIC_CLS -> continue)",
          re.search(r"if _scan_page and pcls\[id\] <= self\.GRAPHIC_CLS:.*?continue",
                    cv, re.S) is not None)
    check("④ 段落类别与段落栈平行登记(同生同长)",
          "pcls: list[int] = []" in cv and "pcls.append(cls)" in cv)
    check("④ 图/表区公式组: 扫描页不重绘 / 原生页绝对锚定([v30]组级门禁 + [v33]绝对落位)",
          re.search(r"_gabs = varcls\[vid\] <= self\.GRAPHIC_CLS", cv) is not None
          and re.search(r"if _scan_page and _gabs:\s*\n\s*_vch, _vl = \(\), \(\)",
                        cv) is not None
          # [v33] 原生页: 图区组按自身 x0/y0 绝对锚定(不参与段落光标累进与行号位移)
          and "vch.x0 if _gabs else x + vch.x0 - var[vid][0].x0" in cv
          and "(vch.y0 - y) if _gabs else fix + vch.y0 - var[vid][0].y0" in cv
          and "l.pts[0][0] if _gabs else l.pts[0][0] + x - var[vid][0].x0" in cv
          and '"lidx": 0 if _gabs else lidx,' in cv
          and "for vch in _vch:" in cv and "for l in _vl:" in cv)
    check("④ 公式组类别与组栈平行登记(每个 var.append 都有 varcls.append 配对)",
          "varcls: list[int] = []" in cv
          and cv.count("varcls.append(") == cv.count("var.append(")
          and cv.count("varcls.append(") == 4)  # [v31] 换行闭组点为第 4 对配对
    check("④ 分段口径不动: xt_cls 哨兵仍写 -1(改它会改 sstk 分段 -> 前瞻变 -> 缓存键变 -> 重译)",
          re.search(r"xt_cls = -1\b", cv) is not None and "xt_cls = -2" not in cv)
    check("④ 判据单一来源: _raster_covers_page 只在一处调用(同一把尺子)",
          cv.count("self._raster_covers_page(") == 1)

    hl = src_of("high_level.py")
    check("④ high_level 把图/表区记 -1",
          'graphic_cls = ["figure", "table"]' in hl and "graphic_cls else 0" in hl)
    check("④ 其余保留区仍记 0(页眉页脚/公式会被重绘, 需清底)",
          'vcls = ["abandon", "figure", "table", "isolate_formula", "formula_caption"]' in hl)

    print(f"\n清底单元: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
