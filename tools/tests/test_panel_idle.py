# -*- coding: utf-8 -*-
"""面板退出契约 + 「铃响 → 面板到台前」的后半截 (v28.79, 2026-09-23)

v28.60 给面板加过一条"心跳静默 IDLE_LIMIT(1800s) = 窗口已关"的兜底自灭腿, 还配了
倒计时与暂停按钮。用户看过一眼就否了: 这条动线上人要在面板里看回包、改稿、重贴,
30 分钟根本不够 —— 一个"估出来的"时限只会把正常干活的人当死窗口处理。**整条腿删掉**。

[v28.79+] 删掉之后留了个窟窿: 浏览器崩了发不出告别, 进程就一直占着 60642(DETACHED
进程, 服务器终端 Ctrl+C 也带不走)。用户给的解法不是把时限加回来, 而是换成**确定性信号**:
**⑤ 出稿成功且这一篇该做的都出齐了 → 面板自己收摊**(退出腿②)。失败、还有块没出稿,
一律不退 —— 那时要改稿重贴、或接着把表格做完。

本套件逐条锁死现在的契约:
  ① 看门狗: 只剩告别腿 —— 心跳静默多久都不自灭(这条是"删掉兜底"的**行为**证据);
  ② 载荷就绪标记: payload_ready() 认 inbox 里最新那份 .ready.json, 坏文件不抛;
  ③ 构建戳: build_stamp() 取三份关键文件里最新的 mtime(表头那行"构建 09-23 20:15"),
     用来一眼认出"60642 上那扇窗跑的是旧进程";
  ④ HTTP 契约 + 页面接线: /api/state 给 build/ready 且**不再有 idle**, /api/idle 下线,
     页面有构建时间位、载荷就绪时自己 window.focus(); 面板把**实际端口**写进回话文件
     (--announce, v28.79+) —— 服务端是分离进程拉起它的, 全靠这个文件才知道它起没起、
     落在哪个端口(sandbox 的对应一半在 test_pause_adopt.py 的 D 段);
  ⑤ 60642 旧实例: 只识别、**绝不代杀** —— 本套件只做静态守卫, 不真发告别
     (用户的活面板可能就挂在 60642 上, 真发一次就把它关了);
  ⑥ 退出腿②(收摊): 判据是"**整篇齐了**"而不是"这一步过了"; 出稿失败/还有块没出稿
     绝不收; 关的是自己那个服务器对象, 不是杀进程。

运行: venv python test_panel_idle.py, 退出码 0=全过
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
TABLE_PIPE = os.path.join(TOOLS, "table_pipe")
for p in (TOOLS, TABLE_PIPE):
    if p not in sys.path:
        sys.path.insert(0, p)

# 环境必须在 import 之前落定: watch_clip 在**模块层**读这些变量
_TMP = tempfile.mkdtemp(prefix="p2z_idle_")
INBOX = os.path.join(_TMP, "inbox")
NAME = "payload_idle"
for k, v in (("P2Z_PROJ", _TMP), ("P2Z_TABLE_DIR", _TMP), ("P2Z_INBOX", INBOX),
             ("P2Z_BODY_NAME", NAME),
             ("P2Z_BODY_PDF", os.path.join(_TMP, "demo.pdf")),
             ("P2Z_PANEL_NO_OPEN", "1")):
    os.environ[k] = v

import panel as PN      # noqa: E402


class FakeSrv:
    """替身服务器: 只关心"有没有被叫去 shutdown"。"""

    def __init__(self):
        self.calls = 0

    def shutdown(self):
        self.calls += 1


def probe(ping_age=None, bye_age=None, window=1.0):
    """跑一次 _watchdog, 返回它是否自灭了。

    真实节拍是 5s 一睡, 一条用例就要干等 5 秒 —— 把 panel 里的 time 换成"不睡的替身",
    于是循环瞬间空转, 用 1 秒真实窗口收结论(把"等得久"换成"判得准")。
    """
    real_time = PN.time
    srv = FakeSrv()
    with PN.LOCK:
        PN.STATE["ping"] = None if ping_age is None else time.time() - ping_age
        PN.STATE["bye_at"] = None if bye_age is None else time.time() - bye_age
    PN.time = types.SimpleNamespace(time=time.time, strftime=time.strftime,
                                    sleep=lambda *_: None)
    try:
        t = threading.Thread(target=PN._watchdog, args=(srv,), daemon=True)
        t.start()
        t.join(window)                      # 空转窗口: 有结论就该在这一秒内出
    finally:
        PN.time = real_time
        with PN.LOCK:
            PN.STATE.update({"ping": None, "bye_at": None})
    return srv.calls


class swap:
    """临时换掉面板模块里的一个名字(monkeypatch), 用完还原 —— ⑥ 段要拿替身跑 _commit。

    换的是**模块里的名字**而不是把函数抄一份到测试里: 抄一份就等于测了另一个人写的代码,
    改了产品代码它也不会红。
    """

    def __init__(self, **kv):
        self.kv = kv

    def __enter__(self):
        self.old = {k: getattr(PN, k) for k in self.kv}
        for k, v in self.kv.items():
            setattr(PN, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(PN, k, v)
        return False


def http_json(url, body=None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _status(url, body=None):
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"} if body is not None else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def write_env_files():
    """造一份最小正文任务(面板 check_workdir 要求有 manifest 在位)。"""
    os.makedirs(INBOX, exist_ok=True)
    with open(os.path.join(INBOX, NAME + ".txt"), "w", encoding="utf-8") as f:
        f.write("[文档] Idle Probe\n[任务] 把下列每个 #S 段落译成简体中文。\n#S1\nHello.\n")
    with open(os.path.join(INBOX, NAME + ".manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"name": NAME, "pdf": os.path.join(_TMP, "demo.pdf"),
                   "items": [{"key": "S1", "parts": [{"page": 0, "seg": 0}]}]}, f)


def ready_file(name, segments, pages, when):
    """落一份载荷就绪标记(格式与 server_server._notify_payload_ready 一致)。"""
    os.makedirs(INBOX, exist_ok=True)
    p = os.path.join(INBOX, name + ".ready.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"name": name, "segments": segments, "pages": pages,
                   "ready_at": when}, f, ensure_ascii=False)
    return p


def start_panel():
    """起一个真面板(不弹窗), 从它打印的地址取端口 -> (进程, 基地址, 日志路径, 回话文件)。

    60642 上若已有别人的实例(用户的活面板), 本实例会自己回落随机端口, 地址照样从
    日志里读 —— 所以这里只认日志里那个地址, 不去碰 60642。

    同时给它 `--announce`(v28.79+): 面板要把**实际端口**写到那个文件里 —— 服务端是分离
    进程拉起它的, 拿不到退出码也读不到它的终端输出, 全靠这个文件才知道"起来了没 / 在
    哪个端口"。
    """
    logp = os.path.join(_TMP, "panel.log")
    annp = os.path.join(_TMP, "panel_announce.json")
    env = dict(os.environ, PYTHONUNBUFFERED="1")   # 管道里 stdout 是块缓冲, 不设就等不到地址
    with open(logp, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen([sys.executable, os.path.join(TABLE_PIPE, "panel.py"),
                                 "--announce", annp],
                                env=env, cwd=TABLE_PIPE, stdout=lf, stderr=subprocess.STDOUT)
    base = None
    for _ in range(100):                            # 最多等 10 秒
        time.sleep(0.1)
        try:
            with open(logp, encoding="utf-8") as f:
                txt = f.read()
        except OSError:
            continue
        i = txt.find("http://127.0.0.1:")
        if i >= 0:
            base = txt[i:].split()[0].strip()
            break
    return proc, base, logp, annp


def announced(path, timeout=5.0):
    """读面板回话文件里的内容; 迟迟没有 -> 空字典。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            time.sleep(0.1)
    return {}


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

    # ---- ① 看门狗: 只剩告别一条腿 ----
    with PN.LOCK:
        PN.STATE.update({"ping": None, "bye_at": None})
    n = probe(ping_age=10)
    check("① 心跳静默 10s -> 不自灭(倒计时兜底已删)", n == 0, f"shutdown {n} 次")
    n = probe(ping_age=1800 + 600)          # 远超过旧的 IDLE_LIMIT
    check("① 心跳静默 40min -> 仍不自灭", n == 0, f"shutdown {n} 次")
    n = probe(ping_age=None, bye_age=PN.BYE_GRACE + 5)
    check("① 从没心跳 + 已告别过宽限期 -> 自灭", n >= 1, f"shutdown {n} 次")
    n = probe(ping_age=0, bye_age=PN.BYE_GRACE + 5)
    check("① 告别后仍有心跳 -> 告别作废(不误杀 F5 刷新)", n == 0, f"shutdown {n} 次")
    check("① 倒计时那套已经从模块里消失",
          not hasattr(PN, "IDLE_LIMIT") and not hasattr(PN, "idle_state")
          and not hasattr(PN, "set_idle_off") and "idle_off" not in PN.STATE,
          [k for k in ("IDLE_LIMIT", "idle_state", "set_idle_off") if hasattr(PN, k)])

    # ---- ② 载荷就绪标记 ----
    check("② inbox 里没有标记 -> None", PN.payload_ready() is None, PN.payload_ready())
    ready_file("payload_a", 12, "1-3", "2026-09-23T10:00:00")
    r = PN.payload_ready()
    check("② 认到那一份并把字段原样带出",
          r and r["name"] == "payload_a" and r["segments"] == 12
          and r["pages"] == "1-3" and r["ready_at"] == "2026-09-23T10:00:00", r)
    p2 = ready_file("payload_b", 7, "4-5", "2026-09-23T11:00:00")
    os.utime(p2, (time.time() + 5, time.time() + 5))     # 让第二份成为最新
    r = PN.payload_ready()
    check("② 多份并存时只认最新那篇", r and r["name"] == "payload_b", r)
    bad = os.path.join(INBOX, "payload_bad.ready.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{不是 JSON")
    os.utime(bad, (time.time() - 600, time.time() - 600))   # 坏的比好的旧
    check("② 坏文件不抛、也不顶替最新那份", (PN.payload_ready() or {}).get("name") == "payload_b",
          PN.payload_ready())

    # ---- ③ 构建戳 ----
    bs = PN.build_stamp()
    check("③ 形如 MM-DD HH:MM", bool(re.fullmatch(r"\d\d-\d\d \d\d:\d\d", bs or "")), bs)
    files = [os.path.join(TABLE_PIPE, f) for f in ("panel.py", "watch_clip.py", "reviewer.py")]
    newest = max(os.path.getmtime(p) for p in files if os.path.exists(p))
    check("③ 取的是三份关键文件里最新的 mtime",
          bs == time.strftime("%m-%d %H:%M", time.localtime(newest)), bs)
    check("③ 沙箱里也不抛(目录引用已改成相对本文件)",
          isinstance(PN.build_stamp(), str), "")

    # ---- ④ HTTP 契约 + 页面接线 ----
    write_env_files()
    rp = ready_file("payload_live", 9, "1-2", time.strftime("%Y-%m-%dT%H:%M:%S"))
    os.utime(rp, (time.time() + 60, time.time() + 60))    # 确定性地压过 ② 那两份
    proc, base, logp, annp = start_panel()
    try:
        check("④ 面板起得来并报出地址", bool(base), logp)
        st = http_json(base + "api/state")
        check("④ /api/state 带 build 与 ready",
              isinstance(st.get("build"), str) and st["build"]
              and isinstance(st.get("ready"), dict)
              and st["ready"].get("name") == "payload_live", st.get("ready"))
        check("④ /api/state 不再有 idle(那套已删)",
              "idle" not in st, list(st.keys()))
        # [v28.79+] 面板起的端口(可能是回落出来的随机端口)要**报出来**: 服务端据此报
        # 真地址, 也据此认出"已经跑着的"那一份(不然每次提字都再弹一扇窗)。
        ann = announced(annp)
        check("④ 面板把**实际**端口写进回话文件(服务端拿不到退出码, 只能靠这个)",
              ann.get("port") == int(base.rstrip("/").rsplit(":", 1)[1]) and ann.get("port"),
              (ann, base))
        check("④ 回话里带 pid 与构建时间(排查'这扇窗是哪一版'用)",
              isinstance(ann.get("pid"), int) and bool(ann.get("build")), ann)
        pg = urllib.request.urlopen(base, timeout=10).read().decode("utf-8")
        check("④ 页面表头有构建时间位", 'id="buildTxt"' in pg)
        check("④ 页面不再有倒计时位与暂停按钮",
              'id="idleTxt"' not in pg and 'id="idleBtn"' not in pg)
        check("④ 载荷就绪时页面自己 focus + 落横幅",
              "/api/bye" in pg and "renderReady" in pg and "window.focus()" in pg)
        pi = http_json(base + "api/ping")
        check("④ 心跳顺带捎回载荷就绪(不另开轮询)",
              pi.get("ok") and (pi.get("ready") or {}).get("name") == "payload_live", pi)
        check("④ /api/idle 已下线(旧前端不该还有活路)",
              _status(base + "api/idle", {"off": True}) in (404, 405),
              _status(base + "api/idle", {"off": True}))
        check("④ 未知路径仍是 404(没有把兜底分支删掉)",
              _status(base + "api/nope") == 404, _status(base + "api/nope"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ---- ⑤ 60642 旧实例: 只识别、不代杀 ----
    st = PN._panel_state(timeout=0.4)
    check("⑤ _panel_state 只认自家键(jobs) 或当没有",
          st is None or (isinstance(st, dict) and "jobs" in st), st)
    src = open(os.path.join(TABLE_PIPE, "panel.py"), encoding="utf-8").read()
    # 只查**真的去调**没有: 名字出现在注释/文档里是对的(那里正解释"为什么不杀"),
    # 所以认的是带引号的命令字面量。
    for bad in ('"taskkill', "'taskkill", '"TerminateProcess', '"os.kill'):
        check("⑤ 源码里没有 %s(不代杀旧实例)" % bad.lstrip("\"'"), bad not in src)
    check("⑤ --takeover 走的是旧实例自己的告别腿",
          "--takeover" in src and "_ask_old_panel_to_quit" in src
          and "/api/bye" in src)
    check("⑤ 起不来时明说落在随机端口, 不假装占了 60642",
          "60642 没腾出来" in src)

    # ---- ⑥ 退出腿②: ⑤ 出稿且**整篇出齐** -> 自己收摊 ----
    # 判据是"整篇齐了", 不是"这一步过了": 面板是多格的(正文 + 表格正文/表注), 顺序随人 ——
    # 正文出完稿、表格还没做就关窗, 等于把人手里的活收走。
    t_body = {"label": "正文 X", "short": "正文", "state": "done", "self": True, "foreign": False}
    t_tbl = {"label": "表格 X", "short": "表格", "state": "todo", "self": False, "foreign": False}
    t_old = {"label": "表注 X", "short": "表注", "state": "stale", "self": False, "foreign": False}
    t_other = {"label": "表格 Y", "short": "表格", "state": "todo", "self": False, "foreign": True}
    check("⑥ 只有正文、已出稿 -> 齐了, 收摊", PN.paper_done([t_body]) is True)
    check("⑥ 还有表格没出稿 -> 没齐(不收摊, 让你接着做)",
          PN.paper_done([t_body, t_tbl]) is False)
    check("⑥ stale(装配过新一轮要重做) 也算没齐", PN.paper_done([t_body, t_old]) is False)
    check("⑥ foreign(烙着别篇的表) 不算本篇欠的", PN.paper_done([t_body, t_other]) is True)
    check("⑥ 没有正文任务(列表空) -> 谈不上齐, 不收摊", PN.paper_done([]) is False)

    # 接线: 只有"出稿成功 + 整篇齐了"才收; 失败、没齐都不退(那两件都还得你在面板上做)。
    class _WCStub:
        class LedgerFailed(Exception):
            pass

        def __init__(self, out):
            self.out = out

        def run_job(self, *a, **k):
            if self.out is None:
                raise _WCStub.LedgerFailed("底片没做成")
            return self.out

        def beep(self, ok):
            pass

    class _Resp:
        def __init__(self):
            self.seen = []

        def _json(self, obj, code=200):
            self.seen.append(obj)

    class _OsStub:
        """真 os 的透传 + 只把 startfile 换成不动手的 —— 测试里真调它会把 PDF 弹出来,
        而别的用法(path.exists 等, ledger 要用)照旧走真的。"""

        def __getattr__(self, k):
            return getattr(os, k)

        def startfile(self, p):
            pass

    JOB = {"label": "正文 X", "render": True}

    def commit(todos, out="D:\\tmp\\out.pdf"):
        """跑一次 ⑤(_commit), 返回 (响应, 收摊被叫了几次)。"""
        calls = []
        resp = _Resp()
        stub = _WCStub(out)
        with swap(wc=stub, paper_tasks=lambda: list(todos),
                  stop_after_done=lambda *a: calls.append(1), os=_OsStub()):
            with PN.LOCK:
                PN.STATE["checked"] = (JOB, ["S1"], {"S1": "你好。"})
                PN.STATE["sha"] = PN._sha("回包文本")
            PN.H._commit(resp, {"text": "回包文本"})
        return resp.seen[-1], len(calls)

    r, n = commit([t_body])
    check("⑥ 出稿成功且整篇齐了 -> 响应带 done 且安排收摊",
          r.get("ok") and r.get("done") is True and n == 1, (r, n))
    r, n = commit([t_body, t_tbl])
    check("⑥ 还有没出稿的 -> 照旧出稿但**不收摊**(只提醒)",
          r.get("ok") and r.get("done") is False and n == 0 and r.get("warn"), (r, n))
    r, n = commit([t_body], out=False)          # run_job 返回假值 = 门禁未过/排版失败
    check("⑥ 出稿失败 -> 绝不收摊(要改稿重贴)", r.get("ok") is False and n == 0, (r, n))
    r, n = commit([t_body], out=None)           # 底片没做成 -> 拦在出稿前
    check("⑥ 底片没做成(拦在出稿前) -> 收摊没被叫", r.get("ok") is False and n == 0, (r, n))

    class _Srv:
        def __init__(self):
            self.calls = 0

        def shutdown(self):
            self.calls += 1

    fake = _Srv()
    with swap(SRV={"h": fake}):
        PN.stop_after_done(0.05)                # 宽限本身是给"响应回到页面"留的, 测试里缩短
        time.sleep(0.6)
    check("⑥ 收摊关的是自己那个服务器对象(不是杀进程)", fake.calls == 1, fake.calls)

    fake2 = _Srv()
    with swap(SRV={"h": fake2}):
        PN.stop_after_done()
        time.sleep(0.2)
        n_now = fake2.calls
    check("⑥ 不是立刻关: 留几秒把「✓ 已出稿」送回到页面上", n_now == 0, n_now)
    check("⑥ 页面收到 done -> 横幅说清楚 + 顺手试关窗",
          "r.done" in pg and "window.close()" in pg)
    check("⑥ main() 把服务器对象交给收摊腿(不交就关不掉自己)",
          'SRV["h"] = srv' in src)

    print(f"\n结果: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
