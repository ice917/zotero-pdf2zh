# -*- coding: utf-8 -*-
"""dual_links 双语版双侧链接单元测试 (v28.64, 2026-09-21)

被锁死的缺陷 —— dual 成品里两侧都点不动:
  * 译文侧**一条引用链接都没有**(除少数 URI) —— 点了完全没反应;
  * 原版侧 145 条全是 **NAMED**(依赖命名目标树), Edge 等简易阅读器点不动。
  而 relink_pages / resolve_links 都假设"成品与原版 1:1", 对 dual 直接
  `FAIL 页数不一致`, 故 dual 需要独立的 dual_links。三条口径(用户拍板):
    译文页点引用 -> **跳译文侧**; 原版页 -> 跳原版侧(顺其原语义); 两侧不跨侧。

本套件锁住四条工程不变量(没锁住就会静默回归):
  * **落点映射 2d+1**: 译文侧链接从 mono 搬, 落点页 d -> 2d+1(dual 坐标系),
    跨侧 0 —— "点中文页跳到英文页"页码完全合法, 只有这条能抓。
  * **原版侧 NAMED 全转 GOTO**: 目标页取 dual **自己的**命名树(已是 dual 坐标),
    y 必须从 PDF 底部原点转 fitz 顶部原点 —— 不转则落点整体镜像, 但页号仍合法。
  * **译文页原有 URI 必须活下来**: 译文侧是"清空重建", 只重建会把它抹掉。
  * **同源门禁**: mono 与 dual 译文侧必须同源(dual 每个字符在 mono 里至少同样多),
    不满足 -> FAIL 不猜、文件不动 —— 搬错的坐标比没链接更坏。

判定手法 (全部合成, 不依赖真实论文):
  造 orig(3 页, 带命名目标树) / mono(3 页, 已 relink 的 GOTO) /
  dual(6 页 = [原版页, 译文页] 交错, 原版页带 NAMED + 自己的命名树, 译文页带 URI)。
  合成命名树有一处非显然的坑: 目标数组里的页引用必须是**间接引用**(`%d 0 R`)——
  写裸数字时 reader 解析出的页号是错的(实测 2 页文档解出 page 6), 且不报错。

运行: venv python test_dual_links.py, 退出码 0=全过
"""
import hashlib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
DUAL_LINKS = os.path.join(TOOLS, "dual_links.py")
VERIFY_LINKS = os.path.join(TOOLS, "verify_links.py")

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

import pymupdf  # noqa: E402

PAGE = (612, 792)
ORIG_LINES = ["Original page one", "Original page two", "Original page three"]
NAMED = ["cite two", "cite one", "cite one"]      # 原版各页挂的命名链接
ORIG_TGT = {"cite one": (0, 700.0), "cite two": (2, 400.0)}   # 名 -> (0 基目标页, PDF 底部原点 y)
MONO_TGT = [2, 0, 0]                              # mono 各页 GOTO 的落点(0 基) —— 与原版同名目标一致
ANCHOR = ["[1]", "[2]", "[3]"]
TR = ["Trans page one %s body" % ANCHOR[0],
      "Trans page two %s body" % ANCHOR[1],
      "Trans page three %s body" % ANCHOR[2]]                       # 译文页文本(dual 侧)
MONO_TR = [TR[i].replace(ANCHOR[i], ANCHOR[i] + " " + ANCHOR[i])
           for i in range(3)]                                       # mono 侧: 锚文本被叠绘 -> 只多不少
URI0 = "https://example.org/ref/%d"


