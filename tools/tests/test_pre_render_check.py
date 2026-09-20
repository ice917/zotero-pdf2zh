# -*- coding: utf-8 -*-
"""渲染前预检(pre_render_check)单元测试

被锁死的东西: 「文献区禁汉化」这道闸门**能不能在出 PDF 之前判死**。

背景(2026-09-19): 这道判据原本只在 post_check 里, 而 post_check 要拿渲染出来的
mono PDF 才跑得动 —— 于是"文献区被汉化"只能在出 PDF 之后才发现, 得删掉重出。
13 篇跨类型质检里 5 篇 FAIL 全属此类。pre_render_check 把它前移到 render 之前,
判据**复用 post_check.check_ref_zh**(同一段代码), 只换文本来源:
  渲染后  pypdf 从 mono PDF 逐页抽文本
  渲染前  imported.json 的 "页码#段序" 键按页拼回

本测试测三件事:
  ① imported.json → {页码: 译文} 的解析(键格式 / 段序 / 缺页)
  ② 判据本身: 编号制与作者-年份制各走一遍 PASS 与 FAIL
  ③ **同源**: 同一份内容, 走 post_check.run_checks 与走本工具, 文献区结论必须一致
     —— 这是前移成立的前提; 两边一旦漂移, 就退化成"翻前放行、翻后判死"

运行: venv python test_pre_render_check.py, 退出码 0=全过
不触网、不读真 PDF: 与 test_post_check.py 同约定, 用合成文本/特征直接调函数。
"""
import json
import os
import shutil
import sys
import tempfile

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import post_check as PC          # noqa: E402
import pre_render_check as PR    # noqa: E402


# ---------------------------------------------------------------- 素材
def numbered(n=15, zh=False):
    """编号制文献页文本(行首 [n])。zh=True 即"被汉化"的样子。"""
    if zh:
        return "\n".join(
            "[%d] 作者%d。论文标题。期刊名称，20%02d，1(2):3-4。" % (i, i, i)
            for i in range(1, n + 1))
    return "\n".join(
        "[%d] Author %d. Some title of the paper. Journal Name, 20%02d, 1(2):3-4."
        % (i, i, i) for i in range(1, n + 1))


def ay(n=7, zh=False):
    """作者-年份制文献页文本(无行首 [n], 形如 "R. Olfati-Saber and ... (2004).")。"""
    if zh:
        return "\n".join(
            "R. 奥法蒂%d and M. 默里%d (20%02d). 第 %d 篇关于一致性问题的论文标题。"
            "IEEE 自动控制汇刊, %d(9):1520-1533." % (i, i, 10 + i, i, 40 + i)
            for i in range(1, n + 1))
    return "\n".join(
        "R. Olfati%d and M. Murray%d (20%02d). Title of the %dth paper on consensus. "
        "IEEE TAC, %d(9):1520-1533." % (i, i, 10 + i, i, 40 + i)
        for i in range(1, n + 1))


def ofeat(pages_text):
    """原文侧特征: 与 post_check.page_features 同路 —— 它 = 抽文本 + text_features,
    所以这里直接喂 text_features 就等价于"从 PDF 抽出来的那页"。"""
    return [PC.text_features(t, i + 1) for i, t in enumerate(pages_text)]


def ref_finding(findings):
    return [f for f in findings if "文献区" in f[2]]


def raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


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

    # ---------- ① imported.json → {页码: 译文} ----------
    tmp = tempfile.mkdtemp(prefix="p2z_prerender_")
    try:
        p = os.path.join(tmp, "x.imported.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"3#1": "第二段", "3#0": "第一段", "1#0": "开篇",
                       "乱七八糟": "忽略我", "2#x": "也忽略"}, f,
                      ensure_ascii=False)

        pages = PR.load_trans_pages(p)
        check("① 键 '3#1' 归到第 3 页", set(pages) == {1, 3}, sorted(pages))
        check("① 同页按段序拼回(输入乱序也稳)", pages[3] == "第一段\n第二段", repr(pages[3]))
        check("① 非 '页码#段序' 的键被忽略",
              all("忽略" not in v for v in pages.values()), pages)
        check("① 单段页照常", pages[1] == "开篇", repr(pages[1]))

        p2 = os.path.join(tmp, "empty.json")
        with open(p2, "w", encoding="utf-8") as f:
            f.write("{}")
        check("① 空 json 不炸", PR.load_trans_pages(p2) == {})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------- ② trans_features 对齐 ----------
    pages = {1: "第一页译文", 3: "第三页译文"}
    tf = PR.trans_features(pages, 4)
    check("② 与原文页数等长", len(tf) == 4, len(tf))
    check("② 页码字段 1 基", [t["page"] for t in tf] == [1, 2, 3, 4], [t["page"] for t in tf])
    check("② 缺页补空(不报错、不计汉化)",
          tf[1]["chars"] == 0 and tf[1]["ref_zh_blocks"] == 0, tf[1])

    # ---------- ③ 判据: 编号制 ----------
    o = ofeat([numbered()])
    fs, bad = PR.check_pages(o, {1: numbered()})
    check("③ 编号制·条目保持英文 → 通过", not bad and len(fs) == 1
          and fs[0][0] == "通过", fs)
    fs, bad = PR.check_pages(o, {1: numbered(zh=True)})
    check("③ 编号制·条目被汉化 → 高危 FAIL", bool(bad) and bad[0][0] == "高", fs)
    check("③ FAIL 结论带页码与条数",
          bool(bad) and "第1页" in bad[0][1] and "15条" in bad[0][2], bad)

    # ---------- ③b 判据: 作者-年份制(无行首 [n]) ----------
    oa = ofeat([ay()])
    check("③b 原文侧认得出作者-年份制文献页",
          PC.is_ref_page(oa[0]), oa[0]["ref_ay_density"])
    check("③b 原文侧无行首 [n]（否则这条测的不是 AY 臂）",
          oa[0]["ref_entries"] == 0, oa[0]["ref_entries"])
    fs, bad = PR.check_pages(oa, {1: ay()})
    check("③b 作者-年份制·条目保持英文 → 通过", not bad, fs)
    fs, bad = PR.check_pages(oa, {1: ay(zh=True)})
    check("③b 作者-年份制·条目被汉化 → 高危 FAIL", bool(bad), fs)

    # ---------- ③c 非文献页不参与 ----------
    body = ("本文研究带通信时滞的多智能体系统一致性问题。"
            "数值仿真表明所提机制在保持一致性的同时保护了初始历史。" * 20)
    ob = ofeat([body])
    check("③c 正文页不被判为文献页", not PC.is_ref_page(ob[0]), ob[0]["ref_density"])
    fs, bad = PR.check_pages(ob, {1: body})
    check("③c 正文页全中文也不产生文献区结论", not ref_finding(fs), fs)

    # ---------- ③d 被跳页的文献页(无译文)不误判 ----------
    fs, bad = PR.check_pages(o, {})
    check("③d 文献页无译文(跳页) → 不判汉化", not bad, fs)

    # ---------- ④ 同源: 与 post_check.run_checks 结论一致 ----------
    # 这是前移成立的前提 —— 同一份内容两边必须同判。
    def parity(orig_feats, text):
        """渲染前预检与渲染后门禁是否同判。返回两边的高危判定(相等才合格)。"""
        pre = [f for f in PR.check_pages(orig_feats, {1: text})[0] if f[0] in ("高", "中")]
        post = [f for f in PC.run_checks(orig_feats, [PC.text_features(text, 1)])[0]
                if f[0] in ("高", "中")]
        return bool(pre), bool(post)

    a, b = parity(o, numbered())
    check("④ 同源·编号制良好 → 两边都 PASS", a is False and b is False, (a, b))
    a, b = parity(o, numbered(zh=True))
    check("④ 同源·编号制汉化 → 两边都 FAIL", a is True and b is True, (a, b))
    a, b = parity(oa, ay())
    check("④ 同源·作者-年份制良好 → 两边都 PASS", a is False and b is False, (a, b))
    a, b = parity(oa, ay(zh=True))
    check("④ 同源·作者-年份制汉化 → 两边都 FAIL", a is True and b is True, (a, b))

    # ---------- ⑤ CLI 兜底 ----------
    tmp5 = tempfile.mkdtemp(prefix="p2z_prerender_cli_")
    try:
        check("⑤ 文件不存在 → 返回 1 不崩",
              _cli_exit(tmp5, "--original", os.path.join(tmp5, "nope.pdf"),
                        "--imported", os.path.join(tmp5, "nope.json")) == 1)
        check("⑤ 参数缺失 → 返回 2(argparse)", _cli_exit(tmp5) != 0)
    finally:
        shutil.rmtree(tmp5, ignore_errors=True)

    print(f"\n结果: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


def _cli_exit(cwd, *argv):
    """跑一次真 CLI(子进程), 只取退出码 —— 不碰真项目任何文件。"""
    import subprocess
    cmd = [sys.executable, os.path.join(TOOLS, "pre_render_check.py")] + list(argv)
    return subprocess.run(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


if __name__ == "__main__":
    sys.exit(main())
