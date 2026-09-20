# -*- coding: utf-8 -*-
"""adopt 采纳管线「必经」入口单元测试 (2026-09-19)

锁死的是**顺序与身份判据**, 不是某个工序的内部算法 (那由 test_seg_reanchor 等负责):
  ① 顺序门禁: 六阶段各自的前置都必须 state=="ok"
  ② --force 留痕
  ③ export: 库内 0 命中 -> 拦 (防"侧车与缓存库不是同一篇"白跑人工环节)
  ④ export: 探到的文档指纹写进台账 (防 inject 静默回落 Cactaceae 默认值)
  ⑤ deliver: 段号守恒 (缺段/多段/⋮ 断点数) + 交件多份歧义 + 失败专属字段不残留 +
     自动认领交件(不给 --text)必须把正文读进来 (曾漏读 -> UnboundLocalError)
  ⑥ import: 侧车指纹变了 -> 拦 (latest.jsonl 是全局单文件, 换论文会覆盖)
  ⑦ inject: 台账无 fp 且未 --fp -> 拦; 有 fp -> 先 dry 后实写, 参数透传
  ⑧ render: 从子进程输出解析产物路径与耗时 (文件名可含空格; 双/单语两个产物优先 mono)
  ⑨ gate: 缺 --expect -> 拦; 双 PASS / post_check FAIL 的两种落账
  ⑩ 台账原子写, 且 failed 只覆盖本阶段 (不抹掉已 ok 的前序阶段)
  ⑪ export 侧车认领 (v28.9): --pdf -> 按文档散列取归档件; 取不到**拒绝**而非回落
     latest.jsonl (拿错侧车 = 导出别人的载荷, 下游回锚还会 PASS, 是最难发现的静默错)
  ㉒ render/gate 的"末 N 页保留原文" (v28.14): gate 缺省沿用 render 实际用的 skip_last
  ㉓ gate 的 --expect/--forbid **重复出现必须累加** (v28.22): 纯 nargs="+" 时 argparse
     是"后者覆盖前者", `--expect A --expect B` 只剩 B -> 门禁报 PASS 却只验了 1/N
     (比 FAIL 更危险的静默漏验)。修法 action="extend"。

运行: venv python test_adopt.py, 退出码 0=全过
"""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import adopt as AD  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="adopt_test_")
FAKE = {"calls": [], "manifest": None, "rc": {}, "out": {}}


def _raw(tag, n=90):
    """造一条够长、且能唯一定位到缓存行的原文。"""
    s = ("%s lorem ipsum dolor sit amet consectetur " % tag)
    while len(s) < n:
        s += s
    return s[:max(n, len(s))]


def write_sidecar(path, pages):
    with open(path, "w", encoding="utf-8") as f:
        for pg, segs in sorted(pages.items()):
            f.write(json.dumps({"page": pg, "segs": segs, "vars": {}}, ensure_ascii=False) + "\n")


