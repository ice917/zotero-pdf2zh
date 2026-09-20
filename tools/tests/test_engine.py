# -*- coding: utf-8 -*-
"""engine 引擎接缝单元测试 (2026-09-20, babeldoc-migration 分支)

被锁死的缺陷 (一类静默错法: 换引擎 = 悄悄改错库):
  产线原来是单引擎写死的 —— 缓存库、侧车、段落身份、重渲染入口四处都按 pdf2zh 1.x
  的路径与口径硬编码在 adopt / seg_* 里。要接 BabelDOC(pdf2zh_next) 时, 只要有一处
  忘了改, 工具就会"照常跑完、照常打印 OK", 而改的是**另一个引擎的库**:
    - 误用 latest.jsonl = 拿别的论文的段表导出载荷 (下游回锚还会 PASS);
    - 误用最新 mono 兜底 = 拿别引擎的成品当自己的验收对象;
    - 拿旧引擎的台账接着做 = 段号体系都不一样。
  本测试锁住的是"接缝被显式表达"这件事, 而不是某个路径字符串。

本测试锁 18 例 (全离线: 合成 tracking json + 内存台账目录, 不碰真实缓存/侧车):
  画像解析
    ① 缺省 = pdf2zh (不回落别的)
    ② 显式 select("next") 生效
    ③ 未知名 -> ValueError (不静默回落缺省)
    ④ pdf2zh 两个路径与历史硬编码逐字节一致 (回归保护)
    ⑤ next: 缓存库/工作根/段表来源; 且**不产侧车**
    ⑥ pdf2zh 八项全接线; next 只有 deliver/gate/status
    ⑦ next 的未接线理由非空且都指向"要在新引擎上重写"(B 类)
    ⑧ sidecar_of 归档件命名 = pdf-<md5:16>.jsonl; next 上返回 None
  段表读取 (BabelDOC translate_tracking.json)
    ⑨ 段号全篇连续 / 页码 / 页内序
    ⑩ cache_key: 有 llm prompt 取 prompt, 无则取 src
    ⑪ raw = pdf_unicode / n_ph / err
    ⑫ batch = (multi_paragraph_id, multi_paragraph_index)
    ⑬ 文件不存在 -> IOError; 结构不对 -> ValueError (不空手返回空表)
    ⑭ next 画像 read_segments 走 tracking; pdf2zh 画像拒答(侧车口径不在本层复刻)
  adopt 接线
    ⑮ 新台账盖引擎戳
    ⑯ 老台账(无 engine 字段) 不被判死 -> 历史台账不作废
    ⑰ 台账引擎与当前引擎不一致 -> 拒绝执行
    ⑱ 未接线的前置阶段不构成前置 (next 上 gate 不再被 render 锁死)
    ⑲ next 上 gate 缺 --mono 时给的是"请用 --mono 指定", 不是 KeyError
    ⑳ 同引擎重复 use_engine 不重绑(保住调用方覆盖); 真换引擎仍会重绑
    ㉑ 四个子工具(seg_export/seg_import/seg_inject/force_rerender)单跑时自带接线门禁: next 拒答 / pdf2zh 走原路

运行: venv python test_engine.py, 退出码 0=全过
"""
import io
import json
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import engine as EG           # noqa: E402
import adopt as AD            # noqa: E402  模块级只定义常量/函数, 导入不写库

HOME = os.path.expanduser("~")

