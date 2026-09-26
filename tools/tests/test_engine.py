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

本测试锁 33 例 (全离线: 合成 tracking json + 合成缓存库 + 内存台账目录, 不碰真实库):
  画像解析
    ① 缺省 = pdf2zh (不回落别的)
    ② 显式 select("next") 生效
    ③ 未知名 -> ValueError (不静默回落缺省)
    ④ pdf2zh 两个路径与历史硬编码逐字节一致 (回归保护)
    ⑤ next: 缓存库/工作根/段表来源; 且**不产侧车**
    ⑥ pdf2zh 八项全接线; next 也八项全接线(rollback 是 B 类最后一项)
    ⑦ 接线表是唯一判据: 全接线之后 stage_ok 仍对未登记阶段当场拒答
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
    ㉑ 接线门禁: 手工单跑的子工具在 next 上都**不再**拒答(报的是各自的业务错), 而
       pdf2zh 上仍走原路; 接线门禁本身由"未接线阶段"那条路径守着(见 ⑦ 与 ⑱)
  载荷出口 (next: 段表 -> #S 载荷; v28.40)
    ㉒ 阅读序 = 上页正文 -> cross_page 页尾 -> cross_page 下页首段 -> 下页正文
    ㉓ 正文取 input 原生形态({vN}/<style> 都在), **不是** pdf_unicode 显示形态; 不注入 ⋮
    ㉔ 跨页配对只锚到一侧时用 p1 == p0 + 1 推出来(锚到的页说了算, 没锚到才猜)
    ㉕ 两侧都锚不到 -> 按递增猜并报警(猜错只影响报告里的页码, 不影响正文)
    ㉖ 载荷出门: next 附第 10 条占位符规则 + manifest 记 engine/source/pool/batch;
       pdf2zh 路线**不带**第 10 条(1.x 载荷字节不变)
    ㉗ 段表不存在 / 所选页里没有段 -> 业务错(rc=1)并指向 --tracking, 不是接线错;
       空载荷不许静默出门
  回写通道 (next: 载荷 -> 缓存; v28.41)
    ㉘ next import **不回锚**, 只做守恒校验: {v1} 少写/多写都 FAIL(按多重集比) 并落返工单
    ㉙ next inject 精确定位整条 prompt, 只改批次 JSON 里 id==multi_paragraph_index 那一条
       (同批另一段原样); 段表与载荷对不上 / 前置断言不成立 -> 拒收**且不写库**
    ㉚ next inject: 段表没有 llm tracker(非 LLM 通道跑出来的) 或没有 multi_paragraph_index
       -> 拒收, 绝不猜(猜错就是改到同批另一段的译文上)
    ㉛ 段表与载荷对账走 manifest 的**定位键 + 正文指纹**, 不重推段表比坐标 —— 载荷页号是
       导出时 --pdf 的锚定结果, 重推参数一变就整列错位(真样本 129 段 #S11 起全错);
       旧版 manifest(无定位键) 拒收并指路重新导出
  渲染出口 (next: pdf2zh_next CLI 子进程; v28.42)
    ㉜ 用 .py 假引擎把整条出口跑通: **不带 `--debug`**(v28.45 起段表根改由 config 的
       [translation].working_dir 决定 —— debug 会把调试图层烘进产物并给产物名加 .debug,
       交付件不能带)、config 走临时副本且 ignore_cache 置 false(否则上游不读不写缓存 =
       第二公里整体失效且无痕)、生产 config 一字节不动、--skip-last 按 pypdf 换算成
       --pages、判成败只看新鲜产物、产物命名认得出 next 的
       `<stem>.no_watermark.<lang>.mono.pdf`(只认 1.x 的 `-mono.pdf` = 渲染成功却报
       "没等到新产物"; 且 adopt 的 mono 判据要能挑出 mono, 否则 gate 拿 dual 做验收)、
       渲染前 0 命中必须告警(整篇重译 = 既花钱又慢)
  撤销通道 (next: 行级还原; v28.43)
    ㉝ next rollback **不整库回滚**(缓存库全机共用, .bak 还原会退掉别的篇):
       · 按 provenance 逐段还原 —— 现行值 == imported.json 里我们注入的那一版才动,
         还原成段表记的 output; 四条各还原到自己那段(不是把某一版抄给所有段);
       · 幂等: 已是引擎译文 -> 不动; 现行值既不是新也不是旧(别人改过) -> **不碰只报告**;
       · prompt 行已不在 -> 报无残留而不是失败; --dry 只演算; 撤销也先备份
       · 三个输入缺一不可(argparse 层拦)、--fp 在 next 上拒答、段表与载荷对不上
         -> 拒收**且一个字节都不写**
  段表根 (next; v28.45)
    ㉞ next 段表根 = config 的 [translation].working_dir(engine.next_working_dir):
       有键 -> 取该值; 缺键/为 "null" -> 回落上游 debug 缺省(<HOME>/.cache/babeldoc/
       working)。这是"第一公里与第二公里必须同一个值"的唯一保证, 写死就会错位

运行: venv python test_engine.py, 退出码 0=全过
"""
import io
import json
import contextlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

# [v34] 台账路径必须在本套件驱动 seg_import(子进程) **之前**改掉 —— 理由与
# test_seg_rework.py 同一处(那里有完整推导): 台账默认落在**入库文件** tools/lessons.tsv,
# 单独跑套件会把合成夹具(如 `FAIL #S3 占位符不守恒`)写进生产翻译提示词并顶掉真教训。
# 本套件走 subprocess 且不传 env=, 继承本进程环境 -> 在这里设一次即可。
# setdefault: run_all.py 的 P11 已经指到临时文件时以它为准。
os.environ.setdefault("PDF2ZH_LESSONS_TSV", os.path.join(
    tempfile.mkdtemp(prefix="pdf2zh_test_lessons_"), "lessons.tsv"))

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


def write_tracking_obj(d, obj, name):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p


def para(text, uni="", mpid=None, mpi=None, n_ph=0):
    """合成一条 translate_tracking 的 paragraph 记录(只填断言用得到的字段)。"""
    return {
        "input": text, "output": "", "pdf_unicode": uni or text,
        "llm_translate_trackers": [],
        "placeholders": [{"type": "formula", "id": i + 1} for i in range(n_ph)],
        "multi_paragraph_id": mpid, "multi_paragraph_index": mpi,
    }


def man_item(key, src, txt, pool="page", page=1, seg=0, batch=None):
    """next 载荷的 manifest 条目 —— 带**定位键(src)与正文指纹(fp)**。

    这两个字段是回写侧对回段表的唯一依据(engine.tracking_segment): 拿它们定位就不必
    重放导出时的 --pdf 锚定, 于是同一份 segment 表在导出/回写两次推导参数不同时也不会
    被误判成"不是那一份"(见 ㉛)。
    """
    rec = {"key": key, "merged": False, "pool": pool,
           "parts": [{"page": page, "seg": seg, "true_page": page}],
           "src": list(src), "fp": EG.text_fp(txt)}
    if batch:
        rec["batch"] = list(batch)
    return rec


# 合成载荷段表: 2 页 + 一对跨页配对。跨页的两段**不在** page[] 里 —— 上游按
# translated_ids 把它们从各自页里摘去单独翻, 漏收 = 每页页尾/页首永远译不到。
TRACK_PAY = {
    "cross_column": [],
    "cross_page": [
        {"paragraph": [para("Tail of page one {v1} closes it.",
                            uni="Tail of page one \U0001d49c closes it.", n_ph=1),
                       para("Head of page two continues here.")]},
    ],
    "page": [
        {"paragraph": [para("Body one-a on page one.", mpid=3, mpi=0),
                       para("Body one-b on page one.", mpid=3, mpi=1),
                       para("{v1}{v2}")]},          # 纯占位符段 -> 无可译文字, 应跳过
        {"paragraph": [para("Body two-a on page two.", mpid=5, mpi=0),
                       para("Body two-b on page two.")]},
    ],
}


def write_tracking(d, name="translate_tracking.json"):
    """合成段表(TRACKING) 落盘 —— 段表读取那一节的入口。"""
    return write_tracking_obj(d, TRACKING, name)


# 合成段表(注入用): 1 页 2 段, 同一批次(multi_paragraph_id=3), 两条 tracker 指向
# **同一条** prompt —— 这正是 next 的 LLM 通道形态: 一次调用翻一批段, 缓存里那一行的
# translation 是一个 JSON 数组, 元素 id = multi_paragraph_index(该段在批次里的下标)。
# 于是"改一段"= 只改数组里那一条, 而不是 1.x 那种"一行一段"。
TRACK_INJ = {
    "cross_column": [], "cross_page": [],
    "page": [
        {"paragraph": [
            {"input": "Alpha paragraph one.", "output": "阿尔法第一段。",
             "pdf_unicode": "Alpha paragraph one.",
             "llm_translate_trackers": [{"input": "PROMPT-BATCH-A", "output": "",
                                         "has_error": False}],
             "placeholders": [], "multi_paragraph_id": 3, "multi_paragraph_index": 0},
            {"input": "Beta paragraph two {v1}.", "output": "贝塔段落二{v1}。",
             "pdf_unicode": "Beta paragraph two \U0001d4aa.",
             "llm_translate_trackers": [{"input": "PROMPT-BATCH-A", "output": "",
                                         "has_error": False}],
             "placeholders": [{"type": "formula", "id": 1}],
             "multi_paragraph_id": 3, "multi_paragraph_index": 1},
        ]},
    ],
}


def seed_cache(db, entries, prompt="PROMPT-BATCH-A", engine="siliconflow"):
    """合成一张缓存库(表结构两引擎同名) —— 注入测试绝不碰真库。

    可重复调用(第二次起只是往同一张表里再插一行): 换 prompt 就是另一条缓存行。
    """
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS _translationcache ("
                "id INTEGER PRIMARY KEY, translate_engine TEXT, translate_engine_params TEXT,"
                " original_text TEXT, translation TEXT,"
                " UNIQUE(translate_engine, translate_engine_params, original_text)"
                " ON CONFLICT REPLACE)")
    con.execute("INSERT INTO _translationcache"
                " (translate_engine, translate_engine_params, original_text, translation)"
                " VALUES (?,?,?,?)",
                (engine, "{}", prompt, json.dumps(entries, ensure_ascii=False)))
    con.commit()
    con.close()


def cache_outputs(db, prompt="PROMPT-BATCH-A"):
    """读回那一行 -> {批次 id: 译文}; 行不在则 None。"""
    con = sqlite3.connect(db)
    r = con.execute("SELECT translation FROM _translationcache WHERE original_text=?",
                    (prompt,)).fetchone()
    con.close()
    return {e["id"]: e["output"] for e in json.loads(r[0])} if r else None


def payload_bodies(txt):
    """载荷文本 -> [(#S 编号, 正文)]。

    正文只取 #S 行的**下一行**: 规则区里密密麻麻写着 ⋮/占位符, 拿整篇文本去
    `"⋮" not in txt` 必假阳性 —— 本函数是"只查正文"的唯一口径。
    """
    ls = txt.splitlines()
    return [(ls[i].strip(), ls[i + 1]) for i in range(len(ls) - 1)
            if ls[i].startswith("#S")]


def run_export(root, engine, args):
    """单跑 seg_export.py(沙箱子进程): 落点全在 root 下, 绝不碰真实 inbox。"""
    env = dict(os.environ, P2Z_ENGINE=engine, P2Z_PROJ=root,
               P2Z_INBOX=os.path.join(root, "inbox"))
    return subprocess.run([sys.executable, os.path.join(TOOLS, "seg_export.py")] + args,
                          env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


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
        check("⑥ next 八项全接线(与 pdf2zh 对等; rollback 是 B 类最后一项)",
              sorted(p2.ok_stages) == sorted(p1.ok_stages) and "rollback" in p2.ok_stages,
              p2.ok_stages)
        why = dict(p2.wired)
        check("⑦ 未接线理由表已清空(八项全接线, 不留死条目)",
              EG._NEXT_NO == {} and all(why[s] is None for s in why),
              (EG._NEXT_NO, why))
        # 接线表仍是唯一判据: 全接线之后, "未登记的阶段"照样当场拒答 —— 下次接新引擎
        # 时正是靠这条(在画像里写清"为什么现在跑不了"), 不能因为当前全接线就以为它没用了。
        check("⑦ 未登记的阶段照样被接线门禁拒答",
              p2.stage_ok("没有这个阶段") == (False, "本画像未登记该阶段"),
              p2.stage_ok("没有这个阶段"))
        check("⑦ rollback 在 next 上也已接线(pdf2zh 一直都有)",
              p2.stage_ok("rollback")[0] and p1.stage_ok("rollback")[0])
        check("⑦ export/import/inject/render 在 next 上已接线(不再被接线门禁拦住)",
              all(p2.stage_ok(s)[0] is True for s in ("export", "import", "inject", "render")),
              [p2.stage_ok(s) for s in ("export", "import", "inject", "render")])

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
        check("⑱ next 上 gate 的前置 render 现在也真的被锁死(render 已接线)",
              r is False and "前置 render" in buf.getvalue(), (r, buf.getvalue()))
        #    规则 b 本身(未接线前置不构成前置)还得留一条锁定: 六阶段现已全接线, next 上
        #    没有活例了, 临时把 render 标成未接线验一次 —— 这条是为"框架先跑起来、某阶段
        #    代码还没写"留的(下次接新引擎时正是靠它), 不能因为当前全接线就删掉。
        _w = AD.ENGINE.wired
        try:
            _w["render"] = "（本用例临时标记: 模拟尚未接线的阶段）"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r = AD.guard({"name": "eng_case2", "engine": "next", "stages": {}},
                             "gate", False)
        finally:
            _w["render"] = None
        check("⑱ 未接线前置不构成前置(临时把 render 标成未接线 -> gate 放行)",
              r is True and "不构成前置" in buf.getvalue(), (r, buf.getvalue()))
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
        # force=True: render 接线后 gate 的前置会真的拦住(⑱), 这里要验的是更后面的那条
        # 判据(缺 --mono), 所以跳过顺序门禁直达它。
        a.name, a.expect, a.forbid, a.mono, a.skip_last, a.force = \
            "eng_gate", ["期望串"], [], "", None, True
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

        # ㉑ 子工具单跑时自带接线门禁 —— adopt 拦的是它自己派发的调用, 拦不住手工
        #    `python seg_inject.py`。少了这道, 某画像下未接线的阶段会照 1.x 口径动
        #    错库/让 1.x 服务端渲染一份"不属于本画像"的产物。
        #    **B 类收口后 next 八项全接线**, 于是这里只剩反向锁: 各入口在 next 上报的必须
        #    是各自的业务错, 不是接线错。这条挡的是"接线表更新了但分流没跟上"(画像说
        #    能跑、代码却按 1.x 口径跑)。接线门禁本身由 ⑦/⑱ 的未登记阶段路径守着。
        _nope_pdf = os.path.join(root, "nope.pdf")
        # 反向: 已接线的工具在 next 上**不再**拒答 —— 报的必须是各自的业务错
        # (载荷/原文打不开), 而不是接线错。
        for tool, argv in (("seg_import.py", ["--manifest", os.path.join(root, "nope.m.json")]),
                           ("seg_inject.py", ["--imported", os.path.join(root, "nope.i.json"),
                                              "--manifest", os.path.join(root, "nope.m.json")]),
                           ("seg_inject.py", ["--rollback",
                                              "--imported", os.path.join(root, "nope.i.json"),
                                              "--manifest", os.path.join(root, "nope.m.json")]),
                           ("force_rerender.py", ["--pdf", _nope_pdf])):
            p = subprocess.run([sys.executable, os.path.join(TOOLS, tool)] + argv,
                               env=dict(os.environ, P2Z_ENGINE="next", P2Z_PROJ=root),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            check("㉑ %s %s 在 next 上不再拒答(已接线)" % (tool, argv[0].lstrip("-")),
                  p.returncode != 2 and "未接线" not in p.stdout, (p.returncode, p.stdout[-160:]))
        # 反向: 同一批命令在 pdf2zh 画像上必须走原路(报的是各自的业务错, 不是接线错)
        for tool, argv in (("seg_export.py", ["--pages", "1", "--name", "x", "--terms", "",
                                              "--sidecar", os.path.join(root, "nope.jsonl")]),
                           ("force_rerender.py", ["--pdf", _nope_pdf])):
            p = subprocess.run([sys.executable, os.path.join(TOOLS, tool)] + argv,
                               env=dict(os.environ, P2Z_ENGINE="pdf2zh", P2Z_PROJ=root),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            check("㉑ pdf2zh 上 %s 不拒答(走原路)" % tool,
                  p.returncode == 1 and "未接线" not in p.stdout, (p.returncode, p.stdout[-160:]))

        # ---------------- 载荷出口 (next: 段表 -> #S 载荷; v28.40) ----------------
        tp2 = write_tracking_obj(tmp, TRACK_PAY, "pay_tracking.json")
        items, warns = EG.tracking_payload_items(tp2)

        # ㉒ 阅读序 —— 跨页池的两段被上游从 page[] 摘走了, 只能靠"段身份 + 页码"
        #    插回原位: 上页末段接在**上页末尾**, 下页首段接在**下页开头**。
        #    漏收 = 每页页尾/页首的正文永远译不到也永远改不了。
        check("㉒ 段数 6(跨页池两段也收进来, 纯占位符段被跳过)", len(items) == 6, len(items))
        check("㉒ 池序 = page,page,cross,cross,page,page",
              [it["pool"] for it in items] == ["page", "page", "cross_page", "cross_page",
                                               "page", "page"],
              [it["pool"] for it in items])
        check("㉒ 正文序 = 上页正文 -> 页尾 -> 下页首段 -> 下页正文",
              [it["parts"][0][2].split()[0] for it in items] ==
              ["Body", "Body", "Tail", "Head", "Body", "Body"],
              [it["parts"][0][2].split()[0] for it in items])
        check("㉒ 页码 = 1,1,1,2,2,2(跨页两段落在配对的两页上)",
              [it["parts"][0][0] for it in items] == [1, 1, 1, 2, 2, 2],
              [it["parts"][0][0] for it in items])
        check("㉒ 未给 --pdf 时跨页页码按序猜并报警",
              any("页码未定" in w for w in warns), warns)

        # ㉓ 正文取 input 原生形态 —— 这条是"照搬 1.x 口径必错"的那一处:
        #    1.x 要回锚 {vN}(因为它把显示形态喂给了译者), next 的 {vN}/<style> 是
        #    引擎自己的替换协议, 载荷带原生形态发出去, 收回来的译文里它照样落回原公式。
        #    反过来发显示形态 = 显示字形进译文 = 中文字体没那个码位 = 豆腐块(v28.38)。
        x = [it for it in items if it["pool"] == "cross_page"][0]
        check("㉓ 正文带 {vN} 原生形态, 不取 pdf_unicode 显示形态",
              "{v1}" in x["parts"][0][2] and "\U0001d49c" not in x["parts"][0][2],
              x["parts"][0][2])
        check("㉓ 不注入 ⋮(跨页由上游整段并成一次调用, 没有断点)",
              not any("\u22ee" in p[2] for it in items for p in it["parts"]))

        # ㉔ 跨页配对只锚到一侧 -> 用 p1 == p0 + 1 推出来(段 0 是锚在页尾那段)。
        #    返回 (p0, p1) 两页页码; 推出来的**不带警告**, 与 ㉕ 的"猜"区分开。
        w24 = []
        check("㉔ 只锚到段 0 -> 下一页 = 本页 + 1",
              EG._cross_pages([{"paragraph": [{"input": "tail-marker one"},
                                              {"input": "head-marker two"}]}],
                              ["prefix tail-marker one", "unrelated second page"], 2, w24)
              == [(1, 2)] and not w24, w24)
        w24b = []
        check("㉔ 推到第 3/4 页(不是常量 1/2, 是锚到的页说了算)",
              EG._cross_pages([{"paragraph": [{"input": "tail-marker one"},
                                              {"input": "head-marker two"}]}],
                              ["x", "y", "z prefix tail-marker one", "w"], 4, w24b) == [(3, 4)],
              w24b)
        w24c = []
        check("㉔ 只锚到段 1 -> 上一页 = 本页 - 1",
              EG._cross_pages([{"paragraph": [{"input": "absent-marker zero"},
                                              {"input": "head-marker two"}]}],
                              ["page one body", "prefix head-marker two"], 2, w24c) == [(1, 2)],
              w24c)
        w24d = []
        check("㉔ 两侧锚出来的页面相连也认(不是只有单侧才推)",
              EG._cross_pages([{"paragraph": [{"input": "tail-marker one"},
                                              {"input": "head-marker two"}]}],
                              ["prefix tail-marker one", "prefix head-marker two"], 2, w24d)
              == [(1, 2)] and not w24d, w24d)

        # ㉕ 两侧都锚不到 -> 按递增猜 + 报警。猜错只污染**报告里的页码**, 正文不受影响
        #    —— 所以这里是"报警但不出错", 不是"拒收"。
        w25 = []
        check("㉕ 两侧都锚不到 -> 按递增猜第 2 页",
              EG._cross_pages([{"paragraph": [{"input": "absent-marker alpha"},
                                              {"input": "absent-marker beta"}]}],
                              ["nothing relevant here", "still nothing"], 3, w25) == [(1, 2)],
              w25)
        check("㉕ 猜出来的页码带警告", any("页码未定" in w for w in w25), w25)
        w25b = []
        pairs25 = EG._cross_pages([{"paragraph": [{"input": "absent-marker alpha"},
                                                  {"input": "absent-marker beta"}]},
                                   {"paragraph": [{"input": "absent-marker gamma"},
                                                  {"input": "absent-marker delta"}]}],
                                  ["n1", "n2", "n3", "n4", "n5", "n6"], 6, w25b)
        check("㉕ 连猜时保持 p1 严格递增(否则插回阅读序会把段弄乱)",
              pairs25 == [(1, 2), (2, 3)] and len(w25b) == 2, (pairs25, w25b))

        # ㉖ 载荷出门 —— 跑真的 seg_export.py(子进程, 沙箱 inbox)
        pr = run_export(root, "next", ["--pages", "1-2", "--name", "pay_next",
                                       "--tracking", tp2, "--terms", ""])
        check("㉖ next 出载荷成功(rc=0)", pr.returncode == 0, (pr.returncode, pr.stdout[-200:]))
        ptxt = ""
        pman = {}
        try:
            with open(os.path.join(root, "inbox", "pay_next.txt"), encoding="utf-8") as f:
                ptxt = f.read()
            with open(os.path.join(root, "inbox", "pay_next.manifest.json"),
                      encoding="utf-8") as f:
                pman = json.load(f)
        except OSError as e:
            check("㉖ 载荷/manifest 落盘", False, e)
        bodies = payload_bodies(ptxt)
        check("㉖ 载荷 6 段(#S 编号 + 正文)", len(bodies) == 6, bodies)
        check("㉖ next 载荷附第 10 条占位符规则",
              "10. 段里的花括号占位符与 <style> 标签一律**原样照抄**" in ptxt)
        check("㉖ 载荷正文带原生 {v1}, 且正文里没有 ⋮",
              any("{v1}" in b for _k, b in bodies) and
              not any("\u22ee" in b for _k, b in bodies),
              [b for _k, b in bodies])
        check("㉖ manifest 记 engine/source(next 载荷不能被当成 1.x 的)",
              pman.get("engine") == "next"
              and (pman.get("source") or {}).get("kind") == "tracking_json",
              (pman.get("engine"), pman.get("source")))
        recs = pman.get("items") or []
        check("㉖ manifest 记 pool(跨页两段可追溯)",
              [r.get("pool") for r in recs] == ["page", "page", "cross_page", "cross_page",
                                                "page", "page"],
              [r.get("pool") for r in recs])
        check("㉖ manifest 记 batch(合并段能对回一次调用; 没并过的段不带该字段)",
              recs[0].get("batch") == [3, 0] and recs[1].get("batch") == [3, 1]
              and recs[4].get("batch") == [5, 0] and "batch" not in recs[5],
              [r.get("batch") for r in recs])
        check("㉖ next 无侧车坐标: page == true_page",
              all(p["page"] == p["true_page"] for r in recs for p in (r.get("parts") or [])),
              recs[0])

        # 1.x 路线**逐字节不变**: 不带第 10 条, manifest 仍只有 name/pages/items。
        sc = os.path.join(root, "sd.jsonl")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"page": 1, "vars": {}, "segs": [{"raw": "Page one sentence."}]},
                               ensure_ascii=False) + "\n")
        pr = run_export(root, "pdf2zh", ["--pages", "1", "--name", "pay_pdf",
                                         "--sidecar", sc, "--terms", ""])
        ptxt_p = ""
        try:
            with open(os.path.join(root, "inbox", "pay_pdf.txt"), encoding="utf-8") as f:
                ptxt_p = f.read()
            with open(os.path.join(root, "inbox", "pay_pdf.manifest.json"),
                      encoding="utf-8") as f:
                pman_p = json.load(f)
        except OSError as e:
            pman_p = {}
            check("㉖ pdf2zh 载荷落盘", False, e)
        check("㉖ pdf2zh 载荷不带第 10 条(1.x 载荷字节不变)",
              pr.returncode == 0 and "花括号占位符" not in ptxt_p and "Page one sentence." in ptxt_p,
              (pr.returncode, ptxt_p[-160:]))
        check("㉖ pdf2zh manifest 仍只有 name/pages/items",
              sorted(pman_p) == ["items", "name", "pages"], sorted(pman_p))

        # ㉗ 段表不存在 = 业务错(rc=1), 且要告诉人拿 --tracking 指一份 —— 若报成 rc=2
        #    的接线错, 下一句就是去翻引擎画像, 方向全错(换篇论文后工作根下的旧段表
        #    会被覆盖, 这是最常见的"段表不见了")。
        pr = run_export(root, "next", ["--pages", "1", "--name", "x", "--terms", "",
                                       "--tracking", os.path.join(tmp, "没有这个段表.json")])
        check("㉗ 段表不存在 -> rc=1 且指向 --tracking(不是接线错)",
              pr.returncode == 1 and "--tracking" in pr.stdout and "未接线" not in pr.stdout,
              (pr.returncode, pr.stdout[-200:]))
        pr = run_export(root, "next", ["--pages", "9-10", "--name", "x", "--terms", "",
                                       "--tracking", tp2])
        check("㉗ 所选页里没有段 -> rc=1(空载荷不许静默出门)",
              pr.returncode == 1 and "没有落在" in pr.stdout, (pr.returncode, pr.stdout[-200:]))

        # ---------------- 回写通道 (next: 载荷 -> 缓存; v28.41) ----------------
        # ㉘ next import: 载荷本来就是引擎原生形态 -> **不回锚**, 只剩守恒校验。
        #    判据与上游同源(上游自己就是 full match), 不是我们自造的严格度。
        def run_import(engine_, argv_):
            env_ = dict(os.environ, P2Z_ENGINE=engine_, P2Z_PROJ=root,
                        P2Z_INBOX=os.path.join(root, "inbox"))
            return subprocess.run([sys.executable, os.path.join(TOOLS, "seg_import.py")] + argv_,
                                  env=env_, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")

        man_next = os.path.join(root, "inbox", "pay_next.manifest.json")
        tfile = os.path.join(tmp, "deliver_next.txt")
        # 正文序(㉒): Body one-a / Body one-b / Tail{v1} / Head / Body two-a / Body two-b
        good = ["#S1\n甲段正文。", "#S2\n乙段正文。", "#S3\n上页末段{v1}收尾。",
                "#S4\n下页首段继续。", "#S5\n丙段正文。", "#S6\n丁段正文。"]

        def send(texts):
            with open(tfile, "w", encoding="utf-8") as f:
                f.write("\n".join(texts) + "\n")
            return run_import("next", ["--manifest", man_next, "--text", tfile])

        pr = send(good)
        imp = {}
        try:
            with open(os.path.join(root, "out", "pay_next.imported.json"), encoding="utf-8") as f:
                imp = json.load(f)
        except OSError:
            pass
        check("㉘ next import 通过守恒校验 -> rc=0",
              pr.returncode == 0 and "结论: PASS" in pr.stdout, (pr.returncode, pr.stdout[-200:]))
        check("㉘ imported.json 按 #S 编号 6 段, {v1} 原样带回来(不回锚)",
              sorted(imp) == ["S%d" % i for i in range(1, 7)] and "{v1}" in imp.get("S3", ""), imp)
        check("㉘ PASS 时不留返工单",
              not os.path.exists(os.path.join(root, "inbox", "pay_next.rework.md")))

        bad = list(good)
        bad[2] = "#S3\n上页末段收尾。"                      # 丢了 {v1}: 版面上少一处公式
        pr = send(bad)
        check("㉘ 丢一个 {v1} -> rc=1 且点名占位符不守恒",
              pr.returncode == 1 and "占位符不守恒" in pr.stdout and "少了" in pr.stdout,
              (pr.returncode, pr.stdout[-300:]))
        check("㉘ FAIL 落返工单(桥的 list_inbox 读得到)",
              os.path.exists(os.path.join(root, "inbox", "pay_next.rework.md")))

        bad2 = list(good)
        bad2[2] = "#S3\n上页末段{v1}收尾{v1}。"              # 多一个: 凭空多一处公式
        pr = send(bad2)
        check("㉘ 多一个 {v1} 同样不守恒(按多重集比, 集合就漏了这条)",
              pr.returncode == 1 and "占位符不守恒" in pr.stdout and "多了" in pr.stdout,
              (pr.returncode, pr.stdout[-300:]))

        # ㉙ next inject: 定位 = 精确匹配该段的**整条 prompt**; 改动 = 批次 JSON 里
        #    id == multi_paragraph_index 那一条。全程不碰真实缓存库 —— 子进程 + 覆写
        #    USERPROFILE: next 画像的库是 <home>/.cache/pdf2zh_next/cache.v1.db, 而
        #    engine.py 的 HOME = expanduser("~")(试验台用的也是这一招)。
        tk_inj = write_tracking_obj(tmp, TRACK_INJ, "inj_tracking.json")
        P1 = TRACK_INJ["page"][0]["paragraph"]
        man_inj_p = write_tracking_obj(tmp, {
            "name": "inj", "pages": "1", "engine": "next",
            "source": {"kind": "tracking_json", "path": tk_inj},
            "items": [
                man_item("S1", ("page", 0, 0), P1[0]["input"], batch=(3, 0)),
                man_item("S2", ("page", 0, 1), P1[1]["input"], batch=(3, 1)),
            ]}, "inj.manifest.json")

        shome = os.path.join(tmp, "home")
        sdb = os.path.join(shome, ".cache", "pdf2zh_next", "cache.v1.db")
        os.makedirs(os.path.dirname(sdb), exist_ok=True)

        def run_inject(tk_, man_, imp_, dry=False):
            ip = os.path.join(tmp, "inj.imported.json")
            with open(ip, "w", encoding="utf-8") as f:
                json.dump(imp_, f, ensure_ascii=False)
            argv = ["--imported", ip, "--manifest", man_, "--tracking", tk_]
            if dry:
                argv.append("--dry")
            return subprocess.run([sys.executable, os.path.join(TOOLS, "seg_inject.py")] + argv,
                                  env=dict(os.environ, P2Z_ENGINE="next", P2Z_PROJ=root,
                                           USERPROFILE=shome, HOME=shome),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")

        seed_cache(sdb, [{"id": 0, "output": "阿尔法第一段。"},
                         {"id": 1, "output": "贝塔段落二{v1}。"}])
        pr = run_inject(tk_inj, man_inj_p, {"S1": "阿尔法第一段（改）。"})
        got = cache_outputs(sdb)
        check("㉙ next inject 实写 rc=0",
              pr.returncode == 0 and "结论: PASS" in pr.stdout, (pr.returncode, pr.stdout[-240:]))
        check("㉙ 只改中 id==mpi 那一条 —— 同批另一段原样不动",
              got == {0: "阿尔法第一段（改）。", 1: "贝塔段落二{v1}。"}, got)
        check("㉙ 实写前先备份(缓存手术的止损坏步骤)", ".bak-" in pr.stdout, pr.stdout[-200:])

        # 段表与载荷对不上(工作根下的段表会被下次渲染覆盖)-> 拒收, 不写库
        tk_other = write_tracking_obj(tmp, {
            "cross_column": [], "cross_page": [],
            "page": [{"paragraph": [TRACK_INJ["page"][0]["paragraph"][0]]}]},
            "inj_tracking_short.json")
        pr = run_inject(tk_other, man_inj_p, {"S1": "再改一次。"})
        check("㉙ 段表与载荷对不上 -> rc=1 且指路 --tracking",
              pr.returncode == 1 and "--tracking" in pr.stdout, (pr.returncode, pr.stdout[-260:]))
        check("㉙ 对不上时**一个字节都没写**", cache_outputs(sdb)[0] == "阿尔法第一段（改）。",
              cache_outputs(sdb))

        # 前置断言: 段表记的 output 与缓存里那一条不一致(说明行找错了 / id 映射错了)
        tk_drift = write_tracking_obj(tmp, {
            "cross_column": [], "cross_page": [],
            "page": [{"paragraph": [dict(TRACK_INJ["page"][0]["paragraph"][0],
                                         output="段表里记的是另一句。")]}]},
            "inj_tracking_drift.json")
        man_drift = write_tracking_obj(tmp, {
            "name": "inj2", "pages": "1", "engine": "next",
            "source": {"kind": "tracking_json", "path": tk_drift},
            "items": [man_item("S1", ("page", 0, 0), P1[0]["input"], batch=(3, 0))]},
            "inj2.manifest.json")
        pr = run_inject(tk_drift, man_drift, {"S1": "乱写。"})
        check("㉙ 前置断言不成立 -> rc=1 且明确写'不做任何写入'",
              pr.returncode == 1 and "前置断言不成立" in pr.stdout, (pr.returncode, pr.stdout[-300:]))
        check("㉙ 断言失败时也不写库", cache_outputs(sdb)[0] == "阿尔法第一段（改）。",
              cache_outputs(sdb))

        pr = run_inject(tk_inj, man_inj_p, {"S2": "贝塔段落二{v1}（改）。"}, dry=True)
        check("㉙ --dry 只演算不写库",
              pr.returncode == 0 and "dry-run 未写库" in pr.stdout
              and cache_outputs(sdb)[1] == "贝塔段落二{v1}。", (pr.returncode, pr.stdout[-200:]))

        # ㉚ 定不了位就拒收, 绝不猜 —— 猜错的后果是**改到同批另一段的译文上**, 而且
        #    改完渲染出来还"看起来正常"(只是那句话变成了别的意思)。
        tk_notrack = write_tracking_obj(tmp, {
            "cross_column": [], "cross_page": [],
            "page": [{"paragraph": [dict(TRACK_INJ["page"][0]["paragraph"][0],
                                         llm_translate_trackers=[])]}]},
            "inj_no_tracker.json")
        man_notrack = write_tracking_obj(tmp, {
            "name": "inj3", "pages": "1", "engine": "next",
            "source": {"kind": "tracking_json", "path": tk_notrack},
            "items": [man_item("S1", ("page", 0, 0), P1[0]["input"], batch=(3, 0))]},
            "inj3.manifest.json")
        pr = run_inject(tk_notrack, man_notrack, {"S1": "改。"})
        check("㉚ 段表没有 llm tracker(非 LLM 通道) -> rc=1",
              pr.returncode == 1 and "没有 llm_translate_trackers" in pr.stdout,
              (pr.returncode, pr.stdout[-260:]))

        tk_nompi = write_tracking_obj(tmp, {
            "cross_column": [], "cross_page": [],
            "page": [{"paragraph": [dict(TRACK_INJ["page"][0]["paragraph"][0],
                                         multi_paragraph_index=None)]}]},
            "inj_no_mpi.json")
        man_nompi = write_tracking_obj(tmp, {
            "name": "inj4", "pages": "1", "engine": "next",
            "source": {"kind": "tracking_json", "path": tk_nompi},
            "items": [man_item("S1", ("page", 0, 0), P1[0]["input"], batch=(3, 0))]},
            "inj4.manifest.json")
        pr = run_inject(tk_nompi, man_nompi, {"S1": "改。"})
        check("㉚ 没有 multi_paragraph_index -> rc=1(绝不猜它在批次里的 id)",
              pr.returncode == 1 and "multi_paragraph_index" in pr.stdout,
              (pr.returncode, pr.stdout[-260:]))

        # manifest 是别的引擎导出的 -> 直接拒(写进本画像的库只会静默无效)
        man_x = write_tracking_obj(tmp, {"name": "inj5", "engine": "pdf2zh", "items": []},
                                   "inj5.manifest.json")
        pr = run_inject(tk_inj, man_x, {"S1": "改。"})
        check("㉚ 别的引擎的 manifest -> rc=1(不许跨画像写库)",
              pr.returncode == 1 and "不是 next" in pr.stdout, (pr.returncode, pr.stdout[-200:]))

        # ㉛ **编号以 manifest 为准**: 定位键把 #S 对回段表, 与导出时的 --pdf 锚定无关。
        #    载荷里的页号是导出时锚定的结果(给了 --pdf 才按页面文字锚, 不然按序猜), 而 #S
        #    编号按页号排序 —— 回写侧若"拿同一份段表重推一遍再比坐标", 两次推导参数不同就
        #    整列错位, 会把一份完全正常的载荷判成"不是从当前段表导出的"(真样本 129 段里
        #    #S11 之后全对不上)。这里让载荷顺序**故意不同于重推结果**, 并塞一对跨页段
        #    (它们的页号根本推不出来) —— 只有按定位键落位才能全中。
        ORD, ORD_TXT = "PROMPT-ORD", ["尾段。", "首段。", "正文一甲。", "正文一乙。"]
        ORD_SRC = ["Tail of page one.", "Head of page two.", "Body one-a.", "Body one-b."]

        def ord_para(i):
            return dict(para(ORD_SRC[i], mpid=9, mpi=i), output=ORD_TXT[i],
                        llm_translate_trackers=[{"input": ORD, "output": "",
                                                 "has_error": False}])

        tk_ord = write_tracking_obj(tmp, {
            "cross_column": [],
            "cross_page": [{"paragraph": [ord_para(0), ord_para(1)]}],
            "page": [{"paragraph": [ord_para(2), ord_para(3)]}],
        }, "ord_tracking.json")
        man_ord = write_tracking_obj(tmp, {
            "name": "ord", "pages": "1-2", "engine": "next",
            "source": {"kind": "tracking_json", "path": tk_ord},
            "items": [
                man_item("S1", ("page", 0, 1), ORD_SRC[3], seg=1, batch=(9, 3)),
                man_item("S2", ("cross_page", 0, 1), ORD_SRC[1], pool="cross_page",
                         page=2, batch=(9, 1)),
                man_item("S3", ("page", 0, 0), ORD_SRC[2], batch=(9, 2)),
                man_item("S4", ("cross_page", 0, 0), ORD_SRC[0], pool="cross_page",
                         page=1, batch=(9, 0)),
            ]}, "ord.manifest.json")
        seed_cache(sdb, [{"id": i, "output": t} for i, t in enumerate(ORD_TXT)], prompt=ORD)
        pr = run_inject(tk_ord, man_ord, {"S1": "改一乙。", "S2": "改首段。",
                                          "S3": "改一甲。", "S4": "改尾段。"})
        check("㉛ 载荷顺序不必等于重推顺序: 跨页段照样落位(与 --pdf 锚定无关)",
              pr.returncode == 0 and "结论: PASS" in pr.stdout, (pr.returncode, pr.stdout[-300:]))
        check("㉛ 四条各改到**自己那段**的批次槽位(定位键错了就会串段)",
              cache_outputs(sdb, ORD) == {0: "改尾段。", 1: "改首段。",
                                          2: "改一甲。", 3: "改一乙。"},
              cache_outputs(sdb, ORD))

        # 旧版(没有定位键/正文指纹)导出的 manifest -> 拒收, 并说清怎么修
        man_old = write_tracking_obj(tmp, {
            "name": "ord_old", "pages": "1", "engine": "next",
            "source": {"kind": "tracking_json", "path": tk_ord},
            "items": [{"key": "S1", "merged": False, "pool": "page",
                       "parts": [{"page": 1, "seg": 0, "true_page": 1}]}]},
            "ord_old.manifest.json")
        pr = run_inject(tk_ord, man_old, {"S1": "改。"})
        check("㉛ 旧版 manifest(无定位键) -> rc=1 且指路重新导出",
              pr.returncode == 1 and "定位不到" in pr.stdout and "重新导出" in pr.stdout,
              (pr.returncode, pr.stdout[-300:]))

        # ---------------- 撤销通道 (next: 行级还原; v28.43) ----------------
        # ㉝ next 的 rollback **不是**"拿 inject 前那份 .bak 整库还原": 缓存库是全机共用的,
        #    整库还原会把快照之后**别的论文**合法落下的译文一并退掉, 而且不报错。它做的是
        #    **行级**还原, 范围 = 本篇载荷那几段, 判据 = provenance(现行值 == imported.json
        #    里我们注入的那一版), 还原成段表记下的 output。上面 ㉛ 刚好把 ORD 那一行的四条
        #    全改成了 "改XXX。", 于是这一段直接用那份现场 —— 四条必须**各还原到自己那段**
        #    (若定位口径串了, 症状就是某段的译文变成别段的意思)。
        def run_rollback(tk_, man_, imp_, dry=False):
            ip = os.path.join(tmp, "roll.imported.json")
            with open(ip, "w", encoding="utf-8") as f:
                json.dump(imp_, f, ensure_ascii=False)
            argv = ["--rollback", "--imported", ip, "--manifest", man_, "--tracking", tk_]
            if dry:
                argv.append("--dry")
            return subprocess.run([sys.executable, os.path.join(TOOLS, "seg_inject.py")] + argv,
                                  env=dict(os.environ, P2Z_ENGINE="next", P2Z_PROJ=root,
                                           USERPROFILE=shome, HOME=shome),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")

        ord_imp = {"S1": "改一乙。", "S2": "改首段。", "S3": "改一甲。", "S4": "改尾段。"}
        ord_after = {0: "改尾段。", 1: "改首段。", 2: "改一甲。", 3: "改一乙。"}
        check("㉝ 前置现场: ㉛ 注入后四条都是我们那一版",
              cache_outputs(sdb, ORD) == ord_after, cache_outputs(sdb, ORD))

        pr = run_rollback(tk_ord, man_ord, ord_imp, dry=True)
        check("㉝ --dry 只演算不写库(库里还是注入后的样子)",
              pr.returncode == 0 and "dry-run 未写库" in pr.stdout
              and cache_outputs(sdb, ORD) == ord_after, (pr.returncode, pr.stdout[-240:]))

        pr = run_rollback(tk_ord, man_ord, ord_imp)
        check("㉝ 实写还原: 四条各还原成**引擎当时的译文**(串段就会把别段的意思抄过来)",
              pr.returncode == 0 and "还原行数: 4" in pr.stdout
              and cache_outputs(sdb, ORD) == {i: t for i, t in enumerate(ORD_TXT)},
              (pr.returncode, cache_outputs(sdb, ORD)))
        check("㉝ 撤销前先备份(缓存手术的止损坏步骤)", ".bak-" in pr.stdout, pr.stdout[-200:])

        pr = run_rollback(tk_ord, man_ord, ord_imp)
        check("㉝ 再撤一次是幂等的: 已是引擎译文 -> 报 0 行不动",
              pr.returncode == 0 and "还原行数: 0" in pr.stdout
              and "已是引擎当时的译文" in pr.stdout, (pr.returncode, pr.stdout[-240:]))

        seed_cache(sdb, [{"id": 0, "output": "尾段。"}, {"id": 1, "output": "首段。"},
                         {"id": 2, "output": "别人改的一甲。"}, {"id": 3, "output": "正文一乙。"}],
                   prompt=ORD)
        pr = run_rollback(tk_ord, man_ord, ord_imp)
        check("㉝ 现行值既不是我们注入的那版、也不是段表那版 -> **不碰只报告**"
              "(少了这条判据, 撤销就成了把引擎译文强塞回去, 会覆盖别人的合法改动)",
              pr.returncode == 0 and "已被别的改动覆盖" in pr.stdout
              and cache_outputs(sdb, ORD)[2] == "别人改的一甲。",
              (pr.returncode, cache_outputs(sdb, ORD)))

        # 段表与载荷对不上 -> 拒收, 且**一个字节都不写**(对账口径与 inject 共用 align_next;
        # 撤销若用另一套口径, 撤的就是别的段, 而两边都"看起来对上了")
        pr = run_rollback(tk_other, man_inj_p, {"S1": "改。"})
        check("㉝ 段表与载荷对不上 -> rc=1 且指路 --tracking",
              pr.returncode == 1 and "--tracking" in pr.stdout, (pr.returncode, pr.stdout[-260:]))
        check("㉝ 对不上时一个字节都没写", cache_outputs(sdb, ORD)[2] == "别人改的一甲。",
              cache_outputs(sdb, ORD))

        # prompt 行已不在(缓存被清 / 那一次调用换了键) -> 报"无残留"而不是失败: 撤销是
        # 止损坏步骤, "没东西可撤"本身就是它要的结果, 判成 FAIL 会让自动回路卡死。
        tk_gone = write_tracking_obj(tmp, {
            "cross_column": [], "cross_page": [],
            "page": [{"paragraph": [dict(TRACK_INJ["page"][0]["paragraph"][0],
                                        llm_translate_trackers=[
                                            {"input": "PROMPT-GONE", "output": "",
                                             "has_error": False}])]}]}, "gone_tracking.json")
        man_gone = write_tracking_obj(tmp, {
            "name": "gone", "pages": "1", "engine": "next",
            "source": {"kind": "tracking_json", "path": tk_gone},
            "items": [man_item("S1", ("page", 0, 0), P1[0]["input"], batch=(3, 0))]},
            "gone.manifest.json")
        pr = run_rollback(tk_gone, man_gone, {"S1": "改。"})
        check("㉝ prompt 行已不在 -> 报无残留且 PASS(不算失败)",
              pr.returncode == 0 and "还原行数: 0" in pr.stdout and "无残留" in pr.stdout,
              (pr.returncode, pr.stdout[-240:]))

        # 三个输入缺一不可 / --fp 是 1.x 的口径 -> 都在 argparse 之前被拦, 不猜
        env_n = dict(os.environ, P2Z_ENGINE="next", P2Z_PROJ=root,
                     USERPROFILE=shome, HOME=shome)

        def ro_argv(extra):
            return subprocess.run([sys.executable, os.path.join(TOOLS, "seg_inject.py"),
                                   "--rollback"] + extra, env=env_n, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")

        p = ro_argv([])
        check("㉝ 缺 --imported/--manifest -> rc=1 且说清这两个参数各管什么",
              p.returncode == 1 and "需要 --imported 与 --manifest" in p.stdout,
              (p.returncode, p.stdout[-240:]))
        p = ro_argv(["--imported", os.path.join(tmp, "没有这个.json"),
                     "--manifest", man_ord])
        check("㉝ imported.json 不存在 -> rc=1(不是拿 traceback 当报错)",
              p.returncode == 1 and "找不到 imported.json" in p.stdout
              and "Traceback" not in p.stdout, (p.returncode, p.stdout[-240:]))
        p = ro_argv(["--imported", os.path.join(tmp, "roll.imported.json"),
                     "--manifest", man_ord, "--fp", "docsummary:12345678:abcd"])
        check("㉝ --fp 是 1.x 的文档作用域指纹 -> 拒答(不静默忽略)",
              p.returncode == 1 and "不需要它" in p.stdout, (p.returncode, p.stdout[-240:]))

        # adopt 侧的分流(子进程本身上面已经验过, 这里只锁"派发对不对"): next 上必须走
        # next 那条, 不许先算 fp / 去找按文档归档的侧车 —— 那套在 next 上没有对应物,
        # 算出来的作用域会指向不存在的文件, 撤销就变成静默空转。
        AD.use_engine("next")
        AD.save_ledger({"name": "roll_next", "engine": "next", "stages": {
            "export": {"manifest": man_ord, "tracking": {"path": tk_ord}},
            "import": {"imported": os.path.join(tmp, "roll.imported.json")}}})
        seen = []
        _rt = AD.run_tool
        AD.run_tool = lambda script, args, capture=False: (seen.append((script, list(args))),
                                                           (0, ""))[1]
        try:
            class RB(object):
                pass
            rb = RB()
            rb.name, rb.fp, rb.pdf, rb.tracking, rb.dry, rb.force = \
                "roll_next", "", "", "", False, True
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = AD.stage_rollback(rb)
        finally:
            AD.run_tool = _rt
            led_rb = AD.load_ledger("roll_next")
            AD.use_engine("pdf2zh")
        check("㉝ adopt 把 rollback 派成 seg_inject 的 next 口径(三输入齐全, 不带 --fp)",
              rc == 0 and seen and seen[-1][0] == "seg_inject.py"
              and "--rollback" in seen[-1][1] and "--fp" not in seen[-1][1]
              and "--imported" in seen[-1][1] and "--manifest" in seen[-1][1]
              and "--tracking" in seen[-1][1], (rc, seen))
        check("㉝ 撤销结果记账(rows/backup 进台账, rollback 是止损坏步骤所以不进门禁)",
              led_rb["stages"]["rollback"]["state"] == "ok"
              and "rows" in led_rb["stages"]["rollback"], led_rb["stages"]["rollback"])

        # ---------------- 渲染出口 (next: CLI 子进程; v28.42) ----------------
        # ㉜ next 的 render 不是"POST 服务端 + 轮询 taskId", 而是起 `pdf2zh_next` CLI
        #    **子进程**(next 根本没有服务端)。本用例拿一个 `.py` 假引擎把整条出口跑通
        #    (force_rerender 见到 `.py` 就用本解释器起它, 那是专为回归留的口子), 只锁
        #    "我们传下去的东西对不对" —— 这三条正是会**静默**失效的地方:
        #      · `--debug` **不许带**(v28.45): 带 debug 确实能让段表落盘(上游只在 debug
        #        或显式 working_dir 时写), 但它会把页码/段号标签、彩色框**烘进产物**,
        #        产物名还多一段 .debug —— 交付件不能带。段表根改由 config 的
        #        [translation].working_dir 决定, 两侧读同一份 config;
        #      · config 必须走**临时副本**且 ignore_cache=false: 上游 BaseTranslator
        #        在 true 时**既不读也不写**缓存(改缓存行 = 白改, 且不报错不留痕),
        #        而生产那份不许动(子进程 ConfigManager 会规范化写回);
        #      · 判成败只能看**新鲜产物**: 上游 ignore_error=True, 内部出错也退 0。
        #    另加渲染前的只读缓存探针(0 命中 = 这次整篇重译 = 花钱, 必须说出来)。
        #    USERPROFILE 覆写成沙箱: 画像的缓存库/工作根都从 ~ 派生, 否则探针会去读
        #    真实 home 下的库 —— 那就不叫离线用例了。
        fake_home = os.path.join(root, "home")
        rdir = os.path.join(root, "render")
        os.makedirs(fake_home, exist_ok=True)
        os.makedirs(rdir, exist_ok=True)
        flog = os.path.join(rdir, "fake_log.json")
        from pypdf import PdfWriter
        rpdf = os.path.join(rdir, "Fake Paper 2026.pdf")     # 名字带空格: 顺带锁产物行解析
        wr = PdfWriter()
        for _ in range(3):
            wr.add_blank_page(width=200, height=200)
        with open(rpdf, "wb") as f:
            wr.write(f)
        rcfg = os.path.join(rdir, "config.toml")
        cfg_text = ('[basic]\ndebug = false\n\n[translation]\nlang_in = "en"\n'
                    'ignore_cache = true\nqps = 200\n')
        with open(rcfg, "w", encoding="utf-8") as f:
            f.write(cfg_text)
        rtk = write_tracking_obj(rdir, {
            "cross_column": [], "cross_page": [],
            "page": [{"paragraph": [
                {"input": "Alpha paragraph one.", "output": "阿尔法第一段。",
                 "pdf_unicode": "Alpha paragraph one.",
                 "llm_translate_trackers": [{"input": "PROMPT-REGRESS-A", "output": "",
                                             "has_error": False}],
                 "placeholders": [], "multi_paragraph_id": None,
                 "multi_paragraph_index": None}]}]}, "translate_tracking.json")
        fake = os.path.join(rdir, "fake_next.py")
        with open(fake, "w", encoding="utf-8") as f:
            f.write("# -*- coding: utf-8 -*-\n"
                    "import json, os, sys\n"
                    "a = sys.argv[1:]\n"
                    "pdf = a[0]\n"
                    "out = a[a.index('--output') + 1]\n"
                    "cfg = a[a.index('--config-file') + 1]\n"
                    "stem = os.path.splitext(os.path.basename(pdf))[0]\n"
                    "base = stem + '.no_watermark.' + a[a.index('--lang-out') + 1]\n"
                    "for kind in ('mono', 'dual'):\n"
                    "    g = open(os.path.join(out, base + '.' + kind + '.pdf'), 'w',"
                    " encoding='utf-8')\n"
                    "    g.write('fake ' + kind)\n"
                    "    g.close()\n"
                    "rec = {'argv': a, 'cfg': cfg,\n"
                    "       'cfg_text': open(cfg, encoding='utf-8').read()}\n"
                    "with open(os.environ['FAKE_NEXT_LOG'], 'w', encoding='utf-8') as g:\n"
                    "    json.dump(rec, g, ensure_ascii=False)\n"
                    "print('translate call count: 18')\n"
                    "print('translate cache call count: 18')\n")
        pr = subprocess.run(
            [sys.executable, os.path.join(TOOLS, "force_rerender.py"),
             "--pdf", rpdf, "--exe", fake, "--config", rcfg, "--output", rdir,
             "--tracking", rtk, "--service", "bing", "--skip-last", "1", "--timeout", "60"],
            env=dict(os.environ, P2Z_ENGINE="next", P2Z_PROJ=root,
                     USERPROFILE=fake_home, HOME=fake_home, FAKE_NEXT_LOG=flog),
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("㉜ next 渲染出口跑通(假引擎, rc=0)", pr.returncode == 0,
              (pr.returncode, (pr.stdout or "")[-300:], (pr.stderr or "")[-500:]))
        rec = {}
        if os.path.exists(flog):
            with open(flog, encoding="utf-8") as f:
                rec = json.load(f)
        av = rec.get("argv") or []
        check("㉜ 子进程参数形状: 原文 + 服务开关 + 语言 + 输出目录, 且**不带 --debug**"
              "(debug 会往交付件里烘调试图层 + 改产物名)",
              av and av[0] == rpdf and "--bing" in av and "--debug" not in av
              and av[av.index("--lang-out") + 1] == "zh-CN"
              and av[av.index("--output") + 1] == rdir, av)
        check("㉜ --skip-last 1 按 pypdf 换算成 --pages 1-2(不是把末页也译了)",
              "--pages" in av and av[av.index("--pages") + 1] == "1-2", av)
        check("㉜ 子进程拿到的是 config **临时副本**, 不是生产那份",
              bool(rec.get("cfg")) and os.path.dirname(rec["cfg"]) != rdir, rec.get("cfg"))
        check("㉜ 副本里 ignore_cache 置 false, 其余字节逐字不变",
              rec.get("cfg_text") == cfg_text.replace("ignore_cache = true",
                                                      "ignore_cache = false"),
              rec.get("cfg_text"))
        with open(rcfg, encoding="utf-8") as f:
            check("㉜ 生产 config 一个字节没动", f.read() == cfg_text)
        rmono = os.path.join(rdir, "Fake Paper 2026.no_watermark.zh-CN.mono.pdf")
        rdual = os.path.join(rdir, "Fake Paper 2026.no_watermark.zh-CN.dual.pdf")
        check("㉜ 判成败只看新鲜产物: 认 next 的 <stem>.no_watermark.<lang>.mono.pdf 命名"
              " + 耗时行与 1.x 同格式",
              ("产物: " + rmono) in pr.stdout and "耗时" in pr.stdout and "-> 成功" in pr.stdout,
              pr.stdout[-400:])
        check("㉜ adopt 的 mono 判定(共享常量)认得出 next 命名 —— 否则 gate 拿 dual 去验收",
              bool(AD.MONO_RE.search(rmono)) and not AD.MONO_RE.search(rdual), rmono)
        check("㉜ 读数行(总调用/缓存命中)照样解析出来",
              "读数: 调用 18 / 缓存命中 18" in pr.stdout, pr.stdout[-300:])
        check("㉜ 渲染前 0 命中要告警(整篇重译 = 既花钱又慢, 不能默默跑)",
              "自检: 缓存命中 0/1 段" in pr.stdout and "⚠ 0 命中" in pr.stdout,
              pr.stdout[-300:])

        # ---------------- 段表根 = config 的 [translation].working_dir (v28.45) -------------
        # ㉞ 这是"第一公里(server 起 pdf2zh_next)与第二公里(本工具定位段表)必须同一个值"
        #    的唯一保证: 双方都读同一份 config 的同一个键。写死或按 debug 缺省推都会错位,
        #    而错位的症状是"找不到段表" = 空载荷 = 静默。
        wd_cfg = os.path.join(rdir, "wd.toml")
        with open(wd_cfg, "w", encoding="utf-8") as f:
            f.write('[translation]\nworking_dir = "%s"\n'
                    % os.path.join(rdir, "nextwork").replace("\\", "/"))
        check("㉞ next 段表根取自 config 的 [translation].working_dir",
              EG.next_working_dir(wd_cfg)
              == os.path.abspath(os.path.join(rdir, "nextwork")),
              EG.next_working_dir(wd_cfg))
        nul_cfg = os.path.join(rdir, "nul.toml")
        with open(nul_cfg, "w", encoding="utf-8") as f:
            f.write('[translation]\nworking_dir = "null"\n')
        check("㉞ working_dir 为 \"null\"(toml 版的 None) -> 回落上游 debug 缺省, 不把 "
              "\"null\" 当目录名",
              EG.next_working_dir(nul_cfg) == EG.NEXT_WORKING_FALLBACK,
              EG.next_working_dir(nul_cfg))
        miss_cfg = os.path.join(rdir, "nope.toml")
        check("㉞ config 不存在/没有该键 -> 回落缺省(不抛异常, 旧配置当场失效但不崩)",
              EG.next_working_dir(miss_cfg) == EG.NEXT_WORKING_FALLBACK
              and EG.next_working_dir(rdir) == EG.NEXT_WORKING_FALLBACK,
              EG.next_working_dir(rdir))

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
