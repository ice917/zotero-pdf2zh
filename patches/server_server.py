## server.py v4.1.7
# guaguastandup
# zotero-pdf2zh
import os
from flask import Flask, request, jsonify, send_file, Response
from werkzeug.serving import WSGIRequestHandler
import base64
import subprocess
import json, toml
import shutil
from pypdf import PdfReader
from utils.venv import VirtualEnvManager
from utils.environment_lifecycle import (
    find_existing_environment,
    format_versions,
    pdf2zh_next_meets_minimum,
    read_versions,
)
from utils.config import Config
from utils.config_map import pdf2zh_next_service_aliases
from utils.config_migration import prepare_config_files
from utils.cropper import Cropper
import traceback
import argparse
import sys  # 用于退出脚本
import re   # 用于解析版本号和提取错误信息
import io
import glob    # [v28.23] 等豆包交件落盘(out/<name>.doubao*.txt)
import hashlib  # [v28.26] 按 PDF 内容散列定位本轮的归档侧车(提字进度源)
import socket  # 用于端口检查
import time    # 用于 SSE 推送间隔
import threading
import uuid    # 用于生成任务唯一标识
from datetime import datetime  # 用于记录任务开始/结束时间
# 导入自动更新模块
from utils.auto_update import check_for_updates, fetch_and_show_notices, perform_update_optimized
# 导入任务管理器（用于 index.html 前端进度显示）
from utils.task_manager import task_manager
# 导入带进度解析的命令执行器
from utils.execute import execute_with_progress

_VALUE_ERROR_RE = re.compile(r'(?m)^ValueError:\s*(?P<msg>.+)$')

# ================================================================================
# [自研补丁 2026-09-19 v28.23] 两趟采纳回路（提字 → 待译 → 回灌重渲染）
#
# 为什么要两趟: 引擎逐页「提取→翻译→排版」, 一次跑完整篇、中途不等外部; 而豆包
# 要看到**全篇**才能定稿(跨页接缝/术语/文风)。所以把「翻译」这一站拆成两趟 ——
# 第一趟只提字(侧车写全篇), 中间交给豆包, 第二趟全命中缓存重排版(零翻译调用)。
#
# 为什么能在一个任务里"停一下": 新插件是异步协议 —— POST 立刻返回 accepted,
# worker 在后台线程里跑, 进度走 /tasks 与 /events, 所以 worker 可以在这里等豆包,
# 界面不卡, 任务卡片显示「待译」。这就是需求里的"停下来, 豆包搞完再继续"。
#
# 开关走**环境变量**(env 优先): 插件推送配置时会按 example 无条件回填, 只有 env
# 不会被覆写(POLISH 被插件置 null 的那次事故即此因)。默认关 → 别的用户路径不变。
# ================================================================================
def _two_pass_enabled():
    return str(os.environ.get("PAUSE_TRANSLATE", "")).strip().lower() in ("1", "true", "on", "yes")


class TwoPassGateRejected(RuntimeError):
    """[v28.28] 豆包交件被内容门禁拒收 —— 两趟回路**显式失败**的专用类型。

    它不表示"代码坏了", 而表示"这份交件不能回灌"。之所以用异常往上抛: 服务端的
    "任务失败"这条路本来就是异常驱动的(引擎侧也是 `raise ValueError(...)`, 见
    "pdf2zh_next 未产出任何 mono/dual 文件"), 抛出去就能落进既有的
    complete_task(failed) + 失败根因提取, 不必在 _execute_translate_job 里另开分支。
    """


def _two_pass_wait_minutes():
    """等豆包交稿的上限(分钟), 可用 PAUSE_WAIT_MINUTES 覆盖; 超时回落成正常翻译。"""
    try:
        v = float(os.environ.get("PAUSE_WAIT_MINUTES", "") or 30)
    except ValueError:
        v = 30.0
    return max(0.0, v)


def _inbox_dir():
    """inbox 落点: 与 adopt.py / seg_export.py / doubao_bridge.py 同一套约定
    (P2Z_INBOX 优先)。没设时用**本文件所在项目**的相对位置, 不焊死盘符。"""
    env = os.environ.get("P2Z_INBOX")
    if env:
        return env
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "inbox")


def _segflow_doc_sidecar(pdf_path):
    """[v28.26] 定位本轮的**按文档归档侧车** (~/.cache/pdf2zh/segflow/pdf-<md5>.jsonl)。

    为什么不用 latest.jsonl: 那是全局单文件, 换论文就覆盖; 并发/重跑时读到的
    可能是别人的进度。散列口径与 converter._segflow_paths() 逐字节一致(整份
    PDF 的 md5 前 16 位), 所以两边指的一定是同一份文件。
    """
    try:
        h = hashlib.md5()
        with open(pdf_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    p = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "segflow",
                     "pdf-%s.jsonl" % h.hexdigest()[:16])
    return p if os.path.exists(p) else ""


