# -*- coding: utf-8 -*-
"""剪贴板中继监听器  v1

管线: mk_job.py(装配) -> [豆包网页版/免费普通对话框] -> 本脚本 -> check_job.py -> mk_appendix.py

为什么做这个: 免费版豆包只有普通对话框, 没有连接器/工作任务, 不能读写本地文件。
用户在网页里翻完, 复制回复, 卡在"存成文件、跑脚本"这两步。本脚本把这两步合并成
"只按一次 Ctrl+C"。

设计原则(与 relay_spec.md 一致):
  只自动化本地侧 —— AI 侧保持手工(一次粘贴 + 一次复制)。浏览器自动化越深越脆弱,
  豆包改一次 DOM 插件就废; 而"手工复制"这条路换任何 AI、任何版本都不会坏。
  校验权不给 AI: 对账永远在本机跑, 被校验方不参与判定。

识别(三重防误触发, 全部实测过):
  1. 单元编号齐全(表格 115 个 k 编号 / 表注 3 个 n 编号) —— 日常复制凑不齐这么多连续编号
  2. 不含装配前缀【待译】/【任务】—— 防止把"发出去的 job 文本"当成回包(它编号也齐全)
  3. 过半单元含中日韩字符 —— 防止把英文原文当成译文

零依赖: 剪贴板走 ctypes 调 Win32 API, 不需要 pyperclip/win32clipboard。
用法:
  python watch_clip.py          常驻监听(默认), 命中后自动对账+出附录, Ctrl+C 停止
  python watch_clip.py --once   只处理当前剪贴板一次就退出(自检用)
  python watch_clip.py --no-open  出稿后不自动打开 DOCX
"""
import ctypes
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

try:
    # line_buffering: 常驻模式下 stdout 不是 tty, 不设这个则反馈会一直卡在缓冲区里,
    # 用户看不到任何输出(实测踩过)。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

SD = os.path.dirname(os.path.abspath(__file__))          # 脚本目录(本件所在)
D = os.environ.get("P2Z_TABLE_DIR") or SD                # 工作目录(数据所在), 缺省=脚本目录
PY = sys.executable
DOCX = os.path.join(D, "appendix_tables_zh.docx")

# 两类任务: 装配器 -> 回包文件 -> 对账器
JOBS = [
    dict(label="表格正文", pre="k", manifest="job_manifest.json",
         resp="job_response.tsv",
         cmd=lambda: [PY, os.path.join(SD, "check_job.py"), "job_response.tsv"]),
    dict(label="表注", pre="n", manifest="notes_manifest.json",
         resp="job_notes_response.tsv",
         cmd=lambda: [PY, os.path.join(SD, "mk_notes_job.py"), "check",
                      "job_notes_response.tsv"]),
]

CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
PREAMBLE_MARK = ("【待译】", "【任务】")

# ---------------------------------------------------------------- 剪贴板(Win32)

CF_UNICODETEXT = 13
u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
u32.OpenClipboard.argtypes = [wintypes.HWND]
u32.OpenClipboard.restype = wintypes.BOOL
u32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
u32.IsClipboardFormatAvailable.restype = wintypes.BOOL
u32.GetClipboardData.argtypes = [wintypes.UINT]
u32.GetClipboardData.restype = wintypes.HANDLE
u32.CloseClipboard.restype = wintypes.BOOL
k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
k32.GlobalUnlock.restype = wintypes.BOOL


def read_clip():
    """读剪贴板纯文本; 剪贴板被别的进程占住时返回 None(下次轮询再试, 不当错误)"""
    if not u32.OpenClipboard(None):
        return None
    try:
        if not u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.c_wchar_p(p).value
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


# ---------------------------------------------------------------- 识别与对账

def manifest_ids(path):
    """按 manifest 里的顺序返回编号列表 —— 回包按此顺序落盘, 保证文件确定性"""
    with io.open(os.path.join(D, path), encoding="utf-8") as f:
        return [u["id"] for u in json.load(f)["units"]]


def parse_units(text, ids, pre):
    """从剪贴板文本里抽 编号<分隔>译文。网页渲染会把 TAB 转成空格, 两种都认。"""
    got = {}
    rx = re.compile(r"^(%s\d{3})[\t ](.*)$" % pre)
    for ln in text.splitlines():
        m = rx.match(ln.rstrip("\r"))
        if m:
            got[m.group(1)] = m.group(2).strip()
    return got if all(i in got for i in ids) else None


