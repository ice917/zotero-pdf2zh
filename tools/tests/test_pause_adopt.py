# -*- coding: utf-8 -*-
"""提字 → 面板确认 → 才出稿: 服务端停在那里 + 面板出稿工序改走 adopt (v28.79, 2026-09-23)

用户侧的三句话(2026-09-23):
  1. 第一步是先把文字提取出来, 速度很快; 后来交给控制面板 —— 这个动作连贯吗, 是提示音
     出来后就弹出控制面板吗
  2. 控制面板这个 30 分钟我觉得可以去掉了 —— 按现在这个想法, 30 分钟远远不够
  3. 现在必须一步一步做: 先提取, 后出现控制面板; 最后确认之后才出稿

对应的三处改动(本套件逐条锁死):
  A. 服务端提字完就**停**: PAUSE_AUTO_ADOPT 缺省 0 -> 响铃落 ready 标记后直接收尾在
     「待译」, 不再自动等交件/回灌/重渲染。设 1 才回到旧的无人值守回路。
  B. 停住那种状态**要拦得住渲染**: 缓存里此刻压着 raw→raw 骨架行, 从别处发起渲染会
     整篇命中它们, 出来是一份全英文 PDF。_skeleton_pending 拿 adopt 台账当判据
     (export 已 ok 而 inject 还没 ok), 会随面板 ⑤ 跑完自己放行。
  C. 面板 ⑤ 的**正文**工序改走 adopt(deliver → import → inject → force_rerender),
     不再单跑 seg_import/seg_inject —— 那一条缺 ⋮ 断点对账/只译半截/逐段不变量三道门,
     且 seg_inject 没给 --fp 会静默回落 Cactaceae 指纹(UPDATE 命中 0 行、渲染还是英文)。
     表格/表注工序不动; 没有 adopt 台账的老载荷(手动 seg_export)照旧走老路。

不触网、不跑翻译: server.py 只做**源码级**抽取(它 import pdf2zh, 起不来), 抽出来的
_auto_adopt_enabled / _skeleton_pending 在隔离命名空间里真跑。

运行: venv python test_pause_adopt.py, 退出码 0=全过
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
TABLE_PIPE = os.path.join(TOOLS, "table_pipe")
REPO = os.path.dirname(TOOLS)
for p in (TOOLS, TABLE_PIPE):
    if p not in sys.path:
        sys.path.insert(0, p)

# 环境必须在 import 之前落定: watch_clip 在**模块层**读这些变量
_TMP = tempfile.mkdtemp(prefix="p2z_pause_")
NAME = "payload_pause"
for k, v in (("P2Z_PROJ", _TMP), ("P2Z_TABLE_DIR", _TMP),
             ("P2Z_INBOX", os.path.join(_TMP, "inbox")),
             ("P2Z_BODY_NAME", NAME),
             ("P2Z_BODY_PDF", os.path.join(_TMP, "demo.pdf")),
             ("P2Z_PANEL_NO_OPEN", "1")):
    os.environ[k] = v

import panel as PN          # noqa: E402
import watch_clip as WC     # noqa: E402

SERVER_PY = os.path.join(REPO, "server", "server.py")
LEDGER = os.path.join(_TMP, "logs", "adopt", NAME + ".json")


def func_src(name, path=SERVER_PY):
    """取函数**原文**(模块级或类方法都认)。server.py 一 import 就拉 pdf2zh(重且要联网),
    为了测两个纯函数去起它不划算 —— 抽出来在隔离命名空间里跑是干净解法。"""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for node in ast.walk(ast.parse("\n".join(lines))):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    return ""


def server_ns(*names):
    """把抽出来的模块级函数装进一个只带它真用得上的标准库的命名空间。"""
    ns = {"os": os, "json": json, "time": time}
    for n in names:
        src = func_src(n)
        assert src, "server.py 里找不到 %s" % n
        exec(compile(src, "<server.%s>" % n, "exec"), ns)
    return ns


def const_src(name):
    """取模块级赋值/常量定义那一段**原文**(PANEL_PORT / _PANEL_ANNOUNCE / _PANEL_LAUNCH)。
    从源码抽而不是在测试里抄一份: 端口换了、文件挪了, 测试要跟着走 —— 抄一份就漏了。"""
    with open(SERVER_PY, encoding="utf-8") as f:
        src = f.read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            seg = ast.get_source_segment(src, node)
            if seg:
                return seg
    return ""


class PopenSpy:
    """替掉 subprocess: 只记"起过几次 / 命令行长什么样"。DEVNULL 得有个值,
    否则 getattr 那两行没问题但 Popen 的 kwargs 会引用到它。"""

    DEVNULL = -3

    def __init__(self):
        self.calls = []

    def Popen(self, argv, **kw):                # noqa: N802 —— 冒充 subprocess.Popen
        self.calls.append(list(argv))
        return None


def panel_ns():
    """[v28.79+] 把 _open_panel 那一组抽进隔离命名空间真跑(subprocess/sys 换成替身)。

    被测的就是"服务端怎么把面板叫到台前"这段: 它决定了提字那一刻用户眼前发生什么 ——
    谎报"已启动"、报错地址(面板回落随机端口时)、或不该弹的时候弹, 都在这里。
    """
    # __file__ 得给: server.py 里它是真的(模块有 __file__), 抽出来单跑就没了 ——
    # _panel_script 靠它把自己所在的 server/ 换算成 tools/table_pipe/panel.py。
    ns = {"os": os, "json": json, "time": time, "sys": sys, "tempfile": tempfile,
          "__file__": SERVER_PY}
    for c in ("PANEL_PORT", "_PANEL_ANNOUNCE", "_PANEL_LAUNCH"):
        s = const_src(c)
        assert s, "server.py 里找不到常量 %s" % c
        exec(compile(s, "<server.%s>" % c, "exec"), ns)
    for n in ("_panel_state", "_announced_port", "_panel_running_port",
              "_wait_panel_port", "_panel_script", "_open_panel"):
        s = func_src(n)
        assert s, "server.py 里找不到 %s" % n
        exec(compile(s, "<server.%s>" % n, "exec"), ns)
    ns["subprocess"] = PopenSpy()
    ns["_PANEL_ANNOUNCE"] = os.path.join(_TMP, "panel_announce_ns.json")   # 别碰真实的那个
    return ns


class JobsStub(BaseHTTPRequestHandler):
    """只答 /api/state = {"jobs": []} —— 冒充一个"自家面板", 给端口探测用。"""

    def do_GET(self):                            # noqa: N802
        body = json.dumps({"jobs": [], "build": "01-01 00:00"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class with_env:
    """临时改环境(给 None = 删掉), 用完还原 —— 被测函数读的就是环境。"""

    def __init__(self, **kv):
        self.kv = kv

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.kv}
        for k, v in self.kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def write_ledger(stages):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump({"name": NAME, "engine": "pdf2zh", "stages": stages}, f)


def drop_ledger():
    try:
        os.remove(LEDGER)
    except OSError:
        pass


def fmt(rows):
    """工序表 -> 一行一句的字符串, 断言好写也好读。"""
    return [" ".join(str(x) for x in row) for row in (rows or [])]


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

    # ---- A. 开关: 缺省"停", 设 1 才回到旧回路 ----
    auto = server_ns("_auto_adopt_enabled")["_auto_adopt_enabled"]
    with with_env(PAUSE_AUTO_ADOPT=None):
        check("A 缺省(没设) -> 停, 不自动走完回路", auto() is False)
    for v in ("1", "true", "TRUE", "on", "yes", " 1 "):
        with with_env(PAUSE_AUTO_ADOPT=v):
            check("A %r -> 开(回到旧自动回路)" % v, auto() is True)
    for v in ("0", "off", "no", "", "  ", "False", "随便写点啥"):
        with with_env(PAUSE_AUTO_ADOPT=v):
            check("A %r -> 关(停在待译)" % v, auto() is False)

    # ---- B. 骨架行护栏: 判据全在台账上, 且会自己变对 ----
    pending = server_ns("_adopt_ledger", "_skeleton_pending")["_skeleton_pending"]
    drop_ledger()
    check("B 没有台账 -> 放行(不是提字档, 与本文无关)", pending(NAME) == "")

    write_ledger({"export": {"state": "failed"}})
    check("B export 没成功 -> 放行(没有骨架行这回事)", pending(NAME) == "")

    write_ledger({"export": {"state": "ok"}})
    why = pending(NAME)
    check("B export ok 而 inject 未做 -> 拦住, 并点明出来的是全英文 PDF",
          bool(why) and "骨架行" in why and "全英文" in why, why)
    check("B 拦下来时给出两条出路(面板 ⑤ / rollback)",
          bool(why) and "中继面板 ⑤" in why and "rollback" in why, why)

    write_ledger({"export": {"state": "ok"}, "inject": {"state": "failed"}})
    check("B inject 失败 -> 仍然拦住(骨架行还在)", pending(NAME) != "")
    write_ledger({"export": {"state": "ok"}, "inject": {"state": "ok"}})
    check("B inject 已 ok -> 自动放行(不需要谁来清标记)", pending(NAME) == "")
    drop_ledger()

    # ---- C-1. 服务端: 提字后就停, 别再自己等交件 ----
    body = func_src("_two_pass_run")
    i_notify = body.find("_notify_payload_ready(")
    i_switch = body.find("_auto_adopt_enabled()")
    i_wait = body.find("_wait_for_doubao_delivery(")
    check("C 顺序: 先落载荷就绪/响铃, 再判开关",
          i_notify >= 0 and i_notify < i_switch, (i_notify, i_switch))
    check("C 判开关的早退在「等交件」之前(关了就不再等)",
          i_switch >= 0 and i_wait > i_switch, (i_switch, i_wait))
    check("C 早退是收尾成「待译」而不是判失败",
          '"paused": True' in body and "待译" in body, "paused/待译 未同时出现")
    ex = func_src("_execute_translate_job")
    check("C 插件那侧把 paused 当成功收尾(卡片落「完成」而不是「失败」)",
          'fileList.get("paused")' in ex and "complete_task(task_id, 'success'" in ex, ex[:0])
    i_guard = ex.find("_skeleton_pending(")
    check("C 骨架行护栏在真渲染之前(force / 关两趟的机翻那两条路)",
          i_guard >= 0 and i_guard < ex.find("self.translate_pdf(input_path, config, task_id)"),
          i_guard)

    # ---- C-2. 面板 ⑤: 正文工序随台账切到 adopt ----
    drop_ledger()
    check("C 没有台账 -> 不启用 adopt", WC.body_use_adopt() is False)
    check("C 没有台账 -> 第二道门为空(照旧只跑 cmd)", WC.body_gate_after() == [])
    old_after = fmt(WC.body_after())
    check("C 没有台账 -> 老路: seg_inject 注入 + 重渲染",
          len(old_after) == 2 and "seg_inject.py" in old_after[0]
          and "force_rerender.py" in old_after[1], old_after)

    write_ledger({"export": {"state": "failed"}})
    check("C 台账 export 未 ok -> 仍走老路", WC.body_use_adopt() is False)
    check("C 台账 export 未 ok -> 第二道门仍为空", WC.body_gate_after() == [])

    write_ledger({"export": {"state": "ok"}, "deliver": {"state": "ok"},
                  "import": {"state": "ok"}, "inject": {"state": "ok"}})
    check("C 台账 export ok -> 启用 adopt", WC.body_use_adopt() is True)
    ga = fmt(WC.body_gate_after())
    check("C 第二道门 = deliver + import, 顺序与自动回路一致",
          len(ga) == 2 and "adopt.py deliver" in ga[0] and "adopt.py import" in ga[1], ga)
    check("C deliver 点名交件件(不 glob, 同名历史交件一堆)",
          "--text" in ga[0] and NAME + "_response.tsv" in ga[0], ga[0])
    check("C 面板这条路不带 --waive(确认不等于放行门禁)",
          all("--waive" not in g for g in ga), ga)
    new_after = fmt(WC.body_after())
    check("C 确认后的工序 = inject + 重渲染",
          len(new_after) == 2 and "adopt.py inject" in new_after[0]
          and "force_rerender.py" in new_after[1], new_after)
    check("C adopt 分支里不再出现没 --fp 的老注入",
          all("seg_inject.py" not in a for a in new_after), new_after)

    # ---- C-3. 第二道门判退就停, 而且不越过「存底片」 ----
    snap = {"n": 0}
    real_snapshot = WC._snapshot
    WC._snapshot = lambda *a, **k: snap.__setitem__("n", snap["n"] + 1)

    def fake_job(gate_after_rows):
        return {"label": "正文 " + NAME, "pre": "S", "blocks": True, "render": True,
                "resp": NAME + "_response.tsv",
                "cmd": lambda: [sys.executable, "-c", "print('cmd PASS')"],
                "gate_after": gate_after_rows,
                "after": lambda: [[sys.executable, "-c", "print('after PASS')"]],
                "out": lambda: os.path.join(_TMP, "out.pdf")}

    logs = []
    try:
        bad = [[sys.executable, "-c",
                "import sys; sys.stderr.write('门禁说不行'); sys.exit(3)"]]
        out = WC.run_job(fake_job(bad), ["S1"], {"S1": "你好。"}, log=logs.append)
        check("C 第二道门不过 -> 不出稿", out is None, out)
        check("C 第二道门不过 -> 连底片都不做(不给被拒的稿子存档)", snap["n"] == 0, snap)
        check("C 第二道门不过 -> 日志点明是哪一道门",
              any("出稿门禁" in l for l in logs)
              and any("门禁说不行" in l for l in logs), logs[-3:])

        out = WC.run_job(fake_job([[sys.executable, "-c", "print('gate PASS')"]]),
                         ["S1"], {"S1": "你好。"}, log=logs.append)
        check("C 第二道门过了 -> 才存底片 -> 才走出稿工序",
              snap["n"] == 1 and bool(out) and out.endswith("out.pdf"), (snap, out))
    finally:
        WC._snapshot = real_snapshot

    # ---- C-4. ③ 检查要点明"这里只跑第一道门" ----
    with open(os.path.join(_TMP, "demo.manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"units": [{"id": "S1", "orig": "Hello."}]}, f)

    def sb_job(gate_after_rows):
        return {"label": "正文 " + NAME, "pre": "S", "blocks": True,
                "manifest": "demo.manifest.json", "resp": NAME + "_response.tsv",
                "cmd": lambda: [sys.executable, "-c", "print('PASS')"],
                "gate_after": gate_after_rows}

    ok, rows, _ = PN.sandbox_gates(sb_job(None), ["S1"], {"S1": "你好。"})
    check("C 没有第二道门的任务 -> 不加注(表格任务不受影响)",
          ok and not any("第一道门" in l for l in rows), rows)
    ok, rows, _ = PN.sandbox_gates(sb_job([[sys.executable, "-c", "pass"]]),
                                   ["S1"], {"S1": "你好。"})
    check("C 有第二道门的任务 -> ③ 明说这里只跑第一道门",
          ok and any("第一道门" in l for l in rows), rows)

    # ---- D. 面板拉起的三个薄弱点(v28.79+, 同日补) ----
    # ① 起完要回头看它到底起没起(分离进程拿不到退出码, 面板找不到 manifest 会自己退);
    # ② 地址要报真的(60642 被占时面板回落随机端口, 再报 60642 就是指错门);
    # ③ 自动回路是无人值守的, 不该往人眼前弹窗。
    L = panel_ns()
    real_running = L["_panel_running_port"]

    L["_panel_running_port"] = lambda: 51234
    note = L["_open_panel"]()
    check("D 已在跑 -> 报出它的端口(哪怕不是 60642), 不起新的",
          "127.0.0.1:51234" in note and L["subprocess"].calls == [], note)

    L["_panel_running_port"] = lambda: 0
    L["subprocess"] = PopenSpy()
    L["_PANEL_LAUNCH"]["at"] = 0.0
    L["_wait_panel_port"] = lambda *a: 51234          # 面板回落到了随机端口
    note = L["_open_panel"]()
    check("D 面板回落随机端口 -> 报的是真地址, 不再写死 60642",
          "127.0.0.1:51234" in note and "不在 60642" in note, note)
    check("D 拉起时把 --announce 交给面板(不交就拿不到真端口)",
          bool(L["subprocess"].calls) and "--announce" in L["subprocess"].calls[0],
          L["subprocess"].calls)

    L["subprocess"] = PopenSpy()
    L["_PANEL_LAUNCH"]["at"] = 0.0
    L["_wait_panel_port"] = lambda *a: 60642          # 正常占到固定端口
    note = L["_open_panel"]()
    check("D 正常起在 60642 -> 报 60642 且说「已启动」",
          "127.0.0.1:60642" in note and "已启动" in note, note)

    L["subprocess"] = PopenSpy()
    L["_PANEL_LAUNCH"]["at"] = 0.0
    L["_wait_panel_port"] = lambda *a: 0              # 面板自己退了(退出码 2)
    note = L["_open_panel"]()
    check("D 起不来 -> 明说没起来, 不再谎报「已启动」",
          "⚠️" in note and "已启动" not in note, note)
    check("D 起不来时给排查方向(P2Z_TABLE_DIR / P2Z_PROJ)",
          "P2Z_TABLE_DIR" in note and "P2Z_PROJ" in note, note)

    L["subprocess"] = PopenSpy()
    L["_PANEL_LAUNCH"]["at"] = time.time()            # 刚起过
    note = L["_open_panel"]()
    check("D 10 秒内不重复起(同一批载荷只弹一扇窗)",
          L["subprocess"].calls == [] and "刚起过" in note, note)

    L["_PANEL_LAUNCH"]["at"] = 0.0
    L["subprocess"] = PopenSpy()
    L["_panel_script"] = lambda: ""                   # 找不到 panel.py
    note = L["_open_panel"]()
    check("D 找不到 panel.py -> 明说没起成, 也不起进程",
          "⚠️" in note and L["subprocess"].calls == [], note)

    # 面板报上来的端口要**探一下才算数**: 文件可能是上一轮留下的(进程没了文件还在),
    # 也可能是端口早被别的程序接管了 —— 不探就报, 等于把用户指到一扇不存在的窗。
    ann_ns = L["_PANEL_ANNOUNCE"]
    L["PANEL_PORT"] = 59997                           # 固定端口挪开, 免得撞上真在跑的面板
    with open(ann_ns, "w", encoding="utf-8") as f:
        json.dump({"port": 59996}, f)                 # 没人听的端口
    check("D 回话文件里的端口要探一下才算数(陈年文件不算在跑)",
          real_running() == 0, real_running())

    stub = ThreadingHTTPServer(("127.0.0.1", 0), JobsStub)
    stub_port = stub.server_address[1]
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    with open(ann_ns, "w", encoding="utf-8") as f:
        json.dump({"port": stub_port}, f)
    check("D 认出「不在 60642 上」的那一份(否则每次提字都再弹一扇窗)",
          real_running() == stub_port, (real_running(), stub_port))
    stub.shutdown()
    stub.server_close()
    os.remove(ann_ns)

    # ---- D-2. 自动回路不弹窗(需求③) ----
    ns2 = server_ns("_auto_adopt_enabled")
    exec(compile(func_src("_notify_payload_ready"), "<server._notify>", "exec"), ns2)
    opened = []

    class _TM:
        def update_task(self, *a, **k):
            pass

    ns2.update({"_inbox_dir": lambda: _TMP,
                "_play_notify_sound": lambda: None,
                "_open_panel": lambda: opened.append(1) or "面板到台前",
                "task_manager": _TM(),
                "datetime": datetime})
    notify = ns2["_notify_payload_ready"]

    with with_env(PAUSE_AUTO_ADOPT="1"):
        notify("t_auto", NAME + "_auto", "1-2")
    n_auto = len(opened)
    with with_env(PAUSE_AUTO_ADOPT=None):
        notify("t_stop", NAME + "_stop", "1-2")
    check("D 自动回路(PAUSE_AUTO_ADOPT=1) 不弹面板 —— 那条路没人要看",
          n_auto == 0, n_auto)
    check("D 停在待译那条路才把面板叫到台前", len(opened) == 1, len(opened))

    print(f"\n结果: {passed} passed, {failed} failed")
    shutil.rmtree(_TMP, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
