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

**本脚本不设确认环节, 这是它的取舍**: 复制即执行, 零交互。
代价是"复制不全"里有一类拦不住 —— 实测(2026-09-21): 把真实回包的**末行截断 3 字符**,
`k115\t两种传粉者` 变成 `k115\t两种`, 编号齐全 / 占位符未丢 / 非空 -> **G1-G5 全过**,
直接跑完出稿。门禁的锚点是编号与占位符, "末行被吃掉一截但还剩内容"两者都不触发,
**判据够不到**。要拦住它只能把人放回回路里 —— 见 panel.py(检查/确认两段, 带核对表)。

零依赖: 剪贴板走 ctypes 调 Win32 API, 不需要 pyperclip/win32clipboard。
用法:
  python watch_clip.py          常驻监听(默认), 命中后自动对账+出附录, Ctrl+C 停止
  python watch_clip.py --once   只处理当前剪贴板一次就退出(自检用)
  python watch_clip.py --no-open  出稿后不自动打开 DOCX
  python panel.py               有确认环节的面板(推荐入口)
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

SD = os.path.dirname(os.path.abspath(__file__))          # 脚本目录(本件所在, table_pipe)
TOOLS = os.path.dirname(SD)                              # 正文管线脚本所在(tools/ 根)
D = os.environ.get("P2Z_TABLE_DIR") or SD                # 工作目录(数据所在), 缺省=脚本目录
PY = sys.executable
DOCX = os.path.join(D, "appendix_tables_zh.docx")

# 正文任务的路径约定(与 tools/engine.py / adopt.py 同一套环境变量):
#   P2Z_PROJ      项目根(缺省 D:\zotero-pdf2zh)
#   P2Z_INBOX     载荷目录(缺省 <PROJ>\inbox)
#   P2Z_BODY_NAME 当前论文载荷名(缺省 payload_lee2026)
#   P2Z_BODY_PDF  当前论文**原文** PDF(缺省按 Lee 2026 已知路径; 换论文显式给)
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
INBOX = os.environ.get("P2Z_INBOX", os.path.join(PROJ, "inbox"))
BODY_NAME = os.environ.get("P2Z_BODY_NAME", "payload_lee2026")
BODY_PDF = os.environ.get("P2Z_BODY_PDF") or os.path.join(
    PROJ, "server", "translated",
    "Lee 等 - 2026 - Architecture Carbon Tool v3 Enabling Sustainability-aware "
    "Silicon System Design Exploration.pdf")


def body_manifest():
    return os.path.join(INBOX, BODY_NAME + ".manifest.json")


def body_payload():
    return os.path.join(INBOX, BODY_NAME + ".txt")