# 合成段表: 2 页 3 段, 第 2 段带 llm prompt(模拟 LLM 通道), 第 1 段是批的一段
TRACKING = {
    "cross_page": [],
    "cross_column": [],
    "page": [
        {"paragraph": [
            {"input": "Chapter 10 Reproductive Biology of Cactaceae",
             "output": "第10章 仙人掌科的生殖生物学",
             "pdf_unicode": "Chapter 10 Reproductive Biology of Cactaceae",
             "llm_translate_trackers": [],
             "placeholders": [], "multi_paragraph_id": 7, "multi_paragraph_index": 0},
            {"input": "Abstract Floral biology of Cactaceae {v1} is emerging.",
             "output": "摘要 仙人掌科的花部生物学{v1}正在兴起。",
             "pdf_unicode": "Abstract Floral biology of Cactaceae 𝒪(n) is emerging.",
             "llm_translate_trackers": [{"input": "PROMPT-BATCH-7", "output": "",
                                         "has_error": False}],
             "placeholders": [{"type": "formula", "id": 1}],
             "multi_paragraph_id": 7, "multi_paragraph_index": 1},
        ]},
        {"paragraph": [
            {"input": "10.1 Introduction",
             "output": "10.1 介绍",
             "pdf_unicode": "10.1 Introduction",
             "llm_translate_trackers": [{"input": "", "output": "", "has_error": True}],
             "placeholders": [], "multi_paragraph_id": None, "multi_paragraph_index": None},
        ]},
    ],
}


def write_tracking(d, name="translate_tracking.json"):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(TRACKING, f, ensure_ascii=False)
    return p


