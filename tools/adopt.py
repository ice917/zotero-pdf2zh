# -*- coding: utf-8 -*-
"""adopt.py — 豆包采纳管线「必经」入口 (方案 A, 2026-09-19)

为什么需要它:
  方案 A 把豆包软件放在翻译管线的**外部**, 五道工序是散的脚本, 顺序靠人记。
  实测踩过的坑全在"顺序/身份"上, 而不是在某个工序内部:
    - 侧车 latest.jsonl 是**全局单文件**, 换论文会覆盖 -> 导出的 payload 与
      注入时的侧车不是同一篇 -> 回锚错位 (seg_export/seg_inject 的 --sidecar
      帮助都写着"新论文请先归档", 但那只是句提醒, 没人拦你);
      [v28.9 已收口: converter 按文档散列另落一份 pdf-<md5>.jsonl, export --pdf 认领它]
    - 豆包交件可能有多个版本 (wang2026.doubao.txt/doubao2/doubao3), 拿错一份
      整轮白干;
    - `seg_inject` 的文档指纹探测失败会**静默回落 Cactaceae 默认值**, 对别的
      论文就是 "UPDATE 命中 0 行" 的 FAIL, 而原因看起来像"缓存没这条";
    - `seg_import` 没 PASS 就注入 = 把未校验的译文写进库;
    - 改了缓存不重渲染/不验收 = "改了但没落页"没人知道。
  本工具把这些判据做成**前置门禁**, 并把每步落成台账 (可审计 / 可断点续跑)。

六阶段 (顺序强制, 后一阶段的前置是前一阶段 state=="ok"):
  1 export   侧车 -> inbox/<name>.{txt,manifest.json}; 登记侧车指纹 + 文档指纹
  2 deliver  登记豆包交件: 交件件唯一/显式 + 段号集合与 manifest 完全一致
             + **内容门禁**(2026-09-20): 留空/只译半截 / 错位带 / 逐段不变量
               (数字·[n]引用·𝒪( ) / ⋮ 断点对账。任一条不过 -> 拒收,
               清单落 server/translated/review/门禁拒收_*.md 供豆包照改;
               确认过的合理改写用 --waive 显式放行(留痕)。
  3 import   seg_import 回锚 -> out/<name>.imported.json (必须 PASS)
  4 inject   seg_inject 写库 (先用 --dry 演算, PASS 才实写)
  5 render   force_rerender 落产物 PDF
             —— 但**先跑渲染前预检** (pre_render_check: 文献区禁汉化)。
                这道闸门原本只在阶段 6 的 post_check 里, 也就是"PDF 出来了才
                发现文献区被汉化", 得删掉重出。判据同源, 只是文本来源从 mono
                PDF 换成 imported.json —— 前移之后那一轮服务端渲染直接省掉。
  6 gate     verify_render(--expect) + post_check 双 PASS 才算交付

另有一个**不在顺序里**的止损阶段 (v28.23):
  7 rollback 撤销骨架行: 暂停档(PAUSE_TRANSLATE=1)第一趟为了让 seg_inject
             (UPDATE-only)有行可改, 会按最终键形态落一批 raw→raw 骨架行;
             回路半途失败时必须**先撤掉再回落**成正常翻译, 否则第二趟命中
             它们 -> 整篇英文 PDF

用法 (解释器同 tools/tests/run_all.py):
  PY = D:/Users/<user>/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  & $PY tools/adopt.py export --name payload_p2_p4 --pages 2-4 --pdf "D:/.../xxx.pdf" --doc "标题 (期刊, 年份)"
  & $PY tools/adopt.py deliver --name payload_p2_p4 --text "D:/.../p2_p4.doubao.txt"
  & $PY tools/adopt.py import  --name payload_p2_p4
  & $PY tools/adopt.py inject  --name payload_p2_p4
  & $PY tools/adopt.py render  --name payload_p2_p4 --pdf "D:/.../xxx.pdf"
  & $PY tools/adopt.py gate    --name payload_p2_p4 --expect "雄花具有长花柱"
  & $PY tools/adopt.py status

  export 的侧车(v28.9): 给了 --pdf 就按**文档内容散列**认领
  ~/.cache/pdf2zh/segflow/pdf-<md5[:16]>.jsonl (由 converter 每篇自动落一份),
  不再需要人工把 latest.jsonl 归档; 找不到会拒绝执行而不是拿"最近翻过的另一篇"顶上。

台账: D:\\zotero-pdf2zh\\logs\\adopt\\<name>.json
  --force 可跳过顺序门禁 (运维单步重跑用), 用了哪几步记在台账 forced_stages 里留痕。
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
INBOX = os.environ.get("P2Z_INBOX", os.path.join(PROJ, "inbox"))
OUTDIR = os.path.join(PROJ, "out")
LEDGER_DIR = os.path.join(PROJ, "logs", "adopt")
SIDECAR = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "segflow", "latest.jsonl")
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "cache.v1.db")
# [自研补丁 2026-09-20] 门禁拒收报告落点: 与 post_check.py 的 review_dir() /
# doubao_bridge.REVIEW 同一目录 —— 桥的 list_reports / get_report 就读这里。
# 落在文件里而不是只印终端, 是因为"豆包能矫正"的前提是它**看得见**自己哪一段被拒。
REVIEW = os.environ.get("P2Z_REVIEW",
                        os.path.join(PROJ, "server", "translated", "review"))


def _local_py():
    """本机解释器路径, 读 git-ignored 的 server/config/local_paths.json
    (开源仓库不携带任何个人绝对路径; 本机行为由该文件 + PDF2ZH_PYTHON 提供)"""
    p = os.path.join(PROJ, "server", "config", "local_paths.json")
    try:
        with open(p, encoding="utf-8") as f:
            return (json.load(f) or {}).get("python") or ""
    except Exception:
        return ""
DEFAULT_PY = os.environ.get("PDF2ZH_PYTHON") or _local_py() or sys.executable
PY = DEFAULT_PY

STAGES = ["export", "deliver", "import", "inject", "render", "gate"]
NEED = {  # 阶段 -> 前置阶段
    "export": [],
    "deliver": ["export"],
    "import": ["deliver"],
    "inject": ["import"],
    "render": ["inject"],
    "gate": ["render"],
}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-. ]{1,80}$")
# 只在 mark(..., "failed") 时写的字段: 阶段转 ok 后必须剔除, 否则台账里会留下
# "ok 却带着失败原因"的自相矛盾(见 mark() 注释)。
_FAIL_ONLY = ("reason", "missing", "extra", "detail")

# 复用一线工具的口径, 避免"门禁的解析规则"与"真正执行的解析规则"漂移:
#   seg_import.KEY_LINE / parse_blocks —— 段号行怎么认 (容忍 markdown 加粗)
#   seg_inject.renumber / detect_fp   —— 页级 {vN} -> 段内 {vk}, 文档指纹怎么探
# 这两个模块在 import 时会把 sys.stdout 重包一层 utf-8 wrapper。直接 import 再
# 恢复 sys.stdout 是不行的: 那些 wrapper 被 GC 时会**关掉底层 buffer** -> 本工具
# 自己的 print 全部 "I/O operation on closed file"。故导入期间先把 sys.stdout
# 换成指向一次性 BytesIO 的替身, 让它们的包装打在替身上。
def _load_siblings():
    import io as _io
    real, sink = sys.stdout, _io.TextIOWrapper(_io.BytesIO(), encoding="utf-8")
    sys.stdout = sink
    try:
        import seg_import, seg_inject
    finally:
        sys.stdout = real
    return seg_import, seg_inject


if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
SI, SJ = _load_siblings()


# ------------------------------------------------------------------ 基础
def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha1_8(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def die(msg):
    print("[adopt] 拒绝执行: %s" % msg)
    return 1


def ledger_path(name):
    return os.path.join(LEDGER_DIR, name + ".json")


def load_ledger(name):
    p = ledger_path(name)
    if not os.path.exists(p):
        return {"name": name, "created": now(), "updated": now(), "stages": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_ledger(led):
    os.makedirs(LEDGER_DIR, exist_ok=True)
    led["updated"] = now()
    p = ledger_path(led["name"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)          # 原子替换, 半截台账不会覆盖上一版


def mark(led, stage, state, **kw):
    rec = led["stages"].setdefault(stage, {})
    rec.update(kw)
    # [自研补丁 2026-09-19] 失败专属字段只描述失败, 转 ok 就作废。mark() 是 update,
    # 不剔除的话台账会自相矛盾 —— 实测 wang2026 的 import 重跑成功后仍是
    # {"state":"ok", "reason":"seg_import 退出码 1"}。
    if state == "ok":
        for k in _FAIL_ONLY:
            rec.pop(k, None)
    rec["state"] = state
    rec["at"] = now()
    return rec


def guard(led, stage, force):
    """顺序强制: 前置阶段必须 state == ok。"""
    if force:
        print("[adopt] --force: 跳过顺序门禁 (%s)" % stage)
        led.setdefault("forced_stages", [])
        if stage not in led["forced_stages"]:
            led["forced_stages"].append(stage)
        return True
    for dep in NEED[stage]:
        st = (led["stages"].get(dep) or {}).get("state")
        if st != "ok":
            die("阶段 %s 的前置 %s 当前为 %s; 请先跑 `adopt.py %s ...`"
                % (stage, dep, st or "未执行", dep))
            return False
    return True


def run_tool(script, args, capture=False):
    """跑一线工具。capture=True 时既实时透传 stdout 又收集文本(供解析产物路径)。"""
    cmd = [PY, os.path.join(TOOLS, script)] + [str(a) for a in args]
    print("[adopt] $ %s" % " ".join(cmd))
    if not capture:
        return subprocess.run(cmd, cwd=PROJ).returncode, ""
    p = subprocess.Popen(cmd, cwd=PROJ, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", bufsize=1)
    lines = []
    for line in p.stdout:
        sys.stdout.write(line)
        lines.append(line)
    p.wait()
    return p.returncode, "".join(lines)


def read_manifest(name):
    p = os.path.join(INBOX, name + ".manifest.json")
    if not os.path.exists(p):
        return None, p
    with open(p, encoding="utf-8") as f:
        return json.load(f), p


def load_sidecar_pages(sidecar):
    pages = {}
    with open(sidecar, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                o = json.loads(line)
                pages[o["page"]] = o
    return pages


def sidecar_id(path):
    if not os.path.exists(path):
        return None
    st = os.stat(path)
    return {"path": path, "size": st.st_size,
            "mtime": int(st.st_mtime), "sha1": sha1_8(path)}


# ------------------------------------------------------------------ 阶段 1: export
def probe_db(man, pages):
    """只读探库: (命中行数, 文档指纹)。

    命中 0 行 = 这次翻译根本没落库(或侧车不是这篇), 此时导出等于让后面的人工
    豆包环节白跑 —— 所以在导出阶段就拦住。
    """
    con = sqlite3.connect("file:%s?mode=ro" % CACHE.replace("\\", "/"), uri=True)
    cur = con.cursor()
    hit, fp = 0, None
    try:
        for it in man["items"]:
            for p in it["parts"]:
                raw = (pages[p["page"]]["segs"][p["seg"]].get("raw") or "")
                if len(raw) < 40:
                    continue
                cache_raw = SJ.renumber(raw, raw)
                n = cur.execute(
                    "SELECT COUNT(*) FROM _translationcache WHERE original_text=?",
                    (cache_raw,)).fetchone()[0]
                hit += n
        fp = SJ.detect_fp(cur, pages, man)
    finally:
        con.close()
    return hit, fp


def pdf_md5_16(pdf):
    """PDF 内容的 16 位散列 —— 文档身份, 与 converter 侧写归档件同一口径。"""
    h = hashlib.md5()
    with open(pdf, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def archived_sidecar(pdf):
    """该篇"按文档归档"的侧车路径 (存在才返回)。见 converter._segflow_paths。"""
    p = os.path.join(os.path.dirname(SIDECAR), "pdf-%s.jsonl" % pdf_md5_16(pdf))
    return p if os.path.exists(p) else None


def resolve_sidecar(args):
    """本次导出用哪份侧车 -> (路径, 来源说明) 或 (None, 拒绝理由)。

    优先级: 显式 --sidecar > --pdf 指向的按文档归档件 > 全局 latest.jsonl。
    给了 --pdf 却找不到归档件时**拒绝执行**, 不回落 latest.jsonl —— 那里躺的是
    "最近翻过的那一篇", 拿错侧车会安静地导出一份别人的载荷(下游回锚还会 PASS)。
    """
    if os.path.abspath(args.sidecar) != os.path.abspath(SIDECAR):
        return args.sidecar, "显式指定"
    if not args.pdf:
        return SIDECAR, "latest.jsonl (全局单文件, 换论文会被覆盖)"
    if not os.path.exists(args.pdf):
        return None, "原文 PDF 不存在: %s" % args.pdf
    p = archived_sidecar(args.pdf)
    if p:
        return p, "按文档归档件"
    want = os.path.join(os.path.dirname(SIDECAR), "pdf-%s.jsonl" % pdf_md5_16(args.pdf))
    return None, ("按 %s 找不到按文档归档的侧车\n"
                  "        期望: %s\n"
                  "        该篇多半是 v28.9 之前的管线下翻的, 没留归档件。两条路:\n"
                  "          a) 先对它跑一次渲染 (会自动补上归档件), 再导出\n"
                  "          b) --sidecar 显式指一份 (自己确认那一份就是这一篇)"
                  % (os.path.basename(args.pdf), want))


def sidecar_doc_fp(sc):
    """读侧车自带的文档画像指纹 (converter v28.23 起随行导出)。

    没有这一项的老侧车返回 ""。取第一行里非空的那个即可 —— 同一篇的每一行都
    是同一个值(引擎在同一次渲染里算出来然后逐页重复写入)。
    """
    try:
        with open(sc, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                v = (json.loads(line) or {}).get("doc_fp") or ""
                if v:
                    return v
                break   # 只看第一行: 有就是有, 没有说明是老侧车
    except Exception:
        pass
    return ""


def stage_export(args):
    led = load_ledger(args.name)
    if not guard(led, "export", args.force):
        return 1
    sc, sc_why = resolve_sidecar(args)
    if sc is None:
        return die(sc_why)
    if not os.path.exists(sc):
        return die("侧车不存在: %s (先跑一次翻译, 或 --sidecar 指向归档件)" % sc)
    print("[adopt] 侧车: %s (%s)" % (sc, sc_why))
    if (led["stages"].get("export") or {}).get("state") == "ok" and not args.force:
        return die("本 run 已导出过; 重做请加 --force (会覆盖 inbox 同名件)")

    rc, _ = run_tool("seg_export.py",
                     ["--pages", args.pages, "--name", args.name, "--sidecar", sc]
                     + (["--doc", args.doc] if args.doc else [])
                     + (["--terms", args.terms] if args.terms else []))
    if rc != 0:
        mark(led, "export", "failed", reason="seg_export 退出码 %d" % rc)
        save_ledger(led)
        return rc

    man, man_p = read_manifest(args.name)
    if man is None:
        mark(led, "export", "failed", reason="manifest 未生成")
        save_ledger(led)
        return die("manifest 未生成: %s" % man_p)
    pages = load_sidecar_pages(sc)
    hit, fp_db = probe_db(man, pages)
    # [v28.23] 指纹优先取侧车自带的那一份。
    # 为什么: "库内命中反探"在**暂停档**下必然 0 命中 —— 骨架档的第一趟按规矩
    # 一列缓存都不写, 于是 export 拒导 -> 服务端回落成付费机器翻译(实测)。
    # 侧车是本次刚渲染出来的那一份, 指纹来自引擎自己算的缓存参数, 比从库行反推
    # 更权威(库行是"翻过的产物", 骨架档恰恰没有产物)。老侧车没有该项, 走原路。
    fp = sidecar_doc_fp(sc)
    if fp:
        if hit == 0:
            print("[adopt] 库内 0 命中 —— 骨架档/首翻的正常形态; 指纹取自侧车: %s" % fp)
    else:
        fp = fp_db
        if hit == 0:
            mark(led, "export", "failed", reason="库内 0 命中")
            save_ledger(led)
            return die("库内查不到本次任何段落原文 —— 侧车与缓存库不是同一篇(或没翻过)。"
                       "请先跑翻译再导出; 若已翻译, 用 --sidecar 指向该篇归档的 latest.jsonl")
        if fp is None:
            print("[adopt] 警告: 探测不到文档摘要指纹(库内行不带 docsummary fp)。")
            print("        该篇无法用文档作用域注入 —— inject 阶段必须显式 --fp, 否则会误用默认值。")

    mark(led, "export", "ok", pages=args.pages, sidecar=sidecar_id(sc),
         sidecar_source=sc_why, doc_fp=fp, db_hits=hit, n_items=len(man["items"]),
         txt=os.path.join(INBOX, args.name + ".txt"), manifest=man_p)
    save_ledger(led)
    print("[adopt] export OK: %d 段 / 库内命中 %d 行 / doc_fp=%s" % (len(man["items"]), hit, fp))
    print("[adopt] 下一步: 把 inbox/%s.txt 交给豆包软件定稿, 再跑 deliver" % args.name)
    return 0


# ------------------------------------------------------------------ 逐段不变量
# [自研补丁 2026-09-20] 交付稿的**内容级**核对。
#
# 为什么加: 2026-09-20 SILAGE 那篇, 交付稿在残片密集区**整段错位** —— 源
# #S277/278/279 的译文出现在交付 #S276/277/278, 几十段整体前移一格。当时的门禁
# 只比对段号集合与 ⋮ 条数, 两者全过, 于是错位稿一路回灌、渲染成错页论文, 而日志
# 里只有 4 条"⋮ 不符", 看不出真问题(反被当成"小毛病"放过)。
#
# 判据只取"译者不该动的东西": 数字 / [n] 引用号 / 𝒪( 记号 (⋮ 那条已有, 这里并入同一份报告)。
# 定标用的是 10 篇**历史上已被采纳**的交件(311 个含数字的段): 数字多重集不符 1 例
# (0.3%, 那例是译者把加粗的 1 展开成"全为 1 的", 属合理改写), [n] 0/25, 𝒪( 0/0,
# ⋮ 0/7 —— 同一套判据打在问题稿上是 131 段不符 + 13 条错位带。判别力够, 故做
# **硬门禁**: 不符即拒收, 不再静默回落机翻; 极少数合理改写由 deliver --waive 显式放行(留痕)。
NUM_TOK = re.compile(r"\d+(?:\.\d+)?")
REF_TOK = re.compile(r"\[\d+(?:\s*[,\-–]\s*\d+)*\]")
BIG_O = re.compile(r"[𝒪𝑂]\s*\(")
_REPORT_ROWS = 60       # 报告里逐段明细的上限(全文可能上百条, 表太长没人看)
# [2026-09-20c] 第三节"载荷↔交付"并排片段的口径: 段长不超过 _SNIP_FULL 就整段照出,
# 否则留头(认得出是哪一句) + 尾(数字常落在没译的尾部)。定这类数只看"人读起来够不够",
# 不参与任何判定 —— 它是证据, 不是判据。
_REPORT_SNIP_FULL = 180
_REPORT_SNIP_HEAD = 110
_REPORT_SNIP_TAIL = 60

# [自研补丁 2026-09-20b] 留空/只译半截判据 (SILAGE doubao2 事故)。定标: SILAGE 790 段
# vs 4 篇**已被采纳**的交件(324 个 >=40 字符的长段) —— 空段 8 例 / 0 例,
# 覆盖率 < 0.15 共 33 例 / 0 例。干净到可以当硬门禁。
# 为什么必须单列: 这类段的"数字不符"是**结果**不是原因。上一版报告把它们全列成
# "数字按载荷原样, 不改不减不增", 译者照那条改会去抠数字, 而真相是"这段没译完" ——
# 改法指错地方, 白跑一轮。分出来之后改法才对得上病因(把丢掉的尾巴补上)。
_GAP_MIN_SRC = 40       # 源段短于这个字符数不判覆盖率: 短段比值噪声大(中文天然短)
_GAP_RATIO = 0.15

# [自研补丁 2026-09-20c] 上标/下标数字归一化。载荷里的脚注/指数经 PDF 提取常常落成
# 行内 ASCII 数字(原文 "complexity4"), 译者按语义写成上标(译文 "复杂度⁴") —— 同一个
# 数字, 两种写法, 不该判成"数字变了"。实测 SILAGE: 不归一化有 3 条伪影(#S22/#S90/#S557),
# 报告会让译者去改**本来是对的**数字, 白跑一轮。归一化对**两侧**都做, 只影响签名, 不改交付文本。
_SCRIPT_DIGITS = {ord(c): str(i) for i, c in enumerate("⁰¹²³⁴⁵⁶⁷⁸⁹")}
_SCRIPT_DIGITS.update({ord(c): str(i) for i, c in enumerate("₀₁₂₃₄₅₆₇₈₉")})
_SCRIPT_DIGITS.update({0x207B: "-", 0x208B: "-"})   # ⁻ / ₋ 上标·下标负号


def inv_sig(text):
    """一段的不变量签名: (数字多重集, [n]引用多重集, 𝒪( 计数)。

    数字先做上标/下标归一化(见 _SCRIPT_DIGITS): "⁴" 与 "4" 是同一个数字的两种写法。
    """
    t = (text or "").translate(_SCRIPT_DIGITS)
    return (tuple(sorted(NUM_TOK.findall(t))),
            tuple(sorted(REF_TOK.findall(t))),
            len(BIG_O.findall(t)))


def gap_defects(src, dst):
    """返回 (留空段号, [(截断段号, 源长, 交付长)...]) —— 源段有内容而交付没译完的段。

    留空 = 交付该段一个字符都没有; 截断 = 交付长度不到源段的 15%(源段 >= 40 字符)。
    源段本身为空的跳过 —— 那种"空对空"不是缺陷。
    """
    empty, short = [], []
    for k in sorted(src):
        s = " ".join((src.get(k) or "").split())
        t = " ".join((dst.get(k) or "").split())
        if not s:
            continue
        if not t:
            empty.append(k)
        elif len(s) >= _GAP_MIN_SRC and len(t) < _GAP_RATIO * len(s):
            short.append((k, len(s), len(t)))
    return empty, short


def _toks(t):
    return ", ".join(t) if t else "无"


def diff_invariants(src_text, dst_text):
    """逐段比对, 返回 (不符项, 载荷侧, 交付侧) 三元组列表(空 = 合格)。"""
    a, b = inv_sig(src_text), inv_sig(dst_text)
    out = []
    if a[0] != b[0]:
        out.append(("数字", _toks(a[0]), _toks(b[0])))
    if a[1] != b[1]:
        out.append(("[n]引用", _toks(a[1]), _toks(b[1])))
    if a[2] != b[2]:
        out.append(("𝒪(记号", "%d 个" % a[2], "%d 个" % b[2]))
    return out


def shift_bands(src, dst):
    """诊"整段错位": 源某段的签名出现在交付的相邻号上 -> 内容没丢, 只是号错位。

    返回 [(偏移, 起段, 止段, 段数), ...], 只留连续 >= 2 段的(单段命中是噪声)。
    邻域只看到 ±2 格 —— 更远就不是"错位"而是"换了一篇", 那种该整篇作废。
    为什么值得单列: 错位带要改的是**编号**, 不是数字; 不分离出来, 豆包面对的是
    131 条"数字不符", 会以为要重译那 131 段 —— 而重译正是重新引入错位的做法。

    [2026-09-20b 修假阳性] 先看 offset 0: 交付该段的签名与载荷该段**本来**就相同,
    说明它在家里, 不是被搬走的 —— 直接跳过。不跳过的话, 签名不具判别力的段会成对
    误报: 实测 SILAGE 那篇的 #S58-61(都在列举 (𝛿1,𝛿2), 签名全是 (1,2)) 被报成
    "载荷 #S58-#S59 后移 2 格" + "载荷 #S60-#S61 前移 2 格" 一对, 看着像互换, 其实
    四段各自都在正确的号上。假错位带比漏报更坏: 报告会让译者去改**本来是对的**编号。
    """
    hits = {}
    for k, s in src.items():
        sig = inv_sig(s)
        if sig == ((), (), 0):
            continue                      # 源段里没有任何不变量 -> 无判别力
        if inv_sig(dst.get(k)) == sig:
            continue                      # 在家的(offset 0 就对), 不是错位
        for off in (-1, 1, -2, 2):        # 近的先试: 近邻全等比远邻全等可信
            if dst.get(k + off) is not None and inv_sig(dst[k + off]) == sig:
                hits[k] = off
                break
    bands, run = [], None
    for k in sorted(hits):
        off = hits[k]
        if run and run[0] == off and k == run[2] + 1:
            run[2], run[3] = k, run[3] + 1
        else:
            if run:
                bands.append(tuple(run))
            run = [off, k, k, 1]
    if run:
        bands.append(tuple(run))
    return [b for b in bands if b[3] >= 2]


def _snip(t):
    """报告里引用一段文本: 短的整段照出, 长的保留头尾。

    为什么要留尾: "数字不符"最常见的一种是**漏译尾巴**(数字落在没译的后半句里),
    只给开头等于把现场截掉; 而"内容漂移"又必须看开头才认得出是**哪一句**。故两头都留。
    """
    s = " ".join((t or "").split())
    if len(s) <= _REPORT_SNIP_FULL:
        return s
    return "%s …(中略)… %s" % (s[:_REPORT_SNIP_HEAD], s[-_REPORT_SNIP_TAIL:])


def write_gate_report(name, text_path, src, got, bad_cut, bad_inv, bands, gaps=(None, None)):
    """把拒收原因落成一份 review/ 报告并返回路径 —— 这是"交给豆包改"的那份东西。

    头部必须带一行含「门禁判定」的结论: doubao_bridge._headline() 只读前 6KB 抓
    这一行, 抓不到的话 list_reports 里这条就是空白, 豆包照样看不见。
    节序 = 按"好改、影响大"排: 留空/截断 -> 错位带 -> 不变量 -> ⋮。
    """
    empty, short = gaps
    empty, short = empty or [], short or []
    os.makedirs(REVIEW, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    p = os.path.join(REVIEW, "门禁拒收_%s_%s.md" % (stamp, name))
    L = []
    L.append("# 门禁拒收：%s" % name)
    L.append("")
    L.append("- 门禁判定: FAIL（交件未通过 deliver 内容门禁，已拒收；任务判失败，现场保留）")
    L.append("- 交件: %s  (sha1=%s)" % (text_path, sha1_8(text_path)))
    L.append("- 载荷: %s" % os.path.join(INBOX, name + ".txt"))
    L.append("- 规模: 交付 %d 段 / 载荷 %d 段" % (len(got), len(src)))
    L.append("- 拒收条目: 留空 %d 段 · 只译半截 %d 段 · 错位带 %d 条 · 逐段不变量 %d 条 · ⋮ %d 条"
             % (len(empty), len(short), len(bands), len(bad_inv), len(bad_cut)))
    L.append("")
    if empty or short:
        L.append("## 一、留空 / 只译半截（先看这里，多半是这批）")
        L.append("")
        L.append("载荷这些段**原有内容**，交付里却没译完：留空的是整段没有，只译半截的是"
                 "句子写到一半就断了（尾部常是公式残块、括号里的参数、从句尾）。"
                 "**请把缺的部分补全**——按载荷逐字译完，包括段尾那个看起来不完整的公式残留"
                 "（它属于原文，照抄/照译，不要因为\"看着没写完\"就丢掉）。")
        L.append("")
        L.append("注意：这些段的\"数字对不上\"是**结果**而不是原因（数字在丢掉的尾巴里），"
                 "所以先修这一段，不要单独去调数字。")
        L.append("")
        if empty:
            L.append("留空（%d 段，整段没内容）：" % len(empty))
            L.append("")
            L.append("`%s`" % "  ".join("#S%d" % k for k in empty[:_REPORT_ROWS]))
            L.append("")
        if short:
            L.append("只译半截（%d 段，交付长度不足原文 15%%）：" % len(short))
            L.append("")
            L.append("| 段号 | 原文长度 | 交付长度 | 原文结尾（照它补完） |")
            L.append("| --- | --- | --- | --- |")
            for k, ls, lt in short[:_REPORT_ROWS]:
                tail = " ".join((src.get(k) or "").split())[-60:]
                L.append("| #S%d | %d | %d | …%s |" % (k, ls, lt, tail))
    if bands:
        L.append("## 二、整段错位（%d 条）" % len(bands))
        L.append("")
        L.append("下面这些区间里，**载荷某段的译文被放到了交付的相邻号上** —— 内容没丢，"
                 "只是整片错了一格。请对照载荷把号改回来，**不要重译**（重译只会再错一次）。")
        L.append("")
        L.append("| 载荷段号区间 | 偏移 | 段数 | 含义 |")
        L.append("| --- | --- | --- | --- |")
        for off, k0, k1, n in bands[:_REPORT_ROWS]:
            move = "前移 %d 格" % -off if off < 0 else "后移 %d 格" % off
            L.append("| #S%d – #S%d | %+d | %d | 这批段的译文被放到了交付的 #S%d – #S%d"
                     "（一律%s） |" % (k0, k1, off, n, k0 + off, k1 + off, move))
        L.append("")
    L.append("## 三、逐段不变量不符（%d 条%s）" % (
        len(bad_inv), "，列前 %d 条" % _REPORT_ROWS if len(bad_inv) > _REPORT_ROWS else ""))
    L.append("")
    L.append("判据: 数字 / [n] 引用号 / 𝒪( 记号按载荷**原样** —— 不改、不减、不增、不换位"
             "（上标·下标数字如 `⁴` 与 `4` 视为同一个数字，算过）。")
    L.append("")
    L.append("每条都附**载荷原文 ↔ 你的译文**并排（长的留头尾）。改之前先把并排读一遍，"
             "对号入座，三种病三种改法：")
    L.append("")
    L.append("1. **译文读起来是相邻段载荷的译文**（内容整体挪了位，往后翻几条会连着挪）"
             "→ 这是内容漂移，按第二节的法子把这一片**按载荷重新对齐**（只改位置，别重译），"
             "**不要去动数字**。")
    L.append("2. **译文只译了载荷的前半句**（数字落在你没译的后半句/段尾）→ 把后半句补译完，"
             "数字自然就回来了。")
    L.append("3. **译文把载荷里的公式/表达式改写成了文字**（如 `1𝑛∑︀𝑛𝑖=1` 意译成\"平均值\"）"
             "→ 按载荷把公式**整块照抄**（rule 7），数字随之还原。")
    L.append("")
    if len(bad_inv) > _REPORT_ROWS:
        L.append("（下面只列前 %d 条；完整条目见台账 detail 字段。）" % _REPORT_ROWS)
        L.append("")
    for k in sorted(bad_inv)[:_REPORT_ROWS]:
        L.append("**#S%d**" % k)
        L.append("")
        L.append("- 不符: %s" % "；".join("%s 载荷 `%s` → 交付 `%s`" % (w, a, b)
                                          for w, a, b in bad_inv[k]))
        L.append("- 载荷: %s" % (_snip(src.get(k)) or "（空）"))
        L.append("- 交付: %s" % (_snip(got.get(k)) or "（空）"))
        L.append("")
    if bad_cut:
        L.append("## 四、分页断点 ⋮ 不符（%d 条）" % len(bad_cut))
        L.append("")
        L.append("⋮ 只允许出现在该段**确实跨页**处，条数必须与载荷清单一致，"
                 "且不得出现在普通段里。")
        L.append("")
        for b in bad_cut[:_REPORT_ROWS]:
            L.append("- %s" % b)
        L.append("")
    L.append("## 改法")
    L.append("")
    L.append("1. 只改上面点到号的段；其余段一个字都不要动（整篇重译 = 重新引入错位）。")
    L.append("2. 相邻的碎片段（如 \"…average component\" 与 \"t error:\"）**各译各的**，"
             "不要并成一句 —— 并段会顶掉一个段号，后面整片上移、末尾留空。"
             "**并段/重切正是内容漂移的头号成因**：一句跨两段时，你按语义重切，切点就跟载荷"
             "对不上了，第三节那一片的\"数字不符\"就是这么来的。")
    L.append("3. 段尾的公式残留照抄，不要因为\"看着不完整\"就省略。")
    L.append("4. 第二节的错位判据只认数字 / [n] / 𝒪( —— 载荷里数学记号多、ASCII 数字少的"
             "段落它可能**漏报**。所以第三节的并排若读出\"译文其实是相邻段的译文\"，"
             "即便第二节没列，也当错位带处理。")
    L.append("5. 改完**用同一个文件名覆盖**重交（`%s.doubao.txt`）—— out/ 里留两份候选"
             "（比如又存一份 `.doubao2.txt`）会让 deliver 判\"交件件不唯一\"直接拒收。" % name)
    L.append("")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return p


# ------------------------------------------------------------------ 阶段 2: deliver
def stage_deliver(args):
    led = load_ledger(args.name)
    if not guard(led, "deliver", args.force):
        return 1
    man, man_p = read_manifest(args.name)
    if man is None:
        return die("manifest 不存在: %s" % man_p)

    if args.clip:
        text = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                              capture_output=True).stdout.decode("utf-8", "replace")
        path = os.path.join(OUTDIR, args.name + ".doubao.txt")
        if os.path.exists(path) and not args.force:
            return die("已存在 %s; 覆盖请加 --force" % path)
        os.makedirs(OUTDIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("[adopt] 剪贴板已落盘: %s" % path)
    elif args.text:
        path = os.path.abspath(args.text)
        if not os.path.exists(path):
            return die("交件件不存在: %s" % path)
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    else:
        cand = sorted(glob.glob(os.path.join(OUTDIR, args.name + ".doubao*.txt")))
        if not cand:
            return die("out/ 下没有 %s.doubao*.txt; 请用 --text 指定交件件" % args.name)
        if len(cand) > 1:
            print("[adopt] 交件候选 %d 份:" % len(cand))
            for c in cand:
                print("    %s  (%s, %d 字节)" % (c, now(), os.path.getsize(c)))
            return die("交件件不唯一, 请用 --text 显式指定要采纳的那一份(拿错整轮白干)")
        path = cand[0]
        print("[adopt] 交件件(唯一候选): %s" % path)
        # 自动认领这条路也要把正文读进来: 下面所有门禁(段号守恒/断点对账)都吃 text
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()

    # 段号守恒 —— 回锚对齐的前提, 也是"豆包是否漏译/多译"的第一道判据
    got = SI.parse_blocks(text)
    want = {int(str(it["key"]).lstrip("S")): it for it in man["items"]}
    miss = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    if miss or extra:
        mark(led, "deliver", "failed", reason="段号不符", missing=miss, extra=extra)
        save_ledger(led)
        if miss:
            print("[adopt] 缺段 %d 个: %s" % (len(miss), miss[:20]))
        if extra:
            print("[adopt] 多段 %d 个: %s" % (len(extra), extra[:20]))
        return die("段号集合与 manifest 不一致, 拒绝进入回锚")
    # 载荷(源文)是逐段不变量的比对基准, 由 export 落在 inbox, 台账记了路径。
    # 读不到就**明说跳过 + 台账记 inv_ok=False**(不冒充全过), 但**不据此拒收整份交件**:
    # 缺的是我们自己产物的路径, 拿它挡住豆包的稿子是本末倒置 —— 而且段号守恒与 ⋮
    # 对账这两道不依赖载荷, 上面已经跑过了。注意此时绝不能拿空基准去逐段比:
    # 那会把每一段都判成"数字多出来", 全线误杀。
    pay = (led["stages"].get("export") or {}).get("txt") or ""
    src = {}
    if pay and os.path.exists(pay):
        with open(pay, encoding="utf-8-sig") as f:
            src = SI.parse_blocks(f.read())
    else:
        print("[adopt] ⚠️ 载荷不可读(%s) —— 跳过逐段不变量核对(整段错位这一轮拦不住), "
              "台账记 inv_ok=False" % (pay or "台账未记录路径"))
    inv_on = bool(src)
    bad_cut, bad_inv = [], {}
    for k, it in sorted(want.items()):
        n_parts = len(it["parts"])
        n_cut = got[k].count("⋮")
        if it["merged"] and n_cut != n_parts - 1:
            bad_cut.append("#S%d 断点 %d != 预期 %d" % (k, n_cut, n_parts - 1))
        elif not it["merged"] and n_cut:
            bad_cut.append("#S%d 普通段出现 ⋮" % k)
        if inv_on:
            d = diff_invariants(src.get(k), got[k])
            if d:
                bad_inv[k] = d
    # 留空 / 只译半截 —— 与不变量同源(都吃载荷), 但病因与改法不同, 故单列一类
    empty, short = gap_defects(src, got) if inv_on else ([], [])
    gaps = empty, short
    if bad_cut or bad_inv or empty or short:
        bands = shift_bands(src, got) if inv_on else []
        if args.waive:
            # 显式放行: 极少数合理改写(实测 311 个含数字的段里 1 例)不该把整篇卡死。
            # 放行必须留痕 —— 台账记 waive + 报告照落, 事后查得出"谁放过了什么"。
            rep = write_gate_report(args.name, path, src, got, bad_cut, bad_inv, bands, gaps)
            mark(led, "deliver", "ok", file=path, sha1=sha1_8(path),
                 n_seg=len(got), chars=len(text), waive=True, waive_at=now(),
                 waived={"cut": len(bad_cut), "inv": len(bad_inv),
                         "empty": len(empty), "short": len(short)}, report=rep)
            save_ledger(led)
            print("[adopt] ⚠️ --waive 放行 %d 条门禁不符(⋮ %d / 不变量 %d / 留空 %d / 半截 %d), "
                  "留痕于台账与 %s"
                  % (len(bad_cut) + len(bad_inv) + len(empty) + len(short),
                     len(bad_cut), len(bad_inv), len(empty), len(short), rep))
            return 0
        rep = write_gate_report(args.name, path, src, got, bad_cut, bad_inv, bands, gaps)
        mark(led, "deliver", "failed", reason="内容门禁不符", file=path,
             sha1=sha1_8(path), n_cut=len(bad_cut), n_inv=len(bad_inv), report=rep,
             n_empty=len(empty), n_short=len(short),
             detail=(bad_cut + ["#S%d %s" % (k, "; ".join(x[0] for x in v))
                                for k, v in sorted(bad_inv.items())])[:20])
        save_ledger(led)
        if empty:
            print("[adopt]   ⚠️ 留空 %d 段: %s" % (len(empty), ["#S%d" % k for k in empty[:20]]))
        if short:
            print("[adopt]   ⚠️ 只译半截 %d 段: %s" % (
                len(short), ["#S%d(%d->%d)" % t for t in short[:20]]))
        for b in bad_cut[:20]:
            print("[adopt]   " + b)
        for k in sorted(bad_inv)[:20]:
            print("[adopt]   #S%d %s" % (k, "; ".join(
                "%s %s->%s" % (w, a, b) for w, a, b in bad_inv[k])))
        for off, k0, k1, n in bands[:10]:
            print("[adopt]   ⚠️ 错位带: 载荷 #S%d-#S%d (%d 段) 的译文被放到交付的 #S%d-#S%d"
                  % (k0, k1, n, k0 + off, k1 + off))
        print("[adopt] 报告: %s" % rep)
        return die("内容门禁不符(留空 %d 段 / 只译半截 %d 段 / 错位带 %d 条 / 逐段不变量 %d 段 / "
                   "⋮ %d 条), 拒绝进入回锚。这不是可忽略的小毛病: 留空与错位会让整篇译文"
                   "对不上号。请让豆包照报告只改点到号的段后重交; 确认无毒可用 --waive 显式放行"
                   % (len(empty), len(short), len(bands), len(bad_inv), len(bad_cut)))

    mark(led, "deliver", "ok", file=path, sha1=sha1_8(path),
         n_seg=len(got), chars=len(text), inv_ok=inv_on)
    save_ledger(led)
    print("[adopt] deliver OK: %d 段 / %d 字符 / sha1=%s (%s)"
          % (len(got), len(text), sha1_8(path),
             "逐段不变量全过" if inv_on else "⚠️ 未做逐段不变量核对(缺载荷)"))
    return 0


# ------------------------------------------------------------------ 阶段 3: import
def stage_import(args):
    led = load_ledger(args.name)
    if not guard(led, "import", args.force):
        return 1
    exp = led["stages"]["export"]
    cur_sc = sidecar_id(exp["sidecar"]["path"])
    if cur_sc and cur_sc["sha1"] != exp["sidecar"]["sha1"] and not args.force:
        return die("侧车已被改写(%s -> %s)。latest.jsonl 是全局单文件, 换论文会覆盖它; "
                   "请把它归档, 再用 --sidecar 指回导出时那一份"
                   % (exp["sidecar"]["sha1"], cur_sc["sha1"]))
    man_p = exp["manifest"]
    text = led["stages"]["deliver"]["file"]
    rc, _ = run_tool("seg_import.py",
                     ["--manifest", man_p, "--text", text, "--sidecar", exp["sidecar"]["path"]])
    if rc != 0:
        mark(led, "import", "failed", reason="seg_import 退出码 %d" % rc)
        save_ledger(led)
        return rc
    imported = os.path.join(OUTDIR, args.name + ".imported.json")
    mark(led, "import", "ok", imported=imported, source=text)
    save_ledger(led)
    print("[adopt] import OK: %s" % imported)
    return 0


# ------------------------------------------------------------------ 阶段 4: inject
def stage_inject(args):
    led = load_ledger(args.name)
    if not guard(led, "inject", args.force):
        return 1
    exp = led["stages"]["export"]
    imported = led["stages"]["import"]["imported"]
    fp = args.fp or exp.get("doc_fp")
    if not fp:
        return die("本篇没有文档指纹(export 时未探到)。请显式 --fp 指定, "
                   "否则 seg_inject 会静默回落 Cactaceae 默认值 -> UPDATE 命中 0 行")
    base = ["--imported", imported, "--manifest", exp["manifest"],
            "--sidecar", exp["sidecar"]["path"], "--fp", fp]
    if args.shift:
        base += ["--shift", args.shift]

    print("[adopt] 第 1 步: dry 演算 (%s)" % fp)
    rc, _ = run_tool("seg_inject.py", base + ["--dry"])
    if rc != 0:
        mark(led, "inject", "failed", reason="dry 演算 FAIL", fp=fp)
        save_ledger(led)
        return die("dry 演算 FAIL —— 未写库。修好(如 --shift)后重跑")
    if args.dry:
        print("[adopt] --dry: 到此为止, 未写库")
        return 0

    print("[adopt] 第 2 步: 实写 (seg_inject 自己会先备份 cache.v1.db)")
    rc, out = run_tool("seg_inject.py", base, capture=True)
    if rc != 0:
        mark(led, "inject", "failed", reason="实写退出码 %d" % rc)
        save_ledger(led)
        return rc
    rows = sum(int(m) for m in re.findall(r"已更新 (\d+) 行", out))
    bak = re.findall(r"备份: (\S+)", out)
    mark(led, "inject", "ok", fp=fp, rows=rows,
         backup=bak[-1] if bak else None, shift=args.shift or None)
    save_ledger(led)
    print("[adopt] inject OK: 更新 %d 行" % rows)
    return 0


# -------------------------------------------------------------- 阶段 7: rollback
def stage_rollback(args):
    """[v28.23] 撤销骨架行 —— 两趟回路半途失败时的**止损坏**步骤。

    暂停档第一趟会按最终键形态落一批 raw→raw 骨架行(它们的存在是必需的: 回灌
    工具 seg_inject 是 UPDATE-only, 没行可改就直接 FAIL)。一旦回路半途失败
    (导出失败 / 交件被拒 / 注入 dry FAIL / 等不到交稿), 这些行必须在"回落成
    正常翻译"**之前**删掉 —— 否则第二趟命中它们, 整篇出英文 PDF。
    作用域两级(seg_inject 侧实现): 有 manifest 就按**本篇载荷段原文**逐段限,
    否则退化为"这份侧车里的全部段"; 再叠一层 `translation == original_text`。
    """
    led = load_ledger(args.name)
    exp = led["stages"].get("export") or {}
    fp = args.fp or exp.get("doc_fp") or ""
    sidecar = (exp.get("sidecar") or {}).get("path") or ""
    manifest = exp.get("manifest") or ""
    # [v28.23] 导出阶段就失败时, 台账里**没有** export 条目, 但那批骨架行已经落库
    # (第一趟跑完才轮到导出)。没有作用域时 seg_inject 只能退回全局 latest.jsonl 的
    # 全部段 —— 而那是"最近渲染过的那一篇": 并发的另一篇一开始渲染, 这份就被顶掉,
    # 于是"撤不掉 -> 回落成机器翻译命中骨架行 -> 整篇英文 PDF", 正是本机制要防的
    # 那一种损坏。故这里补一条**确定性**来源: --pdf 指向的按文档归档件(v28.9 起
    # converter 每篇必落 pdf-<md5>.jsonl)。
    if not sidecar and getattr(args, "pdf", ""):
        sidecar = archived_sidecar(args.pdf) or ""
        fp = fp or sidecar_doc_fp(sidecar)
    # 首选"段原文"作用域: 它不依赖指纹能不能探到(没有文档摘要的短件探不到),
    # 且范围就是本篇载荷那几段, 比 LIKE 指纹更紧。
    argv = ["--rollback"]
    if fp:
        argv += ["--fp", fp]
    if sidecar and os.path.exists(sidecar):
        argv += ["--sidecar", sidecar]
        if manifest and os.path.exists(manifest):
            argv += ["--manifest", manifest]
    rc, _ = run_tool("seg_inject.py", argv)
    if rc != 0:
        mark(led, "rollback", "failed", fp=fp, rc=rc)
        save_ledger(led)
        return rc
    mark(led, "rollback", "ok", fp=fp)
    save_ledger(led)
    print("[adopt] rollback OK: 骨架行已撤 (%s)" % (fp or "按段原文"))
    return 0


# ------------------------------------------------------------------ 阶段 5: render
def stage_render(args):
    led = load_ledger(args.name)
    if not guard(led, "render", args.force):
        return 1
    pdf = os.path.abspath(args.pdf)
    if not os.path.exists(pdf):
        return die("找不到原文: %s" % pdf)

    # [自研补丁 2026-09-19] 渲染前内容预检: 把「文献区禁汉化」这道闸门从阶段 6
    # (出 PDF 之后) 前移到 render 之前。判据与 post_check 同源(共用
    # post_check.check_ref_zh), 只是文本来源换成 imported.json。
    # 为什么值得前移: 重出 PDF 要占服务端一轮。判 FAIL 就不渲染, 那一轮直接省掉;
    # 否则就是用户说的"结果不好 -> 删掉 -> 重新生成"。实测 5 篇历史 FAIL 全属此类。
    imp = (led["stages"].get("import") or {}).get("imported")
    if not imp or not os.path.exists(imp):
        if not args.force:
            return die("找不到回锚译文 (%s), 无法做渲染前预检; 请先跑 "
                       "`adopt.py import` (运维单步重跑可用 --force 跳过)"
                       % (imp or "台账未记录"))
        print("[adopt] --force: 跳过渲染前预检 (无 imported.json)")
    else:
        # 预检只比对 imported.json 里**有译文的那几页**, 末 N 页保留原文的页本来
        # 就不在里面, 所以无需把 skip_last 传下去 (pre_render_check 也没这个参数)。
        rc_c, _ = run_tool("pre_render_check.py",
                           ["--original", pdf, "--imported", imp], capture=True)
        if rc_c != 0:
            mark(led, "render", "failed", pdf=pdf,
                 pre_render="FAIL", reason="渲染前预检: 文献区汉化")
            save_ledger(led)
            return die("渲染前预检 FAIL —— 文献区被汉化, **未渲染**。"
                       "修法: 让豆包照质检报告把文献条目还原成原文后重交, "
                       "再重走 import -> inject")
        print("[adopt] 渲染前预检 PASS")

    rc, out = run_tool("force_rerender.py",
                       ["--pdf", pdf]
                       + (["--skip-last", str(args.skip_last)] if args.skip_last else []),
                       capture=True)
    if rc != 0:
        mark(led, "render", "failed", reason="force_rerender 退出码 %d" % rc, pdf=pdf)
        save_ledger(led)
        return rc
    # [自研补丁 2026-09-19] 产物路径按**整行剩余**解析: 原文文件名可能带空格
    # (实测 "Wang 等 - 2026 - Differentially Private Consensus for Time-Delay
    # Multi-agent Systems.pdf"), 原来的 \S+ 只能吃到空格后的最后一段, 台账把
    # mono 记成 "Systems-mono.pdf" -> gate 阶段拿着个不存在的路径直接拒绝执行。
    # 另外优先取 -mono.pdf: gate 的 verify_render/post_check 要的就是 mono 译文。
    prods = [p.strip() for p in re.findall(r"(?m)^\s*产物:\s*(.+\.pdf)\s*$", out)]
    secs = re.findall(r"耗时 (\d+) 秒", out)
    mono = [p for p in prods if p.lower().endswith("-mono.pdf")]
    prod = mono[-1] if mono else (prods[0] if prods else None)
    if prod is None:
        print("[adopt] 警告: 未从输出解析到产物路径; gate 阶段将回落到'最新 mono'")
    mark(led, "render", "ok", pdf=pdf, mono=prod,
         seconds=int(secs[0]) if secs else None, pre_render="PASS",
         skip_last=int(args.skip_last))
    save_ledger(led)
    print("[adopt] render OK: %s" % prod)
    return 0


# ------------------------------------------------------------------ 阶段 6: gate
def latest_mono():
    d = os.path.join(PROJ, "server", "translated")
    ms = glob.glob(os.path.join(d, "*-mono.pdf"))
    return max(ms, key=os.path.getmtime) if ms else None


def stage_gate(args):
    led = load_ledger(args.name)
    if not guard(led, "gate", args.force):
        return 1
    if not args.expect:
        return die("gate 必须至少给一条 --expect (期望落页的新串); "
                   "否则无法证明'缓存改了且真落到了页上'")
    mono = args.mono or led["stages"]["render"].get("mono") or latest_mono()
    if not mono or not os.path.exists(mono):
        return die("找不到 mono 译文 PDF; 用 --mono 指定")
    print("[adopt] 验收对象: %s" % mono)

    # [自研补丁 2026-09-19] skip_last 缺省沿用 **render 阶段实际用的值**。原先 gate
    # 只有 --skip-last 且缺省 0: 整篇翻(0)的 run 没事, 但末 N 页保留原文的 run 会
    # 被判"文献区汉化 第16,17页(29+14条)" —— 那两页本来就该保持英文, 是 render
    # 参数, 不是译文缺陷。实测 Zhang 2026 (17 页, pre_check 推荐 skipLastPages=3)。
    skip_last = args.skip_last
    if skip_last is None:
        skip_last = int((led["stages"].get("render") or {}).get("skip_last") or 0)
        print("[adopt] skip_last 缺省沿用 render 台账值: %d" % skip_last)

    va = ["--pdf", mono, "--expect"] + list(args.expect)
    if args.forbid:
        va += ["--forbid"] + list(args.forbid)
    rc_v, _ = run_tool("verify_render.py", va)

    pa = [mono]
    if skip_last:
        pa += ["--skip-last", str(skip_last)]
    rc_p, _ = run_tool("post_check.py", pa)

    ok = (rc_v == 0 and rc_p == 0)
    mark(led, "gate", "ok" if ok else "failed", mono=mono,
         verify="PASS" if rc_v == 0 else "FAIL",
         post_check="PASS" if rc_p == 0 else "FAIL",
         expect=list(args.expect), forbid=list(args.forbid), skip_last=skip_last)
    save_ledger(led)
    if ok:
        print("[adopt] gate OK —— 本 run 交付完成 (%s)" % args.name)
        return 0
    return die("验收未过: verify_render=%s post_check=%s"
               % ("PASS" if rc_v == 0 else "FAIL", "PASS" if rc_p == 0 else "FAIL"))


# ------------------------------------------------------------------ status
def stage_status(args):
    os.makedirs(LEDGER_DIR, exist_ok=True)
    if not args.name:
        files = sorted(glob.glob(os.path.join(LEDGER_DIR, "*.json")))
        if not files:
            print("[adopt] 台账为空: %s" % LEDGER_DIR)
            return 0
        print("[adopt] 台账 %d 份 (%s)" % (len(files), LEDGER_DIR))
        for p in files:
            led = load_ledger(os.path.basename(p)[:-5])
            cells = []
            for s in STAGES:
                st = (led["stages"].get(s) or {}).get("state")
                cells.append("%s:%s" % (s, st or "-"))
            print("  %-24s %s" % (led["name"], "  ".join(cells)))
        return 0

    led = load_ledger(args.name)
    print("[adopt] run=%s  created=%s  updated=%s" % (led["name"], led["created"], led["updated"]))
    for s in STAGES:
        rec = led["stages"].get(s)
        if not rec:
            print("  %-8s %s" % (s, "未执行"))
            continue
        extra = {k: v for k, v in rec.items() if k not in ("state", "at")}
        print("  %-8s %-6s %s  %s" % (s, rec["state"], rec["at"],
                                      json.dumps(extra, ensure_ascii=False)[:180]))
    return 0


# ------------------------------------------------------------------ main
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="豆包采纳管线「必经」入口 (方案 A)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--name", required=True, help="run 名 = payload 名 (不含扩展名)")
        p.add_argument("--force", action="store_true", help="跳过顺序门禁(单步重跑用)")

    p = sub.add_parser("export", help="1 侧车 -> inbox payload")
    common(p)
    p.add_argument("--pages", required=True, help="如 2-4 或 1,21-22")
    p.add_argument("--sidecar", default=SIDECAR,
                   help="缺省 latest.jsonl; 给了 --pdf 就按文档自动认领归档件")
    p.add_argument("--pdf", default="",
                   help="原文 PDF 绝对路径; 按文档(md5)自动认领该篇侧车, 免得手工归档")
    p.add_argument("--doc", default="", help='文档抬头(写进 payload 首行), 如 "标题 (期刊, 年份)"')
    p.add_argument("--terms", default="", help="术语表 csv; 缺省用 seg_export 默认(server/glossary/terms.csv)")
    p.set_defaults(fn=stage_export)

    p = sub.add_parser("deliver", help="2 登记豆包交件 (段号守恒 + 留空/半截/错位/不变量/⋮ 内容门禁)")
    common(p)
    p.add_argument("--text", default="", help="交件文件; 缺省自动找 out/<name>.doubao*.txt")
    p.add_argument("--clip", action="store_true", help="从剪贴板取并落盘为 out/<name>.doubao.txt")
    p.add_argument("--waive", action="store_true",
                   help="[2026-09-20] 显式放行内容门禁不符(留痕于台账+报告)。"
                        "只给「确认过的合理改写」用; --force 跳的是顺序门禁, 不跳内容门禁")
    p.set_defaults(fn=stage_deliver)

    p = sub.add_parser("import", help="3 回锚 -> imported.json")
    common(p)
    p.set_defaults(fn=stage_import)

    p = sub.add_parser("inject", help="4 写缓存库 (先 dry 演算)")
    common(p)
    p.add_argument("--shift", default="", help='合并段断点移位, 如 "S4:1"')
    p.add_argument("--fp", default="", help="覆盖台账里的文档指纹(缺省用 export 探到的)")
    p.add_argument("--dry", action="store_true", help="只演算不写库")
    p.set_defaults(fn=stage_inject)

    p = sub.add_parser("rollback", help="7 [v28.23] 撤销骨架行(回路半途失败时止损坏)")
    common(p)
    p.add_argument("--fp", default="", help="文档指纹; 缺省用台账里 export 记下的")
    p.add_argument("--pdf", default="",
                   help="原文 PDF; 台账里没有 export 条目(导出阶段就失败)时靠它定位"
                        "按文档归档的侧车, 避免退回全局 latest.jsonl 误伤别的论文")
    p.set_defaults(fn=stage_rollback)

    p = sub.add_parser("render", help="5 重渲染落产物")
    common(p)
    p.add_argument("--pdf", required=True, help="原文 PDF 绝对路径")
    p.add_argument("--skip-last", type=int, default=0,
                   help="末尾保留页数(原样不译); 与 pre_check 推荐的 skipLastPages 同口径。"
                        "缺省 0=整篇翻。台账会记下它, gate 阶段缺省沿用")
    p.set_defaults(fn=stage_render)

    p = sub.add_parser("gate", help="6 渲染验收 + 翻译质检 (双 PASS)")
    common(p)
    # action="extend" 不可省: 纯 nargs="+" 时 argparse 对**重复出现**的选项是
    # "后者覆盖前者"(不是追加), 于是 `--expect A --expect B --expect C` 只剩 C,
    # 验收静默变成只查一条 —— 实测 2026-09-19 CLAP 收口出现 "通过 1 / 共 1"。
    # extend 让 `--expect A B` 与 `--expect A --expect B` 两种写法都累加。
    p.add_argument("--expect", nargs="+", action="extend", default=[], metavar="TEXT",
                   help="期望落页的新串 (必须至少一条; 缺了无法证明'改了缓存且真落到页上');"
                        " 可写 --expect A B 或 --expect A --expect B, 两者等价且可混用")
    p.add_argument("--forbid", nargs="*", action="extend", default=[],
                   help="期望消失的旧串 (同样支持重复出现累加)")
    p.add_argument("--mono", default="", help="mono 译文; 缺省取 render 登记的产物")
    p.add_argument("--skip-last", type=int, default=None,
                   help="末尾保留页豁免数; 缺省沿用 render 阶段实际用的值(原本缺省 0, "
                        "整篇翻的 run 传了 --skip-last 也会被当成没跳页)")
    p.set_defaults(fn=stage_gate)

    p = sub.add_parser("status", help="看台账 / 列出全部 run")
    p.add_argument("--name", default="", help="缺省列出全部")
    p.set_defaults(fn=stage_status)

    args = ap.parse_args()
    if hasattr(args, "name") and args.name and not SAFE_NAME.match(args.name):
        return die("非法 run 名: %r (仅限字母数字-_ . 与空格)" % args.name)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