def match_job(text):
    """判断这段剪贴板是哪一个任务的回包。返回 (job, got) 或 None。"""
    if any(m in text for m in PREAMBLE_MARK):
        return None                       # 是发出去的 job 文本, 不是回包
    for job in JOBS:
        ids = manifest_ids(job["manifest"])
        got = parse_units(text, ids, job["pre"])
        if not got:
            continue
        n_cjk = sum(1 for v in got.values() if CJK.search(v))
        if n_cjk * 2 < len(got):
            print("  疑似非译文(含中日韩字符的单元 %d/%d), 已跳过" % (n_cjk, len(got)))
            return None
        return job, ids, got
    return None


def run_job(job, ids, got):
    """落盘 -> 对账 -> 通过则出附录。返回 True 表示全链通过。"""
    resp = os.path.join(D, job["resp"])
    with io.open(resp, "w", encoding="utf-8") as f:
        for uid in ids:
            f.write("%s\t%s\n" % (uid, got[uid]))
    print("  回包已落盘 -> %s (%d 单元)" % (job["resp"], len(got)))

    p = subprocess.run(job["cmd"](), cwd=D, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print("\n".join("  " + ln for ln in (p.stdout or "").splitlines()))
    if p.returncode != 0:
        print("  ✗ 门禁未过, 未出稿。修正后重新复制回复即可(本脚本会再抓一次)。")
        return False

    a = subprocess.run([PY, os.path.join(SD, "mk_appendix.py")], cwd=D,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("\n".join("  " + ln for ln in (a.stdout or "").splitlines()))
    if a.returncode != 0:
        print("  ✗ 排版失败:\n" + (a.stderr or "")[-800:])
        return False
    print("  ✓ 附录已出 -> %s" % DOCX)
    return True


def beep(ok=True):
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK if ok else winsound.MB_ICONHAND)
    except Exception:
        pass


# ---------------------------------------------------------------- 主循环

def handle(text, open_docx=True):
    hit = match_job(text)
    if not hit:
        return False
    job, ids, got = hit
    print("\n[%s] 识别到「%s」回包: %d 单元" % (time.strftime("%H:%M:%S"), job["label"], len(got)))
    ok = run_job(job, ids, got)
    beep(ok)
    if ok and open_docx:
        try:
            os.startfile(DOCX)
        except Exception as e:
            print("  (自动打开失败: %s)" % e)
    return True


def check_workdir():
    """工作目录放的是数据(manifest/回包), 脚本可以放在别处。缺数据时给可执行的提示,
    而不是让用户撞一个 FileNotFoundError。"""
    if os.path.exists(os.path.join(D, "job_manifest.json")):
        return True
    print("✗ 工作目录里没有 job_manifest.json:")
    print("    工作目录 = %s" % D)
    print("    脚本目录 = %s" % SD)
    print("  数据在别处时, 用环境变量指过去:")
    print("    set P2Z_TABLE_DIR=<数据目录>     (PowerShell: $env:P2Z_TABLE_DIR=\"...\")")
    return False


def main():
    once = "--once" in sys.argv
    open_docx = "--no-open" not in sys.argv
    if not check_workdir():
        sys.exit(2)
    if not once:
        print(__doc__.split("用法:")[1].strip().splitlines()[0] if "用法:" in __doc__ else "")
        print("=" * 74)
        print("剪贴板中继已启动。步骤:")
        print("  1. 把 job_doubao.txt 整段贴进豆包(普通对话框即可, 免费版够用)")
        print("  2. 等它译完, 在回复上点「复制」")
        print("  3. 不用做别的 —— 本脚本会自动对账、出附录并打开")
        print("  停止: Ctrl+C")
        print("=" * 74)

    last = None
    while True:
        txt = read_clip()
        if txt:
            h = hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()
            if h != last:
                last = h                      # 先记账, 避免同一段反复处理
                try:
                    handle(txt, open_docx)
                except Exception as e:
                    print("  处理异常: %r" % e)
        if once:
            return
        time.sleep(0.7)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止。")
