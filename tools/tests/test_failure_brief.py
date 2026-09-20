# -*- coding: utf-8 -*-
"""[自研补丁 2026-09-18] 失败根因提取(闸门4)单元测试

背景:
    翻译子进程挂掉时, 任务卡片与 HTTP 响应只拿到
    "Command '...' returned non-zero exit status 1." —— 真正根因(traceback)
    躺在输出里没人看。闸门4 负责把它抽成『异常类 + 文件:行』。
    两条来源, 与进度监视器同源:
      ① 控制台屏幕缓冲  —— 前台(有窗口)启动;
      ② 日志文件尾部    —— launch.ps1 无控制台部署。
    日志是**全服务共享**的(服务自身与所有子进程都往同一个文件写), 所以②有三道闸:
      独占闸(还有别的翻译在跑就不用) / 锚点闸(只认本任务开始之后新增的内容) /
      新鲜度闸(超过 120s 没写过不认, 没有锚点时兜底)。

运行: venv python test_failure_brief.py, 退出码 0=全过
不触网: 不启动子进程, 只喂字符串; 不写 __pycache__(server/ 只读导入)。
与真实部署隔离: 运行期间把 `server._FAILURE_LOG_NAMES` 清空, 只认夹具日志 —— 否则
"刚跑过翻译/渲染"会把真日志的尾部混进来, 抽到的根因不是夹具里的那个(见 [9] 处注释)。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_SERVER = os.path.join(_REPO, "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import server  # noqa: E402

# v26.19 实测形态：tqdm 进度条 + 彩色 ANSI + 子进程 traceback
CONSOLE = (
    "\x1b[32m 42%|\u2588\u2588\u2588\x1b[0m| 8/19 [02:24<03:18, 18.05s/it]\n"
    "  0%|\u2588\x1b[0m| 0/19 [00:00<?, ?it/s]\n"
    "Traceback (most recent call last):\n"
    '  File "D:\\zotero-pdf2zh\\venv\\Lib\\site-packages\\pdf2zh\\high_level.py", line 210, in translate_stream\n'
    "    page.pipe(converter)\n"
    '  File "D:\\zotero-pdf2zh\\venv\\Lib\\site-packages\\pdfminer\\pdfinterp.py", line 400, in render_contents\n'
    "    self.render_content(resources)\n"
    '  File "D:\\zotero-pdf2zh\\venv\\Lib\\site-packages\\pdfminer\\pdffont.py", line 1163, in __init__\n'
    "    _apply_font_char_fixes(self.basefont, self.cid2unicode)\n"
    "AttributeError: 'PDFType3Font' object has no attribute 'basefont'\n"
)

# 两段 traceback：应取最后一段（重试场景）
TWO = (
    "Traceback (most recent call last):\n"
    '  File "a.py", line 1, in old\n'
    "ValueError: old failure\n"
    "restarting...\n"
    "Traceback (most recent call last):\n"
    '  File "b.py", line 9, in new\n'
    "RuntimeError: new failure\n"
)

NO_TRACEBACK = "Command 'x' returned non-zero exit status 1."


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

    original_console_tail = server.console_tail
    original_log_names = server._FAILURE_LOG_NAMES
    tmpdir = tempfile.mkdtemp(prefix="pdf2zh_gate4_")
    log_path = os.path.join(tmpdir, "gate4_fake_server_err.log")

    try:
        print("[1] extract_root_cause: 从 tqdm+ANSI 噪声里抽根因")
        r1 = server.extract_root_cause(CONSOLE) or ""
        check("异常类+消息", "AttributeError: 'PDFType3Font' object has no attribute 'basefont'" in r1, repr(r1))
        check("文件:行(函数)", "@ pdffont.py:1163 (__init__)" in r1, repr(r1))
        check("进度条碎片未混入", "it/s]" not in r1, repr(r1))

        print("[2] extract_root_cause: 多段 traceback 取最后一段")
        r2 = server.extract_root_cause(TWO) or ""
        check("取最后一段", "RuntimeError: new failure" in r2, repr(r2))
        check("取最后一段的帧", "@ b.py:9 (new)" in r2, repr(r2))

        print("[3] extract_root_cause: 无 traceback -> None")
        check("返回 None", server.extract_root_cause(NO_TRACEBACK) is None,
              repr(server.extract_root_cause(NO_TRACEBACK)))

        print("[4] failure_brief: 无 stderr 的 CalledProcessError 走控制台尾部")
        server.console_tail = lambda *a, **k: CONSOLE
        exc = subprocess.CalledProcessError(1, ["pdf2zh.exe", "x.pdf"])
        r4 = server.failure_brief(exc) or ""
        check("抽到根因", "AttributeError" in r4, repr(r4))
        check("带文件行", "@ pdffont.py:1163" in r4, repr(r4))

        print("[5] failure_brief: 控制台不可用时退化为异常字符串（不改失败语义）")
        server.console_tail = lambda *a, **k: ""
        r5 = server.failure_brief(exc) or ""
        check("退化不抛错", "non-zero exit status 1" in r5, repr(r5))

        print("[6] failure_brief: 有信息量的普通异常不被屏幕内容污染")
        server.console_tail = lambda *a, **k: CONSOLE
        r6 = server.failure_brief(FileNotFoundError("Dual file not found: C:\\x\\y.pdf")) or ""
        check("保留原消息", "Dual file not found" in r6, repr(r6))

        print("[7] _derive_error_info: CalledProcessError -> errorType + 根因消息")
        probe = server.PDFTranslator.__new__(server.PDFTranslator)   # 绕开 __init__（它会建目录/起线程）
        info = probe._derive_error_info(exc) or {}
        check("errorType", "AttributeError" in (info.get("errorType") or ""), repr(info))
        check("message", "@ pdffont.py:1163" in (info.get("message") or ""), repr(info))

        print("[8] _derive_error_info: ValueError 优先级仍高于 traceback（不回归）")
        info8 = probe._derive_error_info(ValueError("boom: bad config value")) or {}
        check("ValueError 仍被识别", "ValueError" in (info8.get("errorType") or ""), repr(info8))

        print("[9] failure_output_tail: 无控制台时改读日志尾部")
        with open(log_path, "w", encoding="utf-8") as fp:
            fp.write(CONSOLE)
        server.console_tail = lambda *a, **k: ""          # 模拟 launch.ps1 的无控制台部署
        os.environ["PDF2ZH_FAILURE_LOG"] = log_path
        r9 = server.failure_output_tail() or ""
        check("从日志拿到 traceback", "AttributeError" in r9, repr(r9))
        check("日志路径 override 生效", log_path in ";".join(server._failure_log_paths()),
              ";".join(server._failure_log_paths()))
        # [自研补丁 2026-09-19] 与真实部署隔离。_failure_log_paths() = "env 覆盖 +
        # <项目>/logs/{server_err,server_out}.log"，后两者只要**刚跑过翻译/渲染**就落在
        # 120s 新鲜窗口里，会被一并读进尾部 —— 抽到的"根因"成了那次真翻译的输出，
        # 本套件当场假红（实测：12:47 跑完 render，紧随其后的 run_all 在 [12] 失败）。
        # 本套件只喂字符串、不启子进程，故把真日志名临时清空，只留夹具。
        server._FAILURE_LOG_NAMES = ()

        print("[10] failure_brief: 无控制台 + 新鲜日志 -> 仍能抽到根因")
        r10 = server.failure_brief(subprocess.CalledProcessError(1, ["pdf2zh.exe", "x.pdf"])) or ""
        check("抽到根因", "@ pdffont.py:1163" in r10, repr(r10))

        print("[11] 陈旧日志不被认（防把上次失败的 traceback 张冠李戴）")
        old = time.time() - (server._FAILURE_LOG_FRESH_SECONDS + 60)
        os.utime(log_path, (old, old))
        r11 = server.failure_brief(subprocess.CalledProcessError(1, ["pdf2zh.exe", "x.pdf"])) or ""
        check("退化为原异常字符串", "non-zero exit status 1" in r11, repr(r11))
        check("未混入陈旧 traceback", "AttributeError" not in r11, repr(r11))

        print("[12] _derive_error_info: 无控制台 + 新鲜日志 -> errorType 是真异常类")
        with open(log_path, "w", encoding="utf-8") as fp:
            fp.write(CONSOLE)
        info12 = probe._derive_error_info(subprocess.CalledProcessError(1, ["pdf2zh.exe", "x.pdf"])) or {}
        check("errorType", "AttributeError" in (info12.get("errorType") or ""), repr(info12))
        check("message 带文件行", "@ pdffont.py:1163" in (info12.get("message") or ""), repr(info12))

        # ---- [自研补丁 2026-09-18] 共享日志的三道闸 ----
        print("[13] 锚点闸: 只认本任务开始后新增的内容(挡住上次失败的 traceback)")
        with open(log_path, "w", encoding="utf-8") as fp:
            fp.write(CONSOLE)                     # 上一次失败留在日志里的旧 traceback
        server.anchor_failure_logs("T-ANCHOR")
        r13a = server.failure_brief(exc, task_id="T-ANCHOR") or ""
        check("锚点之后无新增 -> 不认旧 traceback", "AttributeError" not in r13a, repr(r13a))
        check("退化为原异常字符串", "non-zero exit status 1" in r13a, repr(r13a))

        FRESH = (
            "Traceback (most recent call last):\n"
            '  File "D:\\zotero-pdf2zh\\venv\\Lib\\site-packages\\pdf2zh\\converter.py", line 42, in frobnicate\n'
            "    page.pipe(self)\n"
            "ZeroDivisionError: division by zero\n"
        )
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write(FRESH)                       # 本次失败新写的 traceback
        r13b = server.failure_brief(exc, task_id="T-ANCHOR") or ""
        check("新增的 traceback 被采纳", "ZeroDivisionError" in r13b, repr(r13b))
        check("旧 traceback 还在文件里但不再被采纳", "AttributeError" not in r13b, repr(r13b))

        print("[14] _tail_file(start=): 锚点语义只读新增部分")
        size = os.path.getsize(log_path)
        check("无新增返回空", server._tail_file(log_path, start=size) == "")
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write("MARKER-fresh\n")
        tail = server._tail_file(log_path, start=size)
        check("只含新增部分", "MARKER-fresh" in tail and "ZeroDivision" not in tail, repr(tail))
        check("start=0 仍读全量", "AttributeError" in (server._tail_file(log_path, start=0) or ""))

        print("[15] 独占闸: 有别的翻译在跑时弃用共享日志(宁缺勿错)")
        now = time.time()
        os.utime(log_path, (now, now))            # 新鲜度闸不拦, 单独验独占闸
        server.task_manager.active_tasks["T-OTHER-ACTIVE"] = {
            "fileName": "o.pdf", "active": True, "progress": 0,
            "status": "running", "message": "",
        }
        check("_sole_task 为假", server._sole_task(None) is False)
        check("failure_output_tail 弃用日志", server.failure_output_tail() == "")
        r15 = server.failure_brief(exc) or ""
        check("failure_brief 退化为原异常字符串",
              "non-zero exit status 1" in r15 and "ZeroDivision" not in r15, repr(r15))
        server.task_manager.active_tasks["T-OTHER-ACTIVE"]["finished"] = True
        check("对方结束后重新可用", "ZeroDivision" in (server.failure_output_tail() or ""))
        server.task_manager.active_tasks.pop("T-OTHER-ACTIVE", None)

        print("[16] 锚点表有上限, 不随任务数无限增长")
        for i in range(server._FAILURE_LOG_ANCHOR_KEEP + 8):
            server.anchor_failure_logs("T-ANCHOR-%d" % i)
        check("不超过上限",
              len(server._FAILURE_LOG_ANCHORS) <= server._FAILURE_LOG_ANCHOR_KEEP,
              str(len(server._FAILURE_LOG_ANCHORS)))
    finally:
        server.console_tail = original_console_tail
        server._FAILURE_LOG_NAMES = original_log_names
        server._FAILURE_LOG_ANCHORS.clear()
        os.environ.pop("PDF2ZH_FAILURE_LOG", None)
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n结果: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
