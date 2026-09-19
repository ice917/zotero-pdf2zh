import os
import re
import select
import subprocess
import sys
import threading
import time
from datetime import datetime

# [自研补丁 2026-09-03] 子进程看门狗: 翻译网络停滞时任务曾永久卡在 running
# (实测 16 分钟无输出), 只能重启服务。加"总时长兜底 + 空闲超时"双保险,
# 可用环境变量(秒)调整: PDF2ZH_TASK_TOTAL_TIMEOUT / PDF2ZH_TASK_IDLE_TIMEOUT
TASK_TOTAL_TIMEOUT = float(os.environ.get("PDF2ZH_TASK_TOTAL_TIMEOUT", str(3 * 60 * 60)))
TASK_IDLE_TIMEOUT = float(os.environ.get("PDF2ZH_TASK_IDLE_TIMEOUT", str(30 * 60)))

# [自研补丁 2026-09-18] 无控制台部署下子进程进度输出的落点:
# launch.ps1 用 -RedirectStandardError 把 stderr 写到这里, 而 tqdm 默认写 stderr。
# 仓库根 = server/utils/execute.py 上溯三级。
PROGRESS_LOG_ENV = "PDF2ZH_PROGRESS_LOG"
PROGRESS_LOG_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "logs",
    "server_err.log",
)
# 进度来源强制开关(回归测试用): console / log / 空=自动探测
PROGRESS_SOURCE_ENV = "PDF2ZH_PROGRESS_SOURCE"


class TaskTimeoutError(RuntimeError):
    """翻译子进程触发看门狗(总超时或空闲超时)。"""


# [自研补丁] Windows 控制台屏幕缓冲是进程级共享资源: 多个翻译任务同时挂
# 控制台监视器会互相串读进度(把别人任务的 x/y 写到自己任务上)。同一时刻
# 只允许一组监视器/宽度守护, 后来任务退化为"无实时进度"(与无控制台环境
# 行为一致, 不影响翻译本身)。
_windows_console_helpers_active = threading.Semaphore(1)  # [2026-09-06 修正] Event 无 acquire, 误用为锁导致首次翻译 AttributeError

from utils.deepseek_thinking import prepare_deepseek_runtime_command
from utils.environment_lifecycle import managed_python_env
from utils.task_manager import task_manager

# Match lines like: "translate ... 10/100"
# MAIN_PROGRESS_RE = re.compile(r"\btranslate\b[^\r\n]*?(\d+)/(\d+)\b", re.IGNORECASE)

# pdf2zh_next
# 核心修改：在 translate 和 分数 之间，严禁出现英文字母 (a-z) 和括号
# 这样就能完美避开类似 "Translate Paragraphs (1/1)" 这种子任务。
MAIN_PROGRESS_RE = re.compile(
    r"\btranslate\s+[^a-z\(\)\r\n]*?(\d+)/(\d+)\b", 
    re.IGNORECASE
)

# Match step-like lines where only status message should be updated
STEP_PROGRESS_RE = re.compile(r"(.+?)\(\d+/\d+\)\s+.*?(\d+)/(\d+)")

# Legacy pdf2zh (1.x) progress format
LEGACY_PROGRESS_RE = re.compile(r"(?:translate|Running|Parse).*?(\d+)/(\d+)", re.IGNORECASE)

# Strip ANSI sequences before regex matching
ANSI_ESCAPE = re.compile(r"(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]")


# 🌟 新增：专门针对 pdf2zh (tqdm) 进度条的精准捕获！
# 特征：匹配竖线 "|" 加上 " 3/17 [" 这样的格式
PDF2ZH_TQDM_RE = re.compile(r"\|\s*(\d+)/(\d+)\s+\[")

WINDOWS_MONITOR_INTERVAL = 0.05
WINDOWS_CONSOLE_SCAN_ROWS = 220
# Floor only. Never report a fake wide terminal (e.g. 200): rich/tqdm will
# draw a line that wraps on the real console, and in-place updates turn into
# duplicated progress spam. Web progress parsing still works because the
# `translate x/y` token stays on one logical line when COLUMNS matches reality.

# DEBUG_PROGRESS_LOG_PATH = os.path.join(
#     os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
#     "_debug_progress.log",
# )
# _DEBUG_PROGRESS_LOG_LOCK = threading.Lock()


