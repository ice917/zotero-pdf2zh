# -*- coding: utf-8 -*-
"""adopt.py — 豆包采纳管线「必经」入口 (方案 A, 2026-09-19)

为什么需要它:
  方案 A 把豆包软件放在翻译管线的**外部**, 五道工序是散的脚本, 顺序靠人记。
  实测踩过的坑全在"顺序/身份"上, 而不是在某个工序内部:
    - 侧车 latest.jsonl 是**全局单文件**, 换论文会覆盖 -> 导出的 payload 与
      注入时的侧车不是同一篇 -> 回锚错位 (seg_export/seg_inject 的 --sidecar
      帮助都写着"新论文请先归档", 但那只是句提醒, 没人拦你);
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
  3 import   seg_import 回锚 -> out/<name>.imported.json (必须 PASS)
  4 inject   seg_inject 写库 (先用 --dry 演算, PASS 才实写)
  5 render   force_rerender 落产物 PDF
             —— 但**先跑渲染前预检** (pre_render_check: 文献区禁汉化)。
                这道闸门原本只在阶段 6 的 post_check 里, 也就是"PDF 出来了才
                发现文献区被汉化", 得删掉重出。判据同源, 只是文本来源从 mono
                PDF 换成 imported.json —— 前移之后那一轮服务端渲染直接省掉。
  6 gate     verify_render(--expect) + post_check 双 PASS 才算交付

用法 (解释器同 tools/tests/run_all.py):
  PY = D:/Users/<user>/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  & $PY tools/adopt.py export --name payload_p2_p4 --pages 2-4 --doc "标题 (期刊, 年份)"
  & $PY tools/adopt.py deliver --name payload_p2_p4 --text "D:/.../p2_p4.doubao.txt"
  & $PY tools/adopt.py import  --name payload_p2_p4
  & $PY tools/adopt.py inject  --name payload_p2_p4
  & $PY tools/adopt.py render  --name payload_p2_p4 --pdf "D:/.../xxx.pdf"
  & $PY tools/adopt.py gate    --name payload_p2_p4 --expect "雄花具有长花柱"
  & $PY tools/adopt.py status

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
DEFAULT_PY = r"D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\python.exe"
PY = os.environ.get("PDF2ZH_PYTHON") or (DEFAULT_PY if os.path.exists(DEFAULT_PY) else sys.executable)

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


def stage_export(args):
    led = load_ledger(args.name)
    if not guard(led, "export", args.force):
        return 1
    if not os.path.exists(args.sidecar):
        return die("侧车不存在: %s (先跑一次翻译, 或 --sidecar 指向归档件)" % args.sidecar)
    if (led["stages"].get("export") or {}).get("state") == "ok" and not args.force:
        return die("本 run 已导出过; 重做请加 --force (会覆盖 inbox 同名件)")

    rc, _ = run_tool("seg_export.py",
                     ["--pages", args.pages, "--name", args.name, "--sidecar", args.sidecar]
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
    pages = load_sidecar_pages(args.sidecar)
    hit, fp = probe_db(man, pages)
    if hit == 0:
        mark(led, "export", "failed", reason="库内 0 命中")
        save_ledger(led)
        return die("库内查不到本次任何段落原文 —— 侧车与缓存库不是同一篇(或没翻过)。"
                   "请先跑翻译再导出; 若已翻译, 用 --sidecar 指向该篇归档的 latest.jsonl")
    if fp is None:
        print("[adopt] 警告: 探测不到文档摘要指纹(库内行不带 docsummary fp)。")
        print("        该篇无法用文档作用域注入 —— inject 阶段必须显式 --fp, 否则会误用默认值。")

    mark(led, "export", "ok", pages=args.pages, sidecar=sidecar_id(args.sidecar),
         doc_fp=fp, db_hits=hit, n_items=len(man["items"]),
         txt=os.path.join(INBOX, args.name + ".txt"), manifest=man_p)
    save_ledger(led)
    print("[adopt] export OK: %d 段 / 库内命中 %d 行 / doc_fp=%s" % (len(man["items"]), hit, fp))
    print("[adopt] 下一步: 把 inbox/%s.txt 交给豆包软件定稿, 再跑 deliver" % args.name)
    return 0


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
    bad = []
    for k, it in sorted(want.items()):
        n_parts = len(it["parts"])
        n_cut = got[k].count("⋮")
        if it["merged"] and n_cut != n_parts - 1:
            bad.append("#S%d 断点 %d != 预期 %d" % (k, n_cut, n_parts - 1))
        elif not it["merged"] and n_cut:
            bad.append("#S%d 普通段出现 ⋮" % k)
    if bad:
        mark(led, "deliver", "failed", reason="分页断点不符", detail=bad[:20])
        save_ledger(led)
        for b in bad[:20]:
            print("[adopt]   " + b)
        return die("⋮ 分页断点对账不符, 拒绝进入回锚")

    mark(led, "deliver", "ok", file=path, sha1=sha1_8(path),
         n_seg=len(got), chars=len(text))
    save_ledger(led)
    print("[adopt] deliver OK: %d 段 / %d 字符 / sha1=%s" % (len(got), len(text), sha1_8(path)))
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

    rc, out = run_tool("force_rerender.py", ["--pdf", pdf], capture=True)
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
         seconds=int(secs[0]) if secs else None, pre_render="PASS")
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

    va = ["--pdf", mono, "--expect"] + list(args.expect)
    if args.forbid:
        va += ["--forbid"] + list(args.forbid)
    rc_v, _ = run_tool("verify_render.py", va)

    pa = [mono]
    if args.skip_last:
        pa += ["--skip-last", str(args.skip_last)]
    rc_p, _ = run_tool("post_check.py", pa)

    ok = (rc_v == 0 and rc_p == 0)
    mark(led, "gate", "ok" if ok else "failed", mono=mono,
         verify="PASS" if rc_v == 0 else "FAIL",
         post_check="PASS" if rc_p == 0 else "FAIL",
         expect=list(args.expect), forbid=list(args.forbid))
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
    p.add_argument("--sidecar", default=SIDECAR, help="缺省 latest.jsonl; 新论文先用归档件")
    p.add_argument("--doc", default="", help='文档抬头(写进 payload 首行), 如 "标题 (期刊, 年份)"')
    p.add_argument("--terms", default="", help="术语表 csv; 缺省用 seg_export 默认(server/glossary/terms.csv)")
    p.set_defaults(fn=stage_export)

    p = sub.add_parser("deliver", help="2 登记豆包交件 (段号守恒门禁)")
    common(p)
    p.add_argument("--text", default="", help="交件文件; 缺省自动找 out/<name>.doubao*.txt")
    p.add_argument("--clip", action="store_true", help="从剪贴板取并落盘为 out/<name>.doubao.txt")
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

    p = sub.add_parser("render", help="5 重渲染落产物")
    common(p)
    p.add_argument("--pdf", required=True, help="原文 PDF 绝对路径")
    p.set_defaults(fn=stage_render)

    p = sub.add_parser("gate", help="6 渲染验收 + 翻译质检 (双 PASS)")
    common(p)
    p.add_argument("--expect", nargs="+", default=[], metavar="TEXT",
                   help="期望落页的新串 (必须至少一条; 缺了无法证明'改了缓存且真落到页上')")
    p.add_argument("--forbid", nargs="*", default=[], help="期望消失的旧串")
    p.add_argument("--mono", default="", help="mono 译文; 缺省取 render 登记的产物")
    p.add_argument("--skip-last", type=int, default=0, help="末尾保留页豁免数")
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
