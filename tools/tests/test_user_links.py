# -*- coding: utf-8 -*-
"""user_links 用户自定义概念链接单元测试 (v28.73, 2026-09-23)

被锁死的契约 —— 这个工具的权力比其余链接工序都大: 它会**删掉**成品里原有的链接,
所以每一条门禁都必须真的拦得住, 而不是"看起来拦住了":

  * **用户 > 原有**: 新锚的矩形压住原有链接(引文锚/URI/NAMED)时删原条并记账 ——
    但"压住"必须真的按矩形相交判, 不是按页判(按页判会把整页原有链接清光)。
  * **不挑"最像的那一处"**: 一个锚命中多处而规格又没给 occurrence 时**必须 FAIL**。
    猜一次的下场是链接静默挂错位置, 而读者点开才知道 —— 比不装更坏。
  * **门禁全部判在落盘之前**: 六类 FAIL(锚空/URL 非 http/global 带 page/tasks 缺 page/
    0 命中/occurrence 越界)任何一条不许通过, 且**文件一个字节都不许动**(锁 md5)。
  * **--check 是真预检**: 与正式跑同一套判据, 但不落盘 —— 面板"提交前提示"就靠它,
    若它偷偷落了盘, 面板的"确认"形同虚设。
  * **撞锚以本篇为准**: tasks 与 global 同一个锚时跳过 global 并**说出来**;
    `--task` 缺席而规格里确实有 tasks 段时也要说出来 —— 静默失效是这套工具最怕的形态。
  * **两条新锚抢同一矩形 -> 两条都 FAIL**: 不替用户在两条自相矛盾的意图里挑一条。
  * **dual 页号是 mono 坐标**: 规格里的 page 一律按 mono 数, dual 落到译文侧(2p-1)。
    不换算就会去绑英文侧, 而英文侧恰好也有同样的拉丁词时**会装错却毫无报错**。
  * **verify_links 第四判据**: 给 --spec 且自定义链接没就位 -> 退 1。
    只验"锚在不在"会漏掉"忘了跑 user_links.py", 那是这个功能最常见的失败方式。
  * **带空格的锚要按原样搜**: 面板给的是用户点的**整行**文字, 行里带空格是常态
    (『GelSight Mini』)。锚若一律去空白再搜, 页面上没有那个串 -> 用户点自己那行
    反被判"全篇找不到"。命中数还要按**出现次数**(并框后)数, 不是按词数。
  * **看得见才算做完**: user_links 只装热区, 变蓝是 style_links 的活。中文锚
    (basefont 与 span['font'] 两套写法)与非嵌入的 Base-14 字体都必须能重绘出来;
    旋转/竖排行(页边戳)必须**跳过** —— 照它自己的 origin 平着重绘会把整串字横铺过页。

判定手法(全部合成, 不依赖真实论文):
  3 页 mono(= 1 基 p1..p3):
    p1 概念锚 x2(第 1 次被一条原有 GOTO 压着) | p2 引文号 [5] x2 | p3 空白
  6 页 dual: 偶数页=原版侧(同文本), 奇数页=译文侧 —— 锁住"落译文侧"。
  ⑪ 1 页: 同一个带空格的锚出现两次 —— 锁住"原样搜"与按出现次数计数。
  ⑫ 1 页: 一条横向锚(非嵌入 Helvetica) + 一条旋转行(模拟页边戳)各挂一条链接 ——
     锁住 style_links 既**重绘得出**前者, 又**不去动**后者。
  ⑬ 1 页: 同一条**折行**长锚出现两次 —— 锁住 [v28.91] 字符级兜底: 引擎的 search_for
     **不跨行**(前提先验: 它在这页上真的返回空), 而工具仍要数成 **2 处**(两行算 1 次
     出现, 不是 4 处), 且「第 1/2 处」按阅读序落到上下两条; 页面上没有的锚仍是 0。
  ⑭ 2 页: 概念锚在第 1、2 页各一次 —— 锁住 --count-hits 的**三口径**读数
     (hits=当前口径 / hits_page / hits_doc): 面板「本页就这 1 处 · 全篇还有」靠它。
  ⑮ 1 页: 页上写**全角**标点(`模型：（1）遗留` / `[13]。`), 锚是**ASCII** 版 —— 锁住
     [v28.92] 标点折叠: 先验 search_for 为空(证明折叠真在干活), 折后要数得对(两次出现
     = 2 处), 而**内容**不同(数字改了)仍旧 0(折叠只放行"宽度差异", 不放行"内容差异"),
     且包围盒仍只盖那一行(折叠会改长度, 下标映射错了框就会盖歪)。
  ⑯ 1 页: 两个**相隔 600pt 的块**, 流里拼得出 `束模` —— 锁住 [v34] 版面连续性守卫:
     先验流里真有这个串(所以不拦就会命中), 而版面上隔着大半页就不许算一次出现。
     ⑬ 是它的**正对照**: 真折行(相邻两行)必须仍然命中, 守卫不能把正常换行也砍掉。
  ⑰ [v35] 定位: `--count-hits` 的 hits_pos(每一处的 页+矩形, 阅读序) + `--render-page`
     的荧光黄块 —— 锁住"◀▶ 指的那一处 **就是** occurrence 装进成品的那一处"(两处各算一套
     会静默装错), dual 页码回写成 mono 页码(否则报一个用户翻不到的页号), 渲染只读
     (成品 md5 不变)且黄块只涂在给定矩形的位置上。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
USER_LINKS = os.path.join(TOOLS, "user_links.py")
VERIFY_LINKS = os.path.join(TOOLS, "verify_links.py")
STYLE_LINKS = os.path.join(TOOLS, "style_links.py")

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pymupdf  # noqa: E402

PAGE = (612, 792)
CONCEPT = "基因漂变"                      # 概念锚(中文, 与真实用法一致)
CITE = "[5]"                             # 引文号锚
URL_G = "https://zh.wikipedia.org/wiki/遗传漂变"
URL_OWN = "https://example.org/own"
Y_C1, Y_C2 = 100.0, 200.0                # p1 上概念锚的两次出现(基线 y)
Y_X1, Y_X2 = 300.0, 400.0                # p2 上引文号锚的两次出现

SPACED = "GelSight Mini"                 # 带空格的锚(面板点选的**整行**文字常带空格)
Y_SPACED1, Y_SPACED2 = 150.0, 250.0      # 它在本页的两次出现(基线 y, 按 y 升序数)
# 折行锚([v28.91] 字符级兜底的靶子): 30 字, 塞进 228pt 宽的框里必然折成两行
WRAPPED = "实现可持续性感知的硅系统设计探索与架构碳工具评估框架"
Y_W1, Y_W2 = 100.0, 300.0                # 两条折行锚的**框顶**(textbox 左上游标)
SWITCH_Y = 250.0                         # 判"上/下"那条的分界线(取两条中间的空档)
# 标点宽度用例([v28.92]): 页上全角, 锚是 ASCII 版的同一个串
FULLW = "模型：（1）遗留"                  # 页上写全角
FULLW_ASCII = "模型:(1)遗留"              # 侧车/用户手里的 ASCII 版(去空白后)
CITED = "见文献[13]。"                    # 页上全角句号
CITED_ASCII = "见文献[13]."               # ASCII 句点
NEG = "模型:(2)遗留"                      # 内容不同(数字) —— 折了标点也不该命中
Y_FW1, Y_FW2, Y_FW3 = 150.0, 250.0, 350.0
# [v34] 折叠**不该**放行的三类: 内容型兼容字(①按 <circle> 分解成 1) + 两个不是"宽窄写法"
# 的标点(顿号、间隔号)。每一对是 (页上写的, 锚), 期望 0 命中 —— 折了就是把"内容不同"
# 当成了"宽度不同", 于是链接会静默挂到别处。
FOLD_DENY = [
    ("①定义", "1定义"),                 # <circle> 兼容分解 -> 折了 ① 就等于 1
    ("模型、遗留", "模型,遗留"),           # 顿号(并列) != 逗号(停顿)
    ("CuSO4·5H2O", "CuSO4.5H2O"),      # 间隔号(人名/分子式分隔) != 句点
]
Y_FW4, Y_FW5, Y_FW6 = 400.0, 450.0, 500.0
# 变蓝用例的两处锚: 横向锚(非嵌入 Helvetica) / 旋转行(模拟页边戳)
RECT_H = pymupdf.Rect(70, 384, 140, 406)
RECT_R = pymupdf.Rect(285, 542, 306, 602)


# ------------------------------------------------------------------ 合成件
def make_mono(path):
    """3 页 mono 成品: p1 概念锚 x2(第 1 处压着一条原有 GOTO), p2 引文号 x2, p3 空白。"""
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page(width=PAGE[0], height=PAGE[1])
    p = doc[0]
    p.insert_text((72, Y_C2), CONCEPT, fontsize=12, fontname="china-s")
    p.insert_text((72, Y_C1), CONCEPT, fontsize=12, fontname="china-s")
    # 原有引文锚: 矩形正好压住第 1 次出现的概念锚(用它验"覆盖原有")
    p.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(70, 88, 122, 104),
                   "page": 0, "to": pymupdf.Point(36, 40)})
    p = doc[1]
    p.insert_text((300, Y_X2), CITE, fontsize=10, fontname="helv")
    p.insert_text((300, Y_X1), CITE, fontsize=10, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def make_dual(path):
    """6 页 dual: 每对 = [原版侧, 译文侧]; 概念锚两侧都有 -> 只有落到译文侧才算对。"""
    doc = pymupdf.open()
    for k in range(3):
        po = doc.new_page(width=PAGE[0], height=PAGE[1])          # 原版侧(偶)
        po.insert_text((72, Y_C1), "original page %d" % (k + 1), fontsize=12)
        pt = doc.new_page(width=PAGE[0], height=PAGE[1])          # 译文侧(奇)
        pt.insert_text((72, Y_C1), "译文第 %d 页" % (k + 1), fontsize=12,
                       fontname="china-s")
    # 概念锚: **原版侧 p1** 与 **译文侧 p1** 各一处, y 不同 -> 从落点 y 就能看出装到了哪一侧
    doc[0].insert_text((72, Y_X1), CONCEPT, fontsize=12, fontname="china-s")
    doc[1].insert_text((72, Y_X2), CONCEPT, fontsize=12, fontname="china-s")
    doc.save(path)
    doc.close()
    return path


def make_spaced(path):
    """1 页: 一个**带空格**的锚出现两次(y 不同), 供 ⑪ 验"原样搜"这一档。

    面板给的是用户点的那一**整行**文字, "GelSight Mini" 这种带空格的行是常态;
    锚若一律去空白再搜, 页面上根本没有 "GelSightMini" 这个串 -> 用户点自己那行
    反被判"全篇找不到"(实测踩过)。
    """
    doc = pymupdf.open()
    p = doc.new_page(width=PAGE[0], height=PAGE[1])
    p.insert_text((72, Y_SPACED2), SPACED, fontsize=12, fontname="helv")
    p.insert_text((72, Y_SPACED1), SPACED, fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def make_blue(path):
    """1 页两处已挂链接的锚, 供 ⑫ 验 style_links 的"变蓝":

      ① 横向锚 —— 用非嵌入的 Base-14 字体(`fontname="helv"` -> Type1 Helvetica,
         extract_font 抽出来是 0 字节)。以前这类一律被跳过, 于是**正文里的拉丁锚
         全是黑字**; 现在靠 PyMuPDF 内置同名字体兜底。
      ② 旋转行 —— 模拟页边戳(实测 arXiv 戳 `dir=(0,-1)`)。照它自己的 origin 平着
         重绘, 等于把这串字**横着铺过整页**。

    链接矩形取"文字外框+余量", 保证锚字的心都在框内(见 style_links.in_link)。
    """
    doc = pymupdf.open()
    p = doc.new_page(width=PAGE[0], height=PAGE[1])
    p.insert_text((72, 400), "ANCHOR1", fontsize=12, fontname="helv")
    p.insert_text((300, 600), "ROTATE1", fontsize=12, fontname="helv", rotate=90)
    p.insert_link({"kind": pymupdf.LINK_URI, "from": RECT_H, "uri": URL_OWN})
    p.insert_link({"kind": pymupdf.LINK_URI, "from": RECT_R, "uri": URL_OWN})
    doc.save(path)
    doc.close()
    return path


def make_wrapped(path):
    """1 页: 同一条**折行**长锚出现两次(上一条框顶 Y_W1, 下一条 Y_W2), 供 ⑬。

    为什么必须用 insert_textbox 而不是 insert_text: insert_text 不折行, 整串排成
    一行 —— 那就落在 search_for 能搜到的那一档上, 兜底那档**根本没被走到**,
    测试会"通过"却什么也没验。
    """
    doc = pymupdf.open()
    p = doc.new_page(width=PAGE[0], height=PAGE[1])
    for y in (Y_W1, Y_W2):
        left = p.insert_textbox(pymupdf.Rect(72, y, 300, y + 90), WRAPPED,
                                fontsize=12, fontname="china-s")
        if left < 0:                     # 没排下 -> 后面的"折行"前提就不成立
            raise AssertionError("折行锚没排进框里(left=%.1f)" % left)
    doc.save(path)
    doc.close()
    return path


def make_fullwidth(path):
    """1 页: 同一条锚在**全角/半角**两侧各写一遍 —— 供 ⑮ 验 [v28.92] 标点折叠。

    为什么非造这一页: 侧车 trans 用 ASCII 标点(`(1)`、`.`), 渲染后的成品页是全角
    (`（1）`、`。`) —— 面板"点整段"取的是**侧车那一侧**, 页面上写的是另一侧, 于是用户
    点自己眼前那行字却被告知"找不到"(实测 Lee 篇 8/117 段全这一类)。
    同一页里并排造**正例**(只差宽窄 -> 该命中)与**反例**(差内容 -> 一个字都不许命中),
    折叠一旦被放宽成模糊匹配, 反例那三条会立刻变红。
    """
    doc = pymupdf.open()
    p = doc.new_page(width=PAGE[0], height=PAGE[1])
    p.insert_text((72, Y_FW1), FULLW, fontsize=12, fontname="china-s")
    p.insert_text((72, Y_FW2), FULLW, fontsize=12, fontname="china-s")
    p.insert_text((72, Y_FW3), CITED, fontsize=12, fontname="china-s")
    for (on_page, _), y in zip(FOLD_DENY, (Y_FW4, Y_FW5, Y_FW6)):
        p.insert_text((72, y), on_page, fontsize=12, fontname="china-s")
    doc.save(path)
    doc.close()
    return path


def make_phantom(path):
    """1 页: 上下隔了 600pt 的两个块, 流里却拼得出 `束模` —— 供 ⑯(版面连续性守卫)。

    第③档是拿 rawdict 的**块序**串出一条流再找子串, 于是"流里相邻"被当成了"版面上挨着"。
    真实语料里这发生在"整页被切成几十块"的图形页上(实测 Lee 篇 2 段, 框纵向跨
    132.7 / 215.6 pt)。这里把它缩成最小可控形态: 两次 insert_text 各成一块, 上块收笔
    `束`、下块起笔 `模` —— 流里是 `文字束模型`,`束模` 连得上, 版面上却隔着大半页。
    """
    doc = pymupdf.open()
    p = doc.new_page(width=PAGE[0], height=PAGE[1])
    p.insert_text((72, 100), "文字束", fontsize=12, fontname="china-s")
    p.insert_text((72, 700), "模型", fontsize=12, fontname="china-s")
    doc.save(path)
    doc.close()
    return path


def make_count(path):
    """2 页: 概念锚在第 1、2 页各一次 —— 供 ⑭ 验三口径(hits / hits_page / hits_doc)。"""
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page(width=PAGE[0], height=PAGE[1])
    doc[0].insert_text((72, Y_C1), CONCEPT, fontsize=12, fontname="china-s")
    doc[1].insert_text((72, Y_C1), CONCEPT, fontsize=12, fontname="china-s")
    doc.save(path)
    doc.close()
    return path


def blue_in(path, pno, rect, limit=4):
    """矩形里有没有"链接蓝"像素 —— style_links 的重绘到底发生了没有。

    判据取自 BLUE=(0,0,0.75) 渲染后的实测值(约 0,0,191): 蓝通道高、红绿都低。
    只要**存在**几粒(limit 放宽到 4, 抗抗锯齿边缘)就算变蓝 —— 这里问的是
    "有没有变蓝", 不是"蓝了多少面积"。
    """
    d = pymupdf.open(path)
    pix = d[pno].get_pixmap(clip=pymupdf.Rect(rect), colorspace=pymupdf.csRGB)
    d.close()
    n = 0
    for y in range(pix.height):
        for x in range(pix.width):
            r, g, b = pix.pixel(x, y)
            if b > 100 and b > r + 60 and b > g + 60:
                n += 1
                if n >= limit:
                    return True
    return False


def mark_pixels(png, x0, y0, x1, y1, step=2):
    """PNG 画素范围内"荧光黄块"的点数 —— --render-page 标的到底画在哪。

    判据 = **黄度**(R-B 与 G-B 都够大), 不是"某一种纯色":
    黄块是 fill=(1,1,0) 配 fill_opacity=0.4 叠上去的, 落在白底与落在黑字上叠出的绝对色
    完全不同(白底 ≈ (255,255,153), 纯黑字上 ≈ (102,102,0)), 拿某个 RGB 精确值去比会
    在"字上没涂到"和"底色没涂到"之间只能认一边。黄度对两者都成立, 对白/黑/灰一律为 0。
    [边界] 底色本来就偏黄/橙时会误判 —— 本用例的合成页是黑白字, 不受影响; 真页面上这张
    图是给人看的, 面板不靠像素判定任何东西, 所以这条边界只关本案。
    直接读 Pixmap 而不是 open(png)(后者会把 DPI 当尺度重排, 坐标对不上):
    PNG 是 pixmap.save 出来的 1:1 位图, 画素坐标 = PDF 点 × zoom。
    """
    pm = pymupdf.Pixmap(png)
    n = 0
    for y in range(int(y0), int(y1) + 1, step):
        for x in range(int(x0), int(x1) + 1, step):
            if x >= pm.width or y >= pm.height:
                continue
            r, g, b = pm.pixel(x, y)
            if r - b >= 60 and g - b >= 60:
                n += 1
    return n


def write_spec(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return path


def run(*args):
    proc = subprocess.run([sys.executable] + [str(a) for a in args],
                          capture_output=True, cwd=TOOLS)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def count_hits(target, anchor, page=None, scope="本篇", dual=False):
    """跑 --count-hits, 回 (rc, obj, out); obj 解析不出来就是 None(调用方据此判 FAIL)。

    为什么单独包一层: 面板的「第几处」读的就是这一问的 JSON(见 panel.py 的 ulCount),
    字段名(hits / hits_page / hits_doc / where)是前后端之间的**契约** —— 改了名字
    面板不会报错, 只会静默显示成"—"。故这里把整包 JSON 都断到。
    [v35] hits_pos(每一处的 {page, rect})同样是契约: 面板的 ◀▶ 靠它定位与画框。
    """
    args = [USER_LINKS, "--target", target, "--count-hits",
            "--anchor", anchor, "--scope", scope]
    if page:
        args += ["--page", str(page)]
    if dual:
        args.append("--dual")
    rc, out, _ = run(*args)
    try:
        obj = json.loads(out.strip().splitlines()[0])
    except Exception:
        obj = None
    return rc, obj, out


def links(path, pno):
    d = pymupdf.open(path)
    out = [{"kind": l["kind"], "uri": l.get("uri"), "page": l.get("page"),
            "from": pymupdf.Rect(l["from"])}
           for l in d[pno].get_links()]
    d.close()
    return out


def uris(path):
    """全篇 URI 链接 [(页0基, uri, 矩形)]。"""
    d = pymupdf.open(path)
    out = []
    for pno in range(len(d)):
        for l in d[pno].get_links():
            if l["kind"] == pymupdf.LINK_URI:
                out.append((pno, l.get("uri") or "", pymupdf.Rect(l["from"])))
    d.close()
    return out


def gotos(path):
    d = pymupdf.open(path)
    out = [(pno, pymupdf.Rect(l["from"]))
           for pno in range(len(d)) for l in d[pno].get_links()
           if l["kind"] == pymupdf.LINK_GOTO]
    d.close()
    return out


def md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


# ---------------------------------------------------------------------- 主
def main():
    tmp = tempfile.mkdtemp(prefix="pdf2zh_test_ulinks_")
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    def mono(name):
        return make_mono(os.path.join(tmp, name))

    def dual(name):
        return make_dual(os.path.join(tmp, name))

    def spec(name, obj):
        return write_spec(os.path.join(tmp, name), obj)

    # ================= ① 正常装: 全局首次出现 + 本篇页内第 2 处 + 覆盖原有 =================
    print("[1] 正常装 + 覆盖原有链接")
    m = mono("m1.pdf")
    rep = os.path.join(tmp, "r1.tsv")
    sp = spec("s1.json", {
        "global": [{"anchor": CONCEPT, "url": URL_G, "occurrence": 1}],
        "tasks": {"t1": [{"anchor": CITE, "url": URL_OWN, "page": 2, "occurrence": 2}]},
    })
    rc, out, err = run(USER_LINKS, "--target", m, "--spec", sp, "--task", "t1",
                       "--report", rep)
    check("① 退出码 0", rc == 0, out + err)
    u = uris(m)
    check("① 装 2 条 URI", len(u) == 2, repr(u))
    g = [x for x in u if x[1] == URL_G]
    o = [x for x in u if x[1] == URL_OWN]
    check("① 全局锚落 p1 第 1 次出现(y=%.0f)" % Y_C1,
          len(g) == 1 and g[0][0] == 0 and abs(g[0][2].y0 - (Y_C1 - 12.5)) < 3, repr(g))
    check("① 本篇锚落 p2 第 2 次出现(y=%.0f)" % Y_X2,
          len(o) == 1 and o[0][0] == 1 and o[0][2].y0 > Y_X2 - 20, repr(o))
    check("① 压住的那条原有 GOTO 被删(用户 > 原有)", gotos(m) == [], repr(gotos(m)))
    with open(rep, encoding="utf-8") as f:
        rtxt = f.read()
    check("① 报告点名了覆盖动作", "覆盖" in rtxt and "引文锚(GOTO)" in rtxt, rtxt)
    check("① 落盘后核对 2/2", "落盘后核对 2/2" in out, out)
    check("① 提醒接着跑 style_links", "style_links" in out, out)

    # ================= ② --check 是真预检(不落盘) =================
    print("[2] --check 预检不落盘")
    m = mono("m2.pdf")
    before = md5(m)
    sp = spec("s2.json", {
        "global": [{"anchor": CONCEPT, "url": URL_G, "occurrence": 1}],
        "tasks": {"t1": [{"anchor": CITE, "url": URL_OWN, "page": 2, "occurrence": 1}]},
    })
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp, "--task", "t1", "--check")
    check("② 预检可装 -> 退 0", rc == 0, out)
    check("② 预检打印计数", "预检: 可装 2 条" in out, out)
    check("② 预检未落盘(md5 不变)", md5(m) == before)
    sp_bad = spec("s2b.json", {"global": [{"anchor": CONCEPT, "url": "ftp://x"}]})
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp_bad, "--check")
    check("② 预检不通过 -> 退 1", rc == 1, out)
    check("② 不通过时也未落盘", md5(m) == before)

    # ================= ③ 六类门禁全部拦在落盘之前 =================
    print("[3] 门禁六连(全部 FAIL, 文件不动)")
    m = mono("m3.pdf")
    before = md5(m)
    # 六条各用**互不相同**的锚: 若两条撞锚, load_spec 会按"本篇覆盖全局"把 global 那条
    # 跳过 —— 报警的 gate 就跟着一起被跳过, 六连只剩四条(实测踩过)。
    sp = spec("s3.json", {
        "global": [
            {"anchor": "   ", "url": URL_G},                          # 锚空
            {"anchor": "非http的网址条目", "url": "ftp://x"},           # URL 非 http(s)
            {"anchor": "带页码的全局条目", "url": URL_G, "page": 1},    # global 带 page
        ],
        "tasks": {"t1": [
            {"anchor": "缺页码的本篇条目", "url": URL_G},                # tasks 缺 page
            {"anchor": "这个锚整篇都不存在", "url": URL_G, "page": 1},   # 0 命中
            {"anchor": CITE, "url": URL_G, "page": 2, "occurrence": 9},  # 越界
        ]},
    })
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp, "--task", "t1")
    check("③ 退 1", rc == 1, out)
    for kw in ("锚文本为空", "必须以 http", "全局条目不许带 page", "必须给 page",
               "找不到", "越界"):
        check("③ 点名: %s" % kw, kw in out, out)
    check("③ 一个字都没写(md5 不变)", md5(m) == before)
    check("③ 报 6 条不通过", "6 条不通过" in out, out)

    # ================= ④ 多命中却没给 occurrence -> FAIL, 不猜 =================
    print("[4] 多命中无 occurrence -> FAIL(不挑最像的那处)")
    m = mono("m4.pdf")
    before = md5(m)
    sp = spec("s4.json", {"global": [{"anchor": CONCEPT, "url": URL_G}]})
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp)
    check("④ 退 1", rc == 1, out)
    check("④ 说明命中几处", "命中 2 处" in out and "occurrence" in out, out)
    check("④ 文件不动", md5(m) == before)

    # ================= ⑤ 撞锚以本篇为准 =================
    print("[5] 撞锚: 本篇覆盖全局")
    m = mono("m5.pdf")
    sp = spec("s5.json", {
        "global": [{"anchor": CONCEPT, "url": URL_G, "occurrence": 1}],
        "tasks": {"t1": [{"anchor": CONCEPT, "url": URL_OWN, "page": 1, "occurrence": 2}]},
    })
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp, "--task", "t1")
    check("⑤ 退 0", rc == 0, out)
    u = uris(m)
    check("⑤ 只装 1 条(全局那条被跳过)", len(u) == 1, repr(u))
    check("⑤ 装的是本篇 URL, 落在第 2 次出现",
          len(u) == 1 and u[0][1] == URL_OWN and u[0][2].y0 > Y_C2 - 20, repr(u))
    check("⑤ 说出被覆盖", "本篇覆盖全局" in out, out)
    check("⑤ 未压住原有 GOTO(第 2 处不重叠)",
          len(gotos(m)) == 1, repr(gotos(m)))

    # ================= ⑥ --task 缺席 + 规格有 tasks -> 必须说出来 =================
    print("[6] --task 缺席的 NOTICE")
    m = mono("m6.pdf")
    sp = spec("s6.json", {
        "global": [{"anchor": CONCEPT, "url": URL_G, "occurrence": 1}],
        "tasks": {"t1": [{"anchor": CITE, "url": URL_OWN, "page": 2, "occurrence": 1}]},
    })
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp)
    check("⑥ 退 0(只装全局)", rc == 0, out)
    check("⑥ 只装 1 条", len(uris(m)) == 1, repr(uris(m)))
    check("⑥ NOTICE 点出未生效的任务", "NOTICE" in out and "t1" in out, out)

    # ================= ⑦ 两条新锚抢同一矩形 -> 两条都 FAIL =================
    print("[7] 新锚互撞")
    m = mono("m7.pdf")
    before = md5(m)
    sp = spec("s7.json", {"tasks": {"t1": [
        {"anchor": CITE, "url": URL_OWN, "page": 2, "occurrence": 1},
        {"anchor": CITE, "url": URL_G, "page": 2, "occurrence": 1},
    ]}})
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp, "--task", "t1")
    check("⑦ 退 1", rc == 1, out)
    check("⑦ 抢同一块矩形 -> 两条都点名", out.count("抢同一块矩形") == 2, out)
    check("⑦ 文件不动", md5(m) == before)

    # ================= ⑧ dual: 页码是 mono 坐标, 落到译文侧 =================
    print("[8] dual 页映射")
    d = dual("d1.pdf")
    sp = spec("s8.json", {"global": [{"anchor": CONCEPT, "url": URL_G, "occurrence": 1}]})
    rc, out, _ = run(USER_LINKS, "--target", d, "--spec", sp, "--dual")
    check("⑧ 退 0", rc == 0, out)
    u = uris(d)
    check("⑧ 装 1 条且落在译文侧(1 基 p1 -> 0 基 index 1)",
          len(u) == 1 and u[0][0] == 1, repr(u))
    check("⑧ 落在译文侧那处(y=%.0f, 不是原版侧的 %.0f)" % (Y_X2, Y_X1),
          len(u) == 1 and u[0][2].y0 > Y_X2 - 20, repr(u))
    d2 = dual("d2.pdf")
    before = md5(d2)
    sp = spec("s8b.json", {"tasks": {"t1": [
        {"anchor": CONCEPT, "url": URL_G, "page": 4, "occurrence": 1}]}})
    rc, out, _ = run(USER_LINKS, "--target", d2, "--spec", sp, "--task", "t1", "--dual")
    check("⑧ dual 下 page 越界(mono 只有 3 页) -> 退 1", rc == 1 and "越界" in out, out)
    check("⑧ 越界时文件不动", md5(d2) == before)
    m = mono("m8.pdf")
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp, "--task", "t1", "--dual")
    check("⑧ --dual 遇奇数页 -> 退 1", rc == 1 and "奇数" in out, out)

    # ================= ⑨ verify_links 第四判据 =================
    print("[9] verify_links 第四判据")
    sp = spec("s9.json", {
        "global": [{"anchor": CONCEPT, "url": URL_G, "occurrence": 1}],
        "tasks": {"t1": [{"anchor": CITE, "url": URL_OWN, "page": 2, "occurrence": 2}]},
    })
    m = mono("m9a.pdf")
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp, "--task", "t1")
    rc, out, _ = run(VERIFY_LINKS, "--target", m, "--spec", sp, "--task", "t1")
    check("⑨ 已就位 -> 退 0", rc == 0, out)
    check("⑨ 报告口径", "用户自定义链接 2 条 | 就位 2" in out, out)

    m2f = mono("m9b.pdf")            # 故意不跑 user_links.py
    rc, out, _ = run(VERIFY_LINKS, "--target", m2f, "--spec", sp, "--task", "t1")
    check("⑨ 漏跑 user_links -> 退 1", rc == 1, out)
    check("⑨ 指明漏跑", "缺链接" in out and "漏跑 user_links" in out, out)
    check("⑨ 计数 0/2 就位", "| 就位 0 |" in out, out)

    sp_miss = spec("s9c.json", {"global": [
        {"anchor": "整篇都没有的锚", "url": URL_G, "occurrence": 1}]})
    rc, out, _ = run(VERIFY_LINKS, "--target", m2f, "--spec", sp_miss)
    check("⑨ 锚不存在 -> 退 1 且报锚未命中",
          rc == 1 and "锚未命中" in out, out)

    rc, out, _ = run(VERIFY_LINKS, "--target", m2f)
    check("⑨ 不给 --spec 时维持旧契约(退 0)", rc == 0, out)
    check("⑨ 不给 --spec 时不打印该段", "用户自定义链接" not in out, out)

    # ================= ⑩ --list-lines(面板选字) =================
    print("[10] --list-lines")
    m = mono("m10.pdf")
    rc, out, _ = run(USER_LINKS, "--target", m, "--page", 1, "--list-lines")
    check("⑩ 退 0", rc == 0, out)
    try:
        obj = json.loads(out.strip().splitlines()[0])
        ok = obj["page"] == 1 and any(CONCEPT in ln for ln in obj["lines"])
    except Exception as e:
        obj, ok = repr(e), False
    check("⑩ 返回本页文字(含概念锚可点选)", ok, repr(obj))
    rc, out, _ = run(USER_LINKS, "--target", m, "--list-lines")
    check("⑩ 缺 --page -> 退 1", rc == 1 and "--page" in out, out)

    # ================= ⑪ 带空格的锚要真的找得到 =================
    # 面板给的是**整行文字**, 行里带空格是常态(『GelSight Mini』这种) —— 锚若被去掉空格
    # 再拿去搜, 页面上就没有这个串了, 用户点的自己那一行会被判"找不到"(实测踩过)。
    print("[11] 带空格的锚")
    m = make_spaced(os.path.join(tmp, "m11.pdf"))
    sp = spec("s11.json", {"global": [{"anchor": SPACED, "url": URL_G,
                                       "occurrence": 2}]})
    # 先看预检的报告文本: 两处出现必须数成**2 处**(按框数), 不是按词数 4 处 —— 数错
    # 了 occurrence 的语义就跟"用户看到第几处"对不上, 而这类错不会退非 0, 只会静默装偏。
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp, "--check")
    check("⑪ 预检能装(没把用户点的那行判成找不到) -> 退 0", rc == 0, out)
    check("⑪ 命中数按出现次数数(2 处, 不是 4 个词)", "命中 2 处, 取第 2 处" in out, out)
    rc, out, _ = run(USER_LINKS, "--target", m, "--spec", sp)
    check("⑪ 带空格锚能装上 -> 退 0", rc == 0, out)
    u = uris(m)
    check("⑪ 落在第 2 次出现(y=%.0f 的那处)" % Y_SPACED2,
          len(u) == 1 and u[0][2].y0 > Y_SPACED2 - 20, repr(u))

    # ================= ⑫ 变蓝(本功能的可见性前置) =================
    # user_links 只管把热区装上, "看得见"是 style_links 的活。两条实测过的坑:
    #   ① 非嵌入字体(Base-14: Times/Helvetica)抽不出字体文件, 以前**整段跳过** ——
    #      于是出现在正文里的拉丁锚全是黑字;
    #   ② 旋转/竖排行(页边戳)照自己的 origin 平着重绘 = 把这串字横着铺过整页。
    print("[12] style_links 变蓝")
    b = make_blue(os.path.join(tmp, "b12.pdf"))
    check("⑫ 重绘之前这里是黑字(基线)", not blue_in(b, 0, RECT_H), "")
    rc, out, _ = run(STYLE_LINKS, "--target", b)
    check("⑫ 退 0", rc == 0, out)
    check("⑫ 非嵌入字体不再被跳过(只跳过旋转那条)", "跳过 1" in out, out)
    check("⑫ 横向锚重绘出蓝字", blue_in(b, 0, RECT_H), "")
    d = pymupdf.open(b)
    t = d[0].get_text()
    d.close()
    # 重绘是**叠绘**(锚文本在文本层里多出一份, 见 style_links 文档里那条已知代价) ——
    # 所以"重绘发生了"= 锚串出现 2 次; 旋转戳若也被重绘, 它的串同样会变成 2 次。
    check("⑫ 横向锚确实被重绘(文本层里 ANCHOR1 出现 2 次)", t.count("ANCHOR1") == 2, t)
    check("⑫ 旋转行未被重绘(ROTATE1 只出现 1 次)", t.count("ROTATE1") == 1, t)

    # ================= ⑬ 折行锚(字符级兜底, [v28.91]) =================
    # 面板最常用的动作是"点右列中文取**整段**", 而整段在成品页上几乎必然折行 ——
    # search_for 不跨行, 于是用户点自己眼前那行字, 工具却说"找不到"(实测 Lee: 44 字整锚
    # 命中 0, 6 字短锚命中 6)。兜底就是为这一档做的, 但它带来的最大风险是**一次出现被
    # 数成两次**(按行各返回一个框), occurrence 的语义会当场错掉 —— 故这里两头都断。
    print("[13] 折行锚")
    m = make_wrapped(os.path.join(tmp, "m13.pdf"))
    d = pymupdf.open(m)
    engine_hits = d[0].search_for(WRAPPED)
    d.close()
    # 前提先验: 这一页真的折了行(引擎口径为空)。若这里非空, 说明用例没造出折行,
    # 后面那几条全是在验 search_for —— "通过"了却一个字都没验兜底。
    check("⑬ 前提成立: search_for 搜不到折行锚(所以才非兜底不可)",
          engine_hits == [], repr(engine_hits))
    rc, obj, out = count_hits(m, WRAPPED, page=1)
    check("⑬ 折行锚算 2 处(两行 = 1 次出现; 按行计数会数成 4)",
          rc == 0 and obj and obj["hits"] == 2, repr((rc, obj, out)))
    rc, obj, out = count_hits(m, WRAPPED, scope="全局")
    check("⑬ 全局口径同为 2 处(occ_list 与本篇走同一个 find_hits)",
          rc == 0 and obj and obj["hits_doc"] == 2, repr((rc, obj, out)))
    for occ, where in ((1, "上"), (2, "下")):
        f = make_wrapped(os.path.join(tmp, "m13_%d.pdf" % occ))
        sp = spec("s13_%d.json" % occ, {"tasks": {"t1": [
            {"anchor": WRAPPED, "url": URL_OWN, "page": 1, "occurrence": occ}]}})
        rc, out, _ = run(USER_LINKS, "--target", f, "--spec", sp, "--task", "t1")
        u = uris(f)
        y0 = u[0][2].y0 if len(u) == 1 else -1.0
        check("⑬ 第 %d 处按阅读序落到%s面那条(y0=%.0f)" % (occ, where, y0),
              rc == 0 and len(u) == 1 and ((y0 < SWITCH_Y) if occ == 1 else (y0 > SWITCH_Y)),
              repr((rc, out, u)))
    h = 0.0
    u = uris(os.path.join(tmp, "m13_1.pdf"))
    if len(u) == 1:
        h = u[0][2].y1 - u[0][2].y0
    check("⑬ 热区是整条锚的包围盒(盖住两行, 高 %.0fpt)" % h, h > 20, repr(u))
    rc, obj, out = count_hits(m, "这段字页面上根本没有", page=1)
    check("⑬ 页面上没有的锚仍是 0 命中(兜底不凭空造命中)",
          rc == 0 and obj and obj["hits"] == 0, repr((rc, obj, out)))

    # ================= ⑭ --count-hits 三口径(面板「第几处」的读数) =================
    # 面板的「第几处」是**自动填**的, 依据就是这一问的 JSON。三个口径必须分得开:
    # hits=当前口径 / hits_page=本页 / hits_doc=全篇 —— 少一个, 面板就答不上
    # "为什么全篇只有 1 处我来问第几处"(那正是用户实测提过的问题)。
    print("[14] --count-hits 三口径")
    m = make_count(os.path.join(tmp, "m14.pdf"))
    rc, obj, out = count_hits(m, CONCEPT, page=1)
    check("⑭ 本篇 p1: hits=1 而 hits_doc=2(面板据此提示切「全局」)",
          rc == 0 and obj and obj["hits"] == 1 and obj["hits_page"] == 1
          and obj["hits_doc"] == 2 and obj["where"] == "第 1 页", repr((rc, obj, out)))
    rc, obj, out = count_hits(m, CONCEPT, scope="全局")
    check("⑭ 全局: hits=hits_doc=2 且 hits_page 为 null(全篇口径没有「本页」)",
          rc == 0 and obj and obj["hits"] == 2 and obj["hits_doc"] == 2
          and obj["hits_page"] is None and obj["where"] == "全篇", repr((rc, obj, out)))
    rc, obj, out = count_hits(m, CONCEPT, page=3)
    check("⑭ 页码越界 -> 退 1(不静默按别的页算)", rc == 1 and "越界" in out, out)
    rc, obj, out = count_hits(m, "   ")
    check("⑭ 空锚 -> 退 1", rc == 1 and "需要 --anchor" in out, out)

    # ================= ⑮ 标点折叠只放行"宽度差异"([v28.92] + [v34] 收紧) ==========
    # 折叠是"两侧走同一把尺子"; 尺子越粗, 两个**不同**的串越容易被折成同一个 -> 链接
    # 静默挂到别处(门禁过、链接装上、读者点开才知道)。故正反两头都断: 正例证明折叠真在
    # 干活(先验 search_for 为空), 反例证明它没被放宽成模糊匹配。文件头 docstring 早就
    # 这么写着, 但代码里从没有过断言(grep `_fold` 零命中) —— 等于没有东西挡着别人改宽。
    print("[15] 标点折叠(正例 + 反例)")
    m = make_fullwidth(os.path.join(tmp, "m15.pdf"))
    d = pymupdf.open(m)
    eng_fw = d[0].search_for(FULLW_ASCII)
    eng_ci = d[0].search_for(CITED_ASCII)
    d.close()
    # 前提先验: 引擎口径真的搜不到(否则后面验的是 search_for, 不是折叠)
    check("⑮ 前提成立: 全角页上 search_for 搜不到 ASCII 锚(折叠才有活干)",
          eng_fw == [] and eng_ci == [], repr((eng_fw, eng_ci)))
    rc, obj, out = count_hits(m, FULLW_ASCII, page=1)
    check("⑮ 全角页配 ASCII 锚 -> 命中 2 处(只差宽窄: （）： 都是 <wide>)",
          rc == 0 and obj and obj["hits"] == 2, repr((rc, obj, out)))
    rc, obj, out = count_hits(m, CITED_ASCII, page=1)
    check("⑮ `。` vs `.` 也认(句号无兼容分解, 靠手列的表) -> 命中 1 处",
          rc == 0 and obj and obj["hits"] == 1, repr((rc, obj, out)))
    rc, obj, out = count_hits(m, NEG, page=1)
    check("⑮ 内容不同(数字 (2) vs (1)) 仍 0 命中 —— 折叠不放行内容差异",
          rc == 0 and obj and obj["hits"] == 0, repr((rc, obj, out)))
    for (on_page, anchor), y in zip(FOLD_DENY, (Y_FW4, Y_FW5, Y_FW6)):
        rc, obj, out = count_hits(m, anchor, page=1)
        check("⑮ 页上 %r 配锚 %r -> 0 命中(不是宽窄写法, 不许折)" % (on_page, anchor),
              rc == 0 and obj and obj["hits"] == 0, repr((rc, obj, out)))
    # 折叠会**改长度**(`…` 类一旦被折, 一个原字映到多个折叠字), 下标映射错了框就会盖歪;
    # 故正例装上以后要核: 第 1/2 处分别落在上下两条, 且框只盖一行。
    f = make_fullwidth(os.path.join(tmp, "m15_a.pdf"))
    sp = spec("s15.json", {"tasks": {"t1": [
        {"anchor": FULLW_ASCII, "url": URL_OWN, "page": 1, "occurrence": 1}]}})
    rc, out, _ = run(USER_LINKS, "--target", f, "--spec", sp, "--task", "t1")
    u = uris(f)
    hh = (u[0][2].y1 - u[0][2].y0) if len(u) == 1 else -1.0
    check("⑮ 折后第 1 处落上一条(y0=%.0f)且框只盖一行(高 %.0fpt)" %
          (u[0][2].y0 if len(u) == 1 else -1.0, hh),
          rc == 0 and len(u) == 1 and u[0][2].y0 < Y_FW2 - 40 and hh < 24,
          repr((rc, out, u)))

    # ================= ⑯ 版面连续性守卫: 流里相邻 != 版面上挨着([v34]) ==============
    # 第③档的"命中"只说明**流里**连着; 整页被切碎成几十块的图形页上, 流里紧接着的两个字
    # 版面上可能一个在页眉一个在页脚 —— 于是拼出一个页面上并不存在的串, 热区横跨大半页。
    # ⑬ 是正对照(真折行必须仍命中), 这里断的是反向: 隔着大半页的两个块不许拼成一次出现。
    print("[16] 版面连续性守卫")
    m = make_phantom(os.path.join(tmp, "m16.pdf"))
    d = pymupdf.open(m)
    flat = "".join(d[0].get_text("text").split())
    d.close()
    check("⑯ 前提成立: 页面的字符流里确实拼得出『束模』(所以非守卫不可)",
          "束模" in flat, repr(flat))
    rc, obj, out = count_hits(m, "束模", page=1)
    check("⑯ 隔 600pt 的两个块不许拼成一次命中 -> 0 处",
          rc == 0 and obj and obj["hits"] == 0, repr((rc, obj, out)))

    # ================= ⑰ [v35] 定位: hits_pos + --render-page 荧光黄 ==================
    # 用户按 ◀▶ 要的不是"第 3 处"这个数字, 而是**文章里的哪一处**。这一节锁三件事:
    #   a) hits_pos 的顺序 = 阅读序, 且 **hits_pos[N-1] 就是 occurrence=N 装进成品的那一处**
    #      —— 两处各算一套的下场是"图上涂第 3 处、链接装在第 2 处", 而且静默;
    #   b) dual 下页码**回写成 mono 页码**(不回写就报一个用户翻不到的页号);
    #   c) --render-page 把荧光黄涂在该矩形的画素位置上, 且成品**一个字节都没动**。
    print("[17] hits_pos 定位 + --render-page 荧光黄")
    m = make_count(os.path.join(tmp, "m17.pdf"))
    rc, obj, out = count_hits(m, CONCEPT, page=1)
    pos = (obj or {}).get("hits_pos") or []
    check("⑰ 本篇: hits_pos 条数 = hits(1), 页号 1, 框住那一行(y=%.0f)" % Y_C1,
          rc == 0 and len(pos) == 1 and pos[0]["page"] == 1
          and abs(pos[0]["rect"][1] - (Y_C1 - 12.5)) < 3, repr((rc, obj, out)))
    rc, obj, out = count_hits(m, CONCEPT, scope="全局")
    pos = (obj or {}).get("hits_pos") or []
    check("⑰ 全局: 2 条且按阅读序(第 1 页在前, 第 2 页在后)",
          rc == 0 and [p["page"] for p in pos] == [1, 2], repr((rc, obj, out)))
    # 端到端同源: 规格写 occurrence=2, 装好的 URI 必须落在 hits_pos[1] 那个矩形上。
    f = make_count(os.path.join(tmp, "m17_e2e.pdf"))
    rc0, obj0, _ = count_hits(f, CONCEPT, scope="全局")
    sp = spec("s17.json", {"global": [{"anchor": CONCEPT, "url": URL_G, "occurrence": 2}]})
    rc, out, _ = run(USER_LINKS, "--target", f, "--spec", sp, "--task", "t1")
    u = uris(f)
    p2 = [p for p in ((obj0 or {}).get("hits_pos") or []) if p["page"] == 2]
    check("⑰ 装进成品的第 2 处 == hits_pos[1](◀▶ 指的那处就是它装的那处)",
          rc == 0 and len(u) == 1 and u[0][0] == 1 and len(p2) == 1
          and abs(u[0][2].y0 - p2[0]["rect"][1]) < 0.02
          and abs(u[0][2].x0 - p2[0]["rect"][0]) < 0.02, repr((rc, out, u, obj0)))
    # dual: 规格页码是 mono 坐标, 位置也必须回写成 mono 页码 —— 第 1 页(不是第 2 页),
    # 且框必须落在**译文侧**那条(y=Y_X2), 不是原版侧那条(y=Y_X1)。
    d = make_dual(os.path.join(tmp, "m17_dual.pdf"))
    rc, obj, out = count_hits(d, CONCEPT, page=1, dual=True)
    pos = (obj or {}).get("hits_pos") or []
    check("⑰ dual: 页码回写成 mono 页码(1 而非 2)且框落译文侧(y=%.0f)" % Y_X2,
          rc == 0 and len(pos) == 1 and pos[0]["page"] == 1
          and abs(pos[0]["rect"][1] - (Y_X2 - 12.5)) < 3, repr((rc, obj, out)))
    # --render-page: 荧光黄必须涂在 rect×zoom 的**画素位置**上, 且只涂在那里。
    png = os.path.join(tmp, "shot17.png")
    before = md5(m)
    rc, out, _ = run(USER_LINKS, "--target", m, "--render-page", "--page", "1",
                     "--out", png, "--rect", "72,88,132,104")
    check("⑰ --render-page 出图(rc=0 且文件在)", rc == 0 and os.path.exists(png), out)
    check("⑰ 渲染是只读的: 成品 md5 未变", md5(m) == before)
    if os.path.exists(png):
        # zoom=2 -> 点 (72,88,132,104) 落到画素 (144,176,264,208); 取稍外一圈的壳
        n_in = mark_pixels(png, 140, 170, 268, 214)
        n_out = mark_pixels(png, 400, 1000, 800, 1400, step=20)
        check("⑰ 荧光黄涂在给定矩形的位置上(壳内 %d 点黄, 别处 %d 点)" % (n_in, n_out),
              n_in >= 200 and n_out == 0, repr((n_in, n_out)))
        # "荧光标记"而不是"描边框": 取矩形**正中间那条横线** —— 描边法在那条线上只会有
        # 左右两条竖边(且都在取样区间之外), 实心涂黄则是整条都黄。这条判据把"退回描边"卡死。
        n_mid = mark_pixels(png, 150, 191, 258, 193, step=1)
        check("⑰ 是实心涂黄而非描边(中线 %d 点全黄)" % n_mid, n_mid >= 100,
              repr((n_mid, n_in, n_out)))
    rc, out, _ = run(USER_LINKS, "--target", m, "--render-page", "--page", "1",
                     "--out", os.path.join(tmp, "shot17b.png"), "--rect", "1,2,3")
    check("⑰ --rect 不是 4 个数 -> 退 1(不静默不画框)",
          rc == 1 and "解析不了" in out, out)
    rc, out, _ = run(USER_LINKS, "--target", m, "--render-page", "--page", "9",
                     "--out", os.path.join(tmp, "shot17c.png"))
    check("⑰ 页越界 -> 退 1", rc == 1 and "越界" in out, out)

    print("\nuser_links: 通过 %d | 失败 %d" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
