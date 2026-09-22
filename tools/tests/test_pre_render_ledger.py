# -*- coding: utf-8 -*-
"""渲染前保底底片 + 本轮硬拦截 单元/集成测试 (v28.69, 2026-09-22)

用户侧的**真实事故**: 表格做完了(没做表注/正文), 直接去"出稿"渲染 —— 出来的 PDF 里
表格页还是英文。渲染这一步**回不了头**(缓存已注入、产物已落盘), 所以它前面必须有闸门;
而渲染之后原始译稿就散在 TSV 与 #S 块里(机读格式, 人事后想核很费劲), 所以它前面还要
留一份**人读存档点**(底片)。

本套件锁死四件事:

  ① import 不许有副作用 —— `mk_appendix` 末尾原本是裸 `main()`: `mk_ledger` 复用它的
     排版函数时**一 import 就真生成 appendix_tables_zh.docx**, 破坏"只读既有文件"契约。
     断言成对: (a) import 后产物**不在**; (b) 显式调 main() 后产物**在** —— (b) 证明 fixture
     是够的, (a) 才不是空转。
  ② 底片内容 —— 正文逐段「原文/译文」交错; 表格保持**真表格**(可翻格上原文下译文,
     无译文格只留原文); 表注从 notes_zh.json 回填; 落点 <PROJ>\\ledger\\<论文名>_译稿.docx;
     且**只读**既有产物(回包/manifest/notes 字节不变)。
  ③ 底片失败**拦在渲染之前**(v28.70 改判) —— 做不出来就抛 `LedgerFailed`, 出稿中止、报错提醒,
     渲染工序一步都不许走。原判"副本而已, 失败也放行"的毛病: PDF 照样出来且与正常那份无异,
     想补底片只能重走一遍, 白多一份 PDF 等人去删。只在会触发重渲染的任务上做(其它只是排版)。
  ④ 本轮硬拦截 —— ① 复制成功即进本轮清单; ④ 出稿**渲染任务**时清单里还有没出稿的,
     直接拒绝(带 blocked 标记); 逃生门 force=true 放行并记台账("强制出稿" + 放弃了谁);
     渲染出稿成功即本轮结束、清单清空。非渲染任务永不拦(否则"先做表格"自己就被拦死)。

不打网络、不动剪贴板、不跑真门禁: run_job 用替身, 面板起在测试进程内的临时端口上。
运行: venv python test_pre_render_ledger.py, 退出码 0=全过
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
TABLE_PIPE = os.path.join(TOOLS, "table_pipe")
for p in (TOOLS, TABLE_PIPE):
    if p not in sys.path:
        sys.path.insert(0, p)

# 环境必须在 import 之前落定: 这批模块在**模块层**读 D / inbox / 论文名。
TMP = tempfile.mkdtemp(prefix="p2z_prl_")
TBL = os.path.join(TMP, "tbl")
INBOX = os.path.join(TMP, "inbox")
BODY = "payload_demo"
PDF = os.path.join(TMP, "Demo et al. - 2026 - Test Paper.pdf")
os.makedirs(TBL, exist_ok=True)
os.makedirs(INBOX, exist_ok=True)
for k, v in (("P2Z_PROJ", TMP), ("P2Z_TABLE_DIR", TBL), ("P2Z_INBOX", INBOX),
             ("P2Z_BODY_NAME", BODY), ("P2Z_BODY_PDF", PDF)):
    os.environ[k] = v

import mk_appendix as MA      # noqa: E402  (import 副作用见 §①)
import mk_ledger as ML        # noqa: E402
import watch_clip as WC       # noqa: E402
import panel as PN            # noqa: E402

APX = os.path.join(TBL, "appendix_tables_zh.docx")
FLAG = os.path.join(TBL, "after_ran.flag")     # 出稿工序"真的跑过"的自证文件(见 §③)
LEDGER = PN.VENDOR_LEDGER
BODY_TEXT = "#S1\n你好世界。\n"


def _w(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def fixture():
    """一套最小但**完整**的数据: 表格(有回包) + 表注 + 正文(有回包), 以及 mk_appendix 的
   4 个输入(齐了才证明"import 不生成产物"这条断言不是空转)。"""
    man = {"tables": {"table_10_1": {"header": ["Genera (n)", "Life form"],
                                     "rows": [["10", "Tree"]]}},
           "units": [{"id": "k001", "table": "table_10_1", "r": 0, "c": 0,
                      "orig": "Genera (n)", "ph": {}},
                     {"id": "k002", "table": "table_10_1", "r": 1, "c": 0,
                      "orig": "10", "ph": {}},
                     {"id": "k003", "table": "table_10_1", "r": 1, "c": 1,
                      "orig": "Tree", "ph": {}}]}
    _w(os.path.join(TBL, "job_manifest.json"), json.dumps(man, ensure_ascii=False))
    _w(os.path.join(TBL, "job_response.tsv"), "k001\t属（数量）\nk003\t乔木状\n")
    _w(os.path.join(TBL, "notes_manifest.json"), json.dumps(
        {"units": [{"id": "n001", "table": "table_10_1", "role": "note",
                    "orig": "Table 10.1 ..."}]}, ensure_ascii=False))
    _w(os.path.join(TBL, "notes_zh.json"), json.dumps(
        {"table_10_1": {"note": "表注译文", "foot": "注a译文"}}, ensure_ascii=False))
    for n in ("table_10_1", "table_10_2"):
        _w(os.path.join(TBL, n + ".tsv"), "Genera (n)\tLife form\n10\tTree\n")
        _w(os.path.join(TBL, n + ".zh.tsv"), "属（数量）\t生活型\n10\t乔木状\n")
    _w(os.path.join(INBOX, BODY + ".txt"),
       "[文档] Demo\n[任务] 把下列每个 #S 段落译成简体中文。\n#S1\nHello world.\n#S2\nSecond one.\n")
    _w(os.path.join(INBOX, BODY + ".manifest.json"), json.dumps(
        {"name": BODY, "pdf": PDF,
         "items": [{"key": "S1", "parts": [{"page": 1, "seg": 0}]},
                   {"key": "S2", "parts": [{"page": 1, "seg": 1}]}]}))
    _w(os.path.join(TBL, BODY + "_response.tsv"), BODY_TEXT + "#S2\n第二段。\n")


def http(base, path, body=None):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def ledger_text():
    with io.open(LEDGER, encoding="utf-8") as f:
        return f.read()


def job_of(pred):
    return next(j for j in WC.available_jobs() if pred(j))


def arm(job, text):
    """把"这次检查"的状态按真实流程摆好(③ 通过后服务器记的就是这三样)。"""
    ids = WC.manifest_ids(job)
    with PN.LOCK:
        PN.STATE["checked"] = (job, ids, {i: "译文" for i in ids})
        PN.STATE["sha"] = PN._sha(text)


def main():                                     # noqa: C901
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS {name}")
        else:
            failed += 1
            print(f"  FAIL {name} {detail}")

    fixture()

    # ---- ① import 无副作用: 一 import 就真生成 docx 是实测踩过的坑 ----
    check("① import mk_appendix 不落 appendix_tables_zh.docx",
          not os.path.exists(APX), APX)
    with contextlib.redirect_stdout(io.StringIO()):
        MA.main()                               # 显式调用才该生成(证明 fixture 够用)
    check("① 显式 main() 才生成(故上一条不是空转)", os.path.exists(APX), APX)
    os.remove(APX)

    # ---- ② 底片内容 + 只读契约 ----
    watched = [os.path.join(TBL, "job_response.tsv"),
               os.path.join(TBL, BODY + "_response.tsv"),
               os.path.join(TBL, "notes_zh.json"),
               os.path.join(TBL, "job_manifest.json")]
    before = {p: io.open(p, "rb").read() for p in watched}
    out = ML.build(log=lambda *_: None)
    want = os.path.join(TMP, "ledger", "Demo et al. - 2026 - Test Paper_译稿.docx")
    check("② 落点 = <PROJ>\\ledger\\<论文名>_译稿.docx", out == want, out)
    check("② 既有产物一个字节都没动",
          all(io.open(p, "rb").read() == before[p] for p in watched))
    real_jobs = WC.JOBS
    WC.JOBS = []                               # 一个 manifest 都没有 = 没有可固化的任务
    check("② 没有任务 manifest -> 不出底片(返回 None)",
          ML.build(log=lambda *_: None) is None)
    WC.JOBS = real_jobs

    from docx import Document
    d = Document(out)
    ps = [p.text for p in d.paragraphs if p.text.strip()]
    check("② 首行标题 = 论文名", ps and ps[0] == "Demo et al. - 2026 - Test Paper", ps[:1])
    check("② 正文逐段「原文/译文」都在", "Hello world." in ps and "你好世界。" in ps, ps)
    check("② 正文按段号升序(原文在前、译文在后)",
          ps.index("Hello world.") < ps.index("你好世界。") < ps.index("Second one."), ps)
    check("② 表注从 notes_zh.json 回填", any(t.startswith("表注: 表注译文") for t in ps), ps)
    check("② 表格是真表格(不是文字块)", len(d.tables) == 1, len(d.tables))
    t = d.tables[0]
    check("② 可翻格: 上原文(灰) / 下译文",
          [p.text for p in t.rows[0].cells[0].paragraphs] == ["Genera (n)", "属（数量）"],
          [p.text for p in t.rows[0].cells[0].paragraphs])
    check("② 无译文格只留原文(不编造)",
          [p.text for p in t.rows[1].cells[0].paragraphs] == ["10"],
          [p.text for p in t.rows[1].cells[0].paragraphs])

    # ---- ③ 底片: 失败要抛(原因带上) / 成功报路径 / 只在渲染任务上做 ----
    real_build = ML.build
    ML.build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    logs = []
    try:
        WC._snapshot(log=logs.append)
        check("③ 底片炸了必须抛 LedgerFailed(不许吞)", False, "没抛, 出稿会被放行")
    except WC.LedgerFailed as e:
        check("③ 底片炸了抛 LedgerFailed, 且原因原样带出", "boom" in str(e), str(e))
    ML.build = real_build
    logs = []
    out = WC._snapshot(log=logs.append)
    check("③ 正常时把产物路径报出来", bool(out) and out.endswith("_译稿.docx"), out)

    def fake_job(render):
        return {"label": "正文 x", "resp": "fake.tsv", "blocks": True, "render": render,
                "cmd": lambda: [sys.executable, "-c", "print('PASS ok')"],
                "after": [[sys.executable, "-c",
                           "open(r'%s','a').write('x')" % FLAG]],
                "out": lambda: os.path.join(TBL, "fake_out.docx")}

    # 拦得彻底 = 出稿工序一步没走(用 after 里那条命令写标记文件来自证)
    ML.build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    if os.path.exists(FLAG):
        os.remove(FLAG)
    try:
        r = WC.run_job(fake_job(True), ["S1"], {"S1": "甲"}, log=lambda *_: None)
        check("③ 底片失败 -> run_job 抛 LedgerFailed(不返回产物)", False, r)
    except WC.LedgerFailed:
        check("③ 底片失败 -> run_job 抛 LedgerFailed(不返回产物)", True)
    check("③ 底片失败拦得彻底: 出稿工序一步没走(没产物)", not os.path.exists(FLAG))
    ML.build = real_build

    calls = []
    real_snap, WC._snapshot = WC._snapshot, (lambda log=print: calls.append(1))
    r = WC.run_job(fake_job(True), ["S1"], {"S1": "甲"}, log=lambda *_: None)
    check("③ 渲染任务: 门禁过后、工序之前存底片", r and len(calls) == 1, calls)
    calls.clear()
    r = WC.run_job(fake_job(False), ["S1"], {"S1": "甲"}, log=lambda *_: None)
    check("③ 非渲染任务: 不存底片(它只是排版)", r and not calls, calls)
    WC._snapshot = real_snap

    # ---- ④ 本轮清单(纯函数) + 外发台账 ----
    tbl_job = job_of(lambda j: j["label"] == "表格正文")
    body_job = job_of(lambda j: j.get("render"))
    check("④ 渲染任务靠显式标记认(不靠标签猜)", PN._is_render(body_job)
          and not PN._is_render(tbl_job) and not PN._is_render(job_of(lambda j: j["label"] == "表注")))
    check("④ 短名: 正文带论文全名也只显示「正文」", PN.short_of(body_job["label"]) == "正文")
    check("④ 短名: 表格/表注原样", PN.short_of("表格正文") == "表格正文"
          and PN.short_of("表注") == "表注")
    if os.path.exists(LEDGER):
        os.remove(LEDGER)
    with PN.LOCK:
        PN.STATE["round"].clear()
    rs = PN.note_sent(tbl_job, 3)
    check("④ 复制即进清单, 且标未出稿",
          [(x["short"], x["done"]) for x in rs] == [("表格正文", False)], rs)
    check("④ 未出稿的进「待补」", PN.round_pending() == ["表格正文"], PN.round_pending())
    check("④ 自己不算自己的待补项", PN.round_pending(exclude="表格正文") == [],
          PN.round_pending(exclude="表格正文"))
    check("④ 外发台账落了「复制」一行(外发历史)", "复制\t表格正文" in ledger_text(),
          ledger_text())

    # ---- ⑤ HTTP 端到端: 硬拦截 / 逃生门 / 非渲染不拦 ----
    srv = ThreadingHTTPServer(("127.0.0.1", 0), PN.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/" % srv.server_address[1]
    opened = []
    real_run, real_beep = PN.wc.run_job, PN.wc.beep
    had_open, real_open = hasattr(PN.os, "startfile"), getattr(PN.os, "startfile", None)

    def stub_run(job, ids, got, log=print):
        log("STUB 出稿(测试替身)")
        return "OUT"

    PN.wc.run_job, PN.wc.beep = stub_run, (lambda ok=True: None)
    PN.os.startfile = opened.append
    try:
        with PN.LOCK:
            PN.STATE["round"].clear()
        PN.note_sent(tbl_job, 3)                       # 本轮: 表格正文 没出稿
        arm(body_job, BODY_TEXT)
        r = http(base, "api/commit", {"text": BODY_TEXT})
        check("⑤ 硬拦: 本轮还有没出稿的任务 -> 渲染任务不许出稿",
              r["ok"] is False and r.get("blocked") is True
              and [p["short"] for p in r["pending"]] == ["表格正文"], r)
        check("⑤ 被拦时没有真跑出稿工序", not opened, opened)
        check("⑤ 拦的话说得清「谁没做」与怎么绕过",
              "表格正文" in r["error"] and "仍然出稿" in r["error"], r["error"])

        r = http(base, "api/commit", {"text": BODY_TEXT, "force": True})
        check("⑤ 逃生门: force 放行并出稿", r["ok"] is True and r["out"] == "OUT", r)
        check("⑤ 逃生门用了就记台账(强制出稿 + 放弃了谁)",
              "强制出稿" in ledger_text() and "放弃未完成: 表格正文" in ledger_text(),
              ledger_text())
        check("⑤ 渲染完成即本轮结束(清单清空)", r["round"] == [], r["round"])
        check("⑤ 出稿后清掉已检查状态(防双击重复出稿)", PN.STATE["checked"] is None)

        PN.note_sent(tbl_job, 3)                       # 本轮: 表格正文 没出稿
        arm(tbl_job, "k001\t属（数量）\n")
        r = http(base, "api/commit", {"text": "k001\t属（数量）\n"})
        check("⑤ 非渲染任务(表格)永不拦 —— 否则第一步就卡死",
              r["ok"] is True and r["out"] == "OUT", r)

        PN.note_sent(body_job, 2)                      # 本轮: 表格正文(已出稿) + 正文
        check("⑤ 已出稿的不算待补",
              PN.round_pending(exclude=body_job["label"]) == [],
              PN.round_pending(exclude=body_job["label"]))
        arm(body_job, BODY_TEXT)
        r = http(base, "api/commit", {"text": BODY_TEXT})
        check("⑤ 补完了就放行, 且本轮随之结束(清单清空)",
              r["ok"] is True and r["out"] == "OUT" and r["round"] == [], r)

        # ---- 底片没做出来: 拦下出稿 + 报错提醒 + 记台账, 且不毁掉重试的路(v28.70) ----
        def stub_ledger_fail(job, ids, got, log=print):
            raise PN.wc.LedgerFailed("底片生成失败(RuntimeError('boom'))")

        PN.note_sent(body_job, 2)
        arm(body_job, BODY_TEXT)
        opened[:] = []
        PN.wc.run_job = stub_ledger_fail
        r = http(base, "api/commit", {"text": BODY_TEXT})
        check("⑤ 底片没做成 -> 拦下出稿并报错(原因原样提醒)",
              r["ok"] is False and "底片" in r["error"] and "boom" in r["error"], r)
        check("⑤ 底片没做成: 渲染工序一步没走(没产物、没开文档)", not opened, opened)
        check("⑤ 底片没做成也记台账(未出稿 + 原因)",
              "未出稿" in ledger_text() and "底片没做成" in ledger_text(), ledger_text())
        check("⑤ 底片没做成: 已检查状态保住(修好后可直接重试 ④, 不用重贴/重跑 ③)",
              PN.STATE["checked"] is not None)
        check("⑤ 底片没做成: 本轮清单不动(那块仍算未出稿)",
              [(x["short"], x["done"]) for x in r["round"]] == [("正文", False)], r["round"])
        PN.wc.run_job = stub_run
        r = http(base, "api/commit", {"text": BODY_TEXT})
        check("⑤ 修好后重试 ④ 一次就放行(没有多余的 PDF 要删)",
              r["ok"] is True and r["out"] == "OUT" and r["round"] == [], r)

        with PN.LOCK:
            PN.STATE["checked"] = None
        r = http(base, "api/commit", {"text": "x"})
        check("⑤ 没先 ③ 检查 -> 仍拒绝(原有契约没被绕过)",
              r["ok"] is False and "检查" in r["error"], r)

        pg = PN.PAGE
        for frag in ('id="forceBtn"', 'id="roundStat"', "renderRound",
                     "仍然出稿", "force:!!force"):
            check("⑤ 前端接线含 %s" % frag, frag in pg)
    finally:
        PN.wc.run_job, PN.wc.beep = real_run, real_beep
        if had_open:
            PN.os.startfile = real_open
        else:
            del PN.os.startfile
        srv.shutdown()

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s: %d passed, %d failed" % (os.path.basename(__file__), passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
