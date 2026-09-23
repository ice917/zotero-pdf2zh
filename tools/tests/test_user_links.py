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

    print("\nuser_links: 通过 %d | 失败 %d" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
