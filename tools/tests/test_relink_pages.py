# -*- coding: utf-8 -*-
"""relink_pages 链接热区重定位单元测试 (v28.65, 2026-09-21)

被锁死的缺陷 —— **热区只剩一个括号宽**:
  译文页的文字是碎片排版(每个字形单独定位), 于是 `search_for('[5]')` 会返回
  **每个字一个矩形**。实测译文页 `[`=[411.60,414.92] / `5`=[414.92,419.90] /
  `]`=[419.90,423.22], 首尾严丝合缝(间隙 0.00), 数字基准线比括号低 ~3pt。
  原实现 `min(hits, key=dist2)` 直接取"离原框中心最近者" -> 那条命中的宽度只有
  3.32pt **一个括号宽**, 结果是"点括号能跳、点数字点不到"。
  实测 GeoTLM 145 条命名链接里 **49 条**中招, 而原版同一批 **0 条** —— 全是本步
  引入的, 不是既有缺陷。修法: 先把碎片命中并成"一次出现"的整框, 再挑最近者。

本套件锁三条: ① `merge_fragments` 的并框判据(该并的并、不该并的不并);
② 端到端 —— 合成一张碎片排版页, 跑真工具, 断言热区**覆盖整个锚**而不是一个字;
③ [v28.67] `zero_pages` 的**页码口径** —— 侧车 `page` 是回调计数, 真实页码是
`pageid + 1`; 旧版拿回调计数当页码, 图多的论文整体漂移 -> **跳过错的页**(该重定位
的页被当回填页跳过, 真正的回填页反而白搜一遍)。

合成碎片排版的手法(实测得出): 逐字 `insert_text`, 且把锚内部的字符**基线压低 3pt**
—— 这样 PyMuPDF 就把它们当成各自独立的碎片, `search_for` 逐字返回矩形, 与真件同构。
(单纯逐字插入或走 TextWriter 都**不会**触发, 会退化成整框命中。)

运行: venv python test_relink_pages.py, 退出码 0=全过
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
RELINK = os.path.join(TOOLS, "relink_pages.py")

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import pymupdf  # noqa: E402
import relink_pages as RL  # noqa: E402

SIZE = 9.5
WID = lambda ch: pymupdf.get_text_length(ch, fontname="helv", fontsize=SIZE)


def frag_marker(page, x, y, marker, drop=3.0):
    """碎片排版地写一个锚(逐字单独定位; 括号在外, 内部字符基线低 drop pt)。"""
    for ch in marker:
        page.insert_text((x, y + (drop if ch not in "[]" else 0.0)), ch, fontsize=SIZE)
        x += WID(ch)
    return x


def plain_marker(page, x, y, marker):
    page.insert_text((x, y), marker, fontsize=SIZE)
    return x + sum(WID(c) for c in marker)


def run(*args):
    proc = subprocess.run([sys.executable] + list(args), capture_output=True)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def links(path, pno, kind=None):
    d = pymupdf.open(path)
    out = [pymupdf.Rect(l["from"]) for l in d[pno].get_links()
           if kind is None or l["kind"] == kind]
    d.close()
    return out


def md5(path):
    import hashlib
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def main():
    tmp = tempfile.mkdtemp(prefix="pdf2zh_test_relink_")
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    # ================= 一、merge_fragments: 并框判据 =================
    # 真件实测值(GeoTLM p1 锚 '[5]' 的第一处出现)
    real = [pymupdf.Rect(411.60, 251.21, 414.92, 264.50),     # [
            pymupdf.Rect(414.92, 254.14, 419.90, 264.11),     # 5 (基线低 2.93pt)
            pymupdf.Rect(419.90, 251.21, 423.22, 264.50)]     # ]
    boxes = RL.merge_fragments(real)
    check("① 真件三连(间隙 0.00, 数字基线低 3pt) 并成 1 框",
          len(boxes) == 1, "并出 %d 框: %s" % (len(boxes), boxes))
    check("① 并出的框覆盖整锚 [411.60, 423.22] (宽 11.62, 不再是 3.32)",
          len(boxes) == 1 and abs(boxes[0].x0 - 411.60) < 0.2
          and abs(boxes[0].x1 - 423.22) < 0.2,
          repr(boxes[0]) if boxes else "无")

    # 同一锚在页面上有两处出现(y=251.2 / y=627.8) -> 必须并成 2 框, 不跨行糊成一团
    two = real + [pymupdf.Rect(341.87, 627.80, 345.18, 641.09),
                  pymupdf.Rect(345.18, 630.73, 350.16, 640.69),
                  pymupdf.Rect(350.16, 627.80, 353.48, 641.09)]
    boxes = RL.merge_fragments(two)
    check("② 两处出现 -> 2 框 (不跨行并)", len(boxes) == 2, repr(boxes))

    # 隔一个空格(约 2.4pt)的下一处引用不能被吞进来
    apart = [pymupdf.Rect(100, 100, 102.64, 113), pymupdf.Rect(102.64, 103, 107.92, 112.84),
             pymupdf.Rect(107.92, 100, 110.56, 113),
             pymupdf.Rect(112.96, 100, 115.6, 113)]      # 空格后紧接的 '['
    boxes = RL.merge_fragments(apart)
    check("③ 隔一个空格(2.4pt) 不并 -> 2 框", len(boxes) == 2,
          "%d 框: %s" % (len(boxes), boxes))

    # 非碎片页(整框命中) 原样返回 —— 别把常态搞坏
    one = [pymupdf.Rect(215.85, 389.79, 226.41, 402.84)]
    boxes = RL.merge_fragments(one)
    check("④ 整框命中(非碎片) 原样返回 1 框", len(boxes) == 1 and boxes[0] == one[0],
          repr(boxes))

    # ================= 二、pick_hit: 挑哪一处 =================
    ctr = (105.5, 96.0)          # 原框中心(页面左上角, 两处出现都在右下方)
    near_second = [pymupdf.Rect(341.87, 627.80, 345.18, 641.09),
                   pymupdf.Rect(345.18, 630.73, 350.16, 640.69),
                   pymupdf.Rect(350.16, 627.80, 353.48, 641.09)]
    got = RL.pick_hit(real, (415.0, 258.0), [])
    check("⑤ pick_hit 取离原框最近的那处(此处即唯一一处)",
          got is not None and abs(got.x0 - 411.60) < 0.2, repr(got))
    got = RL.pick_hit(real + near_second, (345.0, 634.0), [])
    check("⑤ 两处出现时取近者(此例是下面那处 y≈627)",
          got is not None and got.y0 > 600, repr(got))
    got = RL.pick_hit(real + near_second, (415.0, 258.0), [pymupdf.Rect(411.6, 251.2, 423.22, 264.5)])
    check("⑥ 近的那处已被同页前面的锚占用 -> 改选另一处",
          got is not None and got.y0 > 600, repr(got))
    got = RL.pick_hit(real, (415.0, 258.0), [pymupdf.Rect(400, 240, 430, 270)])
    check("⑥ 候选全被占用 -> None(记账, 不改框)", got is None, repr(got))
    check("⑦ hits 为空 -> None", RL.pick_hit([], ctr, []) is None)

    # ================= 三、端到端: 跑真工具 =================
    org = os.path.join(tmp, "orig.pdf")
    tgt = os.path.join(tmp, "mono.pdf")
    rep = os.path.join(tmp, "miss.txt")
    o = pymupdf.open()
    t = pymupdf.open()
    for _ in range(4):
        o.new_page(width=612, height=792)
        t.new_page(width=612, height=792)
    R = lambda pno, rect: (o[pno].insert_link(
        {"kind": pymupdf.LINK_NAMED, "from": pymupdf.Rect(*rect), "nameddest": "x"}),
        t[pno].insert_link(
        {"kind": pymupdf.LINK_NAMED, "from": pymupdf.Rect(*rect), "nameddest": "x"}))

    # p0: 目标页只有一处, 但是碎片 -> 必须搬成"覆盖整锚"的框(回归: 老代码给 2.64 宽)
    plain_marker(o[0], 99, 100, "[5]")
    R(0, (98.5, 89, 112, 103))
    frag_marker(t[0], 200, 200, "[5]")

    # p1: 目标页两处, 原框靠近下面那处 -> 应落到下面那处
    plain_marker(o[1], 99, 100, "[5]")
    plain_marker(o[1], 99, 400, "[5]")
    R(1, (98.5, 389, 112, 403))
    frag_marker(t[1], 200, 200, "[5]")
    frag_marker(t[1], 200, 400, "[5]")

    # p2: 原框里只有 1 个字符 -> 锚太短, 跳过(框不动)
    o[2].insert_text((99, 100), "5", fontsize=SIZE)
    R(2, (90, 89, 112, 103))

    # p3: 目标页没有这个锚 -> 未命中, 保持原位并记报告
    plain_marker(o[3], 99, 100, "[9]")
    R(3, (98.5, 89, 112, 103))

    o.save(org); o.close()
    t.save(tgt); t.close()

    before = links(tgt, 2)
    rc, so, se = run(RELINK, "--target", tgt, "--original", org, "--report", rep)
    check("⑧ 端到端退出码 0", rc == 0, "%d %s" % (rc, se[-300:]))
    check("⑧ 报「重定位 2 | 未命中 1」(短锚那条被跳过)",
          "重定位: 2" in so and "未命中(保持原位): 1" in so, so.strip())

    b0 = links(tgt, 0)[0]
    check("⑨ 碎片锚的热区覆盖整个 '[5]' (宽 %.2f, 而不是一个括号的 2.64)"
          % b0.width, b0.width > 8.0 and abs(b0.x0 - 200) < 0.6,
          "x=[%.2f,%.2f] w=%.2f" % (b0.x0, b0.x1, b0.width))
    check("⑨ 热区完整（左边界在 '[' 左、右边界在 ']' 右）",
          b0.x0 <= 200.1 and b0.x1 >= 210.4, "x=[%.2f,%.2f]" % (b0.x0, b0.x1))

    b1 = links(tgt, 1)[0]
    check("⑩ 两处出现时落到「近」的那处(下, y>380)", b1.y0 > 380,
          "y=[%.1f,%.1f]" % (b1.y0, b1.y1))

    check("⑪ 锚 <2 字符 -> 跳过, 矩形一格未动", links(tgt, 2) == before,
          "%s vs %s" % (links(tgt, 2), before))
    check("⑫ 未命中 -> 矩形保持原位且进了报告",
          os.path.exists(rep) and "'[9]'" in open(rep, encoding="utf-8").read(),
          open(rep, encoding="utf-8").read() if os.path.exists(rep) else "无报告")

    # ============ 四、zero_pages: 页码口径(真实页序, 不是回调计数) ============
    # 侧车的 `page` 是 receive_layout 的**回调计数**(图形对象也各占一号),
    # 真实页码只有 `pageid + 1` 说得准。旧版拿回调计数当页码 -> 跳过错的页。
    def sidecar(name, recs):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return p

    sc = sidecar("drift.jsonl", [
        {"page": 1, "pageid": 0, "segs": [{"raw": "Intro text"}]},   # 真实 p1 有正文
        {"page": 2, "pageid": 0, "segs": [{"raw": "{v0}"}]},         # 图形回调, 仍属真实 p1
        {"page": 3, "pageid": 1, "segs": [{"raw": "Body text"}]},    # 真实 p2 有正文
        {"page": 4, "pageid": 2, "segs": [{"raw": "{v1}"}]},         # 真实 p3 纯字形
    ])
    got = RL.zero_pages(sc)
    check("⑬ 漂移侧车 -> 报**真实**零页 {3}(旧口径报 {2,4}: 跳过错的页)",
          got == {3}, "得 %s" % sorted(got))

    # 同一真实页多条记录: 只要有一条含可译文字, 这页就不算零可译(按页归并)
    sc = sidecar("same.jsonl", [
        {"page": 5, "pageid": 2, "segs": [{"raw": "{v0}"}]},         # 图形回调
        {"page": 6, "pageid": 2, "segs": [{"raw": "Table text"}]},   # 页面回调, 有文字
    ])
    check("⑭ 同页多记录有一条可译 -> 该页不算零页", RL.zero_pages(sc) == set(),
          repr(RL.zero_pages(sc)))

    # 老侧车(无 pageid) -> 回落 page 口径, 不误伤归档件
    sc = sidecar("old.jsonl", [
        {"page": 1, "segs": [{"raw": "Intro"}]},
        {"page": 2, "segs": [{"raw": "{v0}"}]},
    ])
    check("⑮ 无 pageid -> 回落 page 口径", RL.zero_pages(sc) == {2}, repr(RL.zero_pages(sc)))

    # 端到端: --sidecar 指的**真实零页**必须被跳过(热区留在原坐标)
    org2 = os.path.join(tmp, "orig2.pdf")
    tgt2 = os.path.join(tmp, "mono2.pdf")
    o = pymupdf.open()
    t = pymupdf.open()
    for _ in range(2):
        o.new_page(width=612, height=792)
        t.new_page(width=612, height=792)
    for pno in (0, 1):
        plain_marker(o[pno], 99, 100, "[5]")
        o[pno].insert_link({"kind": pymupdf.LINK_NAMED,
                            "from": pymupdf.Rect(98.5, 89, 112, 103), "nameddest": "x"})
        frag_marker(t[pno], 200, 200, "[5]")
        t[pno].insert_link({"kind": pymupdf.LINK_NAMED,
                            "from": pymupdf.Rect(98.5, 89, 112, 103), "nameddest": "x"})
    o.save(org2); o.close()
    t.save(tgt2); t.close()

    sc = sidecar("e2e.jsonl", [
        {"page": 1, "pageid": 0, "segs": [{"raw": "Page one text"}]},
        {"page": 2, "pageid": 1, "segs": [{"raw": "{v0}"}]},         # 真实 p2 纯字形
    ])
    p2_before = links(tgt2, 1)
    rc, so, se = run(RELINK, "--target", tgt2, "--original", org2, "--sidecar", sc)
    check("⑯ 端到端带 --sidecar 退出码 0", rc == 0, "%d %s" % (rc, se[-300:]))
    check("⑯ 真实零页(第 2 页)被跳过 -> 报「跳过(回填页/短锚): 1」",
          "跳过(回填页/短锚): 1" in so, so.strip())
    check("⑰ 零页的热区**一格未动**(留在原坐标)", links(tgt2, 1) == p2_before,
          "%s vs %s" % (links(tgt2, 1), p2_before))
    b0 = links(tgt2, 0)[0]
    check("⑰ 非零页(第 1 页)照常重定位(热区宽 %.2f > 8)" % b0.width,
          b0.width > 8.0 and abs(b0.x0 - 200) < 0.6,
          "x=[%.2f,%.2f] w=%.2f" % (b0.x0, b0.x1, b0.width))

    print("\nrelink_pages 热区重定位单元测试: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