def set_names(doc, mapping):
    """建命名目标树 /Root/Names << /Dests << /Names [(名) [页引用 /XYZ x y z]] >> >>。

    页引用写成**间接引用** `%d 0 R` 而非裸数字 —— 裸数字 reader 会解出错页号
    (实测 2 页文档里 `[%d /XYZ ...]` 解出 page=6), 而且不报任何错。
    """
    leaf = doc.get_new_xref()
    items = " ".join("(%s) [%d 0 R /XYZ 0 %g 0]" % (nm, doc[pg].xref, y)
                     for nm, (pg, y) in mapping.items())
    doc.update_object(leaf, "<< /Names [%s] >>" % items)
    tree = doc.get_new_xref()
    doc.update_object(tree, "<< /Dests %d 0 R >>" % leaf)
    doc.xref_set_key(doc.pdf_catalog(), "Names", "%d 0 R" % tree)


def make_orig(path):
    """3 页原版: 每页一条 NAMED 链接 + 自己的命名目标树。"""
    doc = pymupdf.open()
    for i, t in enumerate(ORIG_LINES):
        p = doc.new_page(width=PAGE[0], height=PAGE[1])
        p.insert_text((72, 100), t, fontsize=12)
        p.insert_text((72, 120), "See %s here" % NAMED[i], fontsize=10)
        p.insert_link({"kind": pymupdf.LINK_NAMED, "from": pymupdf.Rect(72, 112, 200, 126),
                       "nameddest": NAMED[i]})
    set_names(doc, ORIG_TGT)
    doc.save(path)
    doc.close()
    return path


def make_mono(path, tr=None):
    """3 页 mono: 已 relink 的 GOTO(落点 MONO_TGT), 锚文本被叠绘(只多不少)。"""
    tr = tr if tr is not None else MONO_TR
    doc = pymupdf.open()
    for k in range(3):                       # 先把页建全: GOTO 落点页必须先存在
        doc.new_page(width=PAGE[0], height=PAGE[1])
    for k in range(3):
        p = doc[k]
        p.insert_text((72, 100), tr[k], fontsize=12)
        p.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 90, 240, 104),
                       "page": MONO_TGT[k], "to": pymupdf.Point(36, 40)})
    doc.save(path)
    doc.close()
    return path


def make_dual(path):
    """6 页 dual: 偶数页 = 原版侧(NAMED + dual 自己的命名树), 奇数页 = 译文侧(仅 1 条 URI)。"""
    doc = pymupdf.open()
    for k in range(3):
        po = doc.new_page(width=PAGE[0], height=PAGE[1])
        po.insert_text((72, 100), ORIG_LINES[k], fontsize=12)
        po.insert_text((72, 120), "See %s here" % NAMED[k], fontsize=10)
        po.insert_link({"kind": pymupdf.LINK_NAMED, "from": pymupdf.Rect(72, 112, 200, 126),
                        "nameddest": NAMED[k]})
        pt = doc.new_page(width=PAGE[0], height=PAGE[1])
        pt.insert_text((72, 100), TR[k], fontsize=12)
        pt.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 240, 104),
                        "uri": URI0 % k})
    # dual 的命名树用 **dual 坐标**: 原版第 p 页 -> dual 偶数页 2p
    set_names(doc, {nm: (2 * pg, y) for nm, (pg, y) in ORIG_TGT.items()})
    doc.save(path)
    doc.close()
    return path


def run(*args):
    proc = subprocess.run([sys.executable] + list(args), capture_output=True)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def links(path, pno):
    d = pymupdf.open(path)
    out = []
    for l in d[pno].get_links():
        out.append({"kind": l["kind"], "page": l.get("page"),
                    "y": l["to"].y if l.get("to") is not None else None,
                    "from": pymupdf.Rect(l["from"]), "uri": l.get("uri"),
                    "nameddest": l.get("nameddest")})
    d.close()
    return out


