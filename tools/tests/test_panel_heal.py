# -*- coding: utf-8 -*-
"""面板结构自愈测试 (v28.78, 2026-09-23)

要解决的**用户侧**问题: 判据层早就够了(heal_render 治版面伤 / relink_pages 治链接热区),
但只有 CLI —— 用户够不着, 出问题只能找维护者。本件锁死"把入口搬进面板"的两条纪律:

  ① **透明**(用户钉的准入红线, relay_spec 9.6): 只动结构层(版面/链接热区), 不碰译文
     一个字; 每次都出报告(治了哪页、按哪条判据);
  ② **面板不重写判据**: 治什么、怎么治、什么算"没治好"全在工具里 —— 面板只做三件事:
     路径守卫(成品必须落在自动探测候选里, 与概念链接同一道门)/ 组参数 / 一字不改转述。

全部离线: 候选 / 原版 / 侧车 / 子进程全部注入, 不跑真 heal_render、不碰真 PDF。

运行: venv python test_panel_heal.py, 退出码 0=全过
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
TABLE_PIPE = os.path.join(TOOLS, "table_pipe")
for p in (TOOLS, TABLE_PIPE):
    if p not in sys.path:
        sys.path.insert(0, p)

# 环境必须在 import 之前落定(panel 在**模块层**读这些变量); 顺手摘掉密钥 —— 测试绝不打真接口。
_TMP = tempfile.mkdtemp(prefix="p2z_heal_")
os.environ["P2Z_PROJ"] = _TMP
os.environ["P2Z_TABLE_DIR"] = os.path.join(_TMP, "work")
os.environ["P2Z_INBOX"] = os.path.join(_TMP, "inbox")
os.environ["P2Z_BODY_NAME"] = "payload_demo"
os.environ["P2Z_BODY_PDF"] = os.path.join(_TMP, "demo.pdf")
os.environ["P2Z_REVIEW_DIR"] = os.path.join(_TMP, "review")
os.environ.pop("SILICON_API_KEY", None)
os.environ.pop("SILICON_MODEL", None)
os.environ.pop("SILICON_BASE_URL", None)

import panel as PN        # noqa: E402


class FakeRunner(object):
    """假子进程: 记下拿到的参数, 按脚本返回 (rc, out); 可选把报告写到 --report 指的路径。"""

    def __init__(self, rc=0, out="", report=None, touch_report=False):
        self.rc, self.out, self.report = rc, out, report
        self.touch = touch_report
        self.calls = []

    def __call__(self, *args):
        self.calls.append(list(args))
        if self.touch:
            i = list(args).index("--report")
            with open(args[i + 1], "w", encoding="utf-8", newline="") as f:
                f.write(self.report or "")
        return self.rc, self.out

    @property
    def last(self):
        return self.calls[-1]


def pdfs_of(*kinds):
    """按 kind 造候选; path 落在临时目录里(heal_report_path 只拼字符串, 不需文件真在)。"""
    m = {"mono": "-mono.pdf", "dual": "-dual.pdf"}
    return [{"kind": k, "name": "demo" + m[k], "path": os.path.join(_TMP, "demo" + m[k])}
            for k in kinds]


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

    with tempfile.TemporaryDirectory(prefix="p2z_heal_ori_") as td:
        ori = os.path.join(td, "原版.pdf")
        with open(ori, "wb") as f:
            f.write(b"%PDF-1.4 stub")
        mono, dual = pdfs_of("mono"), pdfs_of("dual")

        # ---- ① 入口守卫: 只在"这份成品、这个工具"上干活
        r = PN.heal_run("rm -rf", mono[0]["path"], pdfs=mono, original=ori)
        check("① 不认识的工具 -> error 且列出可用工具",
              "error" in r and "不认识的自愈工具" in r["error"] and "heal_render.py" in r["error"],
              r)
        r = PN.heal_run("heal_render.py", None, pdfs=[], original=ori)
        check("① 没有候选成品 -> error 说先出稿",
              "error" in r and "先出稿" in r["error"], r)
        r = PN.heal_run("heal_render.py", "/别处/x-mono.pdf", pdfs=mono, original=ori)
        check("① 成品不在候选里 -> error(会改 PDF 的工具不许指哪儿改哪儿)",
              "error" in r and "不在可治的那几份里" in r["error"], r)
        r = PN.heal_run("heal_render.py", mono[0]["path"], pdfs=mono,
                        original=os.path.join(td, "没有这个原版.pdf"))
        check("① 缺原版 -> error(没对照治不了结构伤)",
              "error" in r and "找不到原版 PDF" in r["error"], r)
        r = PN.heal_run("relink_pages.py", mono[0]["path"], dry=True, pdfs=mono, original=ori)
        check("① relink 没有只看模式 -> error",
              "error" in r and "没有只看模式" in r["error"], r)

        # ---- ② 组参数: --target/--original/--report 由面板补齐, 报告落成品旁边
        fr = FakeRunner(rc=0, out="HEAL OK 第 7 页 旋转文本")
        r = PN.heal_run("heal_render.py", mono[0]["path"], runner=fr, pdfs=mono, original=ori)
        a = fr.last
        check("② heal_render 补齐 --target/--original/--report",
              a[0] == "heal_render.py" and "--target" in a and "--original" in a
              and "--report" in a, a)
        check("② mono 成品不带 --dual、不带 --dry-run",
              "--dual" not in a and "--dry-run" not in a, a)
        check("② 工具 stdout+stderr 原样透传(不描补不吞错)",
              r["out"] == "HEAL OK 第 7 页 旋转文本" and r["rc"] == 0 and "error" not in r, r)
        check("② 报告落成品旁边: <stem>.heal.自愈报告.txt",
              r["report_path"] == os.path.splitext(mono[0]["path"])[0] + ".heal.自愈报告.txt"
              and a[a.index("--report") + 1] == r["report_path"], (r["report_path"], a))

        fr = FakeRunner(rc=0, out="ok")
        PN.heal_run("heal_render.py", dual[0]["path"], runner=fr, pdfs=dual, original=ori)
        check("② dual 成品补 --dual(页码落译文侧)", "--dual" in fr.last, fr.last)

        # ---- ③ dry: 只看伤情(退出码 1 = 有伤未治, 是"查出伤"不是失败)
        fr = FakeRunner(rc=1, out="dry: 发现 3 页旋转文本未治")
        r = PN.heal_run("heal_render.py", mono[0]["path"], dry=True, runner=fr,
                        pdfs=mono, original=ori)
        check("③ dry 补 --dry-run", "--dry-run" in fr.last, fr.last)
        check("③ dry 退出码 1 -> 不算 error(如实报 rc, 交给前端判有伤)",
              "error" not in r and r["rc"] == 1 and r["dry"] is True, r)

        # ---- ④ relink: 侧车拿得到就传, 拿不到不传
        saved = PN.ul_sidecar
        PN.ul_sidecar = lambda: os.path.join(td, "demo.sidecar.json")
        try:
            fr = FakeRunner(rc=0, out="relink ok")
            r = PN.heal_run("relink_pages.py", mono[0]["path"], runner=fr, pdfs=mono, original=ori)
            check("④ 有侧车 -> 传 --sidecar(重搜锚文本靠它)",
                  "--sidecar" in fr.last
                  and fr.last[fr.last.index("--sidecar") + 1].endswith("demo.sidecar.json"), fr.last)
        finally:
            PN.ul_sidecar = saved
        fr = FakeRunner(rc=0, out="relink ok")
        r = PN.heal_run("relink_pages.py", mono[0]["path"], runner=fr, pdfs=mono, original=ori)
        check("④ 没侧车 -> 不带 --sidecar(不编造路径)",
              "--sidecar" not in fr.last and "--dry-run" not in fr.last, fr.last)
        check("④ 报告命名区分两件工具: <stem>.relink.自愈报告.txt",
              r["report_path"] == os.path.splitext(mono[0]["path"])[0] + ".relink.自愈报告.txt",
              r["report_path"])

        # ---- ⑤ 报告一字不改读回; 报告没落成也不崩
        body = "治伤报告\n第 7 页: 旋转文本已转正\n按判据: 版面伤-旋转\n未治: 无\n"
        fr = FakeRunner(rc=0, out="done", report=body, touch_report=True)
        r = PN.heal_run("heal_render.py", mono[0]["path"], runner=fr, pdfs=mono, original=ori)
        check("⑤ 报告读回逐字节一致(面板不重写判据)",
              r["report"] == body, repr(r["report"]))
        # 换一份没被上个用例写过报告的成品(报告名由成品名决定, 同一份会读到上一轮的残留)
        solo = [{"kind": "mono", "name": "solo-mono.pdf",
                 "path": os.path.join(td, "solo-mono.pdf")}]
        fr = FakeRunner(rc=0, out="done", touch_report=False)   # 工具没写报告
        r = PN.heal_run("heal_render.py", solo[0]["path"], runner=fr, pdfs=solo, original=ori)
        check("⑤ 报告文件缺失 -> report 为空串, 不抛(只读兜底)",
              r["report"] == "" and r["rc"] == 0 and "error" not in r, r)

        # ---- ⑥ 面板接线 + 透明红线写在代码里
        with open(PN.__file__, encoding="utf-8") as f:
            PNSRC = f.read()
        check("⑥ 后端挂上 /api/heal + heal_run + _heal",
              '"/api/heal"' in PNSRC and "def heal_run" in PNSRC and "def _heal" in PNSRC, "")
        check("⑥ 工具白名单写死 HEAL_TOOLS(两件成品级自愈)",
              "HEAL_TOOLS" in PNSRC and "heal_render.py" in PNSRC and "relink_pages.py" in PNSRC, "")
        check("⑥ 成品级两件进面板, 缓存/引擎级(dedouble/force)本期不塞",
              "dedouble_sweep" not in PNSRC.split("HEAL_TOOLS = (")[1].split(")")[0]
              and "force_rerender" not in PNSRC.split("HEAL_TOOLS = (")[1].split(")")[0], "")
        check("⑥ 前端: 卡片 + 下拉 + 三按钮",
              'id="healCard"' in PN.PAGE and 'id="healPdfSel"' in PN.PAGE
              and all(i in PN.PAGE for i in ("healScanBtn", "healRenderBtn", "healRelinkBtn")), "")
        check("⑥ 前端三动线文案齐(只看伤情/治版面伤/治链接伤)",
              "只看伤情" in PN.PAGE and "治版面伤" in PN.PAGE and "治链接伤" in PN.PAGE, "")
        check("⑥ 前端调 /api/heal 并如实转述输出与报告",
              "api('/api/heal'" in PN.PAGE and "r.report" in PN.PAGE and "r.out" in PN.PAGE, "")
        check("⑥ 透明红线写在代码里(不碰译文一个字)",
              "不碰译文一个字" in PNSRC and "不碰译文一个字" in PN.PAGE, "")
        check("⑥ 报告落成品旁边(审计轨迹跟产物走)",
              "自愈报告.txt" in PNSRC and "heal_report_path" in PNSRC, "")

    print(f"\n结构自愈测试: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