def _payload_samples(txt_path, n=8):
    """从载荷 txt 采几行较长的段落原文(跳过 #S 编号行/[]【】规则行),
    用于把载荷**按内容**锚定到侧车 —— 页码覆盖度会打平(2026-09-21 实测:
    Lee 侧车 11 页对 Li manifest 1-7 页覆盖=7, 与 Li 自己的侧车 7=7 平局,
    max 拿错文档, seg_import 回锚全错), 只有内容身份分得出。"""
    samples = []
    try:
        with io.open(txt_path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if len(ln) > 25 and not ln.startswith(("#", "[", "【")):
                    samples.append(ln[:120])
                    if len(samples) >= n:
                        break
    except Exception:
        pass
    return samples


def _sidecar_identity(path, samples):
    """载荷采样行在侧车里的命中数(0=肯定不是这篇)。"""
    if not samples or not path or not os.path.exists(path):
        return 0
    try:
        with io.open(path, encoding="utf-8") as f:
            blob = f.read()
    except Exception:
        return 0
    return sum(1 for s in samples if s in blob)


def _pdf_for_sidecar(sidecar_path):
    """由归档侧车(pdf-<md5[:16]>.jsonl)反查原文 PDF: 扫 server/translated 下的
    PDF 算内容散列对文件名。对不上返回 None(调用方自行回落)。"""
    base = os.path.basename(sidecar_path or "")
    m = re.match(r"^pdf-([0-9a-f]{16})\.jsonl$", base)
    if not m:
        return None
    want = m.group(1)
    tdir = os.path.join(PROJ, "server", "translated")
    try:
        names = [f for f in os.listdir(tdir) if f.lower().endswith(".pdf")
                 and not f.endswith(("-mono.pdf", "-dual.pdf", "-cut.pdf", "-compare.pdf"))]
    except OSError:
        return None
    for f in names:
        p = os.path.join(tdir, f)
        try:
            h = hashlib.md5()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest()[:16] == want:
                return p
        except OSError:
            continue
    return None


def body_sidecar():
    """正文任务的侧车: 在**全部**候选(归档件们 + latest.jsonl)里按
    (内容身份, 页码覆盖度) 选 —— 身份优先: 侧车必须真的含有本载荷的段落原文,
    否则 Lee 侧车会在覆盖度平局下冒充 Li(2026-09-21 实测, 门禁回锚全错)。
    latest.jsonl 会被下一篇解析覆盖, 认错篇 = 回锚错位, 故它只是兜底候选。
    """
    segflow = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "segflow")
    want = set()
    mp = body_manifest()
    if os.path.exists(mp):
        try:
            with io.open(mp, encoding="utf-8") as f:
                for it in json.load(f).get("items") or []:
                    for p in it.get("parts") or []:
                        want.add(int(p.get("page", 0)))
        except Exception:
            want = set()

    def pages_of(path):
        if not path or not os.path.exists(path):
            return set()
        out = set()
        try:
            with io.open(path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        out.add(json.loads(ln)["page"])
        except Exception:
            pass
        return out

    cands = []
    try:
        for f in os.listdir(segflow):
            if f.startswith("pdf-") and f.endswith(".jsonl"):
                cands.append(os.path.join(segflow, f))
    except OSError:
        pass
    cands.append(os.path.join(segflow, "latest.jsonl"))

    samples = _payload_samples(body_payload())

    def score(p):
        return (_sidecar_identity(p, samples), len(pages_of(p) & want))

    return max(cands, key=score)


def body_imported():
    return os.path.join(os.environ.get("P2Z_PROJ", PROJ), "out",
                        BODY_NAME + ".imported.json")


def body_out():
    """重渲染产物(1.x 服务端命名 <stem>-mono.pdf, 落 server/translated)。"""
    return os.path.splitext(BODY_PDF)[0] + "-mono.pdf"


# 任务注册表: 装配器/待译文本/回包文件 -> 门禁 -> 出稿工序。panel.py 复用本表, 不要另起一份。
# 每条任务的 cmd 是**门禁**(检查阶段在临时沙箱里跑, 落盘只发生在确认);
# after 是确认后的出稿工序(表格=排附录; 正文=注入缓存+重渲染);
# out 是最终产物路径(出稿后自动打开)。
JOBS = [
    dict(label="表格正文", pre="k", manifest="job_manifest.json",
         job="job_doubao.txt", resp="job_response.tsv",
         cmd=lambda: [PY, os.path.join(SD, "check_job.py"), "job_response.tsv"],
         after=lambda: [[PY, os.path.join(SD, "mk_appendix.py")]],
         out=lambda: DOCX),
    dict(label="表注", pre="n", manifest="notes_manifest.json",
         job="job_notes_doubao.txt", resp="job_notes_response.tsv",
         cmd=lambda: [PY, os.path.join(SD, "mk_notes_job.py"), "check",
                      "job_notes_response.tsv"],
         after=lambda: [[PY, os.path.join(SD, "mk_appendix.py")]],
         out=lambda: DOCX),
    dict(label="正文 " + BODY_NAME, pre="S", blocks=True,
         manifest=body_manifest(), payload=body_payload(),
         sidecar=body_sidecar(), pdf=BODY_PDF,
         job=body_payload(), resp=BODY_NAME + "_response.tsv",
         cmd=lambda: [PY, os.path.join(TOOLS, "seg_import.py"),
                      "--manifest", body_manifest(),
                      "--text", BODY_NAME + "_response.tsv",
                      "--sidecar", body_sidecar()],
         after=lambda: [
             [PY, os.path.join(TOOLS, "seg_inject.py"),
              "--imported", body_imported(),
              "--manifest", body_manifest(),
              "--sidecar", body_sidecar()],
             [PY, os.path.join(TOOLS, "force_rerender.py"),
              "--pdf", BODY_PDF, "--timeout", "1800"],
         ],
         out=lambda: body_out()),
]

# ---------------------------------------------------------- 正文任务自动切换
# 换论文时不用改 env: 最新 payload(inbox 里 mtime 最新) + 服务器最近处理的原文 PDF
# (history 最新任务的 fileName) 自动接管 BODY_NAME/BODY_PDF。显式设了 env 则不覆盖。

def _latest_payload():
    """inbox 里最新的载荷 txt(有配套 manifest 才算) -> (name, txt, manifest, pdf|None)。
    两种名字都认: 手动 seg_export 的 payload_<名>, 服务器两趟回路自动导出的 <原文stem>。"""
    best = None
    if os.path.isdir(INBOX):
        for f in os.listdir(INBOX):
            if not f.endswith(".txt"):
                continue
            man = os.path.join(INBOX, f[:-4] + ".manifest.json")
            if not os.path.exists(man):
                continue
            p = os.path.join(INBOX, f)
            try:
                t = os.path.getmtime(p)
            except OSError:
                continue
            if best is None or t > best[0]:
                best = (t, f[:-4], p, man)
    if not best:
        return None
    _, name, txt, man = best
    pdf = None
    try:
        with io.open(man, encoding="utf-8") as fh:
            pdf = json.load(fh).get("pdf") or None
    except Exception:
        pdf = None
    return name, txt, man, pdf


def _latest_pdf():
    """服务器 /api/history 最近任务的原文 PDF; 读不到返回 None。"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8890/api/history", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        hs = data.get("history") or []
        if not hs:
            return None
        latest = max(hs, key=lambda h: h.get("startTime", ""))
        fn = latest.get("fileName") or ""
        if fn.lower().endswith(".pdf"):
            p = os.path.join(PROJ, "server", "translated", fn)
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return None


def refresh_body():
    """重探最新论文并重建 JOBS 第三条; 无变化时零开销(面板 state 轮询频繁)。

    顺序讲究: 必须先更新 BODY_NAME 再调 body_sidecar()/body_manifest() —— 它们
    都读全局 BODY_NAME; 且侧车反查 PDF(md5 扫描)只在换篇时做一次, 否则每 5s
    轮询会反复散列 server/translated 下所有 PDF。"""
    global BODY_NAME, BODY_PDF
    lp = None
    if not os.environ.get("P2Z_BODY_NAME"):
        try:
            lp = _latest_payload()
        except Exception:
            lp = None
    changed = bool(lp) and lp[0] != BODY_NAME
    if changed:
        BODY_NAME = lp[0]
    if not os.environ.get("P2Z_BODY_PDF") and lp and changed:
        if lp[3]:
            new_pdf = lp[3]                 # manifest 自带 pdf(v28.53): 同篇强一致
        else:
            # manifest 没带 pdf: 按载荷内容锚定侧车, 再由侧车反查原文 PDF ——
            # history 推断在服务器重启/有待译任务时会指错篇(2026-09-21 实测指到 Lee)。
            try:
                new_pdf = _pdf_for_sidecar(body_sidecar()) or _latest_pdf()
            except Exception:
                new_pdf = None
        if new_pdf:
            BODY_PDF = new_pdf
    if not changed:
        return
    JOBS[2] = dict(label="正文 " + BODY_NAME, pre="S", blocks=True,
                   manifest=body_manifest(), payload=body_payload(),
                   sidecar=body_sidecar(), pdf=BODY_PDF,
                   job=body_payload(), resp=BODY_NAME + "_response.tsv",
                   cmd=lambda: [PY, os.path.join(TOOLS, "seg_import.py"),
                                "--manifest", body_manifest(),
                                "--text", BODY_NAME + "_response.tsv",
                                "--sidecar", body_sidecar()],
                   after=lambda: [
                       [PY, os.path.join(TOOLS, "seg_inject.py"),
                        "--imported", body_imported(),
                        "--manifest", body_manifest(),
                        "--sidecar", body_sidecar()],
                       [PY, os.path.join(TOOLS, "force_rerender.py"),
                        "--pdf", BODY_PDF, "--timeout", "1800"],
                   ],
                   out=lambda: body_out())

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
k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
k32.GlobalAlloc.restype = wintypes.HGLOBAL
k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
k32.GlobalFree.restype = wintypes.HGLOBAL
u32.EmptyClipboard.argtypes = []
u32.EmptyClipboard.restype = wintypes.BOOL
u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
u32.SetClipboardData.restype = wintypes.HANDLE


LAST_ERR = None      # read_clip 最近一次失败的原因; 面板如实转述给用户, 不吞真因


def read_clip():
    """读剪贴板纯文本; 失败返回 None, 原因写进 LAST_ERR(别让调用方只能猜)。

    占住多为瞬时(浏览器/剪贴板管理器会短时持锁), 故 OpenClipboard 短重试 5×120ms。
    但"读不到"有五种成因, 必须分开说 —— 混成一句"没有文本或被占住"会把
    "服务器其实已经死了"这类真因盖掉(2026-09-21 实测踩过)。"""
    global LAST_ERR
    LAST_ERR = None
    for _ in range(5):
        if u32.OpenClipboard(None):
            break
        time.sleep(0.12)
    else:
        LAST_ERR = "剪贴板被别的程序占住(OpenClipboard 重试 5 次均失败)"
        return None
    try:
        if not u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            LAST_ERR = "剪贴板里没有纯文本格式(可能是图片或文件)"
            return None
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            LAST_ERR = "GetClipboardData 返回空句柄"
            return None
        p = k32.GlobalLock(h)
        if not p:
            LAST_ERR = "GlobalLock 失败"
            return None
        try:
            v = ctypes.c_wchar_p(p).value
            if not v:
                LAST_ERR = "剪贴板里是空文本"
            return v
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def write_clip(text):
    """写剪贴板纯文本。panel.py(网页版)的「① 复制待译文本」走这里 —— 服务器进程
    没有 tk 主循环, 直接调 Win32。占住/失败返回 False, 不抛。"""
    data = text.encode("utf-16-le") + b"\x00\x00"
    h = k32.GlobalAlloc(0x0042, len(data))          # GMEM_MOVEABLE | GMEM_ZEROINIT
    if not h:
        return False
    p = k32.GlobalLock(h)
    if not p:
        k32.GlobalFree(h)
        return False
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    for _ in range(5):                          # 占住多为瞬时, 短重试同 read_clip
        if u32.OpenClipboard(None):
            break
        time.sleep(0.12)
    else:
        k32.GlobalFree(h)
        return False
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(CF_UNICODETEXT, h):
            k32.GlobalFree(h)
            return False
    finally:
        u32.CloseClipboard()
    return True


# ---------------------------------------------------------------- 识别与对账

def manifest_path(job):
    """manifest 的绝对路径: 表格任务在工作目录(相对), 正文任务给的是 inbox 绝对路径。"""
    p = job.get("manifest", "")
    return p if os.path.isabs(p) else os.path.join(D, p)


def _body_blocks(path):
    """载荷 txt 的 #S 块 -> {int_key: 原文}。只挑 #S 开头的行, 忽略任务头/规则段
    (与 seg_import.KEY_LINE / parse_blocks 同一口径, 别另起一套解析)。"""
    blocks, cur, buf = {}, None, []
    with io.open(path, encoding="utf-8") as f:
        for ln in f.read().splitlines():
            m = re.match(r"^\s*#?\**\s*S(\d+)\s*\**\s*$", ln)
            if m:
                if cur is not None:
                    blocks[cur] = "\n".join(buf).strip()
                cur, buf = int(m.group(1)), []
            elif cur is not None:
                buf.append(ln)
    if cur is not None:
        blocks[cur] = "\n".join(buf).strip()
    return blocks


def manifest_units(job):
    """-> [{id, orig, ...}]。表格 manifest 的 units 自带 orig; 正文 manifest 的 items
    只有坐标, 原文从载荷 txt 的 #S 块补。manifest 缺失返回 [] —— 调用方据此跳过
    该任务而不是崩(面板/监听器都可只挂表格数据)。
    """
    p = manifest_path(job)
    if not os.path.exists(p):
        return []
    with io.open(p, encoding="utf-8") as f:
        man = json.load(f)
    if "units" in man:
        return man["units"]
    blocks = {}
    src = job.get("payload") or (os.path.splitext(p)[0] + ".txt")
    if os.path.exists(src):
        blocks = _body_blocks(src)
    out = []
    for it in man.get("items") or []:
        k = it.get("key") or it.get("id")
        if k is None:
            continue
        n = int(str(k).lstrip("S"))
        out.append({"id": str(k), "orig": blocks.get(n, "")})
    return out


def manifest_ids(job):
    """按 manifest 里的顺序返回编号列表 —— 回包按此顺序落盘, 保证文件确定性"""
    return [u["id"] for u in manifest_units(job)]


def available_jobs():
    """manifest 在位的任务 —— 面板的任务下拉/监听器的识别只在这些任务里找。"""
    refresh_body()
    return [j for j in JOBS if os.path.exists(manifest_path(j))]


def parse_units(text, ids, pre):
    """从剪贴板文本里抽 编号<分隔>译文。网页渲染会把 TAB 转成空格, 两种都认。
    两种形态都支持(与各任务的门禁契约一致):
      TSV 形态:  k001\t译文 (表格回包)
      块形态:    #S1\n译文… (正文回包, seg_import 的 KEY_LINE 契约)
    返回**读到多少算多少** —— 齐不齐由调用方判。panel.py 要拿"缺哪几条"的明细
    给用户看, 所以不在这里截断。重复编号取首条(与 check_job.py 同口径)。
    """
    got = {}
    rx = re.compile(r"^(%s\d+)[\t ](.*)$" % pre)
    key = re.compile(r"^\s*#?\**\s*(%s\d+)\s*\**\s*$" % pre)
    cur, buf = None, []

    def flush():
        if cur is not None and cur not in got:
            got[cur] = "\n".join(buf).strip()

    for ln in text.splitlines():
        ln = ln.rstrip("\r")
        m = rx.match(ln)
        if m:
            flush()
            cur, buf = m.group(1), []
            got.setdefault(cur, m.group(2).strip())
            continue
        m2 = key.match(ln)
        if m2:
            flush()
            cur, buf = m2.group(1), []
            continue
        if cur is not None:
            buf.append(ln)
    flush()
    return got


def match_job(text, log=print):
    """判断这段文本是哪一个任务的**完整**回包。返回 (job, ids, got) 或 None。"""
    if any(m in text for m in PREAMBLE_MARK):
        return None                       # 是发出去的 job 文本, 不是回包
    for job in available_jobs():
        ids = manifest_ids(job)
        got = parse_units(text, ids, job["pre"])
        if not got or not all(i in got for i in ids):
            continue
        n_cjk = sum(1 for v in got.values() if CJK.search(v))
        if n_cjk * 2 < len(got):
            log("  疑似非译文(含中日韩字符的单元 %d/%d), 已跳过" % (n_cjk, len(got)))
            return None
        return job, ids, got
    return None


def write_resp(path, job, ids, got):
    """回包落盘。表格任务写 TSV(k001\t译文); 正文任务写 #S 块 —— 与 seg_import 的
    KEY_LINE 契约一致, 收回来直接能当 --text 喂回去。"""
    with io.open(path, "w", encoding="utf-8") as f:
        if job.get("blocks"):
            for uid in ids:
                f.write("#%s\n%s\n" % (uid, got[uid]))
        else:
            for uid in ids:
                f.write("%s\t%s\n" % (uid, got[uid]))


def run_job(job, ids, got, log=print):
    """落盘 -> 门禁 -> 通过则跑出稿工序(表格=排附录; 正文=注入缓存+重渲染)。
    返回产物路径(出稿后由调用方打开); 未出稿返回 None。"""
    resp = os.path.join(D, job["resp"])
    write_resp(resp, job, ids, got)
    log("  回包已落盘 -> %s (%d 单元)" % (job["resp"], len(got)))

    p = subprocess.run(job["cmd"](), cwd=D, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for ln in (p.stdout or "").splitlines():
        log("  " + ln)
    if p.returncode != 0:
        log("  ✗ 门禁未过, 未出稿。修正后重新粘贴即可。")
        return None

    after = job.get("after")               # 工序表也是 lambda(惰性求路径), 先调再遍历
    after = after() if callable(after) else (after or [])
    for i, argv in enumerate(after):
        a = subprocess.run(argv, cwd=D, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        for ln in (a.stdout or "").splitlines():
            log("  " + ln)
        if a.returncode != 0:
            log("  ✗ 出稿工序 %d 失败:\n%s" % (i + 1, (a.stderr or "")[-800:]))
            return None
    out = job.get("out")
    out = out() if callable(out) else out
    log("  ✓ 已出稿 -> %s" % out)
    return out


def beep(ok=True):
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK if ok else winsound.MB_ICONHAND)
    except Exception:
        pass


# ---------------------------------------------------------------- 主循环

def handle(text, open_docx=True, log=print):
    hit = match_job(text, log=log)
    if not hit:
        return False
    job, ids, got = hit
    log("\n[%s] 识别到「%s」回包: %d 单元" % (time.strftime("%H:%M:%S"), job["label"], len(got)))
    out = run_job(job, ids, got, log=log)
    beep(bool(out))
    if out and open_docx:
        try:
            os.startfile(out)
        except Exception as e:
            log("  (自动打开失败: %s)" % e)
    return True


def check_workdir():
    """工作目录放的是数据(manifest/回包), 脚本可以放在别处。缺数据时给可执行的提示,
    而不是让用户撞一个 FileNotFoundError。"""
    if available_jobs():
        return True
    print("✗ 工作目录/环境变量里没有任何任务的 manifest:")
    print("    工作目录 = %s" % D)
    print("    脚本目录 = %s" % SD)
    print("  数据在别处时, 用环境变量指过去:")
    print("    set P2Z_TABLE_DIR=<数据目录>     (PowerShell: $env:P2Z_TABLE_DIR=\"...\")")
    print("    set P2Z_PROJ=<项目根>             (正文任务; 缺省 D:\\zotero-pdf2zh)")
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