def fake_run_tool(script, args, capture=False):
    FAKE["calls"].append((script, list(args)))
    if script == "seg_export.py":
        name = args[args.index("--name") + 1]
        with open(os.path.join(AD.INBOX, name + ".manifest.json"), "w", encoding="utf-8") as f:
            json.dump(FAKE["manifest"], f, ensure_ascii=False)
        return 0, ""
    if script == "seg_import.py":
        man_p = args[args.index("--manifest") + 1]
        name = os.path.basename(man_p).replace(".manifest.json", "")
        with open(os.path.join(AD.OUTDIR, name + ".imported.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"1#0": "x"}, f)
        return FAKE["rc"].get(script, 0), ""
    return FAKE["rc"].get(script, 0), FAKE["out"].get(script, "")


def run(argv):
    """以真实 CLI 跑一次 adopt, 返回 (退出码, 捕获的输出)。"""
    buf, old = io.StringIO(), sys.argv
    sys.argv = ["adopt.py"] + argv
    try:
        with contextlib.redirect_stdout(buf):
            rc = AD.main()
    finally:
        sys.argv = old
    return rc, buf.getvalue()


def main():
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    # ---- 沙箱: 常量换成临时目录 (整轮共用, 各 case 用不同 run 名隔离) ----
    AD.PROJ = ROOT
    AD.INBOX = os.path.join(ROOT, "inbox")
    AD.OUTDIR = os.path.join(ROOT, "out")
    AD.LEDGER_DIR = os.path.join(ROOT, "logs", "adopt")
    AD.SIDECAR = os.path.join(ROOT, "latest.jsonl")
    AD.CACHE = os.path.join(ROOT, "cache.v1.db")
    # 门禁拒收报告也落在 review/ —— 不换掉的话测试会把报告写进**真项目**的
    # server/translated/review/ 里, 污染给人看的目录。
    AD.REVIEW = os.path.join(ROOT, "review")
    AD.run_tool = fake_run_tool
    for d in (AD.INBOX, AD.OUTDIR, AD.LEDGER_DIR, AD.REVIEW):
        os.makedirs(d, exist_ok=True)

    pages = {1: [{"raw": _raw("p1s0"), "trans": "t"},
                 {"raw": _raw("p1s1"), "trans": "t"},
                 {"raw": _raw("p1s2"), "trans": "t"}],
             2: [{"raw": _raw("p2s0"), "trans": "t"},
                 {"raw": _raw("p2s1"), "trans": "t"}],
             3: [{"raw": _raw("p3s0_untranslated"), "trans": ""}]}
    write_sidecar(AD.SIDECAR, pages)

    con = sqlite3.connect(AD.CACHE)
    con.execute("CREATE TABLE _translationcache (id INTEGER PRIMARY KEY, original_text TEXT,"
                " translation TEXT, translate_engine_params TEXT)")
    for pg in (1, 2):                       # 页 3 故意不入库 -> 用于"0 命中"case
        for s in pages[pg]:
            con.execute("INSERT INTO _translationcache VALUES (NULL,?,?,?)",
                        (s["raw"], s["trans"], '{"service":"silicon",'
                         '"doc_summary_fp":"docsummary:deadbeef00:cafe"}'))
    con.commit()
    con.close()

    def mk_manifest(name, parts):
        return {"name": name, "pages": "1", "items": [
            {"key": "S%d" % i, "merged": len(p) > 1,
             "parts": [{"page": a, "seg": b} for a, b in p]}
            for i, p in enumerate(parts, 1)]}

    def reset(manifest=None):
        FAKE["calls"], FAKE["manifest"] = [], manifest
        FAKE["rc"], FAKE["out"] = {}, {}
        return manifest

    # ① 顺序门禁: 每个阶段都被它的前置拦住
    ok_all = True
    for st in AD.STAGES[1:]:
        with contextlib.redirect_stdout(io.StringIO()):
            got = AD.guard({"name": "x", "stages": {}}, st, False)
        ok_all = ok_all and (got is False)
    check("① 六阶段顺序门禁 (缺前置一律 False)", ok_all)

    # ② --force 留痕, 且不重复登记
    l2 = {"name": "x", "stages": {}}
    with contextlib.redirect_stdout(io.StringIO()):
        AD.guard(l2, "render", True)
        AD.guard(l2, "render", True)
    check("② --force 记 forced_stages 且不重复", l2["forced_stages"] == ["render"], l2)

    # ③ export: 库内 0 命中 -> 拦
    reset(mk_manifest("c3", [[(3, 0)]]))
    rc, out = run(["export", "--name", "c3", "--pages", "3"])
    l3 = AD.load_ledger("c3")
    check("③ export 库内 0 命中被拦", rc == 1 and l3["stages"]["export"]["state"] == "failed",
          (rc, l3["stages"].get("export")))
    check("③ 报错指向侧车/库不一致", "不是同一篇" in out, out[-200:])

    # ④ export: 命中 -> ok, 且指纹落台账
    reset(mk_manifest("c4", [[(1, 0)], [(1, 1)]]))
    rc, _ = run(["export", "--name", "c4", "--pages", "1"])
    l4 = AD.load_ledger("c4")
    e4 = l4["stages"]["export"]
    check("④ export ok 且区块完整", rc == 0 and e4["state"] == "ok" and e4["n_items"] == 2, e4)
    check("④ 文档指纹已登记", e4["doc_fp"] == "docsummary:deadbeef00:cafe", e4.get("doc_fp"))
    check("④ 侧车指纹已登记", e4["sidecar"]["sha1"] and e4["sidecar"]["size"] > 0, e4.get("sidecar"))

    # ⑤ deliver: 段号守恒
    reset(mk_manifest("c5", [[(1, 0)], [(1, 1)], [(1, 2)]]))
    run(["export", "--name", "c5", "--pages", "1"])
    p5 = os.path.join(AD.OUTDIR, "c5.doubao.txt")
    with open(p5, "w", encoding="utf-8") as f:
        f.write("#S1\n甲\n#S2\n乙\n")                      # 缺 #S3
    rc, out = run(["deliver", "--name", "c5", "--text", p5])
    l5 = AD.load_ledger("c5")
    check("⑤ deliver 缺段被拦", rc == 1 and l5["stages"]["deliver"]["state"] == "failed", rc)
    check("⑤ 缺段号报告准确", l5["stages"]["deliver"]["missing"] == [3],
          l5["stages"]["deliver"])

    # ⑤b 失败专属字段不残留: failed 时写的 reason/missing, 阶段转 ok 后必须消失
    # (mark 是 update, 不剔除就会在台账里留下 "state:ok, reason:退出码1" 的自相矛盾)
    with open(p5, "w", encoding="utf-8") as f:
        f.write("#S1\n甲\n#S2\n乙\n#S3\n丙\n")
    rc, _ = run(["deliver", "--name", "c5", "--text", p5])
    d5 = AD.load_ledger("c5")["stages"]["deliver"]
    check("⑤b 转 ok 后 reason/missing 不残留",
          rc == 0 and d5["state"] == "ok" and "reason" not in d5 and "missing" not in d5, d5)

    # ⑥ deliver: ⋮ 断点数不符 (合并段) + 多份候选歧义
    reset(mk_manifest("c6", [[(1, 0), (2, 0)]]))
    run(["export", "--name", "c6", "--pages", "1"])
    p6 = os.path.join(AD.OUTDIR, "c6.doubao.txt")
    with open(p6, "w", encoding="utf-8") as f:
        f.write("#S1\n甲\n")                               # 合并段应有 1 个 ⋮, 这里 0 个
    rc, out = run(["deliver", "--name", "c6", "--text", p6])
    check("⑥ deliver 合并段 ⋮ 数不符被拦", rc == 1 and "⋮" in out, out[-200:])

    reset(mk_manifest("c7", [[(1, 0)]]))
    run(["export", "--name", "c7", "--pages", "1"])
    for suf in (".txt", "2.txt"):
        with open(os.path.join(AD.OUTDIR, "c7.doubao" + suf), "w", encoding="utf-8") as f:
            f.write("#S1\n甲\n")
    rc, out = run(["deliver", "--name", "c7"])
    check("⑦ 交件多份 -> 要求显式指定", rc == 1 and "不唯一" in out, out[-200:])

    # ⑦b deliver 自动认领交件(不给 --text) 必须把正文读进来
    #     回归: 这条路曾漏了读文件, text 未绑定 -> UnboundLocalError (2026-09-19 Melhani 实测),
    #     段号守恒/断点对账两道门禁全部吃 text, 读不到就等于整条 deliver 不可用
    reset(mk_manifest("c7b", [[(1, 0)], [(1, 1)]]))
    run(["export", "--name", "c7b", "--pages", "1"])
    with open(os.path.join(AD.OUTDIR, "c7b.doubao.txt"), "w", encoding="utf-8") as f:
        f.write("#S1\n甲\n#S2\n乙\n")
    rc, out = run(["deliver", "--name", "c7b"])
    d7b = AD.load_ledger("c7b")["stages"].get("deliver", {})
    check("⑦b 自动认领单份交件 -> 读入正文并通过段号守恒",
          rc == 0 and d7b.get("state") == "ok" and d7b.get("n_seg") == 2,
          (rc, d7b, out[-200:]))

    # ⑧ deliver 通过 + import 侧车被改写 -> 拦
    reset(mk_manifest("c8", [[(1, 0)], [(1, 1)]]))
    run(["export", "--name", "c8", "--pages", "1"])
    p8 = os.path.join(AD.OUTDIR, "c8.doubao.txt")
    with open(p8, "w", encoding="utf-8") as f:
        f.write("#S1\n甲\n#S2\n乙\n")
    rc, _ = run(["deliver", "--name", "c8", "--text", p8])
    l8 = AD.load_ledger("c8")
    check("⑧ deliver ok 记录段数/指纹",
          rc == 0 and l8["stages"]["deliver"]["n_seg"] == 2 and l8["stages"]["deliver"]["sha1"],
          l8["stages"].get("deliver"))
    write_sidecar(AD.SIDECAR, pages)                       # 侧车被"新论文"覆盖
    pages[2].append({"raw": _raw("intruder"), "trans": "t"})
    write_sidecar(AD.SIDECAR, pages)
    rc, out = run(["import", "--name", "c8"])
    check("⑨ 侧车被改写 -> import 被拦", rc == 1 and "侧车已被改写" in out, out[-200:])
    pages[2].pop()
    write_sidecar(AD.SIDECAR, pages)                       # 还原, 供后续 case 使用

    # ⑩ import ok
    reset()
    rc, _ = run(["import", "--name", "c8"])
    l8 = AD.load_ledger("c8")
    check("⑩ import ok", rc == 0 and l8["stages"]["import"]["state"] == "ok", l8["stages"].get("import"))

    # ⑪ inject: 台账无 fp 且未 --fp -> 拦 (防静默回落 Cactaceae 默认值)
    reset(mk_manifest("c11", [[(1, 0)]]))
    run(["export", "--name", "c11", "--pages", "1"])
    l11 = AD.load_ledger("c11")
    l11["stages"]["export"]["doc_fp"] = None               # 模拟探测不到
    l11["stages"]["import"] = {"state": "ok", "imported": os.path.join(AD.OUTDIR, "c11.imported.json")}
    AD.save_ledger(l11)
    rc, out = run(["inject", "--name", "c11"])
    check("⑪ inject 无 fp 被拦", rc == 1 and "Cactaceae" in out, out[-260:])

    # ⑫ inject: 先 dry 后实写, --fp 透传, 行数/备份落账
    reset()
    FAKE["out"]["seg_inject.py"] = "备份: /tmp/x.bak-1\n5#1: 3 字 -> 2 行\n  已更新 2 行\n"
    rc, _ = run(["inject", "--name", "c8"])
    calls = [a for (s, a) in FAKE["calls"] if s == "seg_inject.py"]
    l8 = AD.load_ledger("c8")
    check("⑫ inject 先 dry 后实写 (两次调用)",
          len(calls) == 2 and "--dry" in calls[0] and "--dry" not in calls[1], calls)
    check("⑫ --fp 用台账值透传", "--fp" in calls[1] and
          calls[1][calls[1].index("--fp") + 1] == "docsummary:deadbeef00:cafe",
          calls[1] if calls else None)
    check("⑫ 更新行数与备份落账",
          l8["stages"]["inject"]["rows"] == 2 and l8["stages"]["inject"]["backup"] == "/tmp/x.bak-1",
          l8["stages"].get("inject"))

    # ⑬ inject --dry 不写库 -> 状态保持未执行 (故 render 会被拦)
    l13 = AD.load_ledger("c8")
    rc, _ = run(["inject", "--name", "c8", "--dry"])
    check("⑬ --dry 只演算不落 inject ok", rc == 0 and
          AD.load_ledger("c8")["stages"]["inject"]["state"] == l13["stages"]["inject"]["state"], rc)
    # 复原 inject ok 供后续 render/gate 用
    AD.save_ledger(l13)

    # ⑭ render: 解析产物路径与耗时 (整行取值 —— 文件名可带空格; 两个产物优先取 mono)
    reset()
    mono_fake = os.path.join(ROOT, "Wang 等 - 2026 - Differentially Private Systems-mono.pdf")
    dual_fake = mono_fake[:-len("-mono.pdf")] + "-dual.pdf"
    for p in (dual_fake, mono_fake):
        with open(p, "w", encoding="utf-8") as f:
            f.write("")                                        # gate 会检查产物存在
    FAKE["out"]["force_rerender.py"] = ("提交: a.pdf\n  产物: %s\n  产物: %s\n耗时 99 秒 -> 成功\n"
                                        % (dual_fake, mono_fake))
    rc, _ = run(["render", "--name", "c8", "--pdf", __file__])
    r = AD.load_ledger("c8")["stages"]["render"]
    check("⑭ render 解析 mono 路径 (文件名含空格)", rc == 0 and r["mono"] == mono_fake, r)
    check("⑭ render 解析耗时", r["seconds"] == 99, r)

    # ⑭b [v28.35] 重渲染等待上限与 server 的"等豆包交稿"上限同源(PAUSE_WAIT_MINUTES),
    #     不再由 force_rerender 的 900 秒硬编码当家 —— 两处都在回答"这一轮我愿意等
    #     多久", 各配一套必有一处偏短(实测 81 页的稿子重渲染一轮 > 15 分钟, 超时被
    #     记成 render failed, 而任务其实还在跑)。900 秒留作下限。
    rr = [a for s, a in FAKE["calls"] if s == "force_rerender.py"]
    check("⑭b render 把 --timeout 传给 force_rerender",
          bool(rr) and "--timeout" in rr[-1], rr[-1] if rr else "没调到 force_rerender")
    _env = os.environ.get("PAUSE_WAIT_MINUTES")
    try:
        os.environ.pop("PAUSE_WAIT_MINUTES", None)
        check("⑭b 缺省 30 分 -> 1800 秒", AD.render_timeout() == 1800, AD.render_timeout())
        os.environ["PAUSE_WAIT_MINUTES"] = "5"
        check("⑭b 配得比一轮渲染还短 -> 900 秒保底",
              AD.render_timeout() == 900, AD.render_timeout())
        os.environ["PAUSE_WAIT_MINUTES"] = "60"
        check("⑭b 配 60 分 -> 3600 秒", AD.render_timeout() == 3600, AD.render_timeout())
        os.environ["PAUSE_WAIT_MINUTES"] = "abc"
        check("⑭b 坏值回落 30 分", AD.render_timeout() == 1800, AD.render_timeout())
    finally:
        os.environ.pop("PAUSE_WAIT_MINUTES", None)
        if _env is not None:
            os.environ["PAUSE_WAIT_MINUTES"] = _env

    # ⑭c [v28.35] 长阶段出门写台账只并**自己那一条**, 不能拿进门快照整份覆盖:
    #     render 跑一轮要几分钟, 期间 deliver/import/inject 若写了台账, 整份覆盖就
    #     把它们抹回旧值 —— 台账自相矛盾(render 说 failed, 而 import 明明是后写的 ok),
    #     事后按台账排查会被带偏。
    AD.save_ledger({"name": "c8m", "created": AD.now(), "updated": AD.now(),
                    "stages": {"render": {"state": "running"}}})
    stale = AD.load_ledger("c8m")                     # render 进门时的快照
    disk = AD.load_ledger("c8m")
    AD.mark(disk, "inject", "ok", rows=999)           # 期间别的阶段写了台账
    AD.save_ledger(disk)
    AD.mark(stale, "render", "ok", mono="x.pdf")
    AD.save_ledger_merge(stale, "render")
    merged = AD.load_ledger("c8m")
    check("⑭c 本阶段的改动落盘", merged["stages"]["render"].get("mono") == "x.pdf",
          merged["stages"]["render"])
    check("⑭c 期间并发写入没被抹掉", merged["stages"]["inject"].get("rows") == 999,
          merged["stages"].get("inject"))

    # ⑮ gate: 缺 --expect -> 拦
    rc, out = run(["gate", "--name", "c8"])
    check("⑮ gate 缺 --expect 被拦", rc == 1 and "落页" in out, out[-200:])

    # ⑯ gate: 双 PASS / post_check FAIL
    reset()
    FAKE["rc"]["verify_render.py"] = 0
    FAKE["rc"]["post_check.py"] = 0
    rc, _ = run(["gate", "--name", "c8", "--expect", "共识"])
    g = AD.load_ledger("c8")["stages"]["gate"]
    check("⑯ gate 双 PASS -> ok",
          rc == 0 and g["state"] == "ok" and g["verify"] == "PASS" and g["post_check"] == "PASS", g)
    FAKE["rc"]["post_check.py"] = 1
    rc, out = run(["gate", "--name", "c8", "--expect", "共识"])
    g = AD.load_ledger("c8")["stages"]["gate"]
    check("⑰ post_check FAIL -> gate failed 且 verify 仍记 PASS",
          rc == 1 and g["state"] == "failed" and g["verify"] == "PASS" and g["post_check"] == "FAIL", g)

    # ⑱ failed 不抹掉前序 ok 阶段; 台账无 .tmp 残留
    l8 = AD.load_ledger("c8")
    check("⑱ 后阶段失败不影响前序 ok",
          all(l8["stages"][s]["state"] == "ok" for s in ("export", "deliver", "import", "inject", "render")),
          {s: l8["stages"][s]["state"] for s in AD.STAGES})
    check("⑱ 台账原子写无 .tmp 残留",
          not [f for f in os.listdir(AD.LEDGER_DIR) if f.endswith(".tmp")],
          os.listdir(AD.LEDGER_DIR))

    # ⑲ 非法 run 名
    rc, out = run(["deliver", "--name", "../evil"])
    check("⑲ 非法 run 名被拒", rc == 1 and "非法 run 名" in out, out[-160:])

    check("⑳ status 总览可列出台账", run(["status"])[0] == 0)

    # ㉑ export 的侧车认领 (v28.9): 给 --pdf 就按文档散列取归档件;
    #    取不到**拒绝执行**, 不拿"最近翻过的另一篇"顶上 (那样回锚还会 PASS, 是静默错)
    pdf1 = os.path.join(ROOT, "one.pdf")
    with open(pdf1, "wb") as f:
        f.write(b"%PDF-1.4 one\n")
    arch1 = os.path.join(ROOT, "pdf-%s.jsonl" % AD.pdf_md5_16(pdf1))
    write_sidecar(arch1, pages)

    class _A:                       # resolve_sidecar 只读 sidecar / pdf 两个字段
        sidecar = AD.SIDECAR
        pdf = ""

    a = _A()
    check("㉑ 没给 --pdf -> 仍用 latest.jsonl", AD.resolve_sidecar(a)[0] == AD.SIDECAR,
          AD.resolve_sidecar(a))
    a.pdf = pdf1
    check("㉑ 给了 --pdf 且归档件在 -> 认领归档件", AD.resolve_sidecar(a)[0] == arch1,
          AD.resolve_sidecar(a))
    a.sidecar = os.path.join(ROOT, "manual.jsonl")
    check("㉑ 显式 --sidecar 优先于 --pdf", AD.resolve_sidecar(a)[0] == a.sidecar,
          AD.resolve_sidecar(a))
    a.sidecar = AD.SIDECAR

    pdf2 = os.path.join(ROOT, "two.pdf")
    with open(pdf2, "wb") as f:
        f.write(b"%PDF-1.4 two\n")
    a.pdf = pdf2
    p21, why21 = AD.resolve_sidecar(a)
    check("㉑ 无归档件 -> 拒绝(不回落 latest.jsonl)",
          p21 is None and "找不到按文档归档" in why21, why21)
    a.pdf = os.path.join(ROOT, "nope.pdf")
    p21, why21 = AD.resolve_sidecar(a)
    check("㉑ PDF 不存在 -> 拒绝并点名", p21 is None and "不存在" in why21, why21)

    reset(mk_manifest("c21", [[(1, 0)], [(1, 1)]]))
    rc, out = run(["export", "--name", "c21", "--pages", "1", "--pdf", pdf2])
    check("㉑ 无归档件时导出被拒(不静默换侧车)", rc == 1 and "找不到按文档归档" in out, out[-200:])
    reset(mk_manifest("c22", [[(1, 0)], [(1, 1)]]))
    rc, _ = run(["export", "--name", "c22", "--pages", "1", "--pdf", pdf1])
    _call = [c for c in FAKE["calls"] if c[0] == "seg_export.py"][-1][1]
    e22 = AD.load_ledger("c22")["stages"]["export"]
    check("㉑ 导出确实用了归档件, 来源记进台账",
          rc == 0 and _call[_call.index("--sidecar") + 1] == arch1
          and e22.get("sidecar_source") == "按文档归档件", e22)

    # ㉒ render/gate 的「末 N 页保留原文」(v28.14): 与 pre_check 推荐的 skipLastPages
    #     同口径。原先 render 没有这个参数 -> adopt 复现不出产品行为(插件设了跳页);
    #     gate 的 --skip-last 缺省 0 -> 保留页被判"文献区汉化"。
    #     实测 Zhang 2026: 渲染 skipLastPages=0 后 第16,17页(29+14条) FAIL, 而 pre_check
    #     明确推荐 3 (那几页是文档后缀的参考文献区)。
    reset()
    FAKE["out"]["force_rerender.py"] = ("提交: a.pdf (force=True, service=silicon,"
                                        " skipLastPages=2, 1 字节)\n  产物: %s\n耗时 8 秒 -> 成功\n"
                                        % mono_fake)
    rc, _ = run(["render", "--name", "c8", "--pdf", __file__, "--skip-last", "2", "--force"])
    r = AD.load_ledger("c8")["stages"]["render"]
    _rr = [c for c in FAKE["calls"] if c[0] == "force_rerender.py"][-1][1]
    check("㉒ render --skip-last 透传给 force_rerender",
          rc == 0 and "--skip-last" in _rr and _rr[_rr.index("--skip-last") + 1] == "2", _rr)
    check("㉒ render 把 skip_last 记进台账", r.get("skip_last") == 2, r)

    FAKE["rc"]["verify_render.py"] = 0
    FAKE["rc"]["post_check.py"] = 0
    rc, _ = run(["gate", "--name", "c8", "--expect", "共识"])
    _pc = [c for c in FAKE["calls"] if c[0] == "post_check.py"][-1][1]
    g = AD.load_ledger("c8")["stages"]["gate"]
    check("㉒ gate 缺省沿用 render 的 skip_last",
          rc == 0 and "--skip-last" in _pc and _pc[_pc.index("--skip-last") + 1] == "2", _pc)
    check("㉒ gate 台账同时记下 skip_last", g.get("skip_last") == 2, g)

    rc, _ = run(["gate", "--name", "c8", "--expect", "共识", "--skip-last", "0"])
    _pc = [c for c in FAKE["calls"] if c[0] == "post_check.py"][-1][1]
    check("㉒ 显式 --skip-last 0 覆盖台账值", "--skip-last" not in _pc, _pc)

    # ㉓ [v28.22] gate 的 --expect/--forbid **重复出现必须累加** —— 纯 nargs="+" 时
    #     argparse 对重复选项是"后者覆盖前者"(不是追加), `--expect A --expect B
    #     --expect C` 只剩 C, 于是验收静默退化成"只查一条"。
    #     实测 2026-09-19 CLAP 收口: 传 4 条 expect, 输出 "通过 1 / 共 1" —— 门禁
    #     报 PASS 却只验了 1/4, 比 FAIL 更危险(漏验)。修法 action="extend"。
    reset()
    FAKE["rc"]["verify_render.py"] = 0
    FAKE["rc"]["post_check.py"] = 0
    rc, _ = run(["gate", "--name", "c8", "--expect", "甲", "--expect", "乙",
                 "--expect", "丙", "--forbid", "旧一", "--forbid", "旧二"])
    _vr = [c for c in FAKE["calls"] if c[0] == "verify_render.py"][-1][1]
    got_e = _vr[_vr.index("--expect") + 1: _vr.index("--forbid")]
    got_f = _vr[_vr.index("--forbid") + 1:]
    check("㉓ 重复 --expect 累加(不丢前面的)",
          rc == 0 and got_e == ["甲", "乙", "丙"], (rc, got_e))
    check("㉓ 重复 --forbid 累加", got_f == ["旧一", "旧二"], got_f)
    g23 = AD.load_ledger("c8")["stages"]["gate"]
    check("㉓ 台账记下全部断言(不漏账)",
          g23.get("expect") == ["甲", "乙", "丙"] and g23.get("forbid") == ["旧一", "旧二"], g23)

    # ㉓b 一次给多个 + 重复给 混用, 同样累加
    reset()
    rc, _ = run(["gate", "--name", "c8", "--expect", "甲", "乙", "--expect", "丙"])
    _vr = [c for c in FAKE["calls"] if c[0] == "verify_render.py"][-1][1]
    got_e = _vr[_vr.index("--expect") + 1:]
    check("㉓b 混用写法(--expect A B --expect C)累加",
          rc == 0 and got_e == ["甲", "乙", "丙"], (rc, got_e))

    # ---- ㉔ 逐段不变量门禁 (2026-09-20 SILAGE: 交付稿整段错位, 老门禁只查段号+⋮, 全过) ----
    def mk_payload(name, blocks):
        """写载荷(源文)到 inbox —— 逐段不变量以它为比对基准。"""
        with open(os.path.join(AD.INBOX, name + ".txt"), "w", encoding="utf-8") as f:
            for k, raw in blocks:
                f.write("#S%d\n%s\n" % (k, raw))

    def deliver(name, body):
        p = os.path.join(AD.OUTDIR, name + ".doubao.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return run(["deliver", "--name", name, "--text", p])

    # ㉔a 数字对不上(定理 2 -> 定理 1 那类) -> 拒收
    reset(mk_manifest("d1", [[(1, 0)], [(1, 1)]]))
    run(["export", "--name", "d1", "--pages", "1"])
    mk_payload("d1", [(1, "Theorem 2 gives the following bound"),
                      (2, "plain sentence with no digits at all")])
    rc, out = deliver("d1", "#S1\n定理 1 给出了如下界\n#S2\n这一句里一个数字都没有\n")
    d1 = AD.load_ledger("d1")["stages"]["deliver"]
    check("㉔a 数字不符被拦(定理 2 -> 定理 1)",
          rc == 1 and d1["state"] == "failed" and d1["n_inv"] == 1 and d1["n_cut"] == 0,
          (rc, d1))
    reports = sorted(n for n in os.listdir(AD.REVIEW) if n.startswith("门禁拒收"))
    check("㉔a 拒收报告落 review/ 且台账记了路径",
          os.path.basename(d1["report"]) in reports and d1["report"].endswith("_d1.md"),
          (reports, d1.get("report")))
    head = open(d1["report"], encoding="utf-8").read(1500)
    check("㉔a 报告头部带「门禁判定」(桥 _headline 只读前 6KB 抓这行)",
          "门禁判定: FAIL" in head, head[:150])

    # ㉔b 整段错位 -> 不仅拒收, 还要把"错位带"单独诊出来(豆包要改的是号, 不是重译)
    reset(mk_manifest("d2", [[(1, 0)], [(1, 1)], [(1, 2)], [(2, 0)]]))
    run(["export", "--name", "d2", "--pages", "1"])
    mk_payload("d2", [(1, "Lemma 11"), (2, "Lemma 22"), (3, "Lemma 33"), (4, "Lemma 44")])
    # 末段给个**载荷里没有**的号(引理 55): 若写成"引理 44", 交付#S4 与载荷#S4 同签名,
    # 会被"在家的段不算错位"那条判据跳过(shift_bands 的 offset-0 前置检查) —— 那个
    # 配套场景在 ㉕d 单独测, 这里要的是"纯错位"的完整错位带。
    rc, out = deliver("d2", "#S1\n引理 22\n#S2\n引理 33\n#S3\n引理 44\n#S4\n引理 55\n")
    d2 = AD.load_ledger("d2")["stages"]["deliver"]
    body2 = open(d2["report"], encoding="utf-8").read()
    check("㉔b 错位被拦", rc == 1 and d2["state"] == "failed", (rc, d2))
    check("㉔b 错位带诊断出来(载荷 #S2-#S4 前移 1 格)",
          "#S2 – #S4" in body2 and "前移 1 格" in body2,
          [ln for ln in body2.splitlines() if "#S2 – #S4" in ln])
    check("㉔b 终端也点名错位带", "错位带" in out, out[-300:])

    # ㉔c --waive 显式放行: 少数合理改写不该把整篇卡死, 但必须留痕
    reset()
    rc, out = run(["deliver", "--name", "d1", "--text",
                   os.path.join(AD.OUTDIR, "d1.doubao.txt"), "--waive"])
    d1w = AD.load_ledger("d1")["stages"]["deliver"]
    check("㉔c --waive 放行且留痕", rc == 0 and d1w["state"] == "ok"
          and d1w.get("waive") is True and d1w["waived"]["inv"] == 1, (rc, d1w))

    # ㉔d 载荷读不到 -> **明示跳过**, 不拿空基准逐段比(那会把每段都判成"数字多出来", 全线误杀)
    reset(mk_manifest("d3", [[(1, 0)]]))
    run(["export", "--name", "d3", "--pages", "1"])
    # 假 seg_export 不落载荷文件, 台账记的 inbox/d3.txt 本来就是空的 —— 这正是要测的形态
    _p3 = os.path.join(AD.INBOX, "d3.txt")
    if os.path.exists(_p3):
        os.remove(_p3)
    rc, out = deliver("d3", "#S1\n第 7 节\n")
    d3 = AD.load_ledger("d3")["stages"]["deliver"]
    check("㉔d 缺载荷不误杀, 台账如实记 inv_ok=False",
          rc == 0 and d3["state"] == "ok" and d3["inv_ok"] is False and "载荷不可读" in out,
          (rc, d3, out[-200:]))

    # ---- ㉕ 留空/只译半截 + 错位带假阳性 (2026-09-20b SILAGE doubao2: 8 段留空、
    #      33 段只译半截被老报告误列成"数字不符"; 同时 2 条错位带是签名碰撞的假报) ----
    LONG1 = ("All experiments were implemented in Python 3.12.8 using PyTorch. "
             "Execution was performed on a workstation featuring dual Intel Xeon "
             "Gold 6246 CPUs and 48 logical threads alongside several GPU cards.")
    LONG2 = ("For the grouped case we recompute the average over all n table entries "
             "outside the sampled block, which costs m component-gradient evaluations "
             "per iteration and dominates the bookkeeping overhead in practice.")

    # ㉕a 留空: 源段有内容, 交付该段一个字符都没有 -> 拒收, 且单列一类(不混进"数字不符")
    reset(mk_manifest("g1", [[(1, 0)], [(1, 1)]]))
    run(["export", "--name", "g1", "--pages", "1"])
    mk_payload("g1", [(1, LONG1), (2, LONG2)])
    rc, out = deliver("g1", "#S1\n所有实验都在 Python 3.12.8 中实现，运行工作站的配置包括"
                            "双路 CPU 和若干 GPU。\n#S2\n\n")
    g1 = AD.load_ledger("g1")["stages"]["deliver"]
    check("㉕a 留空段被拦且单列一类", rc == 1 and g1["state"] == "failed" and g1["n_empty"] == 1,
          (rc, g1))
    check("㉕a 终端点名留空", "留空" in out, out[-300:])
    body1 = open(g1["report"], encoding="utf-8").read()
    check("㉕a 报告有「留空 / 只译半截」专节并给可执行改法",
          "## 一、留空 / 只译半截" in body1 and "不要并成一句" in body1
          and "段尾的公式残留照抄" in body1, body1[:80])

    # ㉕b 只译半截: 原文很长而交付不足 15% -> 拒收(定标: 对照组 4 篇 324 个长段 0 例)
    reset(mk_manifest("g2", [[(1, 0)], [(1, 1)]]))
    run(["export", "--name", "g2", "--pages", "1"])
    mk_payload("g2", [(1, LONG1), (2, LONG2)])
    rc, out = deliver("g2", "#S1\n所有实验都在 Python 3.12.8 中实现。执行在\n#S2\n对于分组情形，我们需要"
                            "重新计算所有未被采样的表项，这需要每次迭代评估 m 个分量梯度，并且在实际中"
                            "支配了整个簿记开销。\n")
    g2 = AD.load_ledger("g2")["stages"]["deliver"]
    check("㉕b 只译半截被拦且单列一类",
          rc == 1 and g2["n_short"] == 1 and g2["n_empty"] == 0 and g2["n_inv"] >= 1, (rc, g2))
    check("㉕b 终端报出 源长->交付长", "只译半截" in out and "->" in out, out[-300:])
    body2b = open(g2["report"], encoding="utf-8").read()
    check("㉕b 半截表里给了原文结尾(照它补完)",
          "原文结尾（照它补完）" in body2b and "GPU cards" in body2b,
          [ln for ln in body2b.splitlines() if "#S1 |" in ln])

    # ㉕c 源段本身为空 -> "空对空"不是缺陷, 不该把整篇拦住
    reset(mk_manifest("g3", [[(1, 0)], [(1, 1)]]))
    run(["export", "--name", "g3", "--pages", "1"])
    mk_payload("g3", [(1, ""), (2, "plain sentence with no digits at all")])
    rc, out = deliver("g3", "#S1\n\n#S2\n这一句里一个数字都没有\n")
    g3 = AD.load_ledger("g3")["stages"]["deliver"]
    check("㉕c 空对空不误杀", rc == 0 and g3["state"] == "ok", (rc, g3))

    # ㉕d 签名碰撞不再误报错位带: 四段都在列举同一对符号, 签名全是 (1,2) 且**各自在家**
    #     老判据会报"载荷 #S1-#S2 后移 2 格" + "载荷 #S3-#S4 前移 2 格"一对(看着像互换),
    #     让译者去改本来正确的编号 —— 假错位带比漏报更坏。SILAGE 的 #S58-#S61 就是这个形态。
    reset(mk_manifest("g4", [[(1, 0)], [(1, 1)], [(1, 2)], [(2, 0)]]))
    run(["export", "--name", "g4", "--pages", "1"])
    mk_payload("g4", [(1, "Redundant blocks (d1 small, d2 relatively large) in this regime"),
                      (2, "Homogeneous silos (d1 large, d2 small) Here each group"),
                      (3, "Redundant blocks (d1 small, d2 relatively large) in this regime"),
                      (4, "Homogeneous silos (d1 large, d2 small) Here each group")])
    rc, out = deliver("g4", "#S1\n冗余块（d1 小，d2 相对大）属于此情形，见正文叙述。\n"
                            "#S2\n同质数据仓（d1 大，d2 小）此处每组内部一致。\n"
                            "#S3\n冗余块（d1 小，d2 相对大）属于此情形，见正文叙述。\n"
                            "#S4\n同质数据仓（d1 大，d2 小）此处每组内部一致。\n")
    g4 = AD.load_ledger("g4")["stages"]["deliver"]
    check("㉕d 签名碰撞不再误报错位带(四段各自在家)", rc == 0 and g4["state"] == "ok"
          and "错位带" not in out, (rc, g4, out[-300:]))

    # ㉕e 上标数字归一化: 载荷里 PDF 提取落成行内 ASCII 的脚注("complexity4"), 译者按
    #     语义写成上标("复杂度⁴") —— 同一个数字的两种写法, 不该判成"数字丢了"。
    #     不归一化时这是**指向正确数字**的假报, 会让译者白改一轮(SILAGE 实测 3 条)。
    reset(mk_manifest("g5", [[(1, 0)]]))
    run(["export", "--name", "g5", "--pages", "1"])
    mk_payload("g5", [(1, "Then the iteration complexity4 of SILAGE to reach a point is")])
    rc, out = deliver("g5", "#S1\n则 SILAGE 达到该点的迭代复杂度⁴为\n")
    g5 = AD.load_ledger("g5")["stages"]["deliver"]
    check("㉕e 上标数字不误报(4 与 ⁴ 同一个数)", rc == 0 and g5["state"] == "ok"
          and "错位带" not in out, (rc, g5, out[-200:]))

    # ㉕f 第三节改成"载荷 ↔ 交付"并排 + 改法给出覆盖同名的重交口径。
    #     为什么并排: 上两轮失败都栽在"改法指错地方"(去抠数字, 而病是漏译尾巴/内容漂移)。
    #     body2b 是 ㉕b 那份报告(有一条数字不符), 直接复用。
    check("㉕f 第三节附载荷/交付并排(而不是只列数字多重集)",
          "- 载荷:" in body2b and "- 交付:" in body2b and "载荷 `" in body2b, body2b[:300])
    check("㉕f 第三节每条标出「载荷 N 字 → 交付 M 字」(能一眼看出只译了开头)",
          "- 长度: 载荷 " in body2b and " 字 → 交付 " in body2b, "")
    check('㉕f 改法第 2 条讲"整段译完"并教它比长度自查',
          "每段都要整段译完" in body2b and "只译了开头" in body2b, "")
    check("㉕f 改法明说覆盖同名重交(out/ 不许留第二份候选)",
          "用同一个文件名覆盖" in body2b and ".doubao2.txt" in body2b, body2b[-700:])
    check('㉕f 改法点出"并段/重切"=内容漂移的头号成因',
          "内容漂移的头号成因" in body2b, "")
    check("㉕f 改法交代错位判据会漏报(math 记号多的段落)",
          "漏报" in body2b and "当错位带处理" in body2b, "")

    # ---- ㉖ 「全篇长度比」自查层 (2026-09-20d, 豆包自述"报告只列它检出的段, 我就只改那些段":
    #      第四轮 43 条只译开头的段比值全落在 0.15~0.52, 硬门禁一条都没拦住) ----
    #      要锁两件事: (a) 候补层**不许**变成硬门禁(灰区段不能被拒收);
    #                  (b) 报告必须给出全篇分布+灰区段号, 让"主动扫一遍"有输入。
    SRC_LONG = ("Quantitative evaluation of the proposed approach was conducted on multiple "
                "benchmark datasets under identical experimental settings and the reported "
                "measurements are averaged over several independent repetitions.")

    def frac(r):
        return SRC_LONG[:int(len(SRC_LONG) * r)].rstrip()

    # ㉖a 灰区段(比值约 22%)单独存在时不该被拒收 —— 自查层不是门禁
    reset(mk_manifest("g7", [[(1, 0)]]))
    run(["export", "--name", "g7", "--pages", "1"])
    mk_payload("g7", [(1, SRC_LONG)])
    rc, out = deliver("g7", "#S1\n%s\n" % frac(0.22))
    g7 = AD.load_ledger("g7")["stages"]["deliver"]
    check("㉖a 灰区段(比值约 22%)不判 FAIL —— 自查层没变成硬门禁",
          rc == 0 and g7["state"] == "ok", (rc, g7, out[-200:]))

    # ㉖b #S1 灰区 + #S2 硬线以下 -> 只拒收 #S2, 但报告必须把 #S1 也列进候补表
    reset(mk_manifest("g8", [[(1, 0)], [(1, 1)], [(1, 2)]]))
    run(["export", "--name", "g8", "--pages", "1"])
    mk_payload("g8", [(1, SRC_LONG), (2, SRC_LONG), (3, SRC_LONG)])
    rc, out = deliver("g8", "#S1\n%s\n#S2\n%s\n#S3\n%s\n" % (frac(0.22), frac(0.08), SRC_LONG))
    g8 = AD.load_ledger("g8")["stages"]["deliver"]
    check("㉖b 只有硬线以下的段被拒收(灰区那条不算)",
          rc == 1 and g8["n_short"] == 1 and g8["n_empty"] == 0, (rc, g8))
    body8 = open(g8["report"], encoding="utf-8").read()
    check("㉖b 报告带「附：全篇长度比（自查用，**不判 FAIL**）」一节",
          "## 附：全篇长度比" in body8 and "不判 FAIL" in body8, body8[:200])
    adv8 = body8.split("## 附：全篇长度比", 1)[1] if "## 附：全篇长度比" in body8 else ""
    adv_rows = [ln for ln in adv8.splitlines() if ln.startswith("| #S")]
    check("㉖b 灰区段列进候补表(门禁没判死也要列出来)",
          any(ln.startswith("| #S1 |") for ln in adv_rows), adv_rows)
    check("㉖b 硬线以下的段不进候补表(它们归「只译半截」专表, 不重复)",
          not any(ln.startswith("| #S2 |") for ln in adv_rows), adv_rows)
    check("㉖b 报告给出全篇分布(长段数 + 中位 + 四档)",
          "- 全篇分布（长段 3 个，中位 " in body8, body8[-900:])
    check("㉖b 改法叫它一次扫完、别只改门禁点名的那些",
          "含「附：全篇长度比」列出的候补段" in body8 and "别只改门禁点名的那些" in body8, "")
    check("㉖b 改法告诉它只改几段时用 merge_result(而不是整篇重吐)",
          "merge_result" in body8 and "逐段回读比对" in body8, "")

    # ㉖c ratio_audit 单元: 边界/分档/中位/短段不计 —— 口径错了整套都错, 单独钉住
    src_u = {1: "a" * 200, 2: "b" * 200, 3: "c" * 200, 4: "d" * 39}   # 4 号 39 字 < 60
    got_u = {1: "x" * 44,    # 22%  候补
             2: "y" * 10,    # 5%   硬线以下 -> 不进候补
             3: "z" * 80,    # 40%  正常
             4: "w" * 1}     # 短段 -> 完全不参与
    n_u, mid_u, bk_u, cand_u = AD.ratio_audit(src_u, got_u)
    check("㉖c 短段不计入长段总数", n_u == 3, (n_u, mid_u, bk_u))
    check("㉖c 候补只收 15%~30% 的段(硬线以下的归 short, 不重复)",
          cand_u == [(1, 200, 44)], cand_u)
    check("㉖c 分档计数正确(<15% 1 / 15~30% 1 / 30~50% 1 / >=50% 0)",
          bk_u == (1, 1, 1, 0), bk_u)
    check("㉖c 中位数正确(5%,22%,40% -> 22%)",
          mid_u is not None and abs(mid_u - 0.22) < 1e-9, mid_u)
    check("㉖c 没有长段时返回空(不炸、也不误报)",
          AD.ratio_audit({1: "short"}, {1: "x"}) == (0, None, (0, 0, 0, 0), []),
          AD.ratio_audit({1: "short"}, {1: "x"}))

    # ---- ㉗ 不变量判据: "不漏 + 不幻觉", **复述不算改动** (2026-09-20e) ----
    #      老判据是"数字多重集必须逐个相等", 于是中文把英文省略的主语补出来时
    #      ("𝛿1 is small when… and large when…" -> "…时 𝛿1 小, …时 𝛿1 大") 数字跟着
    #      复述一遍就被判成"增了数字", 报告把译者指去改**本来是对的**段落。
    #      SILAGE 实测: #S699 属这类(零幻觉零真漏), #S348 更早为绕它白改过一轮。
    #      要保住的是"载荷的数字还在不在"和"有没有冒出新的", 不是出现次数。
    S699_SRC = ("Hence 𝛿1 is small when the group mixtures are similar across groups and "
                "large when different groups place mass on different latent families. "
                "Analogously, 𝛿2 is small when each mixture concentrates on one component.")
    S699_DST = ("因此，当组混合在组间相似时 𝛿1 小，当不同组在不同隐族上放置质量时 𝛿1 大。"
                "类似地，当每个混合集中在单个成分上时 𝛿2 小，当每个混合将质量散布在"
                "良好分离的成分上时 𝛿2 大。")
    check("㉗a 复述型(载荷的数字在译文里多出现几次)不判不符 —— 复述不是改数字",
          AD.diff_invariants(S699_SRC, S699_DST) == [],
          AD.diff_invariants(S699_SRC, S699_DST))

    # ㉗b 真漏仍判不符(SILAGE #S11 的形状: 载荷 "complexity2" 的脚注数字被丢掉)
    S11_SRC = "standard gradient descent solves it with a gradient evaluation complexity2 of O(nmL)"
    S11_DST = "标准梯度下降求解它的梯度评估复杂度为 O(nmL)"
    check("㉗b 真漏数字(载荷有的数字在译文里不见了)仍判不符",
          len(AD.diff_invariants(S11_SRC, S11_DST)) == 1
          and AD.diff_invariants(S11_SRC, S11_DST)[0][0] == "数字",
          AD.diff_invariants(S11_SRC, S11_DST))

    # ㉗c 幻觉仍判不符(译文冒出载荷里根本没有的数字)
    check("㉗c 幻觉数字(译文冒出载荷没有的数字)仍判不符",
          len(AD.diff_invariants("see Section 2 for details",
                                 "详见第 2 节与第 5 节")) == 1,
          AD.diff_invariants("see Section 2 for details", "详见第 2 节与第 5 节"))

    # ㉗d 复述与真漏同时存在时, 漏仍要被抓住(别让"复述放行"变成漏报的挡箭牌)
    check('㉗d 复述掩盖不了真漏(重复了 2 却丢了 3, 仍判不符)',
          len(AD.diff_invariants("Theorem 2 and Theorem 3 hold",
                                 "定理 2、定理 2 成立")) == 1,
          AD.diff_invariants("Theorem 2 and Theorem 3 hold", "定理 2、定理 2 成立"))

    # ㉗e [n] 引用同一套判据: 载荷里的引用组在译文里重复引用算过, 无中生有的引用号仍拦
    check("㉗e [n] 引用复述不算改动",
          AD.diff_invariants("as shown in [16,12,25]", "如 [16,12,25] 与 [16,12,25] 所示") == [],
          AD.diff_invariants("as shown in [16,12,25]", "如 [16,12,25] 与 [16,12,25] 所示"))
    check("㉗e 译文冒出载荷没有的引用号仍判不符",
          any(x[0] == "[n]引用" for x in AD.diff_invariants("as shown in [16]",
                                                           "如 [16] 与 [99] 所示")),
          AD.diff_invariants("as shown in [16]", "如 [16] 与 [99] 所示"))

    # ㉗f 端到端: 复述段不再被门禁拒收(判据必须落到 deliver 上, 不能只在单元层)
    reset(mk_manifest("g9", [[(1, 0)]]))
    run(["export", "--name", "g9", "--pages", "1"])
    mk_payload("g9", [(1, "Hence 𝛿1 is small when mixtures are similar and 𝛿2 differs")])
    rc, out = deliver("g9", "#S1\n因此混合相似时 𝛿1 小，而 𝛿1 大时不同；类似地 𝛿2 小、𝛿2 大\n")
    g9 = AD.load_ledger("g9")["stages"]["deliver"]
    check("㉗f 端到端: 复述段不再被拒收", rc == 0 and g9["state"] == "ok",
          (rc, g9, out[-200:]))

    # ㉗g 端到端: 真漏数字仍被拒收(放宽之后不许把真缺陷一起放掉)
    reset(mk_manifest("g10", [[(1, 0)]]))
    run(["export", "--name", "g10", "--pages", "1"])
    mk_payload("g10", [(1, "the iteration complexity2 of SILAGE stays bounded here")])
    rc, out = deliver("g10", "#S1\nSILAGE 的迭代复杂度在这里保持有界\n")
    g10 = AD.load_ledger("g10")["stages"]["deliver"]
    check("㉗g 端到端: 真漏数字仍被拒收", rc == 1 and g10["n_inv"] == 1,
          (rc, g10, out[-300:]))

    # ---- 收尾 ----
    shutil.rmtree(ROOT, ignore_errors=True)
    print("\n结果: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