def sandbox():
    """把 adopt 的落点全改到临时目录 —— 本测试绝不许写真实台账/入库。"""
    root = tempfile.mkdtemp(prefix="eng_test_")
    AD.PROJ = root
    AD.INBOX = os.path.join(root, "inbox")
    AD.OUTDIR = os.path.join(root, "out")
    AD.LEDGER_DIR = os.path.join(root, "logs", "adopt")
    AD.REVIEW = os.path.join(root, "review")
    for d in (AD.INBOX, AD.OUTDIR, AD.LEDGER_DIR, AD.REVIEW):
        os.makedirs(d, exist_ok=True)
    return root


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

    tmp = tempfile.mkdtemp(prefix="eng_test_")
    try:
        # ---------------- 画像解析 ----------------
        os.environ.pop("P2Z_ENGINE", None)
        check("① 缺省引擎 = pdf2zh", EG.active().key == "pdf2zh", EG.active().key)
        check("② 显式 select('next') 生效", EG.select("next").key == "next")

        try:
            EG.select("pdf2zh-next")
            check("③ 未知名报错", False, "没报错")
        except ValueError as e:
            check("③ 未知名报错且列出候选",
                  "pdf2zh" in str(e) and "next" in str(e), str(e))

        p1 = EG.PROFILES["pdf2zh"]
        check("④ pdf2zh 缓存库路径不变",
              p1.cache_db == os.path.join(HOME, ".cache", "pdf2zh", "cache.v1.db"),
              p1.cache_db)
        check("④ pdf2zh 侧车路径不变",
              p1.sidecar == os.path.join(HOME, ".cache", "pdf2zh", "segflow", "latest.jsonl"),
              p1.sidecar)

        p2 = EG.PROFILES["next"]
        check("⑤ next 缓存库 = pdf2zh_next 那张",
              p2.cache_db == os.path.join(HOME, ".cache", "pdf2zh_next", "cache.v1.db"),
              p2.cache_db)
        check("⑤ next 段表来源 = tracking_json 且无侧车",
              p2.seg_source == "tracking_json" and p2.sidecar is None, (p2.seg_source, p2.sidecar))

        check("⑥ pdf2zh 八项全接线", len(p1.ok_stages) == 8, p1.ok_stages)
        check("⑥ next 只接线 deliver/gate/status",
              sorted(p2.ok_stages) == ["deliver", "gate", "status"], p2.ok_stages)
        why = dict(p2.wired)
        check("⑦ next 未接线阶段都带理由",
              all(why[s] for s in ("export", "import", "inject", "render", "rollback")),
              why)
        check("⑦ next 缺 export/inject/render 而 pdf2zh 不缺",
              all(not p2.stage_ok(s)[0] for s in ("export", "inject", "render"))
              and all(p1.stage_ok(s)[0] for s in ("export", "inject", "render")))

        check("⑧ 归档侧车命名 = pdf-<md5:16>.jsonl",
              p1.sidecar_of("0123456789abcdef") ==
              os.path.join(os.path.dirname(p1.sidecar), "pdf-0123456789abcdef.jsonl"),
              p1.sidecar_of("0123456789abcdef"))
        check("⑧ next 上归档侧车 = None", p2.sidecar_of("0123456789abcdef") is None)

        # ---------------- 段表读取 ----------------
        tp = write_tracking(tmp)
        segs = EG.read_tracking_segments(tp)
        check("⑨ 段数 3", len(segs) == 3, len(segs))
        check("⑨ 段号全篇连续 1..3", [s["seq"] for s in segs] == [1, 2, 3],
              [s["seq"] for s in segs])
        check("⑨ 页码/页内序 = [1,1,2] / [0,1,0]",
              [s["page"] for s in segs] == [1, 1, 2] and [s["idx"] for s in segs] == [0, 1, 0],
              ([s["page"] for s in segs], [s["idx"] for s in segs]))
        check("⑩ 有 llm prompt -> cache_key 取 prompt",
              segs[1]["cache_key"] == "PROMPT-BATCH-7", segs[1]["cache_key"])
        check("⑩ 无 llm prompt -> cache_key 取 src",
              segs[0]["cache_key"] == segs[0]["src"] == TRACKING["page"][0]["paragraph"][0]["input"],
              segs[0]["cache_key"])
        check("⑪ raw 取 pdf_unicode(占位符已还原)",
              segs[1]["raw"].endswith("is emerging.") and "{v1}" in segs[1]["src"],
              (segs[1]["raw"][:30], segs[1]["src"][:30]))
        check("⑪ n_ph / err", segs[1]["n_ph"] == 1 and segs[2]["err"] is True,
              (segs[1]["n_ph"], segs[2]["err"]))
        check("⑫ batch 取 multi_paragraph_id/index",
              segs[1]["batch"] == (7, 1) and segs[2]["batch"] == (None, None),
              (segs[1]["batch"], segs[2]["batch"]))
        try:
            EG.read_tracking_segments(os.path.join(tmp, "没有这个文件.json"))
            check("⑬ 文件不存在 -> IOError", False, "没报错")
        except IOError:
            check("⑬ 文件不存在 -> IOError", True)
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write('{"foo": 1}')
        try:
            EG.read_tracking_segments(bad)
            check("⑬ 结构不对 -> ValueError", False, "没报错")
        except ValueError:
            check("⑬ 结构不对 -> ValueError", True)
        check("⑭ next 画像 read_segments 走 tracking",
              len(EG.read_segments(EG.PROFILES["next"], tracking=tp)) == 3)
        try:
            EG.read_segments(EG.PROFILES["pdf2zh"], tracking=tp)
            check("⑭ pdf2zh 画像拒答(侧车口径不在本层复刻)", False, "没拒")
        except NotImplementedError:
            check("⑭ pdf2zh 画像拒答(侧车口径不在本层复刻)", True)
        rt = EG.tracking_report(EG.PROFILES["next"])
        check("⑭ 工作根不存在时体检不炸", isinstance(rt, dict) and "exists" in rt, rt)

        # ---------------- adopt 接线 ----------------
        root = sandbox()
        AD.use_engine("pdf2zh")
        led = AD.load_ledger("eng_case")
        check("⑮ 新台账盖引擎戳", led.get("engine") == "pdf2zh", led)
        check("⑯ 老台账(无 engine)不判死",
              AD.guard({"name": "old", "stages": {"export": {"state": "ok"}}}, "deliver", False))
        AD.use_engine("next")
        led2 = AD.load_ledger("eng_case2")
        check("⑮ next 下新台账盖 next", led2.get("engine") == "next", led2)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = AD.guard({"name": "eng_case", "engine": "pdf2zh",
                          "stages": {"export": {"state": "ok"}}}, "deliver", False)
        check("⑰ 台账引擎不一致 -> 拒绝执行", r is False and "换引擎" in buf.getvalue(),
              buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = AD.guard({"name": "eng_case2", "engine": "next", "stages": {}}, "gate", False)
        check("⑱ 未接线前置不构成前置(gate 不被 render 锁死)", r is True, buf.getvalue())
        AD.use_engine("pdf2zh")           # 换回 1.x: 同一份空台账必须仍被前置锁死
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = AD.guard({"name": "x", "stages": {}}, "gate", False)
        check("⑱ pdf2zh 上 gate 仍被前置 render 锁死",
              r is False and "前置 render" in buf.getvalue(), (r, buf.getvalue()))
        AD.use_engine("next")

        class A(object):
            pass
        a = A()
        a.name, a.expect, a.forbid, a.mono, a.skip_last, a.force = \
            "eng_gate", ["期望串"], [], "", None, False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = AD.stage_gate(a)
        out = buf.getvalue()
        check("⑲ next 上缺 --mono 给的是明确提示, 不是 KeyError",
              rc == 1 and "请用 --mono" in out, (rc, out[-160:]))

        # ⑳ 同一引擎重复 use_engine 不重绑 —— 调用方(测试沙箱/嵌入式)覆盖过的路径
        #    必须保住。锁的是本层引入时踩到的真事故: test_adopt 沙箱改完 AD.SIDECAR/
        #    CACHE 后每次 main() 还会再调一次 use_engine(同引擎) -> 若每次都重绑, 沙箱
        #    被换成真实库("假数据跑真库"), 症状却出现在 ④/⑩/⑫ 三个离现场很远的地方。
        AD.use_engine("pdf2zh")
        keep_sc, keep_db = os.path.join(root, "keep.jsonl"), os.path.join(root, "keep.db")
        AD.SIDECAR, AD.CACHE = keep_sc, keep_db
        AD.use_engine("pdf2zh")
        check("⑳ 同引擎重复调用不踩掉调用方覆盖",
              (AD.SIDECAR, AD.CACHE) == (keep_sc, keep_db), (AD.SIDECAR, AD.CACHE))
        AD.use_engine("next")
        check("⑳ 真换引擎仍会重绑(不是从此不重绑)",
              AD.CACHE == EG.PROFILES["next"].cache_db and AD.SIDECAR == "",
              (AD.SIDECAR, AD.CACHE))

        AD.use_engine("pdf2zh")
        check("⑲ 收尾: 引擎复位 pdf2zh", AD.ENGINE.key == "pdf2zh" and AD.CACHE.endswith("cache.v1.db"),
              AD.CACHE)

        # ㉑ 四个子工具**单跑**时自带接线门禁 —— adopt 拦的是它自己派发的调用, 拦不住
        #    手工 `python seg_inject.py`。少了这道, next 画像下会照 1.x 口径动错库/让
        #    1.x 服务端渲染一份"不属于本画像"的产物。
        _nope_pdf = os.path.join(root, "nope.pdf")
        for tool, argv in (("seg_export.py", ["--pages", "1", "--name", "x"]),
                           ("seg_import.py", ["--manifest", "x"]),
                           ("seg_inject.py", ["--imported", "x", "--manifest", "y"]),
                           ("force_rerender.py", ["--pdf", _nope_pdf])):
            p = subprocess.run([sys.executable, os.path.join(TOOLS, tool)] + argv,
                               env=dict(os.environ, P2Z_ENGINE="next"),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            check("㉑ %s 在 next 上单跑拒答(rc=2)" % tool,
                  p.returncode == 2 and "未接线" in p.stdout, (p.returncode, p.stdout[-160:]))
        # 反向: 同一批命令在 pdf2zh 画像上必须走原路(报的是各自的业务错, 不是接线错)
        for tool, argv in (("seg_export.py", ["--pages", "1", "--name", "x", "--sidecar",
                                              os.path.join(root, "nope.jsonl")]),
                           ("force_rerender.py", ["--pdf", _nope_pdf])):
            p = subprocess.run([sys.executable, os.path.join(TOOLS, tool)] + argv,
                               env=dict(os.environ, P2Z_ENGINE="pdf2zh"),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            check("㉑ pdf2zh 上 %s 不拒答(走原路)" % tool,
                  p.returncode == 1 and "未接线" not in p.stdout, (p.returncode, p.stdout[-160:]))
        shutil.rmtree(root, ignore_errors=True)
    finally:
        os.environ.pop("P2Z_ENGINE", None)
        AD.use_engine("pdf2zh")
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nengine 引擎接缝单元测试: %d PASS / %d FAIL" % (passed, failed))
    sys.stdout.flush()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