# def _debug_progress_log(stage, **fields):
#     try:
#         ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
#         parts = []
#         for key in sorted(fields.keys()):
#             value = str(fields[key]).replace("\r", "\\r").replace("\n", "\\n")
#             if len(value) > 260:
#                 value = value[:260] + "..."
#             parts.append(f"{key}={value}")
#         line = f"[{ts}] [{stage}] " + " ".join(parts)
#         with _DEBUG_PROGRESS_LOG_LOCK:
#             with open(DEBUG_PROGRESS_LOG_PATH, "a", encoding="utf-8", errors="replace") as fp:
#                 fp.write(line + "\n")
#     except Exception:
#         pass


def _windows_visible_console_size():
    """Visible window size, not the (often much wider) screen buffer."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", wintypes.SHORT),
                ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT),
                ("Bottom", wintypes.SHORT),
            ]

        class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", COORD),
                ("dwCursorPosition", COORD),
                ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD),
            ]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            return None
        csbi = CONSOLE_SCREEN_BUFFER_INFO()
        if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(csbi)):
            return None
        win = csbi.srWindow
        width = int(win.Right - win.Left + 1)
        height = int(win.Bottom - win.Top + 1)
        if width > 0 and height > 0:
            return width, height
    except Exception:
        return None
    return None


def _detect_terminal_size(default_cols=80, default_rows=24):
    """Size of the terminal the user actually sees."""
    cols, rows = default_cols, default_rows
    visible = _windows_visible_console_size()
    if visible:
        cols, rows = visible
    else:
        try:
            size = os.get_terminal_size()
            if size.columns > 0:
                cols = size.columns
            if size.lines > 0:
                rows = size.lines
        except (OSError, ValueError, AttributeError):
            pass
    return max(40, int(cols)), max(10, int(rows))


def _apply_terminal_size_env(env, cols, rows):
    env["COLUMNS"] = str(cols)
    env["LINES"] = str(rows)


def _child_terminal_size(cols, rows):
    # POSIX terminals wrap when a line uses every column. rich then emits
    # another update with \r, which only returns to the wrapped line, so
    # `translate` appears to reprint. Draw one column narrower than the
    # parent tty so in-place updates stay on one row.
    return max(40, int(cols) - 1), max(10, int(rows))


def _decode_pty_utf8(data, leftover):
    """[自研补丁 2026-09-03] 增量 UTF-8 解码: 末尾不完整的多字节字符留到
    下一块; 若流中出现真正的非法字节(整包及回退 1~3 字节都无法解码),
    用 errors='replace' 强制前进并清空 leftover——旧实现把非法字节永久
    留在 leftover 头部, 之后每一块都解码失败, 输出/进度解析永久卡死。"""
    buf = leftover + data
    for cut in range(0, 4):
        head = buf[: len(buf) - cut] if cut else buf
        try:
            return head.decode("utf-8"), (buf[len(buf) - cut:] if cut else b"")
        except UnicodeDecodeError:
            continue
    return buf.decode("utf-8", errors="replace"), b""


def _write_pty_chunk(data, leftover, task_id):
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except Exception:
        text, leftover = _decode_pty_utf8(data, leftover)
        sys.stdout.write(text)
        sys.stdout.flush()
        _parse_progress(text, task_id)
        return leftover
    text, leftover = _decode_pty_utf8(data, leftover)
    _parse_progress(text, task_id)
    return leftover


def execute_with_progress(cmd, task_id, args, env_manager):
    """Execute translation command and update task progress in real time."""
    final_cmd = cmd
    final_env = os.environ.copy()
    final_env["PYTHONUNBUFFERED"] = "1"
    final_env["FORCE_COLOR"] = "1"
    final_env["FORCE_TERMINAL"] = "1"
    final_env.pop("NO_COLOR", None)
    final_env["TERM"] = "xterm-256color"

    cols, rows = _detect_terminal_size()
    child_cols, child_rows = _child_terminal_size(cols, rows)
    _apply_terminal_size_env(final_env, child_cols, child_rows)

    if args.enable_venv and env_manager:
        # [自研补丁] 引擎显式传参, 不再让 venv 模块靠全命令行子串嗅探
        engine = 'pdf2zh_next' if str(final_cmd[0]).lower() == 'pdf2zh_next' else 'pdf2zh'
        venv_cmd, venv_env = env_manager.get_command_and_env(final_cmd, engine)
        final_cmd = venv_cmd
        final_env.update(venv_env)
        final_env = managed_python_env(final_env)
        # venv env is copied from os.environ and would undo COLUMNS/LINES.
        _apply_terminal_size_env(final_env, child_cols, child_rows)

    # DeepSeek V4 is special: the plugin stores the user's choice in generic
    # extraData, while pdf2zh_next 2.9+ exposes that choice as explicit CLI
    # flags. Validate the exact runtime that will be executed, then add those
    # flags before any translation/API request begins.
    final_cmd = prepare_deepseek_runtime_command(final_cmd, final_env)

    print(f"[execute_with_progress] {' '.join(final_cmd)}\n")

    if sys.platform != "win32":
        _execute_with_pty(final_cmd, final_env, task_id, child_cols, child_rows)
    else:
        _execute_with_inherit(final_cmd, final_env, task_id, cols)

def _parse_progress(text, task_id):
    """Parse progress info from text and update task_manager."""
    if task_id is None:
        return

    clean = ANSI_ESCAPE.sub("", text)
    
    # 【新增调试日志】把每次 PTY 读取到的"大块头"文本极其原貌（包括 \r 和 \n）打出来
    # _debug_progress_log("MAC_PTY_READ", raw_chunk=repr(clean))

    # 以下完全恢复成你的原始代码！
    # Main translation progress (preferred)
    match = MAIN_PROGRESS_RE.search(clean)
    if match:
        curr, total = int(match.group(1)), int(match.group(2))
        if total > 0:
            pct = int((curr / total) * 100)
            task_manager.update_task(task_id, {
                "progress": pct,
                "status": "running",
                "message": f"translate {curr}/{total}",
            })
            # 【新增调试日志】记录 Main 正则抓到了什么
            # _debug_progress_log("MATCH_MAIN", curr=curr, total=total, pct=pct)
        return

    # Step status text (secondary)
    match = STEP_PROGRESS_RE.search(clean)
    if match:
        step_name = match.group(1).strip()
        task_manager.update_task(task_id, {
            "status": "running",
            "message": step_name,
        })
        # 【新增调试日志】
        # _debug_progress_log("MATCH_STEP", step=step_name)
        return

    # 🌟 3. 处理 pdf2zh 原生引擎的 tqdm 进度条
    tqdm_matches = PDF2ZH_TQDM_RE.findall(clean)
    if tqdm_matches:
        curr, total = int(tqdm_matches[-1][0]), int(tqdm_matches[-1][1])
        if total > 0:
            task_manager.update_task(task_id, {
                "progress": int((curr / total) * 100),
                "status": "running",
                "message": f"translate {curr}/{total}",
            })
        # [自研补丁 2026-09-03] tqdm 命中即终态: 必须 return, 否则
        # legacy 正则可能在同一文本上二次匹配并覆写进度
        return

    # Legacy format
    match = LEGACY_PROGRESS_RE.search(clean)
    if match:
        curr, total = int(match.group(1)), int(match.group(2))
        if total > 0:
            pct = int((curr / total) * 100)
            task_manager.update_task(task_id, {
                "progress": pct,
                "status": "running",
            })
            # 【新增调试日志】记录 Legacy 正则是不是抓错了
            # _debug_progress_log("MATCH_LEGACY", curr=curr, total=total, pct=pct)

# def _parse_progress(text, task_id):
#     """Parse progress info from text and update task_manager."""
#     if task_id is None:
#         return

#     clean = ANSI_ESCAPE.sub("", text)

#     # Main translation progress (preferred)
#     # 子步骤状态文本 (次要)
#     # 子步骤状态文本 (次要)
#     match = STEP_PROGRESS_RE.search(clean)
#     if match:
#         # 只提取当前正在做什么，比如 "Parse PDF and Create Intermediate Representation"
#         step_name = match.group(1).strip() 
        
#         # ⚠️ 绝对不要在这里计算和更新 progress！只更新 message！
#         task_manager.update_task(task_id, {
#             "status": "running",
#             "message": step_name,
#         })
#         return

#     # Step status text (secondary)
#     match = STEP_PROGRESS_RE.search(clean)
#     if match:
#         step_name = match.group(1).strip()
#         task_manager.update_task(task_id, {
#             "status": "running",
#             "message": step_name,
#         })
#         return

#     # Legacy format
#     match = LEGACY_PROGRESS_RE.search(clean)
#     if match:
#         curr, total = int(match.group(1)), int(match.group(2))
#         if total > 0:
#             pct = int((curr / total) * 100)
#             task_manager.update_task(task_id, {
#                 "progress": pct,
#                 "status": "running",
#             })


def _execute_with_pty(final_cmd, final_env, task_id, cols, rows):
    """macOS/Linux: run command in PTY and parse progress from stream."""
    import pty

    master_fd, slave_fd = pty.openpty()

    try:
        import fcntl
        import termios
        import struct

        winsize = struct.pack("HHHH", int(rows), int(cols), 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
    except Exception:
        pass

    process = subprocess.Popen(
        final_cmd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=final_env,
        bufsize=0,
        close_fds=True,
    )
    os.close(slave_fd)
    leftover = b""

    # [自研补丁] 看门狗: 总时长兜底 + 无输出空闲判定(POSIX 路径)
    start_ts = time.monotonic()
    last_activity = start_ts

    try:
        while True:
            now = time.monotonic()
            if now - start_ts > TASK_TOTAL_TIMEOUT:
                process.kill()
                raise TaskTimeoutError(
                    f"翻译总时长超过 {TASK_TOTAL_TIMEOUT / 60:.0f} 分钟, "
                    "已终止子进程(可用环境变量 PDF2ZH_TASK_TOTAL_TIMEOUT 调整)")
            if now - last_activity > TASK_IDLE_TIMEOUT:
                process.kill()
                raise TaskTimeoutError(
                    f"翻译连续 {TASK_IDLE_TIMEOUT / 60:.0f} 分钟无任何输出, "
                    "判定网络停滞, 已终止子进程(可用 PDF2ZH_TASK_IDLE_TIMEOUT 调整)")

            readable, _, _ = select.select([master_fd], [], [], 0.5)
            if master_fd in readable:
                try:
                    data = os.read(master_fd, 4096)
                    if not data:
                        break
                    last_activity = time.monotonic()
                    leftover = _write_pty_chunk(data, leftover, task_id)
                except OSError:
                    break

            if process.poll() is not None:
                try:
                    import fcntl as _fcntl

                    fl = _fcntl.fcntl(master_fd, _fcntl.F_GETFL)
                    _fcntl.fcntl(master_fd, _fcntl.F_SETFL, fl | os.O_NONBLOCK)
                    while True:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        leftover = _write_pty_chunk(data, leftover, task_id)
                except Exception:
                    pass
                break

        os.close(master_fd)
        return_code = process.wait()
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, final_cmd)

    except Exception:
        if process.poll() is None:
            process.kill()
        try:
            os.close(master_fd)
        except Exception:
            pass
        raise


def _match_progress_line(line):
    """
    从一行输出里取 (curr, total)，取不到返回 None。

    [自研补丁 2026-09-18] 两个正则都要试。旧代码只试 MAIN_PROGRESS_RE
    (要求出现 "translate" 字样)，而 pdf2zh 1.x 的页进度条是
    `tqdm.tqdm(total=total_pages)` —— **不带 desc**，实测输出形如
    ` 42%|███▏| 8/19 [00:02<00:03, 2.89it/s]`，压根没有 "translate"
    这个词，所以旧代码在 pdf2zh 1.x 上永远匹配不上(进度恒为 0)。
    PTY 那条路径(_parse_progress)本来就有 PDF2ZH_TQDM_RE 兜底，这里是补齐。
    """
    m = MAIN_PROGRESS_RE.search(line)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = PDF2ZH_TQDM_RE.search(line)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _open_progress_log():
    """
    [自研补丁 2026-09-18] 无控制台部署的进度来源。

    launch.ps1 是 `-RedirectStandardOutput/Error + -WindowStyle Hidden` 启动服务的,
    服务与子进程都**没有控制台**, 子进程 tqdm 继承的是**日志文件句柄**
    (实测 logs/server_err.log 里躺着完整的 ` 42%|███| 8/19 [..]` 流)。
    所以读屏幕缓冲永远读不到进度 —— 与闸门4 栽的是同一个坑。
    从**当前文件末尾**开始跟读: 只认本次任务新写的行, 不会被上一次任务
    留在日志里的旧 x/y 污染(否则会锁错 total)。
    """
    path = os.environ.get(PROGRESS_LOG_ENV) or PROGRESS_LOG_DEFAULT
    try:
        fp = open(path, "rb")
        fp.seek(0, os.SEEK_END)
    except OSError:
        return None
    return {"fp": fp, "buf": b"", "seq": 0}


def _read_progress_log(state):
    """
    读出自上次以来新写入的完整行, 返回 (pairs, seq_end);
    pairs 为 [(seq, line), ...], seq 全局单调递增。

    tqdm 输出到**非 tty** 时仍用 '\\r' 原地刷新(实测), 所以 '\\r' 和 '\\n'
    都当行分隔符 —— 不切 '\\r' 的话整个进度流是一条几十 KB 的巨行,
    正则只能看到最后那次刷新。残段留到下次, 避免读到半行。
    """
    fp = state["fp"]
    try:
        chunk = fp.read()
    except OSError:
        return [], state["seq"]
    if not chunk:
        return [], state["seq"]

    data = state["buf"] + chunk
    idx = max(data.rfind(b"\n"), data.rfind(b"\r"))
    if idx < 0:
        # 一直没有分隔符: 只留尾部, 防止无换行输出把缓冲撑爆
        state["buf"] = data[-65536:]
        return [], state["seq"]
    head, state["buf"] = data[:idx], data[idx + 1:]

    pairs = []
    seq = state["seq"]
    for raw in head.split(b"\r"):
        for piece in raw.split(b"\n"):
            if not piece:
                continue
            seq += 1
            pairs.append((seq, piece.decode("utf-8", "replace")))
    state["seq"] = seq
    return pairs, seq


def _sole_translation(task_id):
    """[自研补丁 2026-09-18] 本任务是否独占输出流(没有别的翻译在跑)。

    logs/server_err.log 是全服务共享的: 服务自身与**所有**子进程都往同一个
    文件写, 另一个并发翻译的 tqdm 会原样落在里面。页数相同时 locked_total
    也挡不住, 所以只在独占时才敢把日志当进度源。
    取不到任务表就返回 False(不独占) —— 宁可不出进度, 也不能把别人的
    进度显示成自己的。
    """
    try:
        return not task_manager.has_other_running(task_id)
    except Exception:
        return False


def _monitor_windows_console_translate_progress(task_id, stop_event, activity=None):
    """
    Windows-only monitor: 解析 "translate ... x/y" 或 pdf2zh 1.x 的 tqdm 页进度条,
    让 SSE 能更新任务卡片, 同时不动终端里原生的多进度条 UI。

    两条来源, 按部署形态自动选:
      ① 控制台屏幕缓冲 —— 前台(有窗口)启动时用;
      ② 日志文件尾部   —— launch.ps1 无控制台部署时用(见 _open_progress_log)。

    [自研补丁] activity: 可选的 [最后活动时间戳, 是否曾解析到进度] 列表,
    供空闲看门狗判断子进程是否真的在推进。
    """
    if task_id is None:
        return

    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        # _debug_progress_log("MONITOR_IMPORT_ERROR", task_id=task_id)
        return

    STD_OUTPUT_HANDLE = -11
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class COORD(ctypes.Structure):
        _fields_ = [
            ("X", wintypes.SHORT),
            ("Y", wintypes.SHORT),
        ]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", wintypes.SHORT),
            ("Top", wintypes.SHORT),
            ("Right", wintypes.SHORT),
            ("Bottom", wintypes.SHORT),
        ]

    class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", COORD),
            ("dwCursorPosition", COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", SMALL_RECT),
            ("dwMaximumWindowSize", COORD),
        ]

    try:
        kernel32 = ctypes.windll.kernel32

        get_std_handle = kernel32.GetStdHandle
        get_std_handle.argtypes = [wintypes.DWORD]
        get_std_handle.restype = wintypes.HANDLE

        get_csbi = kernel32.GetConsoleScreenBufferInfo
        get_csbi.restype = wintypes.BOOL

        read_console = kernel32.ReadConsoleOutputCharacterW
        read_console.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            COORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        read_console.restype = wintypes.BOOL
    except Exception as e:
        # _debug_progress_log("MONITOR_WINAPI_ERROR", task_id=task_id, error=str(e))
        return

    handle = get_std_handle(STD_OUTPUT_HANDLE)
    # [自研补丁 2026-09-18] 句柄"有效"不等于"是控制台": stdout 被重定向到文件时
    # GetStdHandle 返回的是**文件句柄**, 旧代码会把它当控制台用, 然后
    # GetConsoleScreenBufferInfo 失败 → break → 监视器静默退出(进度恒 0,
    # 且 activity[1] 永远 False 连带废掉空闲看门狗)。这里实探一次。
    console_usable = False
    if handle not in (None, 0, INVALID_HANDLE_VALUE):
        try:
            probe = CONSOLE_SCREEN_BUFFER_INFO()
            console_usable = bool(get_csbi(handle, ctypes.byref(probe)))
        except Exception:
            console_usable = False

    forced_source = (os.environ.get(PROGRESS_SOURCE_ENV) or "").strip().lower()
    if forced_source == "log":
        console_usable = False
    elif forced_source == "console" and handle not in (None, 0, INVALID_HANDLE_VALUE):
        console_usable = True

    log_state = None
    if console_usable:
        source_mode = "console"
    else:
        source_mode = "log"
        # [自研补丁 2026-09-18] 日志是全服务共享的, 只在独占时才敢当进度源:
        # 有并发翻译时退化为"无实时进度"(与无控制台环境的旧行为一致, 不影响翻译)。
        if not _sole_translation(task_id):
            # _debug_progress_log("MONITOR_LOG_NOT_SOLE", task_id=task_id)
            return
        log_state = _open_progress_log()
        if log_state is None:
            # 既没有控制台也读不到日志(非 launch.ps1 形态): 保持旧行为——静默返回,
            # 只是没有实时进度, 不影响翻译本身。
            # _debug_progress_log("MONITOR_NO_SOURCE", task_id=task_id)
            return

    # _debug_progress_log("MONITOR_STARTED", task_id=task_id, source=source_mode)

    locked_total = None
    last_curr = None
    last_pos = None
    last_step = None
    idle_ticks = 0
    last_error = ""

    while not stop_event.is_set():
        try:
            pair_candidates = []
            latest_step = None
            latest_step_pos = -1
            # 候选位置: 控制台用行号, 日志用递增 seq, 一律"越大越新"
            cursor_pos = 0
            translate_line_sample = None

            if console_usable:
                csbi = CONSOLE_SCREEN_BUFFER_INFO()
                if not get_csbi(handle, ctypes.byref(csbi)):
                    # _debug_progress_log("MONITOR_CSBI_FAIL", task_id=task_id)
                    break

                width = max(int(csbi.dwSize.X), 1)
                buffer_rows = max(int(csbi.dwSize.Y), 1)
                cursor_y = int(csbi.dwCursorPosition.Y)
                cursor_pos = cursor_y

                start_row = max(0, cursor_y - WINDOWS_CONSOLE_SCAN_ROWS)
                end_row = min(buffer_rows - 1, cursor_y + 2)
                if end_row < start_row:
                    start_row, end_row = 0, min(buffer_rows - 1, WINDOWS_CONSOLE_SCAN_ROWS)

                for row in range(start_row, end_row + 1):
                    buf = ctypes.create_unicode_buffer(width)
                    chars_read = wintypes.DWORD(0)
                    ok = read_console(
                        handle,
                        buf,
                        width,
                        COORD(0, row),
                        ctypes.byref(chars_read),
                    )
                    if not ok or chars_read.value <= 0:
                        continue

                    line = buf.value[: chars_read.value].strip()
                    if not line:
                        continue

                    if translate_line_sample is None and "translate" in line.lower():
                        translate_line_sample = line[:220]

                    step_m = STEP_PROGRESS_RE.search(line)
                    if step_m and row >= latest_step_pos:
                        latest_step = step_m.group(1).strip()
                        latest_step_pos = row

                    matched = _match_progress_line(line)
                    if not matched:
                        continue

                    curr, total = matched
                    if total <= 0:
                        continue
                    if locked_total is not None and total != locked_total:
                        continue

                    pair_candidates.append((row, curr, total))
            else:
                # [自研补丁 2026-09-18] 无控制台部署: 从日志尾部跟读。
                # 行按写入先后返回, 位置用递增 seq 表示(越大越新)。
                pairs, _seq_end = _read_progress_log(log_state)
                if not pairs:
                    stop_event.wait(WINDOWS_MONITOR_INTERVAL)
                    continue
                cursor_pos = pairs[-1][0]

                # 期间另起了翻译 -> 本批日志里可能混着它的 tqdm。只推进读取位置
                # (_read_progress_log 已把新内容消费掉), 不回报; 等重新独占再继续。
                if not _sole_translation(task_id):
                    stop_event.wait(WINDOWS_MONITOR_INTERVAL)
                    continue

                for seq, raw_line in pairs:
                    line = ANSI_ESCAPE.sub("", raw_line).strip()
                    if not line:
                        continue

                    step_m = STEP_PROGRESS_RE.search(line)
                    if step_m:
                        latest_step = step_m.group(1).strip()
                        latest_step_pos = seq

                    matched = _match_progress_line(line)
                    if not matched:
                        continue

                    curr, total = matched
                    if total <= 0:
                        continue
                    if locked_total is not None and total != locked_total:
                        continue

                    pair_candidates.append((seq, curr, total))

            if pair_candidates:
                idle_ticks = 0
                if locked_total is None:
                    max_total = max(c[2] for c in pair_candidates)
                    pair_candidates = [c for c in pair_candidates if c[2] == max_total]
                    locked_total = max_total
                    if console_usable:
                        # 首次锁 total: 挑离光标最近的候选
                        pair_candidates.sort(key=lambda c: (abs(c[0] - cursor_pos), -c[0], -c[1]))
                    else:
                        pair_candidates.sort(key=lambda c: c[0])
                elif console_usable:
                    def _rank(candidate):
                        pos = candidate[0]
                        dist_prev = abs(pos - last_pos) if last_pos is not None else abs(pos - cursor_pos)
                        return (dist_prev, abs(pos - cursor_pos), -pos)

                    pair_candidates.sort(key=_rank)
                else:
                    # 日志模式: 同一批里最新的一行就是当前真实进度
                    pair_candidates.sort(key=lambda c: c[0])

                pick = pair_candidates[0] if console_usable else pair_candidates[-1]
                pos, curr, total = pick

                if last_curr is None or curr >= last_curr:
                    last_pos = pos
                    if last_curr is None or curr != last_curr:
                        last_curr = curr
                        # Keep 100% for final completion update only.
                        pct = 99 if curr >= total else int((curr / total) * 100)
                        task_manager.update_task(task_id, {
                            "progress": pct,
                            "status": "running",
                            "message": f"translate {curr}/{total}",
                        })
                        # [自研补丁] 进度真实推进, 喂狗
                        if activity is not None:
                            activity[0] = time.monotonic()
                            activity[1] = True
                        # _debug_progress_log(
                        #     "PARSE_PROGRESS",
                        #     task_id=task_id,
                        #     row=pos,
                        #     curr=curr,
                        #     total=total,
                        #     pct=pct,
                        #     locked_total=locked_total,
                        # )
            else:
                idle_ticks += 1
                if idle_ticks % 40 == 0:
                    pass  # _debug_progress_log(
                    #     "PARSE_IDLE",
                    #     task_id=task_id,
                    #     cursor=cursor_pos,
                    #     locked_total=locked_total,
                    #     sample=translate_line_sample,
                    # )

            if latest_step and latest_step != last_step:
                last_step = latest_step
                task_manager.update_task(task_id, {
                    "status": "running",
                    "message": latest_step,
                })
                # [自研补丁] 阶段切换也算活动, 喂狗
                if activity is not None:
                    activity[0] = time.monotonic()
                    activity[1] = True
                # _debug_progress_log("PARSE_STEP", task_id=task_id, step=latest_step)
        except Exception as e:
            err_text = str(e)
            if err_text != last_error:
                last_error = err_text
                # _debug_progress_log("MONITOR_ERROR", task_id=task_id, error=err_text)

        stop_event.wait(WINDOWS_MONITOR_INTERVAL)


def _guard_windows_console_min_width(stop_event, min_cols):
    """
    Restore the window width we started with if the user shrinks it mid-run.
    Do not force a fake 160-column window: that only widens the buffer and
    makes rich wrap even harder on the visible area.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return

    STD_OUTPUT_HANDLE = -11
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", wintypes.SHORT),
            ("Top", wintypes.SHORT),
            ("Right", wintypes.SHORT),
            ("Bottom", wintypes.SHORT),
        ]

    class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", COORD),
            ("dwCursorPosition", COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", SMALL_RECT),
            ("dwMaximumWindowSize", COORD),
        ]

    try:
        kernel32 = ctypes.windll.kernel32

        get_std_handle = kernel32.GetStdHandle
        get_std_handle.argtypes = [wintypes.DWORD]
        get_std_handle.restype = wintypes.HANDLE

        get_csbi = kernel32.GetConsoleScreenBufferInfo
        get_csbi.restype = wintypes.BOOL

        set_buffer_size = kernel32.SetConsoleScreenBufferSize
        set_buffer_size.argtypes = [wintypes.HANDLE, COORD]
        set_buffer_size.restype = wintypes.BOOL

        set_window_info = kernel32.SetConsoleWindowInfo
        set_window_info.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(SMALL_RECT)]
        set_window_info.restype = wintypes.BOOL
    except Exception:
        return

    handle = get_std_handle(STD_OUTPUT_HANDLE)
    if handle in (None, 0, INVALID_HANDLE_VALUE):
        return

    while not stop_event.is_set():
        try:
            csbi = CONSOLE_SCREEN_BUFFER_INFO()
            if not get_csbi(handle, ctypes.byref(csbi)):
                break

            win = csbi.srWindow
            width = int(win.Right - win.Left + 1)
            height = int(win.Bottom - win.Top + 1)

            if width < min_cols:
                max_w = max(int(csbi.dwMaximumWindowSize.X) or min_cols, 1)
                target_width = min(int(min_cols), max_w)
                if width >= target_width:
                    stop_event.wait(0.08)
                    continue
                target_height = max(int(csbi.dwSize.Y), height)
                target_buffer_width = max(int(csbi.dwSize.X), target_width)

                set_buffer_size(handle, COORD(target_buffer_width, target_height))

                new_top = max(0, min(int(win.Top), target_height - height))
                new_rect = SMALL_RECT(
                    0,
                    new_top,
                    target_width - 1,
                    new_top + height - 1,
                )
                set_window_info(handle, True, ctypes.byref(new_rect))
        except Exception:
            pass

        stop_event.wait(0.08)


