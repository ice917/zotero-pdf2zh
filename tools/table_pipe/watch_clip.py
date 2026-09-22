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
# out 是最终产物路径(出稿后自动打开);
# outs/ins 是"出过稿没有"的判据(产物 / 输入, 见 released); paper 是篇名(v28.71)。
JOBS = [
    dict(label="表格正文", pre="k", manifest="job_manifest.json",
         job="job_doubao.txt", resp="job_response.tsv",
         cmd=lambda: [PY, os.path.join(SD, "check_job.py"), "job_response.tsv"],
         after=lambda: [[PY, os.path.join(SD, "mk_appendix.py")]],
         out=lambda: DOCX,
         outs=lambda: _table_outs(),
         ins=lambda: [os.path.join(D, "job_manifest.json")]),
    dict(label="表注", pre="n", manifest="notes_manifest.json",
         job="job_notes_doubao.txt", resp="job_notes_response.tsv",
         cmd=lambda: [PY, os.path.join(SD, "mk_notes_job.py"), "check",
                      "job_notes_response.tsv"],
         after=lambda: [[PY, os.path.join(SD, "mk_appendix.py")]],
         out=lambda: DOCX,
         outs=lambda: [os.path.join(D, "notes_zh.json")],
         ins=lambda: [os.path.join(D, "notes_manifest.json")]),
    dict(label="正文 " + BODY_NAME, pre="S", blocks=True, render=True, paper=BODY_NAME,
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
         out=lambda: body_out(),
         outs=lambda: [body_out()],
         ins=lambda: [body_payload()]),
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
    JOBS[2] = dict(label="正文 " + BODY_NAME, pre="S", blocks=True, render=True,
                   paper=BODY_NAME,
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
                   out=lambda: body_out(),
                   outs=lambda: [body_out()],
                   ins=lambda: [body_payload()])

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
    """manifest 在位的任务 —— 面板的任务下拉/监听器的识别只在这些任务里找。
    表格/表注的显示名带上**篇名**(见 _stamp_paper), 所以下拉与台账都看得出这表是哪篇的。"""
    refresh_body()
    return [_stamp_paper(j) for j in JOBS if os.path.exists(manifest_path(j))]


# ------------------------------------------------- 篇名 & "出过稿没有"(v28.71)
# 面板要回答的问题从"你碰过的做完了吗"换成"**这篇**该做的都做了吗", 于是需要两样东西:
#   ① 篇名 —— 表格/表注的 manifest 里由装配器烙进去(mk_job.py --paper; 取不到就留空,
#      老工作目录照样能跑, 面板按"一个工作目录 = 一篇"兜底并标出来让人核);
#   ② "出过稿没有" —— 判据是**产物**而不是内存记录: 回填产物(table_*.zh.tsv /
#      notes_zh.json)只有 ④ 门禁全过之后才写(③ 跑在临时沙箱里, 不落盘), 所以"产物在"
#      就等于"这一刻确实出过稿"; 产物比它的**输入**(manifest/载荷)旧 = 装配过新一轮 ->
#      判"要重做"。这样重启面板、隔天回来都不失忆, 也不必再维护一份会过期的"本轮清单"。

_PAPER_CACHE = {}                    # manifest 路径 -> (mtime, 篇名); 面板 5s 轮询, 别每次解 JSON


def paper_of(job):
    """job 的篇名: 正文任务就是它自己那篇(建表时写入), 表格/表注读 manifest 里烙的篇名。
    读不到/没烙 -> "" (调用方按未标篇名处理, 不编造)。"""
    if job.get("paper"):
        return job["paper"]
    if job.get("render"):
        return ""
    p = manifest_path(job)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return ""
    hit = _PAPER_CACHE.get(p)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with io.open(p, encoding="utf-8") as f:
            paper = (json.load(f).get("paper") or "").strip()
    except Exception:
        paper = ""
    _PAPER_CACHE[p] = (mt, paper)
    return paper


def _stamp_paper(job):
    """把篇名缀到表格/表注的显示名后面 -> "表格正文 Cactaceae2009"。没烙篇名就原样返回。"""
    p = paper_of(job)
    if not p or job.get("render"):
        return job
    j = dict(job)
    j["label"] = job["label"] + " " + p
    j["paper"] = p
    return j


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def _table_outs():
    """表格正文的回填产物: manifest 里每张表一个 <表>.zh.tsv(check_job.py 只在门禁全过时写)。"""
    try:
        with io.open(manifest_path(JOBS[0]), encoding="utf-8") as f:
            return [os.path.join(D, t + ".zh.tsv") for t in (json.load(f).get("tables") or {})]
    except Exception:
        return []


def released(job):
    """这块**出过稿没有** -> (状态, 说明); 状态 in {"done", "stale", "todo"}。

    产物齐全且都不比输入旧 = done; 产物在但比输入旧 = stale(装配过新一轮, 得重做);
    产物缺 = todo。没有产物判据的任务(理论上不该有)一律算 todo —— 宁可多拦一次。
    """
    outs = list((job.get("outs") or (lambda: []))())
    ins = [t for t in (_mtime(p) for p in (job.get("ins") or (lambda: []))()) if t is not None]
    if not outs:
        return "todo", "没有产物判据"
    ot = [_mtime(p) for p in outs]
    if any(t is None for t in ot):
        return "todo", "还没出过稿"
    if ins and min(ot) < max(ins):
        return "stale", "产物比输入旧(装配过新一轮)"
    return "done", ""


# ---------------- 回包"包装"清洗(v28.62): 剪贴板这条路本来就不绑豆包 ----------------
# 契约只有两条(编号守恒 + 占位符原位), 任何网页 AI 都能接; 但**别家的包装不一样** ——
# 豆包网页版习惯裸文本, 而 ChatGPT/Claude/Kimi 这类爱套代码围栏、爱加首尾客套、爱把编号
# 列成 Markdown 清单。下面三条只动**包装**, 不碰译文一个字:
#   ① 代码围栏行(``` / ```markdown)整行跳过。整篇被围起来时内容是**围栏中间的行**,
#      所以只丢围栏本身 —— 否则 ``` 会当成译文混进末段(实测这类脏东西门禁抓不到)。
#   ② 译文**末尾**的客套行丢弃。判据刻意收紧两档: 必须(起首即客套词) **且**(不超
#      _SMALLTALK_MAX 字) **且**(与译文之间隔着空行) —— 宁可漏洗不可误吃: 末段真正文
#      被吃掉的损失远大于多留一句"希望对你有所帮助"。
#   ③ 编号行容忍行首包装(清单符 `- * •` / `1. 2) (3)` / 引用符 `>` / 粗体 `**`)。有的 AI
#      把编号回成清单; 不认就会**一条都读不到**(③ 只会说"编号不齐", 用户完全看不出
#      问题在"它给我加了个 1. ")。
# 首部客套**不用管**: 编号行之前没有当前段, 那些行本来就落不到任何一段上。
_FENCE = re.compile(r"^\s*```[A-Za-z0-9_+#.-]*\s*$")
_SMALLTALK_MAX = 40
# 客套词表刻意写得"窄": 光有"以上是…"不算客套 —— 必须接译文/翻译/全文这类**元词**, 否则
# 末段真句子会被吃掉("以上是结果分析。" 是正文, "以上是全文译文" 才是收尾话)。
_SMALLTALK = re.compile(
    r"^[\s>*_`\-—–]*(?:"
    r"以上(?:就)?是(?:全文|全部|本篇|本段)?(?:的)?(?:译文|翻译)|"
    r"以下是(?:译文|翻译)|如下是(?:译文|翻译)|下面是(?:译文|翻译)|"
    r"(?:译文|翻译)(?:如下|如上|结束|到此|完成)|全文(?:译文)?(?:如上|结束|到此)|"
    r"好的|收到|明白|没问题|我已|我将|我会|"
    r"请(?:核对|检查|确认|告知|告诉我)|如需|如果(?:需要|有)|希望|祝|感谢|谢谢"
    r")")
_LEADER = r"(?:[-*•>]|\(?\d{1,3}[.)、])?"


def _trim_tail(buf):
    """丢掉译文末尾的客套行(见上面 ② 的三道判据)。返回截断后的行表。"""
    rows = list(buf)
    while rows and not rows[-1].strip():
        rows.pop()                                  # 尾随空行: 纯包装, 无条件丢
    while len(rows) >= 2 and not rows[-2].strip():  # 与译文隔着空行的客套: 才判客套
        cand = rows[-1].strip()
        if len(cand) > _SMALLTALK_MAX or not _SMALLTALK.match(cand):
            break
        rows.pop()
        rows.pop()                                  # 客套行 + 它前面的空行一起去掉
        while rows and not rows[-1].strip():
            rows.pop()
    return rows


def parse_units(text, ids, pre):
    """从剪贴板文本里抽 编号<分隔>译文。网页渲染会把 TAB 转成空格, 两种都认。
    两种形态都支持(与各任务的门禁契约一致):
      TSV 形态:  k001\t译文 (表格回包)
      块形态:    #S1\n译文… (正文回包, seg_import 的 KEY_LINE 契约)
    返回**读到多少算多少** —— 齐不齐由调用方判。panel.py 要拿"缺哪几条"的明细
    给用户看, 所以不在这里截断。重复编号取首条(与 check_job.py 同口径)。
    """
    got = {}
    rx = re.compile(r"^\s*%s\s*\**\s*(%s\d+)\**\s*[\t ](.*)$" % (_LEADER, pre))
    key = re.compile(r"^\s*%s\s*\**\s*#?\**\s*(%s\d+)\**\s*$" % (_LEADER, pre))
    cur, buf = None, []

    def flush(final=False):
        if cur is not None and cur not in got:
            got[cur] = "\n".join(_trim_tail(buf) if final else buf).strip()

    for ln in text.splitlines():
        ln = ln.rstrip("\r")
        if _FENCE.match(ln):
            continue                                # 围栏行只是包装, 不是译文
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
    flush(final=True)                               # 末段: 收尾客套在这里清
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


class LedgerFailed(RuntimeError):
    """底片没做出来 —— **不是渲染出错, 是没让它开工**(v28.70)。

    v28.69 原判是"底片只是副本, 做不出来也不拦渲染"。用户一眼看出毛病: **PDF 照样出来、
    而且和正常那份一模一样**, 于是想补一份底片就得重走一遍出稿, 白多出一份同样的 PDF
    等人去删 —— 与其留这个尾巴, 不如当场停住、直接报错提醒。
    """


def _snapshot(log=print):
    """渲染前的**保底底片**: 把此刻已过的译稿固化成一份人读 Word(mk_ledger.build)。

    只在会触发重渲染的任务上做(正文): 渲染一出, 缓存被注入、产物落盘, 原始译稿就散在
    TSV 与 #S 块里了 —— 那是机读格式, 人事后想核"当时到底译成了什么"很费劲。底片就是
    **渲染前的人读存档点**: 正文逐段「原文/译文」对照 + 表格保持原表结构(见 mk_ledger)。

    返回产物路径; **做不出来就抛 LedgerFailed** —— 调用方据此中止出稿并报错, 不渲染
    (理由见 LedgerFailed)。此处**不吞异常**: 原因要原样送到人眼前, 不能只剩一句"失败了"。
    """
    try:
        import mk_ledger                       # 惰性导入: mk_ledger 反过来 import 本模块
        out = mk_ledger.build(log=log)
    except Exception as e:                     # noqa: BLE001 —— 任何异常都归一成一种失败
        raise LedgerFailed("底片生成失败(%r)" % (e,))
    if not out:
        raise LedgerFailed("没有可固化的译稿(译稿没落盘? 回看 ③ 的检查结论)")
    return out


def run_job(job, ids, got, log=print):
    """落盘 -> 门禁 -> 渲染前存底片 -> 出稿工序(表格=排附录; 正文=注入缓存+重渲染)。
    返回产物路径(出稿后由调用方打开); 未出稿返回 None。
    **底片做不出来则抛 LedgerFailed**(v28.70): 拦在渲染之前, 由调用方报错提醒 —— 渲染了
    也只会白多一份与正常无异的 PDF 等人去删(回包已落盘, 修好后重跑这一步即可)。"""
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

    if job.get("render"):                      # 渲染前存底片(只读); 做不出来 -> 冒泡, 不渲染
        _snapshot(log)

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
    try:
        out = run_job(job, ids, got, log=log)
    except LedgerFailed as e:                  # 底片没做出来 -> 已拦下出稿(渲染一步没走)
        beep(False)
        log("  ✗ %s" % e)
        log("  ✗ 已拦下出稿 —— 渲染只会白多一份 PDF。回包已落盘, 修好后重来一次即可。")
        return True
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
