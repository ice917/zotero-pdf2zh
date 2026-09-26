# -*- coding: utf-8 -*-
"""面板概念链接写端点的「陈旧下标」护栏 (v36.3, 2026-09-26)

**事故**: 2026-09-26 09:08:23 真规格 user_links.json 被清成空模板, 里面唯一一条真
条目没了(靠 09:00 的快照捞回)。机制已在 v36.3 定死: `_ul_del`/`_ul_edit` 只认下标
(idx), **不认身份**; 而概念链接区的清单只在加载与 ulAbsorb() 时刷新 —— 页面开着不动
清单就冻着。冻着的旧清单 + 盘上已变 => "删第 0 行"删掉的是**盘上**第 0 行, 也就是
点击者从没看见过的那一条。静默, 不可逆。(那一下是谁点的当时无留痕, 不可考 ——
正是"无留痕"本身断了可考性, 所以 v36.2 先补了留痕。)

**本套件锁死 v36.3 的补救**: 前端把它那一行的 (anchor,url) 一起回显, 后端在下标之外
**先认身份**; 对不上就拒绝, 且盘上一个字都不动、也不留痕(盘没动就没有"改动"可记)。
失败方向永远是"不动盘" —— 拒绝只是让用户刷一下, 猜错就是丢资产, 两者不对称。

**为什么直调 `H._ul_del`/`H._ul_edit`, 而不是起服务器**: 这两个端点除
`self._json`/`self._ul_state`/`self._ul_entry` 之外只碰模块级函数与**真文件**,
用替身接住回包就完成了**含真实落盘的**端到端 —— 不起进程、不占端口、不依赖时序,
也不碰真规格(P2Z_PROJ 指向临时目录, UL_SPEC 随之落到那里)。

[v36.4] 加了第 ⑩ 节: 「装入」(`H._ul_apply`)的两条去向。同一套替身直接可用 ——
`_ul_target` 也是"只用 b + 模块级 ul_pdfs + self._json"的真方法。`ul_run` 换成记录器
(真跑是两趟子进程), 而那份 PDF 是**真文件**, 所以"改的是哪一份"验得住。

运行: venv python test_panel_ulguard.py, 退出码 0=全过
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
TABLE_PIPE = os.path.join(TOOLS, "table_pipe")
for p in (TOOLS, TABLE_PIPE):
    if p not in sys.path:
        sys.path.insert(0, p)

# 环境必须在 import 之前落定: wc.PROJ / UL_SPEC 都是模块层读的
_TMP = tempfile.mkdtemp(prefix="p2z_ulguard_")
for k, v in (("P2Z_PROJ", _TMP), ("P2Z_TABLE_DIR", _TMP),
             ("P2Z_INBOX", os.path.join(_TMP, "inbox")),
             ("P2Z_BODY_NAME", "payload_ulguard"),
             ("P2Z_BODY_PDF", os.path.join(_TMP, "demo.pdf")),
             ("P2Z_PANEL_NO_OPEN", "1")):
    os.environ[k] = v

import panel as PN      # noqa: E402

TASK = PN.ul_task()
A = {"anchor": "甲锚", "url": "https://a.example/1", "page": 4}
B = {"anchor": "乙锚", "url": "https://b.example/2", "page": 5}


class Stub:
    """只接住回包的替身 —— 端点真正干的活(读盘/判据/落盘)一行都不替。"""

    _ul_entry = PN.H._ul_entry          # 真方法(它只用 b, 不碰 self 的别的状态)
    _ul_target = PN.H._ul_target        # 同上: 只用 b + 模块级 ul_pdfs() + self._json

    def __init__(self):
        self.out = []

    def _json(self, obj, code=200):
        self.out.append(obj)

    def _ul_state(self):
        self.out.append({"__state__": True})

    def error(self):
        r = self.out[0] if self.out else {}
        return (r or {}).get("error") if isinstance(r, dict) else None

    def touched(self):
        return any(isinstance(r, dict) and r.get("__state__") for r in self.out)


def write_spec(entries):
    os.makedirs(_TMP, exist_ok=True)
    with io.open(PN.UL_SPEC, "w", encoding="utf-8") as f:
        json.dump({"_readme": ["test"], "global": [], "tasks": {TASK: entries}},
                  f, ensure_ascii=False, indent=2)


def spec_text():
    with io.open(PN.UL_SPEC, encoding="utf-8") as f:
        return f.read()


def spec_entries():
    with io.open(PN.UL_SPEC, encoding="utf-8") as f:
        return (json.load(f).get("tasks") or {}).get(TASK) or []


def audit_lines():
    try:
        with io.open(PN.UL_AUDIT, encoding="utf-8") as f:
            return [x for x in f.read().splitlines() if x.strip()]
    except OSError:
        return []


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

    def post(fn, body):
        st = Stub()
        fn(st, body)
        return st

    # 每条用例都从"盘上是什么"重新铺一遍, 免得互相污染
    def case(entries):
        write_spec(entries)
        return spec_text()

    # ---- ① 陈旧下标: 盘上那条已经换了人, 前端还拿着旧清单 ----
    # 这一幕就是事故的形态: 另一处把 A 删了, 盘上第 0 行成了 B; 冻着的页面第 0 行仍是 A。
    before = case([B])                  # 盘上只剩 B(旧代码在这一下会**删掉 B**)
    st = post(PN.H._ul_del, {"scope": "本篇", "idx": 0,
                             "oldanchor": A["anchor"], "oldurl": A["url"]})
    check("① 陈旧下标删 -> 拒绝", bool(st.error()), f"out={st.out}")
    check("① 陈旧下标删 -> 盘上一个字没动", spec_text() == before)
    check("① 陈旧下标删 -> 不动状态(没走到 _ul_state)", not st.touched())
    check("① 陈旧下标删 -> 不留痕(盘没动就没有改动可记)", audit_lines() == [])

    # ---- ② 陈旧下标改: 同一道判据 ----
    before = case([B])
    st = post(PN.H._ul_edit, {"scope": "本篇", "oldscope": "本篇", "idx": 0,
                              "anchor": "新锚", "url": "https://new.example/9",
                              "page": 4,
                              "oldanchor": A["anchor"], "oldurl": A["url"]})
    check("② 陈旧下标改 -> 拒绝", bool(st.error()), f"out={st.out}")
    check("② 陈旧下标改 -> 盘上一个字没动", spec_text() == before)

    # ---- ③ 缺回显字段 = 旧页面(不能留一条不校验的旁路) ----
    before = case([A, B])
    st = post(PN.H._ul_del, {"scope": "本篇", "idx": 0})
    check("③ 缺 oldanchor -> 拒绝", bool(st.error()), f"out={st.out}")
    check("③ 缺 oldanchor -> 盘上一个字没动", spec_text() == before)
    check("③ 缺 oldanchor -> 报的是「刷新页面」", "刷新" in (st.error() or ""),
          f"err={st.error()}")
    st = post(PN.H._ul_edit, {"scope": "本篇", "oldscope": "本篇", "idx": 0,
                              "anchor": "新锚", "url": "https://new.example/9",
                              "page": 4})
    check("③ 缺 oldanchor(改) -> 拒绝", bool(st.error()), f"out={st.out}")
    check("③ 缺 oldanchor(改) -> 盘上一个字没动", spec_text() == before)

    # ---- ④ 判据是 (anchor,url) **全等**: 锚一样、网址对不上也拒 ----
    before = case([A])
    st = post(PN.H._ul_del, {"scope": "本篇", "idx": 0,
                             "oldanchor": A["anchor"],
                             "oldurl": "https://stale.example/x"})
    check("④ 锚一样但网址对不上 -> 拒绝(判据含 url)", bool(st.error()),
          f"out={st.out}")
    check("④ 锚一样但网址对不上 -> 盘上一个字没动", spec_text() == before)

    # ---- ⑤ 对得上就真删 —— 护栏不许把功能焊死 ----
    case([A, B])
    st = post(PN.H._ul_del, {"scope": "本篇", "idx": 1,
                             "oldanchor": B["anchor"], "oldurl": B["url"]})
    check("⑤ 回显对得上 -> 删成功", not st.error(), f"out={st.out}")
    check("⑤ 删的正是那一条(留下的还是 A)", spec_entries() == [A],
          f"左={spec_entries()}")
    check("⑤ 真删了 -> 留痕一行", len(audit_lines()) == 1, f"{audit_lines()}")

    # ---- ⑥ 对得上就真改 —— 且位置不变 ----
    case([A])
    st = post(PN.H._ul_edit, {"scope": "本篇", "oldscope": "本篇", "idx": 0,
                              "anchor": "甲锚改", "url": "https://c.example/3",
                              "page": 4,
                              "oldanchor": A["anchor"], "oldurl": A["url"]})
    check("⑥ 回显对得上 -> 改成功", not st.error(), f"out={st.out}")
    check("⑥ 改的是那一条、就地替换",
          spec_entries() == [{"anchor": "甲锚改", "url": "https://c.example/3",
                              "page": 4}], f"左={spec_entries()}")

    # ---- ⑦ 越界下标(原有行为不许丢) ----
    before = case([A])
    st = post(PN.H._ul_del, {"scope": "本篇", "idx": 5,
                             "oldanchor": A["anchor"], "oldurl": A["url"]})
    check("⑦ 越界下标 -> 拒绝", bool(st.error()), f"out={st.out}")
    check("⑦ 越界下标 -> 盘上一个字没动", spec_text() == before)

    # ---- ⑧ 判据只写一套: 两个写端点都得过这道门 ----
    src = io.open(os.path.join(TABLE_PIPE, "panel.py"), encoding="utf-8").read()
    check("⑧ 写端点都接上了 ul_echo_check", src.count("ul_echo_check(") == 3,
          f"出现 {src.count('ul_echo_check(')} 次(定义 1 + 调用 2)")
    check("⑧ _ul_add 不走这道门(它不改旧条目)",
          "ul_echo_check" not in src.split("def _ul_add")[1].split("def _ul_edit")[0])

    # ---- ⑨ 前端接线: 两个写请求都得把回显字段发出去 ----
    page = PN.PAGE
    check("⑨ 删: 请求体带上这一行的原文",
          "oldanchor:String(dit.anchor||'')" in page
          and "oldurl:String(dit.url||'')" in page)
    check("⑨ 改: 请求体带上进编辑态那一刻的原文",
          "body.oldanchor=String(ul.edit.anchor||'')" in page
          and "body.oldurl=String(ul.edit.url||'')" in page)
    check("⑨ 回显取自**同一份冻清单**(渲染与取值同源)",
          "var dit=((v[0]==='全局'?ul.global:ul.own)||[])[parseInt(v[1],10)]" in page)

    # ---- ⑩ [v36.4] 「装入」的两条去向: 就地改成品 / 另存为改副本 ----
    # 用户 2026-09-26 提的"顺带覆盖 Zotero 附件"那条已据证据否掉(理由见 panel._ul_apply 的
    # 文档串): 只留这两支, 两支**都只碰我们自己的文件**。判据落在"哪一份文件被改动"上 ——
    # ul_run 换成记录器(真跑是两趟子进程), 但那份 PDF 是**真文件**, 所以验得住。
    real_run = PN.ul_run
    calls = []

    def fake_run(*args, **kw):
        calls.append(list(args))
        return 0, "装好了"

    def targets():
        """记录器里每一次被指到的 --target(参数表: [脚本, --target, <那份文件>, ...])。"""
        return [c[2] for c in calls]

    src = os.path.join(_TMP, "demo-mono.pdf")
    copy = os.path.join(_TMP, "demo-mono.links.pdf")

    def blob(path):
        with io.open(path, "rb") as f:
            return f.read()

    with io.open(src, "wb") as f:
        f.write(b"%PDF-1.4 FAKE")           # 内容无关紧要: 这一节证的只是"哪一份被改动"

    try:
        PN.ul_run = fake_run
        check("⑩ 另存为件不进成品候选(拿带链接的那份再装一遍这条路不存在)",
              [p["name"] for p in PN.ul_pdfs()] == ["demo-mono.pdf"],
              [p["name"] for p in PN.ul_pdfs()])
        base = PN.ul_pdfs()[0]["path"]

        calls.clear()
        st = post(PN.H._ul_apply, {"target": base, "mode": "inplace"})
        check("⑩ inplace: 两趟都打在成品本身",
              st.out[-1].get("ok") and targets() == [base, base], (st.out[-1], calls))
        check("⑩ inplace: 不造副本", not os.path.exists(copy))
        check("⑩ 顺序由代码钉死: user_links 在前, style_links 在后",
              os.path.basename(calls[0][0]) == "user_links.py"
              and os.path.basename(calls[1][0]) == "style_links.py", calls)

        calls.clear()
        st = post(PN.H._ul_apply, {"target": base, "mode": "saveas"})
        r = st.out[-1]
        check("⑩ saveas: 改的是 <成品>.links.pdf 这份副本",
              r.get("ok") and r.get("file") == copy and targets() == [copy, copy],
              (r, calls))
        check("⑩ saveas: 副本是干净成品的复制, 成品一个字节没动",
              blob(copy) == blob(src) == b"%PDF-1.4 FAKE")

        with io.open(copy, "wb") as f:       # 先在旧副本上糊点东西…
            f.write(b"dirty-leftover")
        post(PN.H._ul_apply, {"target": base, "mode": "saveas"})
        check("⑩ saveas: 每次都从干净成品复制, 不在旧副本上叠",
              blob(copy) == b"%PDF-1.4 FAKE")

        def bad_run(*a, **kw):
            calls.append(list(a))
            return 1, "门禁没过"

        PN.ul_run = bad_run
        os.remove(copy)
        st = post(PN.H._ul_apply, {"target": base, "mode": "saveas"})
        check("⑩ saveas 没过 -> 副本被清掉(不留半成品等人当成品看)",
              st.out[-1].get("ok") is False and not os.path.exists(copy), st.out[-1])
        check("⑩ 没过就没动成品", blob(src) == b"%PDF-1.4 FAKE")

        def half_run(*a, **kw):              # 链接写进去了, 变蓝那步没过
            calls.append(list(a))
            return (0, "链接写好了") if len(calls) == 1 else (2, "染色没过")

        PN.ul_run = half_run
        calls.clear()                        # 这条判据按"这是第几次调用"分岔 —— 得从头数
        st = post(PN.H._ul_apply, {"target": base, "mode": "saveas"})
        r = st.out[-1]
        check("⑩ 写了但没染色 -> 如实报 links_written(不吞成'没装')",
              r.get("ok") is False and r.get("links_written") is True and r.get("file") == copy, r)
        check("⑩ 这条路上副本留着(链接真在里面, 删了才是丢活)", os.path.exists(copy))

        st = post(PN.H._ul_apply, {"target": base, "mode": "覆盖zotero"})
        check("⑩ 认不出的去向 -> 拒(前端说了不算)", bool(st.error()), st.out)
        st = post(PN.H._ul_apply, {"target": os.path.join(_TMP, "别人的.pdf"), "mode": "inplace"})
        check("⑩ 前端指一份不在候选里的 -> 拒(不许指哪儿改哪儿)", bool(st.error()), st.out)
    finally:
        PN.ul_run = real_run
        for p in (src, copy):
            if os.path.exists(p):
                os.remove(p)

    print(f"\n{'=' * 62}\n面板概念链接护栏: {passed} 过 / {failed} 败")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