def _execute_with_inherit(final_cmd, final_env, task_id, cols):
    """
    Windows: inherit stdout/stderr so terminal keeps native multi-progress UI.
    Progress parsing is done by a side monitor reading console buffer.
    """
    process = subprocess.Popen(
        final_cmd,
        stdout=None,
        stderr=None,
        env=final_env,
        bufsize=0,
        text=False,
    )

    # _debug_progress_log("EXECUTE_START", task_id=task_id, cmd=" ".join(final_cmd))

    stop_event = threading.Event()
    # [自研补丁 2026-09-03] 控制台监视器/宽度守护进程级唯一:
    # 多任务同时挂监视器会串读同一屏幕缓冲, 进度互相覆盖
    helpers_acquired = _windows_console_helpers_active.acquire(blocking=False)
    monitor_thread = None
    width_guard_thread = None
    # [自研补丁] 空闲看门狗活动信号: [最后活动时间戳, 是否曾解析到进度]
    activity = [time.monotonic(), False]
    if helpers_acquired:
        monitor_thread = threading.Thread(
            target=_monitor_windows_console_translate_progress,
            args=(task_id, stop_event, activity),
            daemon=True,
        )
        monitor_thread.start()

        width_guard_thread = threading.Thread(
            target=_guard_windows_console_min_width,
            args=(stop_event, cols),
            daemon=True,
        )
        width_guard_thread.start()
    else:
        print("ℹ️ [进度] 已有翻译任务占用控制台监视器, 本任务不显示实时进度(不影响翻译)")

    return_code = None
    start_ts = time.monotonic()
    try:
        while True:
            try:
                return_code = process.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if now - start_ts > TASK_TOTAL_TIMEOUT:
                    process.kill()
                    raise TaskTimeoutError(
                        f"翻译总时长超过 {TASK_TOTAL_TIMEOUT / 60:.0f} 分钟, "
                        "已终止子进程(可用环境变量 PDF2ZH_TASK_TOTAL_TIMEOUT 调整)")
                # 仅在监视器确实解析到过进度后才启用空闲判定,
                # 避免无控制台环境(监视器早退)误杀正常长任务
                if activity[1] and now - activity[0] > TASK_IDLE_TIMEOUT:
                    process.kill()
                    raise TaskTimeoutError(
                        f"翻译连续 {TASK_IDLE_TIMEOUT / 60:.0f} 分钟无进度更新, "
                        "判定网络停滞, 已终止子进程(可用 PDF2ZH_TASK_IDLE_TIMEOUT 调整)")
    finally:
        stop_event.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=1.5)
        if width_guard_thread is not None:
            width_guard_thread.join(timeout=1.0)
        if helpers_acquired:
            _windows_console_helpers_active.release()

    # _debug_progress_log("EXECUTE_END", task_id=task_id, return_code=return_code)

    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, final_cmd)
