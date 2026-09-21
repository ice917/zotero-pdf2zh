# -*- coding: utf-8 -*-
"""面板「兜底退出」的可暂停契约单元/集成测试 (v28.60, 2026-09-21)

要解决的**用户侧**问题: 面板靠"心跳静默 IDLE_LIMIT(1800s) = 窗口已关"自灭 ——
这只是个**估计**。估紧了会把"切去豆包翻长文"误杀(早期 30s 版实测踩过, 前端还把
连不上服务器谎报成"剪贴板里没有文本"); 估宽了则窗口真死了还霸着 60642 半小时,
下一次启动只能回落随机端口, 而用户手里那扇旧窗连的却是死服务器(2026-09-21 实测)。
阈值怎么定都有代价, 于是把它做成**用户可控**: 头部显示倒计时, 一键暂停/继续。

四条语义(本测试逐条锁死):
  1. 倒计时由**服务器**给(前端自己算会漂), 前端只做两次心跳之间的本地递减;
  2. 暂停只停"心跳静默自灭"这条腿 —— 关窗的**告别腿照走**, 否则暂停就成了孤儿
     进程制造机;
  3. **继续时必须把心跳时钟归零** —— 暂停期间的心跳早已过期, 不归零就是"点了继续
     服务器当场自灭";
  4. 从没收到过心跳时**不自灭**(窗口可能还没开起来)。

运行: venv python test_panel_idle.py, 退出码 0=全过
"""
import json
import os
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
NAME = "payload_idle"
for k, v in (("P2Z_PROJ", _TMP), ("P2Z_TABLE_DIR", _TMP),
             ("P2Z_INBOX", os.path.join(_TMP, "inbox")),
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


def probe(idle_off, ping_age=10, limit=1, bye_age=None, window=1.0):
    """跑一次 _watchdog, 返回它是否自灭了。

    真实节拍是 5s 一睡, 一条用例就要干等 5 秒 —— 把 panel 里的 time 换成"不睡的替身",
    于是循环瞬间空转, 用 1 秒真实窗口收结论(把"等得久"换成"判得准")。
    """
    real_time, real_limit = PN.time, PN.IDLE_LIMIT
    srv = FakeSrv()
    with PN.LOCK:
        PN.STATE["ping"] = None if ping_age is None else time.time() - ping_age
        PN.STATE["bye_at"] = None if bye_age is None else time.time() - bye_age
        PN.STATE["idle_off"] = idle_off
    PN.IDLE_LIMIT = limit
    PN.time = types.SimpleNamespace(time=time.time, strftime=time.strftime,
                                    sleep=lambda *_: None)
    try:
        t = threading.Thread(target=PN._watchdog, args=(srv,), daemon=True)
        t.start()
        t.join(window)                      # 空转窗口: 有结论就该在这一秒内出
    finally:
        PN.time, PN.IDLE_LIMIT = real_time, real_limit
        with PN.LOCK:
            PN.STATE.update({"ping": None, "bye_at": None, "idle_off": False})
    return srv.calls


def http_json(url, body=None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def write_env_files():
    """造一份最小正文任务(面板 check_workdir 要求有 manifest 在位)。"""
    os.makedirs(os.path.join(_TMP, "inbox"), exist_ok=True)
    with open(os.path.join(_TMP, "inbox", NAME + ".txt"), "w", encoding="utf-8") as f:
        f.write("[文档] Idle Probe\n[任务] 把下列每个 #S 段落译成简体中文。\n#S1\nHello.\n")
    with open(os.path.join(_TMP, "inbox", NAME + ".manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"name": NAME, "pdf": os.path.join(_TMP, "demo.pdf"),
                   "items": [{"key": "S1", "parts": [{"page": 0, "seg": 0}]}]}, f)


def start_panel():
    """起一个真面板(不弹窗), 从它打印的地址取端口 -> (进程, 基地址, 日志路径)。"""
    logp = os.path.join(_TMP, "panel.log")
    env = dict(os.environ, PYTHONUNBUFFERED="1")   # 管道里 stdout 是块缓冲, 不设就等不到地址
    with open(logp, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen([sys.executable, os.path.join(TABLE_PIPE, "panel.py")],
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
    return proc, base, logp


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

    # ---- ① 倒计时读数: 由服务器给, 且没心跳时不敢乱报 ----
    with PN.LOCK:
        PN.STATE.update({"ping": None, "bye_at": None, "idle_off": False})
    d = PN.idle_state()
    check("① 从没收到心跳 -> left 为 None(不吓唬人)", d["left"] is None and not d["off"], d)
    check("① 带上上限供前端显示", d["limit"] == PN.IDLE_LIMIT, d)
    with PN.LOCK:
        PN.STATE["ping"] = time.time() - 60
    d = PN.idle_state()
    check("① 心跳 60s 前 -> 剩余 ≈ 上限-60",
          d["left"] is not None and abs(d["left"] - (PN.IDLE_LIMIT - 60)) <= 2, d)
    with PN.LOCK:
        PN.STATE["ping"] = time.time() - (PN.IDLE_LIMIT + 100)
    check("① 已超时也报 0 而不是负数", PN.idle_state()["left"] == 0, PN.idle_state())

    # ---- ② 暂停 / 继续: 继续必须把时钟归零, 否则"点了继续当场自灭" ----
    with PN.LOCK:
        PN.STATE["ping"] = time.time() - (PN.IDLE_LIMIT + 100)
    d = PN.set_idle_off(True)
    check("② 暂停后 off=True 且不再显示倒计时", d["off"] is True and d["left"] is None, d)
    d = PN.set_idle_off(False)
    check("② 继续时时钟归零(剩余回到满格)",
          d["off"] is False and d["left"] is not None and d["left"] >= PN.IDLE_LIMIT - 2, d)
    with PN.LOCK:
        PN.STATE.update({"ping": None, "bye_at": None, "idle_off": False})

    # ---- ③ 看门狗的决策: 暂停只挡住"心跳静默"这条腿 ----
    n = probe(idle_off=False)
    check("③ 心跳静默超限 -> 自灭", n >= 1, f"shutdown 调用 {n} 次")
    n = probe(idle_off=True)
    check("③ 已暂停 -> 再静默也不自灭", n == 0, f"shutdown 调用 {n} 次")
    n = probe(idle_off=False, ping_age=None)
    check("③ 从没收到心跳 -> 不自灭(窗口可能还没开起来)", n == 0, f"shutdown 调用 {n} 次")
    n = probe(idle_off=True, ping_age=None, bye_age=PN.BYE_GRACE + 5)
    check("③ 暂停不影响告别腿: 关窗 15s 后照退", n >= 1, f"shutdown 调用 {n} 次")
    # 这一条只验"告别被新心跳撤销", 故把闲置腿的上限放到很大 —— 否则空转 1 秒就会
    # 撞上 limit=1 的闲置判据, 测出来的是另一条腿
    n = probe(idle_off=False, ping_age=0, bye_age=PN.BYE_GRACE + 5, limit=3600)
    check("③ 告别后仍有心跳 -> 告别作废(不误杀 F5 刷新)", n == 0, f"shutdown 调用 {n} 次")

    # ---- ④ HTTP 契约 + 页面接线(真起一个面板) ----
    write_env_files()
    proc, base, logp = start_panel()
    try:
        check("④ 面板起得来并报出地址", bool(base), logp)
        st = http_json(base + "api/state")
        check("④ /api/state 带 idle(开页即显倒计时)",
              isinstance(st.get("idle"), dict) and st["idle"]["off"] is False, st.get("idle"))
        pg = urllib.request.urlopen(base, timeout=10).read().decode("utf-8")
        check("④ 页面有倒计时位与暂停按钮",
              'id="idleTxt"' in pg and 'id="idleBtn"' in pg and "/api/idle" in pg)
        pi = http_json(base + "api/ping")
        check("④ 心跳顺带捎回倒计时(不另开轮询)",
              pi["ok"] and pi["idle"]["left"] is not None
              and pi["idle"]["left"] >= PN.IDLE_LIMIT - 5, pi)
        off = http_json(base + "api/idle", {"off": True})["idle"]
        check("④ POST /api/idle 能暂停", off["off"] is True and off["left"] is None, off)
        check("④ 暂停后心跳仍被接收(不再显示倒计时)",
              http_json(base + "api/ping")["idle"]["left"] is None, "")
        on = http_json(base + "api/idle", {"off": False})["idle"]
        check("④ 继续后倒计时即刻满格(时钟归零)",
              on["off"] is False and on["left"] >= PN.IDLE_LIMIT - 5, on)
        check("④ 未知路径仍是 404(没有把兜底分支删掉)",
              _status(base + "api/nope") == 404, _status(base + "api/nope"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\n结果: {passed} passed, {failed} failed")
    return 1 if failed else 0


def _status(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


if __name__ == "__main__":
    sys.exit(main())