def md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def main():
    tmp = tempfile.mkdtemp(prefix="pdf2zh_test_dual_")
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    orig = make_orig(os.path.join(tmp, "orig.pdf"))
    mono = make_mono(os.path.join(tmp, "mono.pdf"))
    dual = make_dual(os.path.join(tmp, "dual.pdf"))
    pristine = os.path.join(tmp, "dual_pristine.pdf")
    with open(dual, "rb") as f, open(pristine, "wb") as g:      # dual_links 之前的样子(留作对照)
        g.write(f.read())
    mono_src = [links(mono, k) for k in range(3)]

    # ---- ⓪ 前置: 合成件本身符合预期(否则下面全是空中楼阁) ----
    check("⓪ 原版侧 3 页各 1 条 NAMED", [len([x for x in links(orig, k)
           if x["kind"] == pymupdf.LINK_NAMED]) for k in range(3)] == [1, 1, 1])
    d = pymupdf.open(dual)
    nm = d.resolve_names()
    d.close()
    check("⓪ dual 命名树可读且用 dual 坐标(cite one->p1, cite two->p5)",
          nm.get("cite one", {}).get("page") == 0 and nm.get("cite two", {}).get("page") == 4, repr(nm))
    check("⓪ 修复前: 译文侧 0 条 GOTO / 原版侧 3 条 NAMED",
          all(len([x for x in links(pristine, p) if x["kind"] == pymupdf.LINK_GOTO]) == 0
              for p in (1, 3, 5))
          and sum(len([x for x in links(pristine, p) if x["kind"] == pymupdf.LINK_NAMED])
                  for p in (0, 2, 4)) == 3)

    # ---- ① 跑 dual_links: 两侧都补上, 且不跨侧 ----
    rc, so, se = run(DUAL_LINKS, "--dual", dual, "--mono", mono)
    check("① 退出码 0", rc == 0, "%d %s" % (rc, se[-300:]))
    check("① 报「译文侧补链接 6 条(3 GOTO + 3 URI) / 原版侧 NAMED->GOTO 3 条 / 跨侧落点 0」",
          "译文侧补链接 6 条" in so and "原版侧 NAMED->GOTO 3 条" in so and "跨侧落点 0" in so,
          so.strip())

    # ---- ② 译文侧: 落点映射 d -> 2d+1, 且热区坐标原样照搬 mono ----
    exp_goto = {1: (5, 0), 3: (1, 1), 5: (1, 2)}     # dual 页 -> (落点页, mono 源页)
    ok = True
    for dp, (tpage, k) in exp_goto.items():
        ls = [x for x in links(dual, dp) if x["kind"] == pymupdf.LINK_GOTO]
        src = [x for x in mono_src[k] if x["kind"] == pymupdf.LINK_GOTO]
        if not (len(ls) == len(src) == 1 and ls[0]["page"] == tpage and ls[0]["from"] == src[0]["from"]):
            ok = False
            print("     p%d -> %s (期望 落点 p%d + 同 rect)" % (dp + 1, ls, tpage + 1))
    check("② 译文侧 3 页各 1 条 GOTO, 落点是 2d+1 且 rect 与 mono 逐条相同", ok)

    # ---- ③ 原版侧: NAMED 全转 GOTO, 目标页用 dual 命名树, y 从底部原点转顶部原点 ----
    named_left = sum(len([x for x in links(dual, p) if x["kind"] == pymupdf.LINK_NAMED])
                     for p in range(6))
    check("③ 原版侧 NAMED 残留 0", named_left == 0, "残留 %d" % named_left)
    exp_named = {0: (4, 792 - 400.0), 2: (0, 792 - 700.0), 4: (0, 792 - 700.0)}
    ok = True
    for dp, (tpage, ny) in exp_named.items():
        ls = [x for x in links(dual, dp) if x["kind"] == pymupdf.LINK_GOTO]
        if not (len(ls) == 1 and ls[0]["page"] == tpage and abs(ls[0]["y"] - ny) < 0.6):
            ok = False
            print("     p%d -> %s (期望 落点 p%d y=%.1f)" % (dp + 1, ls, tpage + 1, ny))
    check("③ 原版侧 3 条 GOTO 落点页正确, y 未镜像(底部原点 -> fitz 顶部原点)", ok)

    # ---- ④ 译文页原有 URI 必须活下来(译文侧是清空重建) ----
    uris = [x["uri"] for p in (1, 3, 5) for x in links(dual, p) if x["kind"] == pymupdf.LINK_URI]
    check("④ 译文页原有 3 条 URI 全在(清空重建没抹掉)",
          sorted(uris) == sorted(URI0 % k for k in range(3)), repr(uris))

    # ---- ⑤ 落点不跨侧: 第 p 页的 GOTO 落点页与 p 同奇偶 ----
    cross = [(p + 1, x["page"] + 1) for p in range(6) for x in links(dual, p)
             if x["kind"] == pymupdf.LINK_GOTO and (x["page"] % 2) != (p % 2)]
    check("⑤ 跨侧落点 0", not cross, repr(cross))

    # ---- ⑥ 同源门禁: mono 不是这一版 dual 的 mono -> FAIL 且文件不动 ----
    other = make_mono(os.path.join(tmp, "mono_other.pdf"),
                      tr=["Zzz one %s" % ANCHOR[0], "Zzz two %s" % ANCHOR[1], "Zzz three %s" % ANCHOR[2]])
    before = md5(dual)
    rc, so, se = run(DUAL_LINKS, "--dual", dual, "--mono", other)
    check("⑥ 不同源 -> 退出码 1 且报「不同源」", rc == 1 and "不同源" in so, "%d %s" % (rc, so.strip()[-200:]))
    check("⑥ 门禁拦下时文件未被改动", md5(dual) == before)

    # ---- ⑦ 页数门禁: dual 页数 ≠ mono × 2 -> FAIL ----
    short = os.path.join(tmp, "mono_short.pdf")
    doc = pymupdf.open(mono)
    doc.delete_page(2)
    doc.save(short)
    doc.close()
    rc, so, se = run(DUAL_LINKS, "--dual", dual, "--mono", short)
    check("⑦ 页数不成对 -> 退出码 1 且报「页数」", rc == 1 and "页数" in so, "%d %s" % (rc, so.strip()[-200:]))

    # ---- ⑧ 幂等: 再跑一次结果不变 ----
    per_before = [len(links(dual, p)) for p in range(6)]
    rc, so, se = run(DUAL_LINKS, "--dual", dual, "--mono", mono)
    per_after = [len(links(dual, p)) for p in range(6)]
    check("⑧ 幂等: 再跑退出码 0 且逐页条数不变", rc == 0 and per_before == per_after,
          "%d %s -> %s" % (rc, per_before, per_after))

    # ---- ⑨ verify_links 第三条判据: 好 dual 全绿 ----
    rc, so, se = run(VERIFY_LINKS, "--target", dual, "--original", orig)
    check("⑨ 落点页一致性 3/3 命中 0 失配", "可校验 3 | 命中 3 | 失配 0" in so, so.strip()[-300:])
    check("⑨ 落点侧不串: 跨侧 0 | 残留 NAMED 0 | 两侧条数不等 0 页",
          "跨侧 0 | 残留 NAMED 0 | 两侧条数不等 0 页" in so, so.strip()[-300:])

    # ---- ⑩ verify_links 抓得住: 未修 / 串侧 ----
    rc, so, se = run(VERIFY_LINKS, "--target", pristine, "--original", orig)
    check("⑩ 未跑 dual_links 的 dual 被点名「残留 NAMED 3」", "残留 NAMED 3" in so, so.strip()[-300:])
    badf = os.path.join(tmp, "dual_cross.pdf")
    with open(dual, "rb") as f, open(badf, "wb") as g:
        g.write(f.read())
    d = pymupdf.open(badf)
    d[3].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(400, 400, 500, 420),
                      "page": 0, "to": pymupdf.Point(36, 40)})
    d.save(badf + ".t", garbage=3, deflate=True)
    d.close()
    os.replace(badf + ".t", badf)
    rc, so, se = run(VERIFY_LINKS, "--target", badf, "--original", orig)
    check("⑩ 人为造一条串侧 -> 报「跨侧 1」", "跨侧 1 " in so, so.strip()[-300:])

    print("\ndual_links 双语版双侧链接单元测试: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