def _segflow_progress(sidecar):
    """[v28.26] 读侧车得 (已提取段数, 已到第几页) —— 引擎逐页追加, 文件即真相。

    为什么拿文件当进度源, 不信现成的进度解析: 那套解析依赖①抓控制台屏幕缓冲区
    或②launch.ps1 重定向出来的日志尾; 在 IDE 终端里两者都拿不到, 卡片会永远停在
    "正在初始化 0%"(实测 2026-09-19 23:47: 引擎已 16/81, /api/tasks 仍是 0%)。
    侧车由引擎自己每页写, 与"服务端跑在哪种终端里"完全无关。

    侧车格式: **一行 = 一页**, 记录形如 {page, pageid, doc_fp, segs:[{raw,trans}…]}
    —— 段数必须数 segs 的长度, 不能数行数(行数=页数, 实测 22 行对应 350 段)。
    页数取 pageid(LTPage.pageid, 0 基): 侧车的 page 是回调计数, 图形对象也各占一号,
    拿它当页码会漂。
    """
    segs = pages = 0
    try:
        with open(sidecar, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                segs += len(o.get("segs") or [])
                pid = o.get("pageid")
                if pid is not None:
                    pages = max(pages, int(pid) + 1)
    except OSError:
        return 0, 0
    return segs, pages


# [v28.27] 载荷就绪提示音: 默认用随服务端自带的 WAV, 可用环境变量换成任意 wav。
# 为什么不用前端那个提示音(server/bo.mp3): 前端只在任务**结束**时响, 而两趟流程
# 在「待译」这一刻任务仍算 active, 那一下是静的 —— 要有人来接手的时刻恰好在中间。
NOTIFY_SOUND_ENV = "PDF2ZH_NOTIFY_SOUND"
NOTIFY_SOUND_FILE = "notify-complete.wav"   # 16bit PCM 44.1kHz 单声道, 约 0.4 秒
NOTIFY_SOUND_REPEAT = 2                     # 一声太短容易错过, 响两下


def _notify_sound_path():
    """提示音文件: 环境变量优先, 其次随服务端自带的那个; 都没有返回空串。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.environ.get(NOTIFY_SOUND_ENV), os.path.join(here, NOTIFY_SOUND_FILE)):
        if p and os.path.exists(p):
            return p
    return ""


def _play_notify_sound():
    """响就绪铃。winsound 只吃 WAV; 文件缺失(换机/被删)时退回蜂鸣, 全程静默失败。"""
    try:
        import winsound
    except ImportError:         # 非 Windows 平台: 没有就绪铃, 不影响任务
        return
    path = _notify_sound_path()
    if path:
        try:
            for i in range(max(1, NOTIFY_SOUND_REPEAT)):
                if i:
                    time.sleep(0.25)
                winsound.PlaySound(path, winsound.SND_FILENAME)
            return
        except Exception as e:
            print(f"⚠️ [两趟] 提示音没响成({path}): {e}")
    try:
        for _ in range(3):
            winsound.Beep(880, 160)
            time.sleep(0.07)
    except Exception:
        pass


def _notify_payload_ready(task_id, name, pages_str):
    """[v28.26] 载荷就绪 → 响铃 + 醒目横幅 + ready 标记 + 卡片落到「待译」。

    为什么要有声音: 提字这趟不走 LLM, 比机器翻译快一个量级, 人一转头的功夫就过去
    了; 而"文件已生成、该叫豆包了"是这条链上唯一需要人**立刻**接手的时刻。
    声音之外再落一个 ready 标记: "响过了但当时没人"时仍可追溯这份载荷是哪一轮的。
    """
    n_items = 0
    try:
        with open(os.path.join(_inbox_dir(), name + ".manifest.json"), encoding="utf-8") as f:
            n_items = len(json.load(f).get("items") or [])
    except Exception:
        pass
    try:
        os.makedirs(_inbox_dir(), exist_ok=True)
        with open(os.path.join(_inbox_dir(), name + ".ready.json"), "w", encoding="utf-8") as f:
            json.dump({"name": name, "segments": n_items, "pages": pages_str,
                       "ready_at": datetime.now().isoformat(timespec="seconds")},
                      f, ensure_ascii=False, indent=1)
    except OSError as e:
        print(f"⚠️ [两趟] ready 标记没落成: {e}")

    print("\n" + "=" * 68)
    print("🔔 [两趟] 载荷已就绪：inbox/%s.txt  (%d 段 / 覆盖 %s 页)" % (name, n_items, pages_str))
    print("        下一步：把这份载荷交给豆包定稿 (豆包可用桥的 get_payload 直接取)")
    print("=" * 68 + "\n")
    _play_notify_sound()
    task_manager.update_task(task_id, {
        'status': '待译',
        'message': '待译：载荷已就绪（%d 段 / 覆盖 %s 页），等待豆包交稿' % (n_items, pages_str),
    })


def _adopt_run_name(pdf_path):
    """run 名 = 原文文件名(去扩展名)。adopt 侧只允许字母数字-_ . 与空格。"""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    name = re.sub(r"[^0-9A-Za-z_\-\. ]+", "_", stem).strip()
    return (name or "run")[:60]


def _run_tool(script, argv, timeout=None):
    """跑项目内工具(adopt.py 等), 返回 (rc, 输出尾部)。用本进程解释器, cwd=项目根。

    工具失败**不能**让翻译任务崩: 一律返回 rc!=0 交给调用方回落处理。
    """
    proj = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
    cmd = [sys.executable, os.path.join(proj, "tools", script)] + [str(a) for a in argv]
    env = dict(os.environ)
    env["P2Z_PROJ"] = proj
    try:
        p = subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or ""))[-2000:]
    except Exception as exc:
        return 1, "工具调用失败: %s" % exc

# ================================================================================
# [自研补丁 2026-09-18 闸门4] 任务失败根因提取
#
# 问题（v26.19 实测）：Windows 上 execute_with_progress 走 _execute_with_inherit,
# 子进程 stdout/stderr **直接继承控制台**（为了保留 tqdm 原生多进度条 UI），
# 于是 pdf2zh 的 traceback 只落在屏幕上、没有任何一处被捕获。server 侧最后
# 只剩 CalledProcessError 的
#   "Command '[...]' returned non-zero exit status 1."
# —— 任务记录(/api/history)与 HTTP 响应里就是这么一句没有信息量的话，
# 想找真正的 AttributeError 得人工往上翻终端。
#
# 处置：失败那一刻，把控制台屏幕缓冲区的尾部读回来，抽出**最后一个** Traceback
# 的『异常类: 消息 @ 文件:行 (函数名)』，写进任务记录与响应。
# 读不到控制台（服务无控制台/输出被重定向）就静默降级——绝不因为诊断失败
# 而改变失败本身的语义。
# ================================================================================

_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
# tqdm/rich 进度条碎片：对诊断无贡献，且会把 traceback 行切碎
_PROGRESS_NOISE_RE = re.compile(r'(\d{1,3}%\|)|(\[\d+:\d+<)|(it/s\])|(s/it\])|(\?{3}%)')
_TRACEBACK_HEAD_RE = re.compile(r'^\s*Traceback \(most recent call last\)')
_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')


def _clean_console_text(text):
    """去掉 ANSI 转义 / 回车残留 / tqdm 进度碎片，留下可读行"""
    if not text:
        return ''
    text = _ANSI_RE.sub('', text)
    out = []
    for raw in text.replace('\r', '\n').split('\n'):
        ln = raw.rstrip()
        if not ln.strip() or _PROGRESS_NOISE_RE.search(ln):
            continue
        out.append(ln)
    return '\n'.join(out)


def extract_root_cause(text):
    """
    从（可能混着进度条的）文本里抽**最后一个** Traceback 的根因。
    返回 '异常类: 消息 @ 文件:行 (函数名)'；抽不到返回 None。
    取最后一个：一次失败可能在屏幕上留下多段 traceback（重试等），最后那段
    才是本次的。frame 取最深一层，与 tools/parse_smoke.py 的口径一致。
    """
    lines = _clean_console_text(text).split('\n')
    head = None
    for i, ln in enumerate(lines):
        if _TRACEBACK_HEAD_RE.match(ln):
            head = i
    if head is None:
        return None
    frames, exc_line = [], None
    for ln in lines[head + 1:]:
        m = _FRAME_RE.search(ln)
        if m:
            frames.append((os.path.basename(m.group(1)), int(m.group(2)), m.group(3)))
            continue
        if ln[:1] not in (' ', '\t'):
            exc_line = ln.strip()      # 顶格的最后一行 = "异常类: 消息"
    if not exc_line:
        return None
    where = ''
    if frames:
        f, n, fn = frames[-1]
        where = ' @ %s:%d (%s)' % (f, n, fn)
    return exc_line + where


def console_tail(max_lines=400):
    """
    Windows：一次性读回控制台屏幕缓冲区尾部文本；不可用返回 ''。
    没有控制台（服务以无控制台方式跑）或句柄不是控制台（输出被重定向到文件）
    时，ReadConsoleOutputCharacterW 会失败，这里静默返回 ''。
    """
    if sys.platform != 'win32':
        return ''
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return ''

    class COORD(ctypes.Structure):
        _fields_ = [('X', wintypes.SHORT), ('Y', wintypes.SHORT)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [('Left', wintypes.SHORT), ('Top', wintypes.SHORT),
                    ('Right', wintypes.SHORT), ('Bottom', wintypes.SHORT)]

    class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [('dwSize', COORD), ('dwCursorPosition', COORD),
                    ('wAttributes', wintypes.WORD), ('srWindow', SMALL_RECT),
                    ('dwMaximumWindowSize', COORD)]

    STD_OUTPUT_HANDLE = -11
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    try:
        kernel32 = ctypes.windll.kernel32
        get_std_handle = kernel32.GetStdHandle
        get_std_handle.argtypes = [wintypes.DWORD]
        get_std_handle.restype = wintypes.HANDLE   # 不设会把 64 位句柄截断
        get_csbi = kernel32.GetConsoleScreenBufferInfo
        get_csbi.restype = wintypes.BOOL
        read_out = kernel32.ReadConsoleOutputCharacterW
        read_out.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD,
                             COORD, ctypes.POINTER(wintypes.DWORD)]
        read_out.restype = wintypes.BOOL

        handle = get_std_handle(STD_OUTPUT_HANDLE)
        if handle in (None, 0, INVALID_HANDLE_VALUE):
            return ''
        csbi = CONSOLE_SCREEN_BUFFER_INFO()
        if not get_csbi(handle, ctypes.byref(csbi)):
            return ''
        width = max(int(csbi.dwSize.X), 1)
        cursor_y = int(csbi.dwCursorPosition.Y)
        start = max(0, cursor_y - int(max_lines) + 1)
        rows = []
        for row in range(start, cursor_y + 1):
            buf = ctypes.create_unicode_buffer(width)
            got = wintypes.DWORD(0)
            if read_out(handle, buf, width, COORD(0, row), ctypes.byref(got)) and got.value > 0:
                rows.append(buf.value[:got.value])
        return '\n'.join(rows)
    except Exception:
        return ''


# 失败输出流日志来源: launch.ps1 的 -RedirectStandardOutput/Error 落点。
# 仓库根 = server.py 所在目录的上一级。
_FAILURE_LOG_ENV = 'PDF2ZH_FAILURE_LOG'          # 覆盖/追加用的测试钩子
_FAILURE_LOG_NAMES = ('server_err.log', 'server_out.log')
_FAILURE_LOG_TAIL_BYTES = 256 * 1024             # 只读尾部，别把整个日志读进来
_FAILURE_LOG_FRESH_SECONDS = 120                 # 超过这个岁数的日志不认（防张冠李戴）
# [自研补丁 2026-09-18] 任务开始时刻各日志文件的字节数。失败时只认锚点之后新增的
# 内容，否则会把"上一次失败"留在日志里的 traceback 张冠李戴成本次根因。
_FAILURE_LOG_ANCHORS = {}
_FAILURE_LOG_ANCHOR_LOCK = threading.Lock()
_FAILURE_LOG_ANCHOR_KEEP = 32


def _failure_log_paths():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = []
    override = (os.environ.get(_FAILURE_LOG_ENV) or '').strip()
    if override:
        paths.append(override)
    paths.extend(os.path.join(base, 'logs', name) for name in _FAILURE_LOG_NAMES)
    return paths


def _log_offsets():
    """各日志文件当前的字节数（还不存在的记 None）。"""
    offsets = {}
    for path in _failure_log_paths():
        try:
            offsets[path] = os.path.getsize(path)
        except OSError:
            offsets[path] = None
    return offsets


def anchor_failure_logs(task_id):
    """
    [自研补丁 2026-09-18 闸门4] 任务开始时刻锚定日志偏移，见 _FAILURE_LOG_ANCHORS。
    只在任务登记处调用一次即可（所有路由都经过那里）。
    """
    if task_id is None:
        return
    try:
        with _FAILURE_LOG_ANCHOR_LOCK:
            _FAILURE_LOG_ANCHORS[task_id] = _log_offsets()
            while len(_FAILURE_LOG_ANCHORS) > _FAILURE_LOG_ANCHOR_KEEP:
                _FAILURE_LOG_ANCHORS.pop(next(iter(_FAILURE_LOG_ANCHORS)))
    except Exception:
        pass


def _sole_task(task_id):
    """
    [自研补丁 2026-09-18] 本任务是否独占输出流（没有别的翻译在跑）。
    logs/*.log 是全服务共享的：服务自身与**所有**子进程都往同一个文件写，
    并发翻译时另一个任务的 traceback 会落在本任务的锚点窗口里。
    取不到任务表就返回 False（不独占）——宁可不给根因，也不给错的根因。
    """
    try:
        return not task_manager.has_other_running(task_id)
    except Exception:
        return False


def _tail_file(path, max_bytes=_FAILURE_LOG_TAIL_BYTES, start=None):
    """
    start 给定时只读该字节偏移之后的新增内容（锚点语义）：没有新增就返回 ''，
    避免把锚点之前的旧 traceback 当成本次根因。
    """
    try:
        size = os.path.getsize(path)
        begin = 0
        if start is not None:
            if size <= start:
                return ''
            begin = start
        with open(path, 'rb') as fp:
            if size - begin > max_bytes:
                begin = size - max_bytes
            fp.seek(begin)
            return fp.read().decode('utf-8', errors='replace')
    except Exception:
        return ''


def failure_output_tail(task_id=None):
    """
    [自研补丁 2026-09-18 闸门4] 失败时刻"子进程输出流的尾部"。
    两条来源, 按部署形态自动选:
      ① 控制台屏幕缓冲 —— 前台启动(有窗口)时用, 见 console_tail();
      ② 日志文件尾部 —— launch.ps1 是 `-RedirectStandardOutput/Error + -WindowStyle Hidden`
         启动的, 服务与子进程都**没有控制台**, 子进程 stdout/stderr 继承的正是这两个
         日志句柄(实测: tqdm 进度条原样落在 logs/server_err.log 里)。
         服务跑在无控制台的部署里, ①必然为空, 这时只有②能拿到 traceback。

    宁可没有根因, 也不要错的根因, 所以②有三道闸:
      1) 独占闸 —— 还有别的翻译在跑时直接弃用(共享日志里混着它的输出);
      2) 锚点闸 —— 只认本任务开始之后新增的内容(挡住上一次失败的 traceback);
      3) 新鲜度闸 —— 超过 FRESH_SECONDS 没写过的日志不认(没有锚点时兜底)。
    """
    screen = console_tail()
    if screen.strip():
        return screen

    if not _sole_task(task_id):
        return ''

    anchor = None
    if task_id is not None:
        with _FAILURE_LOG_ANCHOR_LOCK:
            anchor = _FAILURE_LOG_ANCHORS.get(task_id)

    now = time.time()
    chunks = []
    for path in _failure_log_paths():
        try:
            if not os.path.isfile(path):
                continue
            if now - os.path.getmtime(path) > _FAILURE_LOG_FRESH_SECONDS:
                continue
            chunks.append(_tail_file(path, start=(anchor or {}).get(path)))
        except Exception:
            continue
    return '\n'.join(c for c in chunks if c)


def failure_brief(exc, task_id=None):
    """
    喂给任务记录(error=...)与 HTTP 响应的一句话根因。
    优先级：异常自带的子进程 stderr → 子进程输出流尾部 → 异常自身字符串。
    """
    txt = str(exc).strip().replace('\n', ' ')
    if isinstance(exc, subprocess.CalledProcessError):
        blob = (getattr(exc, 'stderr', None) or '') or failure_output_tail(task_id)
        root = extract_root_cause(blob)
        if root:
            return root
    elif not txt or len(txt) < 12 or '请查看' in txt:
        # 只有异常本身没什么信息量时才去翻输出流，避免把上一轮失败的
        # traceback 张冠李戴到本次（如明确的 ValueError 提示已足够）
        root = extract_root_cause(failure_output_tail(task_id))
        if root:
            return root
    return txt[:300] or exc.__class__.__name__


# [自研补丁 2026-09-03] 自动质检并发上限: 每个翻译任务收尾都会起后台 QC 线程,
# 不加限制时批量提交会瞬时起多个 pypdf 进程抢内存
_QC_SEMAPHORE = threading.Semaphore(2)

# [自研补丁 2026-09-03] 同名任务幂等去重(报告 🔴4): 上传与产物都是确定性
# 文件名, 重复点击/异步重试会让后到请求截断正在被子进程读取的输入文件、
# 产物互相覆盖。接收请求即在此登记 fileName->taskId, 同文件名的并发请求
# 直接复用进行中的任务; 任务结束(成功/失败)释放。
_SUBMIT_LOCK = threading.Lock()
_INFLIGHT_FILES = {}

__version__ = "4.1.7"
update_log = "远程/Docker 优先 HTTP 挂附件；新旧插件协议兼容；进度条显示翻译百分比；加固附件文件名；补齐额外字段白名单，可从下拉添加 *_enable_json_mode（默认关闭）。请同时更新插件和 Server。"

############# config file #########
pdf2zh      = 'pdf2zh'
pdf2zh_next = 'pdf2zh_next'
venv        = 'venv'

# TODO: 强制设置标准输出和标准错误的编码为 UTF-8
# sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
# sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Windows 下防止子进程弹出控制台窗口
if sys.platform == 'win32':
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0

# 所有系统: 获取当前脚本server.py所在的路径
root_path     = os.path.dirname(os.path.abspath(__file__))
config_folder = os.path.join(root_path, 'config')
output_folder = os.path.join(root_path, 'translated')
config_path = { # 配置文件路径
    pdf2zh:      os.path.join(config_folder, 'config.json'),
    pdf2zh_next: os.path.join(config_folder, 'config.toml'),
    venv:        os.path.join(config_folder, 'venv.json'),
}

######### venv config #########
venv_name = { # venv名称
    pdf2zh:      'zotero-pdf2zh-venv',
    pdf2zh_next: 'zotero-pdf2zh-next-venv',
}

default_env_tool = 'auto' # 自动沿用已有 uv/conda；新环境优先 uv
enable_venv = True

PORT = 8890     # 默认端口号


class _QuietAccessHandler(WSGIRequestHandler):
    # 进度查询很勤，默认访问日志会插进 tqdm/rich 进度条中间。
    _SILENT_PREFIXES = (
        '/api/tasks',
        '/api/history',
        '/events',
        '/health',
        '/favicon',
    )

    def log_request(self, code='-', size='-'):
        path = (getattr(self, 'path', None) or '').split('?', 1)[0]
        if any(path == prefix or path.startswith(prefix + '/') for prefix in self._SILENT_PREFIXES):
            return
        super().log_request(code, size)


class PDFTranslator:

    def __init__(self, args):
        self.app = Flask(__name__)
        # [自研补丁 2026-09-03] 请求体上限: base64 PDF 全量驻留内存,
        # 不设上限时远程/本机异常客户端可用超大 body 打爆内存(Flask 自动回 413)
        self.app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
        if args.enable_venv:
            self.env_manager = VirtualEnvManager(config_path[venv], venv_name, args.env_tool, args.enable_mirror, args.skip_install, args.mirror_source)
        self.cropper = Cropper()
        self.setup_routes()

    def setup_routes(self):
        # 新增：首页路由 - 提供 index.html 前端进度监控页面
        self.app.add_url_rule('/', 'index', self.index)

        self.app.add_url_rule('/translate', 'translate', self.translate, methods=['POST'])
        self.app.add_url_rule('/crop', 'crop', self.crop, methods=['POST'])
        self.app.add_url_rule('/crop-compare', 'crop-compare', self.crop_compare, methods=['POST'])
        self.app.add_url_rule('/compare', 'compare', self.compare, methods=['POST'])
        self.app.add_url_rule(
            '/translatedFile/<path:filename>',
            'download',
            self.download_file,
            methods=['GET', 'HEAD'],
        )
        self.app.add_url_rule(
            '/translatedInfo',
            'translated_info',
            self.translated_info,
            methods=['GET'],
        )

        # 新增：健康检查端点 - 用于检查服务器状态
        self.app.add_url_rule('/health', 'health', self.health_check)
        # 新增：SSE 端点 - 实时推送翻译进度给 index.html 前端
        self.app.add_url_rule('/events', 'events', self.events)
        # 新增：历史记录 API - 供 index.html 前端获取翻译历史
        self.app.add_url_rule('/api/history', 'history', self.get_history)
        self.app.add_url_rule('/api/tasks', 'tasks', self.get_tasks)
        # 新增：配置信息 API - 供 index.html 前端显示当前服务配置
        self.app.add_url_rule('/api/config', 'config', self.get_config)
        # 新增：favicon 路由
        self.app.add_url_rule('/favicon.svg', 'favicon', self.favicon)
        # 新增：提示音音频路由
        self.app.add_url_rule('/bo.mp3', 'notification_sound', self.notification_sound)

    ##################################################################
    # 健康检查端点 /health - 检查服务器状态
    # 返回JSON格式的服务器状态信息，包括状态码、版本号和消息
    ##################################################################
    def health_check(self):
        return jsonify({
            'status': 'ok',
            'version': __version__,
            'message': 'PDF2zh Server is running',
            'outputDir': os.path.abspath(output_folder),
        }), 200

    ##################################################################
    # 首页路由 / - 提供 index.html 前端进度监控页面
    ##################################################################
    def index(self):
        try:
            index_path = os.path.join(root_path, 'index.html')
            if os.path.exists(index_path):
                return send_file(index_path)
            else:
                return jsonify({'status': 'error', 'message': 'index.html not found'}), 404
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    ##################################################################
    # SSE (Server-Sent Events) 端点 /events - 实时推送翻译进度给前端
    # index.html 通过 EventSource('/events') 接收数据
    ##################################################################
    def events(self):
        def generate():
            while True:
                try:
                    tasks_data = {
                        'type': 'tasks',
                        'data': task_manager.get_active_tasks_list()
                    }
                    yield f"data: {json.dumps(tasks_data)}\n\n"
                    time.sleep(1)  # 每秒推送一次
                except GeneratorExit:
                    break
        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache, no-store',
                'X-Accel-Buffering': 'no',
            },
        )

    ##################################################################
    # 历史记录 API /api/history - 供 index.html 前端获取翻译历史
    ##################################################################
    def get_history(self):
        return jsonify({'status': 'success', 'history': task_manager.get_history()})

    def get_tasks(self):
        return jsonify({'status': 'success', 'tasks': task_manager.get_active_tasks_list()})

    ##################################################################
    # 配置信息 API /api/config - 供 index.html 前端显示当前服务配置
    ##################################################################
    def get_config(self):
        config_info = {
            'version': __version__,
            'port': args.port,
            'enable_venv': args.enable_venv,
            'env_tool': args.env_tool,
            'enable_mirror': args.enable_mirror,
            'mirror_source': args.mirror_source if args.enable_mirror else '-',
            'skip_install': args.skip_install,
            'enable_winexe': args.enable_winexe,
        }
        return jsonify({'status': 'success', 'config': config_info})

    ##################################################################
    # Favicon 路由
    ##################################################################
    def favicon(self):
        favicon_path = os.path.join(root_path, 'favicon.svg')
        if os.path.exists(favicon_path):
            return send_file(favicon_path, mimetype='image/svg+xml')
        return '', 404

    ##################################################################
    # 提示音音频路由
    ##################################################################
    def notification_sound(self):
        sound_path = os.path.join(root_path, 'bo.mp3')
        if os.path.exists(sound_path):
            return send_file(sound_path, mimetype='audio/mpeg')
        return '', 404

    ##################################################################
    @staticmethod
    def _safe_upload_filename(raw_name):
        if not isinstance(raw_name, str):
            raise ValueError("Invalid PDF filename")
        name = raw_name.strip()
        if (
            not name
            or name in {'.', '..'}
            or '/' in name
            or '\\' in name
            or os.path.basename(name) != name
        ):
            raise ValueError("Invalid PDF filename")
        if not name.lower().endswith('.pdf'):
            raise ValueError("Only PDF uploads are accepted")
        return name

    def process_request(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Invalid JSON request")
        config = Config(data)

        file_name = self._safe_upload_filename(data.get('fileName'))
        base = os.path.abspath(output_folder)
        input_path = os.path.abspath(os.path.join(base, file_name))
        try:
            if os.path.commonpath([base, input_path]) != base:
                raise ValueError("Invalid PDF filename")
        except ValueError:
            raise ValueError("Invalid PDF filename")

        file_content = data.get('fileContent', '')
        if not isinstance(file_content, str):
            raise ValueError("Invalid PDF content")
        # [自研补丁 2026-09-03] data URL 前缀兼容变体: 旧代码只识别精确小写
        # 'data:application/pdf;base64,', 大小写差异或缺 MIME 的变体会整串进
        # b64decode 产生垃圾文件。统一按 ';base64,' 标记截断(大小写不敏感)。
        if file_content.lower().startswith('data:'):
            marker = re.search(r';base64,', file_content, flags=re.IGNORECASE)
            if marker:
                file_content = file_content[marker.end():]
        try:
            decoded = base64.b64decode(file_content)
        except Exception as exc:
            raise ValueError(f"Invalid PDF content: {exc}") from exc

        # [自研补丁] 解码后校验 %PDF 魔数: 拒绝截断/伪装/非 PDF 内容落盘
        if not decoded.startswith(b'%PDF'):
            raise ValueError("文件内容不是有效的 PDF (缺少 %PDF 文件头), 请检查上传内容")

        with open(input_path, 'wb') as f:
            f.write(decoded)

        return input_path, config

    def _existing_output_files(self, paths):
        existing = []
        for path in paths or []:
            if path and os.path.exists(path):
                existing.append(os.path.abspath(path))
        return existing

    def _success_files_payload(self, paths):
        existing = self._existing_output_files(paths)
        return {
            'status': 'success',
            'fileList': [os.path.basename(p) for p in existing],
            'outputDir': os.path.abspath(output_folder),
            'filePaths': existing,
        }

    def _success_files_response(self, paths):
        return jsonify(self._success_files_payload(paths)), 200

    def _build_task_info(self, task_id, input_path, config, engine, start_time, status='开始处理'):
        output_types = []
        if config.mono: output_types.append('mono')
        if config.dual: output_types.append('dual')
        if config.mono_cut: output_types.append('mono-cut')
        if config.dual_cut: output_types.append('dual-cut')
        if config.compare: output_types.append('compare')
        if config.crop_compare: output_types.append('crop-compare')
        config_summary = {
            'sourceLang': config.sourceLang,
            'targetLang': config.targetLang,
            'outputTypes': output_types,
        }
        if engine == pdf2zh:
            config_summary['threadNum'] = config.thread_num
            config_summary['babeldoc'] = config.babeldoc
        elif engine == pdf2zh_next:
            config_summary['qps'] = config.qps
            config_summary['dualMode'] = config.dual_mode
            config_summary['noWatermark'] = config.no_watermark
            config_summary['ocr'] = config.ocr or config.auto_ocr
            config_summary['poolSize'] = config.pool_size
        if config.skip_last_pages and config.skip_last_pages > 0:
            config_summary['skipLastPages'] = config.skip_last_pages
        if config.no_watermark:
            config_summary['noWatermark'] = config.no_watermark

        model_name = config.llm_api.get('model', '')
        service = config.service
        if not model_name:
            if service == 'siliconflowfree':
                model_name = 'siliconflowfree (免费服务)'
            elif service == 'bing':
                model_name = 'Bing 翻译'
            elif service == 'google':
                model_name = 'Google 翻译'
            else:
                model_name = f'{service} (默认模型)'

        return {
            'taskId': task_id,
            'active': True,
            'finished': False,
            'fileName': os.path.basename(input_path),
            'engine': engine,
            'service': config.service,
            'modelName': model_name,
            'startTime': start_time.isoformat(),
            'progress': 0,
            'status': status,
            'message': '正在初始化...',
            'config': config_summary,
        }

    @staticmethod
    def _truthy_flag(value):
        if value is True:
            return True
        if value is False or value is None:
            return False
        return str(value).strip().lower() in {'1', 'true', 'yes', 'async', 'accepted'}

    def _client_wants_async_job(self):
        # 新插件会带 asyncJob=true 或 X-PDF2zh-Protocol: accepted。
        # 旧官方插件 / DCC 4.0.3 都没有这个标记，必须同步返回 success+fileList，
        # 否则它们会把 accepted 当成失败，条目下挂不上文件。
        header = (request.headers.get('X-PDF2zh-Protocol') or '').strip().lower()
        if header in {'accepted', 'async'}:
            return True
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return False
        if 'asyncJob' in data:
            return self._truthy_flag(data.get('asyncJob'))
        protocol = str(data.get('clientProtocol') or '').strip().lower()
        return protocol in {'accepted', 'async'}

    def _start_accepted_job(self, task_id, task_info, worker, context):
        # 新插件：POST 立刻 accepted，翻完后按 taskId 取结果，避免 Windows 长连接被掐。
        # 旧插件：阻塞到完成，再返回 {status: success, fileList, ...}。
        task_manager.add_task(task_id, task_info)
        # [自研补丁 2026-09-18 闸门4] 锚定失败日志偏移：本次任务的 traceback 只会
        # 出现在这个位置之后，失败时据此排除"上一次失败"留在日志里的旧 traceback。
        anchor_failure_logs(task_id)
        if self._client_wants_async_job():
            def run():
                try:
                    worker()
                except Exception as exc:
                    task_manager.complete_task(
                        task_id, 'failed', str(exc), error=failure_brief(exc, task_id=task_id))
                    self._exception_payload(exc, context=context, task_id=task_id)

            threading.Thread(target=run, daemon=True).start()
            return jsonify({'status': 'accepted', 'taskId': task_id}), 200

        print(f"ℹ️ [Zotero PDF2zh Server] 旧插件协议：同步等待完成后返回 fileList ({context})")
        try:
            payload = worker() or {}
            if payload.get('status') == 'error':
                return jsonify(payload), 500
            if payload.get('status') != 'success':
                return jsonify({
                    'status': 'error',
                    'message': payload.get('message') or '操作失败，请查看详细日志。',
                }), 500
            return jsonify(payload), 200
        except Exception as exc:
            task_manager.complete_task(
                task_id, 'failed', str(exc), error=failure_brief(exc, task_id=task_id))
            return self._handle_exception(exc, context=context, task_id=task_id)

    def _complete_job_files(self, task_id, paths, message):
        payload = self._success_files_payload(paths)
        if not payload['fileList']:
            task_manager.complete_task(task_id, 'failed', '操作失败，请查看详细日志。', error='无文件生成')
            return {'status': 'error', 'message': '操作失败，请查看详细日志。'}
        task_manager.complete_task(
            task_id,
            'success',
            message,
            file_list=payload['fileList'],
            file_paths=payload['filePaths'],
            output_dir=payload['outputDir'],
            result=payload,
        )
        return payload

    def translated_info(self):
        # 列出 translated 目录里的 PDF，供网页预览 / 排障使用
        try:
            stem = (request.args.get('stem') or '').strip()
            base = os.path.abspath(output_folder)
            files = []
            if os.path.isdir(base):
                for name in os.listdir(base):
                    if not name.lower().endswith('.pdf'):
                        continue
                    if stem and not (
                        name.startswith(stem + '-')
                        or name.startswith(stem + '.')
                        or name.startswith(stem + '_')
                    ):
                        continue
                    full = os.path.abspath(os.path.join(base, name))
                    try:
                        if os.path.commonpath([base, full]) != base:
                            continue
                    except ValueError:
                        continue
                    if not os.path.isfile(full):
                        continue
                    files.append({
                        'fileName': name,
                        'filePath': full,
                        'size': os.path.getsize(full),
                        'mtime': os.path.getmtime(full),
                    })
            files.sort(key=lambda item: item['mtime'], reverse=True)
            return jsonify({
                'status': 'ok',
                'outputDir': base,
                'files': files,
            }), 200
        except Exception as e:
            traceback.print_exc()
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # 下载文件 /translatedFile/<filename>
    # 支持 ?preview=true 参数用于 index.html 的在线预览功能
    def download_file(self, filename):
        try:
            # Flask/Werkzeug has already decoded the route parameter once.
            # Decoding again would reinterpret literal names such as "%2F.pdf"
            # and break cross-platform downloads.
            filename = os.path.basename(filename or '')
            if not filename or filename in {'.', '..'}:
                return jsonify({'status': 'error', 'message': 'Invalid path'}), 400
            base = os.path.abspath(output_folder)
            full = os.path.abspath(os.path.join(output_folder, filename))
            # 防止目录穿越
            if os.path.commonpath([base, full]) != base:
                return jsonify({'status': 'error', 'message': 'Invalid path'}), 400

            if os.path.exists(full):
                # 如果 preview=true，则以内联方式返回（用于浏览器内预览）
                is_preview = request.args.get('preview') == 'true'
                return send_file(full, as_attachment=not is_preview)
            # 新增：不存在时明确返回 404，而不是什么都不返回
            return jsonify({'status': 'error', 'message': f'File not found: {filename}'}), 404
        except Exception as e:
            traceback.print_exc()
            return jsonify({'status': 'error', 'message': str(e)}), 500

    ############################# 核心逻辑 #############################
    # 翻译 /translate
    @staticmethod
    def _active_task_id_for_file(file_name):
        """[自研补丁 2026-09-03] 同文件名且仍在进行中的任务 ID(无则 None)"""
        if not file_name:
            return None
        try:
            for task in task_manager.get_active_tasks_list():
                if (task.get('fileName') == file_name
                        and task.get('active')
                        and not task.get('finished')):
                    return task.get('taskId')
        except Exception:
            pass
        return None

    @staticmethod
    def _completed_task_id_for_file(file_name):
        """[自研补丁 2026-09-07] 同文件名且已成功完成的最近任务 ID(无则 None)。

        背景: 插件挂载靠"自己持有的 taskId 轮询"会话, 外部直提(API/脚本)
        的任务完成后插件无感知; 且去重只查活跃任务 → 插件重提同名文件会
        触发无谓重跑(即使缓存命中也要等排版)。查 history 后, 插件拿到历史
        taskId 轮询 /api/history 直接命中 fileList → 秒级下载挂载, 零重跑。
        注: history 为内存态, server 重启后自然退回重跑路径(缓存兜底)。"""
        if not file_name:
            return None
        try:
            for hist in task_manager.get_history():
                if (hist.get('fileName') == file_name
                        and hist.get('status') == 'success'
                        and (hist.get('fileList') or hist.get('filePaths'))):
                    return hist.get('taskId')
        except Exception:
            pass
        return None

    @staticmethod
    def _release_inflight(file_name, task_id):
        """[自研补丁] 释放在途文件名登记(仅当登记者仍是本任务)"""
        if not file_name:
            return
        with _SUBMIT_LOCK:
            if _INFLIGHT_FILES.get(file_name) == task_id:
                _INFLIGHT_FILES.pop(file_name, None)

    def _register_inflight(self, task_id):
        """[自研补丁 2026-09-03] 同名任务幂等去重(报告 🔴4)。
        在落盘上传文件**之前**调用: 命中进行中任务返回 (dup_id, file_name);
        否则占位登记返回 (None, file_name); 无文件名返回 (None, None)。"""
        data = request.get_json(silent=True) or {}
        fname = None
        if isinstance(data, dict):
            fname = self._safe_upload_filename(data.get('fileName')) or None
        if not fname:
            return None, None
        with _SUBMIT_LOCK:
            dup = self._active_task_id_for_file(fname) or _INFLIGHT_FILES.get(fname)
            if dup and dup != task_id:
                return dup, fname
            # [自研补丁 2026-09-07] 已完成同名任务复用: 返回历史 taskId。
            # 新插件: accepted+taskId → 轮询 /api/history 命中 → 秒挂载;
            # 旧插件同步协议: _wait_for_task_payload 的 history 分支返回
            # complete_task 存的 result payload, 同样直达产物。均零重跑。
            dup_done = self._completed_task_id_for_file(fname)
            if dup_done:
                return dup_done, fname
            _INFLIGHT_FILES[fname] = task_id
            return None, fname

    def _wait_for_task_payload(self, task_id, timeout=1800):
        """[自研补丁] 旧插件同步协议下等待复用任务结束, 返回其 success
        payload; 任务失败/超时返回 None。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            active_task = None
            for task in task_manager.get_active_tasks_list():
                if task.get('taskId') == task_id:
                    active_task = task
                    break
            if active_task is None:
                # 已移出活跃列表(完成 30s 后清理), 查历史
                for hist in task_manager.get_history():
                    if hist.get('taskId') == task_id:
                        return hist.get('result') if hist.get('status') == 'success' else None
                return None
            if not active_task.get('active'):
                return active_task.get('result') if active_task.get('status') == '完成' else None
            time.sleep(2)
        return None

    def translate(self):
        # 生成任务ID并记录开始时间（用于 index.html 前端进度显示）
        task_id = str(uuid.uuid4())
        start_time = datetime.now()
        inflight_name = None

        try:
            # [自研补丁 2026-09-12 v23.3] force 直通道: 缓存手术/修复译文后,
            # 同名重提会被历史去重拦成空操作(返回旧产物), 永远用不上新缓存。
            # 请求带 "force": true 时跳过三类去重(活跃/在途/历史), 强制真渲染。
            _req_data = request.get_json(silent=True) or {}
            _force = isinstance(_req_data, dict) and bool(_req_data.get("force"))

            # [自研补丁 2026-09-03] 同名任务幂等去重(报告 🔴4): 必须在
            # process_request 落盘之前判定, 否则后到请求会截断正在被子进程
            # 读取的输入文件、产物互相覆盖。
            if _force:
                print("⚠️ [/translate] force=true: 跳过去重, 强制重新渲染")
                fname = None
                if isinstance(_req_data, dict):
                    fname = self._safe_upload_filename(_req_data.get("fileName")) or None
                if fname:
                    with _SUBMIT_LOCK:
                        _INFLIGHT_FILES[fname] = task_id
                    inflight_name = fname
                dup_id = None
            else:
                dup_id, fname = self._register_inflight(task_id)
                if dup_id:
                    print(f"ℹ️ [/translate] 同名文件「{fname}」已有进行中任务 {dup_id}，复用该任务")
                    if self._client_wants_async_job():
                        return jsonify({
                            'status': 'accepted',
                            'taskId': dup_id,
                            'message': '该文件正在翻译中，已复用现有翻译任务',
                        }), 200
                    # 旧插件同步协议: 等待复用任务结束后返回它的产物
                    payload = self._wait_for_task_payload(dup_id)
                    if payload and payload.get('status') == 'success':
                        return jsonify(payload), 200
                    if self._active_task_id_for_file(fname):
                        # 等待超时但任务仍在跑: 不能重提(会覆盖输入), 交回任务列表
                        return jsonify({
                            'status': 'accepted', 'taskId': dup_id,
                            'message': '该文件翻译仍在进行中，请稍后在任务列表查看结果',
                        }), 200
                    # 复用任务确已失败: 登记本任务重新翻译
                    with _SUBMIT_LOCK:
                        _INFLIGHT_FILES.setdefault(fname, task_id)
                    inflight_name = fname
                else:
                    inflight_name = fname

            input_path, config = self.process_request()
            infile_type = self.get_filetype(input_path)
            engine = config.engine
            task_info = self._build_task_info(
                task_id, input_path, config, engine, start_time, status='开始翻译'
            )

            if infile_type != 'origin':
                # [自研补丁 2026-09-03] 校验失败的上传件已落盘, 清理避免孤儿文件
                try:
                    os.remove(input_path)
                except OSError:
                    pass
                self._release_inflight(inflight_name, task_id)
                return jsonify({'status': 'error', 'message': 'Input file must be an original PDF file.'}), 400

            def _guarded_worker():
                # [自研补丁] 任务结束(成功/失败/异常)一定释放在途登记
                try:
                    return self._execute_translate_job(task_id, input_path, config, engine)
                finally:
                    self._release_inflight(inflight_name, task_id)

            return self._start_accepted_job(
                task_id,
                task_info,
                _guarded_worker,
                '/translate',
            )
        except Exception as e:
            self._release_inflight(inflight_name, task_id)
            task_manager.complete_task(task_id, 'failed', str(e), error=failure_brief(e, task_id=task_id))
            return self._handle_exception(e, context='/translate', task_id=task_id)

    def _wait_for_doubao_delivery(self, task_id, name, minutes, since_ts):
        """[v28.23] 等豆包交件落盘 out/<name>.doubao*.txt; 超时返回 ""。

        为什么轮询文件而不是等 MCP 反向通知: 交件本身就是"文件落盘"(桥的
        submit_result 与剪贴板粘贴落盘走同一个命名), 而 MCP 服务端→客户端
        没有"唤醒 agent 干活"的标准语义 —— 现在是**我们等它**, 不是它等我们推。

        只认 since_ts 之后落盘的文件: out/ 里常常堆着同一篇的历史交件
        (wang2026.doubao1..5 实测), 认错一份整轮白干 —— adopt 的 deliver 门禁
        也是为这个设的。认"本次任务开始后才出现的、最新的一份", 是自动回路里
        唯一站得住的判据(无人可问, 不能拿旧的充新的)。
        """
        proj = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
        pattern = os.path.join(proj, "out", name + ".doubao*.txt")
        floor = since_ts - 1.0   # 让 1s 内的时间戳粒度(±1)不误杀
        deadline = time.time() + minutes * 60.0
        waited = 0
        while True:
            hits = [f for f in glob.glob(pattern)
                    if os.path.isfile(f) and os.path.getsize(f) > 0
                    and os.path.getmtime(f) >= floor]
            if hits:
                return max(hits, key=os.path.getmtime)
            if time.time() >= deadline:
                return ""
            task_manager.update_task(task_id, {
                'status': '待译',
                'message': f'待译：等待豆包交稿（已等 {waited // 60} 分钟，上限 {minutes:g} 分钟）',
            })
            time.sleep(5)
            waited += 5

    def _two_pass_adopt(self, task_id, input_path, config):
        """[v28.23] 两趟回路的**开关还原壳**。

        坑(实测踩到, 症状极具欺骗性): 第一趟要把 PAUSE_TRANSLATE 置 1、第二趟
        必须置空, 顺手在 finally 里 pop 就顺手把"运营开关"也 pop 了 —— 于是
        第一个任务跑完, 后续任务读不到开关, 静默退回"直接机器翻译"(第二个任务
        照样烧钱, 而日志看起来一切正常)。运营开关是环境变量, 不是一次性令牌:
        一进一出原样还回去。
        """
        _op_switch = os.environ.get("PAUSE_TRANSLATE")
        try:
            return self._two_pass_run(task_id, input_path, config)
        finally:
            if _op_switch is None:
                os.environ.pop("PAUSE_TRANSLATE", None)
            else:
                os.environ["PAUSE_TRANSLATE"] = _op_switch

    def _two_pass_run(self, task_id, input_path, config):
        """[v28.23] 一趟任务内的两趟采纳回路。

        第一趟 PAUSE_TRANSLATE=1 → 引擎不调翻译 LLM、不写缓存、返回原文, 跑完即得
        全篇侧车(豆包要的整篇载荷); export 裁成 inbox 载荷 → 任务停在「待译」;
        豆包交件后 deliver/import/inject 把译文灌回缓存库; 第二趟关掉开关重跑,
        全命中缓存 → 零翻译调用, 产物仍由引擎排版。

        止损两条路(2026-09-20 分家, 见下面 abort_fallback / abort_keep_delivery):
        「没有交件可留」的中断(导出失败、等稿超时)撤骨架行后回落成正常翻译, 用户至少
        拿到东西; 「交件被内容门禁拒收」保留现场并判失败 —— 静默回落机翻会让用户拿着
        一份机翻产物以为是自己的稿渲染出来的。
        """
        name = _adopt_run_name(input_path)
        try:
            total_pages = len(PdfReader(input_path).pages)
        except Exception:
            total_pages = 0
        wait_min = _two_pass_wait_minutes()
        t_start = time.time()   # 交件判据的下界: 只认此刻之后落盘的文件

        def abort_fallback(why):
            """止损坏收场: **撤骨架行** -> 回落成正常机器翻译。

            只给"没有交件可留"的中断用(载荷导出失败 / 等稿超时)。撤骨架行这步不能
            省: 第一趟为了让 seg_inject(UPDATE-only)有行可改, 按最终键形态落了
            raw→raw 行; 不撤就重跑, 第二趟全命中它们 —— 整篇出英文 PDF。撤干净了
            才敢回落成机器翻译, 用户至少拿到东西。
            """
            print(f"⚠️ [两趟] {why}，撤骨架行后回落成正常翻译")
            rc2, out2 = _run_tool("adopt.py", [
                "rollback", "--name", name, "--pdf", os.path.abspath(input_path), "--force",
            ])
            if rc2 != 0:
                print(f"⚠️ [两趟] rollback 也失败了(rc={rc2})，残留骨架行需人工核：\n{out2}")
            task_manager.update_task(task_id, {
                'status': '回灌重渲染',
                'message': '回路中断：已撤骨架行，按正常翻译重跑',
            })
            return self.translate_pdf(input_path, config, task_id)

        def abort_keep_delivery(why, out=""):
            """[v28.28] 交件被内容门禁拒收 -> **保留现场 · 判失败 · 不跑机翻**。

            为什么不能沿用"撤骨架行 + 回落机翻"(2026-09-20 SILAGE 那晚的真相):
              ① 撤了就白跑 —— 骨架行是 seg_inject(UPDATE-only)唯一的落脚点, 撤掉
                 之后修复回路得从整趟提字(81 页)重来;
              ② 不撤又跑机翻更糟 —— 骨架行按最终键形态躺在库里, 机翻会整篇命中
                 它们, 直接出一份**整页英文**的 PDF;
              ③ 真正的坑是"静默": 用户拿到的是机翻产物, 却以为是自己交的稿渲染出来
                 的, 对着"翻译重复、排版乱序"排查了一整晚 —— 而那份 PDF 里一个豆包
                 的字都没有。
            所以这里判失败, 把拒收原因指给用户(adopt 已把清单落成 review/ 报告),
            现场原样留着等豆包照报告改完重交 —— 重交只走 deliver/import/inject + 重渲染。
            """
            print("\n" + "=" * 70)
            print(f"🛑 [两趟] {why} —— 保留现场, 任务判失败(不再回落机翻)")
            print("   交件已被拒收：骨架行与交件都还在，**不必重跑提字**。")
            print("   修法：让豆包按 server/translated/review/ 下最新的「门禁拒收」报告"
                  "改完后重交（桥的 list_reports / get_report 可直接读）。")
            print("   ⚠️ 切勿改回「正常档」重跑本篇：库里的骨架行会被机翻整篇命中，"
                  "产物会是全英文 PDF。")
            if out:
                print("   —— adopt 输出（含拒收清单与报告路径）——")
                print(out[-1500:])
            print("=" * 70 + "\n")
            raise TwoPassGateRejected(
                f"{why}。交件未通过内容门禁，已拒收；现场保留（骨架行与交件都在，"
                f"不必重跑提字）。请让豆包按 server/translated/review/ 下最新的"
                f"「门禁拒收」报告改完后重交，再走 deliver/import/inject + 重渲染。")

        # ---- 第一趟: 提字（翻译这一步整篇不走 LLM）----
        print(f"🔍 [两趟] 第一趟·提字 (PAUSE_TRANSLATE=1, run={name}, {total_pages} 页)")
        task_manager.update_task(task_id, {
            'status': '提字中',
            'message': '第一趟：提取全篇原文（不翻译、不写缓存）',
        })
        os.environ["PAUSE_TRANSLATE"] = "1"
        # [v28.26] 提字进度: 侧车逐页追加 → 每 3 秒把"第几页/几段"写到卡片上。
        # 引擎那套进度解析要靠①抓控制台或②launch.ps1 的日志尾, IDE 终端里两样都
        # 没有(实测卡在 0%); 侧车是引擎自己写的文件, 拿它当进度源与终端形态无关。
        sidecar = _segflow_doc_sidecar(os.path.abspath(input_path))
        stop_watch = threading.Event()

        def _watch():
            while not stop_watch.wait(3):
                # 重跑同一篇时旧侧车还在, 首页处理时会把它**截断**重写; 等它新起来
                # 再读, 否则开头几秒会把上一轮的"第 20 页 / 350 段"当成这一轮的进度。
                try:
                    if os.path.getmtime(sidecar) < t_start:
                        continue
                except OSError:
                    continue
                segs, pages = _segflow_progress(sidecar)
                if not segs:
                    continue
                task_manager.update_task(task_id, {
                    'status': '提字中',
                    'message': ('提字中：第 %d/%d 页 · 已提取 %d 段（不翻译、不写缓存）'
                                % (pages, total_pages, segs)) if total_pages else
                               ('提字中：已提取 %d 段（不翻译、不写缓存）' % segs),
                    'progress': min(99, int(pages * 100 / total_pages)) if total_pages else 0,
                })

        threading.Thread(target=_watch, daemon=True).start()
        try:
            self.translate_pdf(input_path, config, task_id)
        finally:
            stop_watch.set()
            os.environ.pop("PAUSE_TRANSLATE", None)

        # ---- 侧车 -> inbox 载荷（豆包要的整篇）----
        rc, out = _run_tool("adopt.py", [
            "export", "--name", name,
            "--pages", f"1-{total_pages}" if total_pages else "1-1",
            "--pdf", os.path.abspath(input_path), "--force",
        ])
        if rc != 0:
            return abort_fallback(f"载荷导出失败(rc={rc})：{out[-400:]}")
        # [v28.26] 「待译」搬到 export **成功之后**再置: 以前是引擎一跑完就报待译,
        # 而那份"待译"可能对应一次失败的导出 —— 卡片会撒谎(载荷还没落盘就叫人来交稿)。
        _notify_payload_ready(task_id, name, f"1-{total_pages}" if total_pages else "1")

        # ---- 等豆包交稿 ----
        delivered = self._wait_for_doubao_delivery(task_id, name, wait_min, t_start)
        if delivered:
            print(f"🔍 [两趟] 收到豆包交件: {delivered}")
            # --text 显式点名: 认的就是刚等到的这一份, 不让 deliver 去 glob
            # (同名历史交件一堆)。--force 是给"同一篇重跑"开的: 它只跳过**顺序**
            # 门禁, 段号守恒 / ⋮ 断点对账 / dry 演算这些内容门禁一个不减。
            # [v28.35] deliver 追加 --waive: 自动回路里内容门禁**留痕放行**。
            # v28.28 立的"拒收 -> 判失败"是**人在场**时的正确收场(用户看到失败,
            # 让豆包照报告改完重交); 但这一趟是无人值守的复核回路 —— 收件、回锚、
            # 重渲染连着跑, 一旦拒收整条动线就停在这里, 而真正能改内容的豆包拿不到
            # 这条失败(桥只读 inbox/out 与 review/, 读不到任务状态), 用户回来只看
            # 到"失败", 稿子却还躺在 out/ 里没人用。SILAGE 那轮就是这么卡了 12 分钟
            # 等人工抬 mtime 才推进。
            # 放行不是静默: 报告照落 review/(桥的 list_reports/get_report 直接可读)、
            # 台账记 waive/waived 计数、这条日志与下面的告警都点名。清单仍在, 只是
            # 不再挡住渲染 —— 用户看得见"这一轮放行了哪几类、各几条"。
            # 覆盖面仅限**内容门禁不符**(留空/半截/⋮/逐段不变量/错位带); 段号守恒、
            # 载荷不可读、交件不唯一这些结构错误走的是 die, waive 到不了那里, 仍然拒收。
            # 要恢复严格口径: 把下面的 --waive 去掉即可(v28.28 的行为一字未删)。
            for stage in (["deliver", "--name", name, "--text", delivered, "--force", "--waive"],
                          ["import", "--name", name, "--force"],
                          ["inject", "--name", name, "--force"]):
                rc, out = _run_tool("adopt.py", stage)
                if rc != 0:
                    # [v28.28] 交件阶段失败 = 有交件可留 -> 保留现场判失败, 不回落机翻。
                    # 段号守恒 / ⋮ 对账 / 逐段不变量 / import / inject 任一不过都算。
                    return abort_keep_delivery(f"{stage[0]} 阶段被拒", out)
                if stage[0] == "deliver" and "--waive 放行" in out:
                    # 放行必须让人看见 —— 报告路径已经在 adopt 的输出里, 这里再点一次名
                    print("⚠️ [两趟] 内容门禁**留痕放行**: " + out.strip().splitlines()[-1])
        else:
            return abort_fallback(f"等待交稿超时（{wait_min:g} 分钟）")

        # ---- 第二趟: 重渲染（有回灌则全命中缓存, 零翻译调用）----
        task_manager.update_task(task_id, {
            'status': '回灌重渲染',
            'message': '译文已回灌，重渲染中',
        })
        print("🔍 [两趟] 第二趟·重渲染")
        return self.translate_pdf(input_path, config, task_id)

    def _execute_translate_job(self, task_id, input_path, config, engine):
        def addFileList(fileList, filePath):
            if os.path.exists(filePath):
                fileList.append(filePath)

        if engine == pdf2zh:
            print("🔍 [Zotero PDF2zh Server] PDF2zh 开始翻译文件...")
            # [v28.23] 两趟采纳回路: 开着开关才走「提字 → 待译 → 重渲染」;
            # 关着时行为与过去完全一致(默认关, 别的用户路径不变)。
            if _two_pass_enabled():
                fileList = self._two_pass_adopt(task_id, input_path, config)
            else:
                fileList = self.translate_pdf(input_path, config, task_id)
            mono_path, dual_path = fileList[0], fileList[1]
            if config.mono_cut:
                mono_cut_path = self.get_filename_after_process(mono_path, 'mono-cut', engine)
                self.cropper.crop_pdf(config, mono_path, 'mono', mono_cut_path, 'mono-cut')
                addFileList(fileList, mono_cut_path)
            if config.dual_cut:
                dual_cut_path = self.get_filename_after_process(dual_path, 'dual-cut', engine)
                self.cropper.crop_pdf(config, dual_path, 'dual', dual_cut_path, 'dual-cut')
                addFileList(fileList, dual_cut_path)
            if config.crop_compare:
                crop_compare_path = self.get_filename_after_process(dual_path, 'crop-compare', engine)
                self.cropper.crop_pdf(config, dual_path, 'dual', crop_compare_path, 'crop-compare')
                addFileList(fileList, crop_compare_path)
            if config.compare:
                if config.babeldoc:  # babeldoc 不支持 compare
                    # [自研补丁] 旧代码静默跳过, 用户不知道为何没产物, 补日志
                    print("ℹ️ babeldoc(pdf2zh 1.x 实验内核) 不支持 compare, 已跳过该产物")
                else:
                    compare_path = self.get_filename_after_process(dual_path, 'compare', engine)
                    self.cropper.merge_pdf(dual_path, compare_path)
                    addFileList(fileList, compare_path)

        elif engine == pdf2zh_next:
            print("🔍 [Zotero PDF2zh Server] PDF2zh_next 开始翻译文件...")
            if config.mono_cut or config.mono:
                config.no_mono = False
            if config.dual or config.dual_cut or config.crop_compare or config.compare:
                config.no_dual = False

            if config.no_dual and config.no_mono:
                raise ValueError("⚠️ [Zotero PDF2zh Server] pdf2zh_next 引擎至少需要生成 mono 或 dual 文件, 请检查 no_dual 和 no_mono 配置项")

            fileList = []
            retList = self.translate_pdf_next(input_path, config, task_id)

            # [自研补丁 2026-09-03] 产物守卫: 引擎只产出 mono/dual 之一或
            # 零产物时, 旧代码 retList[1] 直接 IndexError, 报错无业务含义
            if not retList:
                raise ValueError("pdf2zh_next 未产出任何 mono/dual 文件, 请查看引擎日志确认失败原因")
            if config.no_mono:
                dual_path = retList[0]
            elif config.no_dual:
                mono_path = retList[0]
                fileList.append(mono_path)
            else:
                if len(retList) < 2:
                    raise ValueError(
                        f"pdf2zh_next 只产出了 1 个文件({os.path.basename(retList[0])}), "
                        "但当前设置要求同时生成 mono 和 dual, 请检查 no_dual/no_mono 配置")
                mono_path, dual_path = retList[0], retList[1]
                fileList.append(mono_path)

            # Canonicalize every pdf2zh_next dual output so later operations
            # can recover its layout even after the file is attached to Zotero
            # and uploaded again in a separate request.
            primary_dual_path = None
            LR_dual_path = None
            TB_dual_path = None
            if not config.no_dual:
                primary_dual_path = self._canonicalize_pdf2zh_next_dual(dual_path, config.dual_mode)
                if config.dual_mode == 'LR':
                    LR_dual_path = primary_dual_path
                    if config.dual_cut or config.crop_compare:
                        _, TB_dual_path = self.cropper.pdf_dual_mode(primary_dual_path, 'LR', 'TB')
                else:
                    TB_dual_path = primary_dual_path

                if config.dual:
                    fileList.append(primary_dual_path)

            if config.mono_cut:
                mono_cut_path = self.get_filename_after_process(mono_path, 'mono-cut', engine)
                self.cropper.crop_pdf(config, mono_path, 'mono', mono_cut_path, 'mono-cut')
                addFileList(fileList, mono_cut_path)

            if config.dual_cut:
                if not TB_dual_path:
                    raise ValueError("dual-cut 需要 TB dual 输入，但未能准备该布局。")
                dual_cut_path = self.get_filename_after_process(TB_dual_path, 'dual-cut', engine)
                self.cropper.crop_pdf(config, TB_dual_path, 'dual', dual_cut_path, 'dual-cut')
                addFileList(fileList, dual_cut_path)

            if config.crop_compare:
                if not TB_dual_path:
                    raise ValueError("crop-compare 需要 TB dual 输入，但未能准备该布局。")
                crop_compare_path = self.get_filename_after_process(TB_dual_path, 'crop-compare', engine)
                self.cropper.crop_pdf(config, TB_dual_path, 'dual', crop_compare_path, 'crop-compare')
                addFileList(fileList, crop_compare_path)

            if config.compare:
                if config.dual_mode == 'LR':
                    if not LR_dual_path:
                        raise ValueError("compare 需要 LR dual 输入，但未能准备该布局。")
                    compare_path = self.get_filename_after_process(LR_dual_path, 'compare', engine)
                    if os.path.exists(compare_path):
                        os.remove(compare_path)
                    shutil.copyfile(LR_dual_path, compare_path)
                    addFileList(fileList, compare_path)
                else:
                    if not TB_dual_path:
                        raise ValueError("compare 需要 TB dual 输入，但未能准备该布局。")
                    compare_path = self.get_filename_after_process(TB_dual_path, 'compare', engine)
                    self.cropper.merge_pdf(TB_dual_path, compare_path)
                    addFileList(fileList, compare_path)
        else:
            raise ValueError(f"⚠️ [Zotero PDF2zh Server] 输入了不支持的翻译引擎: {engine}, 目前脚本仅支持: pdf2zh/pdf2zh_next")

        existing = [p for p in fileList if os.path.exists(p)]
        missing  = [p for p in fileList if not os.path.exists(p)]

        for m in missing:
            print(f"⚠️ 期望生成但不存在: {m}")
        for f in existing:
            size = os.path.getsize(f)
            print(f"🐲 翻译成功, 生成文件: {f}, 大小为: {size/1024.0/1024.0:.2f} MB")

        if not existing:
            task_manager.complete_task(task_id, 'failed', '操作失败，请查看详细日志。', error='无文件生成')
            return {'status': 'error', 'message': '操作失败，请查看详细日志。'}

        payload = self._success_files_payload(existing)
        task_manager.complete_task(
            task_id,
            'success',
            f'成功生成 {len(existing)} 个文件',
            file_list=payload['fileList'],
            file_paths=payload['filePaths'],
            output_dir=payload['outputDir'],
            result=payload,
        )

        # [自研补丁 2026-09-03] 翻译后自动质检: 后台线程跑 pre_check(翻前体检)
        # + post_check(质检门禁), 报告写入 translated/review/, 失败只打日志,
        # 绝不影响翻译主流程与响应。
        # 兼容两种产物命名: pdf2zh 1.x 的 name-mono.pdf 与
        # pdf2zh_next(BabelDOC) 的 name.{lang}.mono.pdf
        mono_for_qc = next(
            (p for p in existing
             if p.endswith(('-mono.pdf', '.mono.pdf'))), None)
        if mono_for_qc:
            # [自研补丁 2026-09-03] 把任务实际跳页数传给 post_check(报告 🔴5):
            # 质检只豁免末尾连续 skipLastPages 个原文保留页, 封堵文献页失败误放行
            qc_skip_last = int(getattr(config, 'skip_last_pages', 0) or 0)
            threading.Thread(
                target=self._run_auto_qc,
                args=(input_path, mono_for_qc, qc_skip_last),
                daemon=True,
            ).start()

        return payload

    def _qc_python(self):
        """[自研补丁] 质检脚本解释器: 优先用 pdf2zh 托管 venv 的 python
        (内有 pypdf); sys.executable 是 uv 基础解释器, 没有质检依赖。"""
        try:
            existing = self.env_manager._existing(
                "pdf2zh", self.env_manager.curr_envtool)
            if existing and existing[2]:
                return str(existing[2])
        except Exception:
            pass
        return sys.executable

    def _run_auto_qc(self, input_path, mono_path, skip_last=0):
        """[自研补丁] 翻译成功后自动执行翻前体检与质检门禁（后台线程调用）"""
        # [自研补丁] 信号量限流: 批量翻译收尾时最多 2 个 QC 进程并发
        with _QC_SEMAPHORE:
            self._run_auto_qc_locked(input_path, mono_path, skip_last=skip_last)

    def _run_auto_qc_locked(self, input_path, mono_path, skip_last=0):
        tools_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools')
        qc_python = self._qc_python()
        # [自研补丁 2026-09-03] post_check 带 --skip-last(报告 🔴5)
        post_args = [mono_path]
        if skip_last and skip_last > 0:
            post_args += ['--skip-last', str(skip_last)]
        commands = (('pre_check.py', [input_path]),
                    ('post_check.py', post_args))
        for script, script_args in commands:
            try:
                script_path = os.path.join(tools_dir, script)
                if not os.path.isfile(script_path):
                    print(f"⚠️ [自动质检] 缺少 {script_path}，跳过")
                    continue
                print(f"🧪 [自动质检] 运行 {script} ({qc_python}) ...")
                proc = subprocess.run(
                    [qc_python, '-X', 'utf8', script_path] + script_args,
                    capture_output=True, text=True, encoding='utf-8',
                    errors='replace', timeout=300)
                if proc.returncode == 0:
                    status = 'PASS'
                elif proc.returncode == 1:
                    status = 'FAIL'
                else:
                    status = f'异常退出码 {proc.returncode}'
                out = (proc.stdout or '').strip()
                tail = '\n'.join(out.splitlines()[-8:]) if out else '(无输出)'
                print(f"🧪 [自动质检] {script} → {status}\n{tail}")
            except Exception as exc:
                print(f"⚠️ [自动质检] {script} 执行异常: {exc}")

    def _handle_exception(self, exc, status_code=500, context=None, task_id=None):
        return jsonify(self._exception_payload(exc, context=context, task_id=task_id)), status_code

    def _exception_payload(self, exc, context=None, task_id=None):
        # [自研补丁 2026-09-18 闸门4] 先取根因，再打印。
        # 顺序不能颠倒：traceback.print_exception 会把 server 自己的
        # CalledProcessError 栈写进控制台，而闸门4 正是从控制台尾部取
        # "最后一个 Traceback"——颠倒过来就只会抽到我们自己那句没营养的话。
        info = self._derive_error_info(exc, task_id=task_id)
        if context:
            print(f"⚠️ [Zotero PDF2zh Server] {context} Error: {exc}")
        else:
            print(f"⚠️ [Zotero PDF2zh Server] Error: {exc}")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        payload = {
            'status': 'error',
            'ok': False,
            'message': info['message'],
        }
        error_type = info.get('errorType')
        if error_type:
            payload['errorType'] = error_type
        if isinstance(exc, subprocess.CalledProcessError):
            payload['exitCode'] = exc.returncode
        return payload

    def _derive_error_info(self, exc, task_id=None):
        parts = []
        if isinstance(exc, subprocess.CalledProcessError) and getattr(exc, 'stderr', None):
            parts.append(exc.stderr)
        formatted = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if formatted:
            parts.append(formatted)
        blob = '\n'.join(part for part in parts if part)
        # [自研补丁 2026-09-18 闸门4] Windows 继承输出流模式拿不到子进程 stderr，
        # 这里补上"输出流尾部"（有控制台读屏幕；launch.ps1 无控制台的部署读日志尾部），
        # 否则 CalledProcessError 的 blob 里只有一句
        # "returned non-zero exit status 1"，根因完全丢失
        if isinstance(exc, subprocess.CalledProcessError) and not (exc.stderr or '').strip():
            stream = failure_output_tail(task_id)
            if stream:
                blob = '\n'.join(p for p in (blob, stream) if p)

        ve_msg = self._extract_value_error(blob)
        if ve_msg:
            return {
                'errorType': 'ValueError',
                'message': ve_msg,
            }

        # [自研补丁 闸门4] 子进程自己的 traceback 比"最后一行可读文本"信息量大得多：
        # 它带异常类 + 崩在哪个文件哪一行哪一函数
        root = extract_root_cause(blob)
        if root:
            error_type = root.split(':', 1)[0].strip()   # 'AttributeError: ...' → 'AttributeError'
            if not error_type or ' ' in error_type:
                error_type = exc.__class__.__name__
            return {
                'errorType': error_type,
                'message': root,
            }

        def _tail_readable(text):
            lines = [ln.rstrip() for ln in text.splitlines()]
            for ln in reversed(lines):
                if not ln:
                    continue
                if ln.startswith(('Traceback', 'File ')):
                    continue
                return ln
            return str(exc).strip() or exc.__class__.__name__

        fallback_message = _tail_readable(blob) if blob else (str(exc).strip() or exc.__class__.__name__)
        return {
            'errorType': exc.__class__.__name__,
            'message': fallback_message,
        }

    @staticmethod
    def _extract_value_error(blob):
        if not blob:
            return None
        if not isinstance(blob, str):
            blob = str(blob)

        matches = list(_VALUE_ERROR_RE.finditer(blob))
        if not matches:
            return None

        match = matches[-1]
        msg = match.group('msg').strip()

        tail_lines = []
        for line in blob[match.end():].splitlines():
            if not line:
                break
            if line.startswith('Traceback') or _VALUE_ERROR_RE.match(line):
                break
            if line[:1] in (' ', '\t') or line.startswith('^'):
                tail_lines.append(line.strip())
            else:
                break

        if tail_lines:
            msg += ' ' + ' '.join(tail_lines)

        return msg or None

    # 裁剪 /crop
    def crop(self):
        try:
            input_path, config = self.process_request()
            infile_type = self.get_filetype(input_path)

            source_path = input_path
            if infile_type == 'dual' and self.get_dual_mode(input_path, config.dual_mode) == 'LR':
                # Crop means a crop result, not merely a layout conversion.
                # Normalize LR -> alternating-page TB internally, then continue
                # through the normal dual -> dual-cut operation.
                _, source_path = self.cropper.pdf_dual_mode(input_path, 'LR', 'TB')

            new_type = self.get_filetype_after_crop(input_path)
            if new_type == 'unknown':
                return jsonify({
                    'status': 'error',
                    'errorType': 'InvalidPDFOperation',
                    'message': f'当前 PDF 类型 {infile_type} 不能再次执行裁剪。请选择原文、mono 或 dual 文件。'
                }), 400

            new_path = self.get_filename_after_process(input_path, new_type, config.engine)
            self.cropper.crop_pdf(config, source_path, infile_type, new_path, new_type)
            print(f"🔍 [Zotero PDF2zh Server] 开始裁剪文件: {source_path}, {infile_type}, 裁剪类型: {new_type}, {new_path}")

            if os.path.exists(new_path):
                return self._success_files_response([new_path])
            return jsonify({'status': 'error', 'message': f'Crop failed: {new_path} not found'}), 500
        except Exception as e:
            return self._handle_exception(e, context='/crop')

    def crop_compare(self):
        task_id = str(uuid.uuid4())
        start_time = datetime.now()
        try:
            input_path, config = self.process_request()
            infile_type = self.get_filetype(input_path)
            engine = config.engine

            if infile_type == 'crop-compare':
                return jsonify({
                    'status': 'error',
                    'errorType': 'InvalidPDFOperation',
                    'message': '该 PDF 已经是“裁剪后双语对照”结果，无需再次执行 crop-compare。请选择原文或 dual 附件。'
                }), 409
            if infile_type not in {'origin', 'dual', 'dual-cut'}:
                return jsonify({
                    'status': 'error',
                    'errorType': 'InvalidPDFOperation',
                    'message': f'当前 PDF 类型 {infile_type} 不能执行 crop-compare。请选择原文、dual 或 dual-cut 文件。'
                }), 400

            task_info = self._build_task_info(
                task_id, input_path, config, engine, start_time, status='开始处理'
            )
            return self._start_accepted_job(
                task_id,
                task_info,
                lambda: self._execute_crop_compare_job(
                    task_id, input_path, config, engine, infile_type
                ),
                '/crop-compare',
            )
        except Exception as e:
            task_manager.complete_task(task_id, 'failed', str(e), error=failure_brief(e, task_id=task_id))
            return self._handle_exception(e, context='/crop-compare', task_id=task_id)

    def _execute_crop_compare_job(self, task_id, input_path, config, engine, infile_type):
        if infile_type == 'origin':
            if engine != pdf2zh_next:  # [自研补丁] 去掉冗余布尔判断
                config.engine = 'pdf2zh'
                fileList = self.translate_pdf(input_path, config, task_id)
                input_path = fileList[1]
                if not os.path.exists(input_path):
                    raise FileNotFoundError(f'Dual file not found: {input_path}')
            else:
                # crop-compare internally requires alternating-page TB dual.
                config.dual_mode = 'TB'
                config.no_dual = False
                config.no_mono = True
                fileList = self.translate_pdf_next(input_path, config, task_id)
                input_path = fileList[0]
                if not os.path.exists(input_path):
                    raise FileNotFoundError(f'Dual file not found: {input_path}')

        infile_type = self.get_filetype(input_path)
        if infile_type == 'dual-cut':
            new_path = self.get_filename_after_process(input_path, 'crop-compare', engine)
            self.cropper.merge_pdf(input_path, new_path)
        elif infile_type == 'dual':
            source_path = input_path
            if self.get_dual_mode(input_path, config.dual_mode) == 'LR':
                _, source_path = self.cropper.pdf_dual_mode(input_path, 'LR', 'TB')
            new_path = self.get_filename_after_process(input_path, 'crop-compare', engine)
            self.cropper.crop_pdf(config, source_path, 'dual', new_path, 'crop-compare')
        else:
            raise ValueError(f'当前 PDF 类型 {infile_type} 不能执行 crop-compare。请选择原文、dual 或 dual-cut 文件。')

        if not os.path.exists(new_path):
            raise RuntimeError(f'Crop-compare failed: {new_path} not found')
        fileName = os.path.basename(new_path)
        size = os.path.getsize(new_path)
        print(f"🐲 双语对照成功(裁剪后拼接), 生成文件: {fileName}, 大小为: {size/1024.0/1024.0:.2f} MB")
        return self._complete_job_files(
            task_id, [new_path], f'成功生成 {fileName}'
        )

    # /compare
    def compare(self):
        task_id = str(uuid.uuid4())
        start_time = datetime.now()
        try:
            input_path, config = self.process_request()
            infile_type = self.get_filetype(input_path)
            engine = config.engine

            if infile_type == 'compare':
                return jsonify({
                    'status': 'error',
                    'errorType': 'InvalidPDFOperation',
                    'message': '该 PDF 已经是双语对照结果，无需再次执行 compare。请选择原文或 dual 附件。'
                }), 409
            if infile_type not in {'origin', 'dual'}:
                return jsonify({
                    'status': 'error',
                    'errorType': 'InvalidPDFOperation',
                    'message': f'当前 PDF 类型 {infile_type} 不能执行 compare。请选择原文或 dual 文件。'
                }), 400

            task_info = self._build_task_info(
                task_id, input_path, config, engine, start_time, status='开始处理'
            )
            return self._start_accepted_job(
                task_id,
                task_info,
                lambda: self._execute_compare_job(
                    task_id, input_path, config, engine, infile_type
                ),
                '/compare',
            )
        except Exception as e:
            task_manager.complete_task(task_id, 'failed', str(e), error=failure_brief(e, task_id=task_id))
            return self._handle_exception(e, context='/compare', task_id=task_id)

    def _execute_compare_job(self, task_id, input_path, config, engine, infile_type):
        if infile_type == 'origin':
            if engine != pdf2zh_next:  # [自研补丁] 去掉冗余布尔判断
                config.engine = 'pdf2zh'
                fileList = self.translate_pdf(input_path, config, task_id)
                input_path = fileList[1]
                if not os.path.exists(input_path):
                    raise FileNotFoundError(f'Dual file not found: {input_path}')
            else:
                config.dual_mode = 'LR'
                config.no_dual = False
                config.no_mono = True
                fileList = self.translate_pdf_next(input_path, config, task_id)
                dual_path = fileList[0]
                if not os.path.exists(dual_path):
                    raise FileNotFoundError(f'Dual file not found: {dual_path}')
                new_path = self.get_filename_after_process(input_path, 'compare', engine)
                if os.path.exists(new_path):
                    os.remove(new_path)
                os.rename(dual_path, new_path)
                fileName = os.path.basename(new_path)
                print(f"🐲 双语对照成功, 生成文件: {fileName}, 大小为: {os.path.getsize(new_path)/1024.0/1024.0:.2f} MB")
                return self._complete_job_files(
                    task_id, [new_path], f'成功生成 {fileName}'
                )

        infile_type = self.get_filetype(input_path)
        if infile_type != 'dual':
            raise ValueError(f'当前 PDF 类型 {infile_type} 不能执行 compare。请选择原文或 dual 文件。')

        new_path = self.get_filename_after_process(input_path, 'compare', engine)
        if self.get_dual_mode(input_path, config.dual_mode) == 'LR':
            if os.path.exists(new_path):
                os.remove(new_path)
            shutil.copyfile(input_path, new_path)
        else:
            self.cropper.merge_pdf(input_path, new_path)

        if not os.path.exists(new_path):
            raise RuntimeError(f'Compare failed: {new_path} not found')
        fileName = os.path.basename(new_path)
        print(f"🐲 双语对照成功, 生成文件: {fileName}, 大小为: {os.path.getsize(new_path)/1024.0/1024.0:.2f} MB")
        return self._complete_job_files(
            task_id, [new_path], f'成功生成 {fileName}'
        )

    def get_filetype(self, path):
        name = os.path.basename(str(path))
        # Check terminal/specific suffixes before generic dual/mono markers.
        if 'crop-compare.pdf' in name:
            return 'crop-compare'
        if 'dual-cut.pdf' in name:
            return 'dual-cut'
        if 'mono-cut.pdf' in name:
            return 'mono-cut'
        if 'compare.pdf' in name:
            return 'compare'
        if name.endswith('.LR_dual.pdf') or name.endswith('.TB_dual.pdf') or 'dual.pdf' in name:
            return 'dual'
        if 'mono.pdf' in name:
            return 'mono'
        if 'cut.pdf' in name:
            return 'origin-cut'
        return 'origin'

    def get_dual_mode(self, path, fallback='TB'):
        name = os.path.basename(str(path))
        if name.endswith('.LR_dual.pdf'):
            return 'LR'
        if name.endswith('.TB_dual.pdf'):
            return 'TB'
        mode = str(fallback or 'TB').upper()
        return mode if mode in {'LR', 'TB'} else 'TB'

    def _canonicalize_pdf2zh_next_dual(self, dual_path, mode):
        if not dual_path or not os.path.exists(dual_path):
            raise FileNotFoundError(f"Dual file not found: {dual_path}")
        mode = 'LR' if str(mode).upper() == 'LR' else 'TB'
        path = str(dual_path)
        if path.endswith('.LR_dual.pdf') or path.endswith('.TB_dual.pdf'):
            current = self.get_dual_mode(path)
            if current == mode:
                return path
            lr_path, tb_path = self.cropper.pdf_dual_mode(path, current, mode)
            return lr_path if mode == 'LR' else tb_path
        if path.endswith('.dual.pdf'):
            target = path[:-len('.dual.pdf')] + f'.{mode}_dual.pdf'
        else:
            target = path[:-4] + f'.{mode}_dual.pdf' if path.endswith('.pdf') else path + f'.{mode}_dual.pdf'
        if os.path.exists(target):
            os.remove(target)
        os.replace(path, target)
        return target

    def get_filetype_after_crop(self, path):
        filetype = self.get_filetype(path)
        print(f"🔍 [Zotero PDF2zh Server] 获取文件类型: {filetype} from {path}")
        if filetype == 'origin':
            return 'origin-cut'
        if filetype == 'mono':
            return 'mono-cut'
        if filetype == 'dual':
            return 'dual-cut'
        return 'unknown'

    def get_filetype_after_cropCompare(self, path):
        filetype = self.get_filetype(path)
        if filetype in {'origin', 'dual', 'dual-cut'}:
            return 'crop-compare'
        return 'unknown'

    def get_filetype_after_compare(self, path):
        filetype = self.get_filetype(path)
        if filetype in {'origin', 'dual'}:
            return 'compare'
        return 'unknown'

    def get_filename_after_process(self, inpath, outtype, engine):
        inpath = str(inpath)
        intype = self.get_filetype(inpath)
        if intype == 'dual':
            # Remove the layout marker from derived terminal products.
            for suffix in ('.LR_dual.pdf', '.TB_dual.pdf'):
                if inpath.endswith(suffix):
                    base = inpath[:-len(suffix)]
                    return base + (f'-{outtype}.pdf' if engine == pdf2zh else f'.{outtype}.pdf')

        if engine != pdf2zh_next:  # [自研补丁] 去掉冗余布尔判断
            if intype == 'origin':
                if outtype == 'origin-cut':
                    return inpath.replace('.pdf', '-cut.pdf')
                return inpath.replace('.pdf', f'-{outtype}.pdf')
            return inpath.replace(f'{intype}.pdf', f'{outtype}.pdf')

        if intype == 'origin':
            if outtype == 'origin-cut':
                return inpath.replace('.pdf', '.cut.pdf')
            return inpath.replace('.pdf', f'.{outtype}.pdf')
        return inpath.replace(f'{intype}.pdf', f'{outtype}.pdf')

    def translate_pdf(self, input_path, config, task_id=None):
        # TODO: 如果翻译失败了, 自动执行跳过字体子集化, 并且显示生成的文件的大小
        config.update_config_file(config_path[pdf2zh])
        if config.targetLang == 'zh-CN': # TOFIX, pdf2zh 1.x converter没有通过
            config.targetLang = 'zh'
        if config.sourceLang == 'zh-CN': # TOFIX, pdf2zh 1.x converter没有通过
            config.sourceLang = 'zh'
        # [自研补丁 2026-09-03] 关键防御: pdf2zh 子进程的 ConfigManager 会把
        # --config 指向的文件注册为自己的配置并在启动时"规范化"写回——实测会把
        # POLISH 等未知键置 null 污染服务器主配置。因此传"临时副本"给子进程,
        # 主配置永不被子进程触碰。
        task_config_path = str(config_path[pdf2zh]) + '.task'
        try:
            shutil.copyfile(str(config_path[pdf2zh]), task_config_path)
        except Exception as _e:
            print(f"⚠️ 创建任务配置副本失败, 回退为直接传主配置: {_e}")
            task_config_path = str(config_path[pdf2zh])
        cmd = [
            pdf2zh,
            input_path,
            '--t', str(config.thread_num),
            '--output', str(output_folder),
            '--service', str(config.service),
            '--lang-in', str(config.sourceLang),
            '--lang-out', str(config.targetLang),
            '--config', task_config_path,
        ]

        # [自研补丁] 公式保护: pdf2zh 1.x 不识别 --formular-*-pattern,
        # 需改用 -f (--vfont) / -c (--vchar); 配方便用 config.toml 的 [pdf] 段。
        # 读取失败只告警不加参数, 不影响正常翻译。
        try:
            with open(config_path[pdf2zh_next], 'r', encoding='utf-8') as _f:
                _pdf_cfg = (toml.load(_f).get('pdf') or {})
            _vfont = _pdf_cfg.get('formular_font_pattern')
            _vchar = _pdf_cfg.get('formular_char_pattern')
            if _vfont and _vfont != 'null':
                cmd.extend(['-f', str(_vfont)])
            if _vchar and _vchar != 'null':
                cmd.extend(['-c', str(_vchar)])
        except Exception as _e:
            print(f"⚠️ 读取公式保护配方失败, 本次不加 -f/-c: {_e}")

        if config.skip_last_pages and config.skip_last_pages > 0:
            total_pages = len(PdfReader(input_path).pages)
            end = total_pages - config.skip_last_pages
            if end < 1:
                # [自研补丁] 边界守卫: 跳页数≥总页数会生成 '-p 1-0' 非法参数
                raise ValueError(
                    f"'最后几页跳过翻译'={config.skip_last_pages} 会跳过全部 {total_pages} 页, "
                    "请在插件设置里调小该值后重试")
            # [自研补丁] '-p' 与页码区间分两个 token, 旧写法 '-p 1-5' 单 token 内嵌空格属脆弱写法
            cmd.extend(['-p', f'1-{end}'])
        if config.skip_font_subsets:
            cmd.append('--skip-subset-fonts')
        if config.babeldoc:
            print("🔍 [Zotero PDF2zh Server] 目前不推荐使用pdf2zh 1.x + babeldoc, 如有需要，请直接使用pdf2zh_next")
            cmd.append('--babeldoc')
        # [自研补丁 2026-09-19 v28.9] 把本篇原文 PDF 的绝对路径交给 pdf2zh 子进程:
        # 侧车要按"文档"归档 (converter 的 _segflow_paths 读 P2Z_DOC_PDF), 而转换器
        # 自己拿不到输入路径 —— 上游 TranslateConverter 签名里没有它。子进程默认
        # 继承 os.environ, 故在父进程设一次即可; 唯一假设是"同时只翻一篇", 与既有
        # 的共享日志独占闸是同一假设。转换器读不到时只写 latest.jsonl, 不阻断翻译。
        os.environ["P2Z_DOC_PDF"] = os.path.abspath(input_path)
        try:
            # 使用 execute_with_progress 替代原来的 execute_in_env / subprocess.run
            # 实时解析子进程输出中的进度信息并更新 task_manager
            execute_with_progress(cmd, task_id, args, self.env_manager if args.enable_venv else None)
        except subprocess.CalledProcessError as e:
            print(f"⚠️ 翻译失败, 错误信息: {e}, 尝试跳过字体子集化, 重新渲染\n")
            cmd.append('--skip-subset-fonts')
            execute_with_progress(cmd, task_id, args, self.env_manager if args.enable_venv else None)
        # [自研补丁] 用 splitext 取 stem: 旧写法 .replace('.pdf','') 会替换
        # 文件名中所有出现, 在 'a.pdf.b.pdf' 类名字上与 stem 语义分歧
        fileName = os.path.splitext(os.path.basename(input_path))[0]
        if config.babeldoc:
            output_path_mono = os.path.join(output_folder, f"{fileName}.{config.targetLang}.mono.pdf")
            output_path_dual = os.path.join(output_folder, f"{fileName}.{config.targetLang}.dual.pdf")
        else:
            output_path_mono = os.path.join(output_folder, f"{fileName}-mono.pdf")
            output_path_dual = os.path.join(output_folder, f"{fileName}-dual.pdf")
        output_files = [output_path_mono, output_path_dual]
        for f in output_files: # 显示生成
            if not os.path.exists(f):
                print(f"⚠️ 未找到期望生成的文件: {f}")
                continue
            size = os.path.getsize(f)
            print(f"🐲 pdf2zh 翻译成功, 生成文件: {f}, 大小为: {size/1024.0/1024.0:.2f} MB")
        return output_files

    def translate_pdf_next(self, input_path, config, task_id=None):
        if config.service in pdf2zh_next_service_aliases:
            config.service = pdf2zh_next_service_aliases[config.service]
        config.update_config_file(config_path[pdf2zh_next])

        cmd = [
            pdf2zh_next,
            input_path,
            '--' + config.service,
            '--qps', str(config.qps),
            '--output', str(output_folder),
            '--lang-in', str(config.sourceLang),
            '--lang-out', str(config.targetLang),
            '--config-file', str(config_path[pdf2zh_next]), # 使用默认的config path路径
        ]
        # TODO: 增加术语表的地址
        if config.no_watermark:
            cmd.extend(['--watermark-output-mode', 'no_watermark'])
        else:
            cmd.extend(['--watermark-output-mode', 'watermarked'])
        if config.skip_last_pages and config.skip_last_pages > 0:
            total_pages = len(PdfReader(input_path).pages)
            end = total_pages - config.skip_last_pages
            if end < 1:
                # [自研补丁] 边界守卫: 跳页数≥总页数会生成非法页码区间
                raise ValueError(
                    f"'最后几页跳过翻译'={config.skip_last_pages} 会跳过全部 {total_pages} 页, "
                    "请在插件设置里调小该值后重试")
            cmd.extend(['--pages', f'1-{end}'])
        if config.no_dual:
            cmd.append('--no-dual')
        if config.no_mono:
            cmd.append('--no-mono')
        if config.trans_first:
            cmd.append('--dual-translate-first')
        if config.skip_clean:
            cmd.append('--skip-clean')
        if config.disable_rich_text_translate:
            cmd.append('--disable-rich-text-translate')
        if config.enhance_compatibility:
            cmd.append('--enhance-compatibility')
        if config.save_auto_extracted_glossary:
            cmd.append('--save-auto-extracted-glossary')
        if config.disable_glossary:
            cmd.append('--no-auto-extract-glossary')
        if config.dual_mode == 'TB': # TB or LR, LR是defualt的
            cmd.append('--use-alternating-pages-dual')
        if config.translate_table_text:
            cmd.append('--translate-table-text')
        if config.ocr:
            cmd.append('--ocr-workaround')
        if config.auto_ocr:
            cmd.append('--auto-enable-ocr-workaround')
        if config.font_family and config.font_family in ['serif', 'sans-serif', 'script']:
            cmd.extend(['--primary-font-family', config.font_family])
        if config.pool_size and config.pool_size > 1:
            cmd.extend(['--pool-max-worker', str(config.pool_size)])

        # [自研补丁] splitext 取 stem, 理由同 pdf2zh 分支
        fileName = os.path.splitext(os.path.basename(input_path))[0]
        no_watermark_mono = os.path.join(output_folder, f"{fileName}.no_watermark.{config.targetLang}.mono.pdf")
        no_watermark_dual = os.path.join(output_folder, f"{fileName}.no_watermark.{config.targetLang}.dual.pdf")
        watermark_mono = os.path.join(output_folder, f"{fileName}.{config.targetLang}.mono.pdf")
        watermark_dual = os.path.join(output_folder, f"{fileName}.{config.targetLang}.dual.pdf")

        output_path = []
        if config.no_watermark: # 无水印
            if not config.no_mono:
                output_path.append(no_watermark_mono)
            if not config.no_dual:
                output_path.append(no_watermark_dual)
        else: # 有水印
            if not config.no_mono:
                output_path.append(watermark_mono)
            if not config.no_dual:
                output_path.append(watermark_dual)

        if args.enable_winexe and os.path.exists(args.winexe_path):
            cmd = [f"{args.winexe_path}"] + cmd[1:]  # Windows可执行文件
            # 将所有是路径的字段, 改为os.path.normpath
            cmd = [os.path.normpath(arg) if os.path.isfile(arg) or os.path.isdir(arg) else arg for arg in cmd]
            # 设置工作目录为 exe 所在目录，确保相对路径解析正确
            exe_dir = os.path.dirname(args.winexe_path)

            # 打印开关状态
            print(f"🔧 [winexe] winexe_attach_console={args.winexe_attach_console}")

            if args.winexe_attach_console:

                # 附着父控制台模式
                print("🚀 [winexe] mode=attach-console")
                print(f"📁 [winexe] cwd={exe_dir}")

                # 隐藏敏感信息后的命令显示
                safe_cmd = []
                for i, arg in enumerate(cmd):
                    if i > 0 and any(sensitive in cmd[i-1].lower() for sensitive in ['key', 'token', 'secret', 'password']):
                        safe_cmd.append('***')
                    else:
                        safe_cmd.append(arg)
                print(f"⚡ [winexe] cmd={' '.join(safe_cmd)}")

                # Do not launch a separate ``--help`` process just to probe
                # console visibility.  Some standalone pdf2zh executables do
                # substantial initialization even for help output.  The real
                # translation process below is the only process needed here;
                # DeepSeek capability validation remains handled separately.
                # 执行主命令 - 附着父控制台
                print("🔍 [winexe] 开始执行（预期在当前终端显示实时日志）...")
                process = subprocess.Popen(
                    cmd,
                    shell=False,
                    cwd=exe_dir,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )

                stderr_lines = []
                if process.stderr:
                    for line in process.stderr:
                        stderr_lines.append(line)
                        sys.stderr.write(line)
                        sys.stderr.flush()
                    process.stderr.close()

                return_code = process.wait()
                if return_code != 0:
                    stderr_text = ''.join(stderr_lines)
                    value_error = self._extract_value_error(stderr_text)
                    if value_error:
                        raise ValueError(value_error)
                    print(f"❌ pdf2zh.exe 执行失败，退出码: {return_code}")
                    print("   操作失败，请查看详细日志。")
                    raise RuntimeError(f"pdf2zh.exe 执行失败，退出码: {return_code}")

            else:
                # 回退模式：静默模式（旧行为）
                print("🔇 [winexe] mode=silent")
                r = subprocess.run(
                    cmd,
                    shell=False,
                    cwd=exe_dir,
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8"
                )
                if r.returncode != 0:
                    value_error = self._extract_value_error(r.stderr or '')
                    if value_error:
                        raise ValueError(value_error)
                    raise RuntimeError(f"pdf2zh.exe 退出码 {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
        elif args.enable_venv:
            # 使用 execute_with_progress 替代原来的 execute_in_env
            # 实时解析子进程输出中的进度信息并更新 task_manager
            execute_with_progress(cmd, task_id, args, self.env_manager)
        else:
            execute_with_progress(cmd, task_id, args, None)
        existing = [p for p in output_path if os.path.exists(p)]

        for f in existing:
            size = os.path.getsize(f)
            print(f"🐲 pdf2zh_next 翻译成功, 生成文件: {f}, 大小为: {size/1024.0/1024.0:.2f} MB")

        if not existing:
            raise RuntimeError("操作失败，请查看详细日志。")

        return existing

    def run(self, host, port, debug=False):
        print(f"🌐 Server将启动在: http://{host}:{port}")
        print(f"📊 翻译进度监控页面: http://localhost:{port}/")
        print(f"💡 健康检查端点: http://localhost:{port}/health")
        self.app.run(
            host=host,
            port=port,
            debug=debug,
            threaded=True,
            request_handler=_QuietAccessHandler,
        )

def prepare_path():
    os.makedirs(output_folder, exist_ok=True)
    # Never overwrite an existing user config with the .example template.
    # Migration only adds missing defaults; user/custom values always win.
    prepare_config_files(config_path)

# ================================================================================
# ######################### 主程序入口 ############################
# ================================================================================

def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', '1', 'y'):
        return True
    elif v.lower() in ('no', 'false', 'f', '0', 'n'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str, default='127.0.0.1', help='Server bind host; use 0.0.0.0 only when remote access is intentionally required')
    parser.add_argument('--port', type=int, default=PORT, help='Port to run the server on')

    parser.add_argument('--enable_venv', type=str2bool, default=enable_venv, help='脚本自动开启虚拟环境')
    parser.add_argument('--env_tool', choices=['auto', 'uv', 'conda'], default=default_env_tool, help='环境管理工具；auto 会沿用已有 uv/conda，新环境优先 uv')
    parser.add_argument('--check_update', type=str2bool, default=True, help='启动时检查更新')
    parser.add_argument('--update_source', type=str, default='gitee', help='优先更新源 gitee 或 github；失败时自动尝试另一个源')
    parser.add_argument('--debug', type=str2bool, default=False, help='Enable debug mode')
    parser.add_argument('--enable_winexe', type=str2bool, default=False, help='使用pdf2zh_next Windows可执行文件运行脚本, 仅限Windows系统')
    parser.add_argument('--enable_mirror', type=str2bool, default=True, help='启用下载镜像加速, 仅限中国大陆用户')
    parser.add_argument('--mirror_source', type=str, default='https://mirrors.ustc.edu.cn/pypi/simple', help='自定义您的PyPI镜像源, 仅限中国大陆用户')
    parser.add_argument('--winexe_path', type=str, default='./pdf2zh-v2.6.3-BabelDOC-v0.5.7-win64/pdf2zh/pdf2zh.exe', help='Windows可执行文件的路径')
    parser.add_argument('--winexe_attach_console', type=str2bool, default=True, help='Winexe模式是否尝试附着父控制台显示实时日志 (默认True)')
    parser.add_argument('--skip_install', type=str2bool, default=False, help='跳过虚拟环境中的安装')
    args = parser.parse_args()
    # 2. 打印提示信息
    print("\n===== 💡提示💡 =====")
    print("如果您遇到问题......")
    print("1️⃣ 请阅读本项目的【github主页】, 这里有最准确的信息")
    print("    · 🤖 github: https://github.com/guaguastandup/zotero-pdf2zh")
    print("    · 🤖 如果国内无法访问github, 请移步: gitee: https://gitee.com/guaguastandup/zotero-pdf2zh\n")

    print("2️⃣ 加入zotero-pdf2zh插件QQ群: 请在 GitHub / Gitee 仓库主页查看最新群号和入群口令")
    print("    · 【提问前】您需要先确保已经阅读过本项目主页的教程以及常见问题汇总")
    print("    · 【提问时】您必须将本终端输出的所有信息复制到txt文件中, 并截图您的zotero插件设置, 一并发送到群里, 否则您将不会得到回复, 感谢配合!\n")

    print("\n==== 🌍翻译期间请勿关闭此窗口🌍 =====\n")

    # 3. 打印启动参数
    print("🚀 启动参数:", args, "\n")
    print("🏠 当前版本: ", __version__)
    print("🏠 当前路径: ", root_path, "\n")

    # 4. 环境检查（端口、目录权限、Python版本、虚拟环境）
    print("🔍 开始环境检查...")
    all_checks_passed = True

    # 4.1 端口检查
    print("\n--- 网络端口检查 ---")
    port = args.port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        print(f"🔍 检查端口 {port} 是否被占用...")
        if s.connect_ex(('localhost', port)) == 0:
            print(f"❌ 端口 {port} 已被占用！")
            print("\n💡 解决方案:")
            print("   1. 选择其他端口启动: python server.py --port XXXX")
            print("   2. 或在Zotero插件设置中修改Server IP端口号")
            print(f"   3. 或停止占用端口 {port} 的其他程序")
            all_checks_passed = False
        else:
            print(f"✅ 端口 {port} 可用")

    # 4.2 目录权限检查
    print("\n--- 目录权限检查 ---")
    required_dirs = [
        ('translated', '翻译输出目录'),
        ('config', '配置文件目录')
    ]

    for dir_name, description in required_dirs:
        dir_path = os.path.join(root_path, dir_name)
        if not os.path.exists(dir_path):
            print(f"⚠️  {description} ({dir_name}) 不存在，尝试创建...")
            try:
                os.makedirs(dir_path, exist_ok=True)
                print(f"✅ {description} 创建成功: {dir_path}")
            except Exception as e:
                print(f"❌ 无法创建 {description}: {e}")
                print(f"\n💡 解决方案:")
                print(f"   1. 手动创建 {dir_name} 文件夹")
                print(f"   2. 检查当前用户是否有创建目录的权限")
                print(f"   3. 尝试以管理员身份运行（Windows: 右键'以管理员身份运行'）")
                all_checks_passed = False
        else:
            # 检查写入权限
            if not os.access(dir_path, os.W_OK):
                print(f"❌ {description} ({dir_name}) 没有写入权限！")
                print(f"\n💡 解决方案:")
                print(f"   1. 检查 {dir_name} 文件夹的权限设置")
                print(f"   2. 在Windows中: 右键文件夹 -> 属性 -> 安全 -> 编辑权限")
                print(f"   3. 在Linux/Mac中: chmod 755 {dir_path}")
                all_checks_passed = False
            else:
                print(f"✅ {description} ({dir_name}) 权限正常")

    # 4.3 Python版本检查
    print("\n--- Python环境检查 ---")
    print(f"🐍 Python版本: {sys.version}")
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 8):
        print(f"❌ Python版本过低！需要 Python 3.8 或更高版本")
        print(f"💡 解决方案:")
        print(f"   1. 安装 Python 3.8 或更高版本")
        print(f"   2. 从 python.org 下载最新版 Python")
        all_checks_passed = False
    else:
        print(f"✅ Python版本符合要求")

    # 4.4 虚拟环境检查
    if args.enable_venv:
        print("\n--- 虚拟环境检查 ---")
        print(f"🔧 环境管理模式: {args.env_tool}")
        if args.env_tool == 'auto':
            print("💡 auto: 优先沿用已有 uv/conda；没有已有环境时优先创建 uv。")

        found = []
        for engine_name in (pdf2zh, pdf2zh_next):
            existing = find_existing_environment(engine_name, args.env_tool)
            if existing:
                tool, env_dir, python_path = existing
                found.append(engine_name)
                print(f"✅ {engine_name}: {tool} -> {env_dir}")
                try:
                    versions = read_versions(python_path, engine_name)
                    printed = format_versions(versions)
                    if printed:
                        print(f"   📦 {printed}")
                    if engine_name == pdf2zh_next and not pdf2zh_next_meets_minimum(
                        versions.get("pdf2zh-next")
                    ):
                        print(
                            "   ⚠️ pdf2zh_next 低于 2.9.0，DeepSeek V4 思考控制可能不可用。"
                        )
                except Exception as exc:
                    print(f"   ⚠️ 无法读取包版本: {exc}")
            else:
                print(f"ℹ️ {engine_name}: 暂无托管环境，首次使用时将自动创建。")
        if not found:
            print("💡 尚未创建翻译环境；Server 本身可以先正常启动。")

    # 检查总结
    print("\n" + "="*60)
    if all_checks_passed:
        print("✅ 所有检查通过！Server准备启动...")
    else:
        print("❌ 部分检查未通过，可能影响Server正常运行")
        print("\n⚠️  您可以选择:")
        print("   1. 根据上述提示修复问题后重新启动")
        print("   2. 忽略警告继续运行（可能遇到错误）")

        try:
            user_input = input("\n是否继续启动？(y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # [自研补丁 2026-09-03] 无控制台(start_all 隐藏窗口/后台)启动时
            # input() 抛 EOFError 会直接 traceback 中断启动; 服务本应自行运行,
            # 非交互场景默认继续。
            user_input = 'y'
            print("\n（未检测到交互输入，默认继续启动）")
        if user_input != 'y':
            print("👋 已取消启动，请修复问题后重试")
            sys.exit(0)

    print("="*60 + "\n")
    print("💡 请保持此窗口开启，翻译期间请勿关闭\n")

    # [自研补丁 2026-09-03] 通知拉取与更新检查移到 daemon 线程:
    # GitHub 被墙环境下同步网络检查最坏阻塞 1~2 分钟, 每次启动都卡住;
    # 后台检查不阻塞 Flask 启动, 失败只在线程内打印。发现新版本时仅提示
    # (后台线程不与 Flask 启动竞争 stdin/文件), 更新方式: 重启后按提示操作
    # 或运行 python manage_packages.py / update_packages.py。
    def _startup_remote_checks():
        try:
            fetch_and_show_notices(__version__, args.update_source)
        except Exception as exc:
            print(f"📢 项目通知检查失败，已跳过: {exc}")
        if args.check_update:
            try:
                update_info = check_for_updates(__version__, args.update_source)
                if update_info:
                    local_v, remote_v = update_info
                    print(f"🎉 发现 Server 新版本！当前版本: {local_v}, 最新版本: {remote_v}")
                    print("   请运行 python manage_packages.py 完成更新后重启服务。")
            except Exception as exc:
                print(f"⚠️ [自动更新] 后台检查失败: {exc}")

    threading.Thread(target=_startup_remote_checks, daemon=True,
                     name='startup-remote-checks').start()

    # 7. 配置迁移 + 正常启动。VirtualEnvManager 会在这里对已有用户
    #    每个 Server 版本最多询问一次是否安全更新翻译环境。
    prepare_path()
    translator = PDFTranslator(args)
    translator.run(args.host, args.port, debug=args.debug)
