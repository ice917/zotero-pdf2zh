# -*- coding: utf-8 -*-
"""[自研补丁 2026-09-18] 无控制台部署下的进度监视器单元测试

背景(与闸门4 同源):
    launch.ps1 是 `-RedirectStandardOutput/Error + -WindowStyle Hidden` 启动服务的,
    服务与子进程都**没有控制台**。旧监视器只读控制台屏幕缓冲, 于是:
      ① 句柄虽是"有效"的, 却是文件句柄 → GetConsoleScreenBufferInfo 失败 → 静默退出;
      ② 顺带 activity[1] 永远 False, 把空闲看门狗也废掉。
    实测进度落在 logs/server_err.log 里, 格式为 pdf2zh 1.x 的 tqdm:
        ` 42%|███| 8/19 [00:02<00:03, 2.89it/s]`  (\\r 分隔, **无 "translate" 前缀**)

    日志是全服务共享的(服务自身与**所有**子进程都往同一个文件写), 所以日志源只在
    "本任务独占(没有别的翻译在跑)" 时才启用, 否则退化为不出进度 —— 见 [F]/[G]。

运行: venv python test_progress_log.py, 退出码 0=全过
不触网: 不启动子进程, 只喂日志文件; 用 PDF2ZH_PROGRESS_SOURCE=log 强制日志源。
"""
import os
import shutil
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_SERVER = os.path.join(_REPO, "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import utils.execute as execute  # noqa: E402
from utils.task_manager import task_manager  # noqa: E402

TQDM_LINE = b"\r 42%|\xe2\x96\x88\xe2\x96\x88\xe2\x96\x88| 8/19 [00:02<00:03, 2.89it/s]"
TQDM_ZERO = b"\r  0%|          | 0/19 [00:00<?, ?it/s]"
TQDM_DONE = b"\r100%|\xe2\x96\x88\xe2\x96\x88\xe2\x96\x88\xe2\x96\x88| 19/19 [15:01<00:00, 55.97s/it]"


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

    print("[A] _match_progress_line: pdf2zh 1.x 无 desc 的 tqdm 也要认")
    check("A1 tqdm 进度条(无 translate 前缀)", execute._match_progress_line(
        " 42%|\u2588\u2588\u2588\u258f| 8/19 [00:02<00:03, 2.89it/s]") == (8, 19))
    check("A2 tqdm 0/19", execute._match_progress_line(
        "  0%|          | 0/19 [00:00<?, ?it/s]") == (0, 19))
    check("A3 旧格式 translate x/y 仍认", execute._match_progress_line(
        "translate 10/100") == (10, 100))
    check("A4 子任务括号不计入页进度", execute._match_progress_line(
        "Translate Paragraphs (1/1) 3/17") is None)
    check("A5 无关行返回 None", execute._match_progress_line("hello world") is None)

    print("[B] _read_progress_log: \\r 也当行分隔、残段留到下轮")
    tmpdir = tempfile.mkdtemp(prefix="pdf2zh_progress_")
    try:
        log_path = os.path.join(tmpdir, "server_err.log")
        with open(log_path, "wb") as fp:
            fp.write(TQDM_ZERO + TQDM_LINE + b"\r")

        state = {"fp": open(log_path, "rb"), "buf": b"", "seq": 0}
        pairs, seq = execute._read_progress_log(state)
        # 文件里的内容在 open 之后就该被读到(此处不 seek 末尾, 验解析本身)
        texts = [t for _, t in pairs]
        check("B1 \\r 分隔成多行", len(pairs) == 2, f"pairs={texts}")
        check("B2 seq 从 1 单调递增", [s for s, _ in pairs] == [1, 2])
        check("B3 无新内容返回空", execute._read_progress_log(state)[0] == [])

        # 追加半行: 不应吐出半行
        with open(log_path, "ab") as fp:
            fp.write(b"\r 57%|\xe2\x96\x88\xe2\x96\x88| 1")
        pairs2, _ = execute._read_progress_log(state)
        check("B4 残段不进结果", pairs2 == [], f"{pairs2}")

        # 补全该行: 上一轮残段 + 本轮新数据拼起来
        with open(log_path, "ab") as fp:
            fp.write(b"1/19 [00:05<00:05, 2.0it/s]\r")
        pairs3, _ = execute._read_progress_log(state)
        joined = "".join(t for _, t in pairs3)
        check("B5 跨轮拼接出完整行", "11/19" in joined, f"{joined!r}")
        check("B6 拼出的行能解析", execute._match_progress_line(joined) == (11, 19))
        state["fp"].close()

        print("[C] _open_progress_log: 从文件末尾跟读, 不吃上次任务的旧进度")
        os.environ[execute.PROGRESS_LOG_ENV] = log_path
        st = execute._open_progress_log()
        check("C1 能打开日志", st is not None)
        check("C2 起点在末尾(旧内容不可见)", execute._read_progress_log(st)[0] == [])
        st["fp"].close()

        print("[D] 端到端: 无控制台 + 日志源 → 任务卡片进度推进 + 喂活动信号")
        os.environ[execute.PROGRESS_SOURCE_ENV] = "log"
        task_id = "T-PROGRESS-LOG"
        task_manager.active_tasks[task_id] = {
            "fileName": "t.pdf", "active": True, "progress": 0,
            "status": "running", "message": "",
        }
        stop_event = threading.Event()
        activity = [time.monotonic(), False]
        th = threading.Thread(
            target=execute._monitor_windows_console_translate_progress,
            args=(task_id, stop_event, activity),
            daemon=True,
        )
        th.start()
        time.sleep(0.4)  # 等它打开日志并 seek 到末尾

        # tqdm 每次刷新以 \r 开头, 所以"当前行"是被"下一次刷新"结尾的;
        # 这里按同样口径写入(末尾补 \r), 复现产线节奏。
        with open(log_path, "ab") as fp:
            fp.write(TQDM_ZERO + TQDM_LINE + b"\r")
            fp.flush()
        time.sleep(0.4)
        got = task_manager.active_tasks[task_id]
        check("D1 进度不再是 0", got["progress"] == 42, f"progress={got['progress']}")
        check("D2 消息带 curr/total", got["message"] == "translate 8/19", got["message"])
        check("D3 activity[1] 置真(空闲看门狗复活)", activity[1] is True)

        with open(log_path, "ab") as fp:
            fp.write(TQDM_DONE + b"\r")
            fp.flush()
        time.sleep(0.4)
        got = task_manager.active_tasks[task_id]
        check("D4 完成前进度封顶 99", got["progress"] == 99, f"progress={got['progress']}")

        print("[E] 日志源不可用时保持旧行为(静默返回, 不抛错)")
        os.environ[execute.PROGRESS_LOG_ENV] = os.path.join(tmpdir, "no_such_file.log")
        activity2 = [time.monotonic(), False]
        execute._monitor_windows_console_translate_progress(
            "T-MISSING", threading.Event(), activity2)
        check("E1 无源时不抛错且不喂活动", activity2[1] is False)

        stop_event.set()
        th.join(timeout=2.0)
        check("E2 监视器可正常停止", not th.is_alive())
        task_manager.active_tasks.pop(task_id, None)

        print("[F] 共享日志串读闸: 期间另起翻译 -> 不吃它的进度, 恢复独占后继续")
        os.environ[execute.PROGRESS_LOG_ENV] = log_path
        sole_id, other_id = "T-SOLE", "T-OTHER"
        task_manager.active_tasks[sole_id] = {
            "fileName": "s.pdf", "active": True, "progress": 0,
            "status": "running", "message": "",
        }
        stop_f = threading.Event()
        activity_f = [time.monotonic(), False]
        th_f = threading.Thread(
            target=execute._monitor_windows_console_translate_progress,
            args=(sole_id, stop_f, activity_f),
            daemon=True,
        )
        th_f.start()
        time.sleep(0.4)  # 等它打开日志并 seek 到末尾

        # 另一个翻译开始了: 它的 tqdm 与本任务的混在同一个日志文件里
        task_manager.active_tasks[other_id] = {
            "fileName": "o.pdf", "active": True, "progress": 0,
            "status": "running", "message": "",
        }
        with open(log_path, "ab") as fp:
            fp.write(b"\r 16%|\xe2\x96\x88| 3/19 [00:00<00:04, 4.0it/s]\r")
            fp.flush()
        time.sleep(0.4)
        got = task_manager.active_tasks[sole_id]
        check("F1 非独占期间的进度未被告成自己的", got["progress"] == 0,
              f"progress={got['progress']}")
        check("F2 非独占期间不喂活动信号", activity_f[1] is False)

        # 另一个翻译结束 -> 重新独占, 后续进度照常
        task_manager.active_tasks[other_id]["finished"] = True
        with open(log_path, "ab") as fp:
            fp.write(b"\r 79%|\xe2\x96\x88\xe2\x96\x88| 15/19 [00:04<00:01, 3.2it/s]\r")
            fp.flush()
        time.sleep(0.4)
        got = task_manager.active_tasks[sole_id]
        check("F3 恢复独占后进度继续", got["progress"] == 78, f"progress={got['progress']}")
        check("F4 消息同步更新", got["message"] == "translate 15/19", got["message"])
        stop_f.set()
        th_f.join(timeout=2.0)
        task_manager.active_tasks.pop(sole_id, None)
        task_manager.active_tasks.pop(other_id, None)

        print("[G] 启动时就不独占 -> 监视器直接退出(退化, 不影响翻译本身)")
        task_manager.active_tasks["T-BLOCK"] = {
            "fileName": "b.pdf", "active": True, "progress": 0,
            "status": "running", "message": "",
        }
        task_manager.active_tasks["T-OTHER2"] = {
            "fileName": "o2.pdf", "active": True, "progress": 0,
            "status": "running", "message": "",
        }
        check("G1 有并发任务时 _sole_translation 为假",
              execute._sole_translation("T-BLOCK") is False)
        activity_g = [time.monotonic(), False]
        execute._monitor_windows_console_translate_progress(
            "T-BLOCK", threading.Event(), activity_g)
        check("G2 非独占时监视器同步返回且不喂活动", activity_g[1] is False)
        task_manager.active_tasks["T-OTHER2"]["active"] = False
        check("G3 对方结束后恢复独占", execute._sole_translation("T-BLOCK") is True)
        task_manager.active_tasks.pop("T-BLOCK", None)
        task_manager.active_tasks.pop("T-OTHER2", None)
        check("G4 任务表里只剩自己时算独占", execute._sole_translation("T-NOBODY") is True)
    finally:
        os.environ.pop(execute.PROGRESS_SOURCE_ENV, None)
        os.environ.pop(execute.PROGRESS_LOG_ENV, None)
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n结果: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
