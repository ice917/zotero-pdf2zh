# -*- coding: utf-8 -*-
"""seg_import 返工单单元/集成测试 (v28.13, 2026-09-19)

要解决的**用户侧**问题: import 判 FAIL 时, 控制台打的是工具内部坐标
(实测 `FAIL p9#4 高价值字形未回锚 ["{v29}='3'"]`), 而:
  - 侧车的 page 是 receive_layout 的回调计数(图形对象也各占一号), 一篇 6 页的
    论文会报出 p9 —— 用户拿这个数字在 PDF 里翻不到那一页;
  - 报错只落控制台、不落盘, 译者(桥只读 inbox/)看不见;
  - 用户读完报错还得自己推断"该改哪一段、缺的是哪个字符、它在原文哪里"。

修法: FAIL 时自动写 inbox/<name>.rework.md (桥的 list_inbox / get_payload 直接
读得到), 写明 **真实页码 + #S编号 + 缺的字符 + 原文上下文**; PASS 时删掉旧单子
(过期单子比没有更糟: 译者会照它改已经改好的段)。真实页码由 seg_export 写进
manifest 的 true_page, 故本测试同时锁死 part_true_page 的三级退化口径。

保真战例: Padmaprabhan p6#4 "include {v29}D fabrication"({v29}='3') 被译成
"三维制造" → 数字字形没有落点 → FAIL。侧车内部坐标 9 / 真实页码 6。

⑨ 节 [v28.82] 锁**回锚埋点**的端到端: 本套件是全库唯一走 seg_import **真入口**
(subprocess) 的沙箱, 故埋点"到底会不会落盘、会不会改退出码"也在这里验 ——
埋点若永不触发就等于没有。

⑩ 节 [v28.83] 锁返工单「必须改」一节的**分栏**(甲"载荷没译完 -> 补译" / 乙"只差一串
字符 -> 写回"): 判据 fail_where 用 9 篇真译文的 26 处真 FAIL 逐条对账验证过。

运行: venv python test_seg_rework.py, 退出码 0=全过
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOLS, "seg_import.py")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import seg_import as SI  # noqa: E402

NAME = "demo2026"
HINT_MAX = SI.HINT_MAX          # 与实现同源, 免得阈值改了测试还在断言旧数字
HINT_N = HINT_MAX + 5           # ⑧ 造 HINT_N 条提示: 超限才会触发截断+计数

# 侧车内部坐标 9 / pageid 5(=真实第 6 页): 正是 Padmaprabhan 的漂移形态
SIDECAR = [
    {"page": 9, "pageid": 5, "vars": {"29": "3", "0": "alpha"},
     "segs": [{"raw": "Exciting future avenues include {v29}D fabrication for {v0} work."}]},
]
MANIFEST = {"name": NAME, "items": [
    {"key": "S1", "merged": False,
     "parts": [{"page": 9, "seg": 0, "true_page": 6}]}]}


def sandbox(tmp, manifest=MANIFEST, sidecar=SIDECAR):
    root = tempfile.mkdtemp(dir=tmp)
    os.makedirs(os.path.join(root, "inbox"), exist_ok=True)
    sp = os.path.join(root, "side.jsonl")
    with open(sp, "w", encoding="utf-8") as f:
        for o in sidecar:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    mp = os.path.join(root, "inbox", NAME + ".manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    return root, sp, mp


def run_import(root, sp, mp, text):
    """把 text 写成一份交件 -> 跑 seg_import; 返回 (rc, stdout, stderr, note路径)"""
    tp = os.path.join(root, "deliver.txt")
    with open(tp, "w", encoding="utf-8") as f:
        f.write(text)
    env = dict(os.environ, P2Z_PROJ=root, P2Z_INBOX=os.path.join(root, "inbox"),
               # [v28.82] 埋点台账钉在沙箱里: 免得回归往真项目 logs/ 里写行(污染工作区)
               P2Z_LEDGER=os.path.join(root, "logs", "reanchor_ledger.jsonl"))
    proc = subprocess.run([sys.executable, SCRIPT, "--manifest", mp,
                           "--text", tp, "--sidecar", sp],
                          env=env, cwd=root, capture_output=True)
    note = os.path.join(root, "inbox", NAME + ".rework.md")
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"), note)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def main():
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS {name}")
        else:
            failed += 1
            print(f"  FAIL {name} {detail}")

    # ---- ① part_true_page 的三级口径 ----
    o = SIDECAR[0]
    check("① 有 true_page 就用它",
          SI.part_true_page({"page": 9, "seg": 0, "true_page": 6}, o) == 6)
    check("① 老清单退回落 pageid(0 基 +1)",
          SI.part_true_page({"page": 9, "seg": 0}, o) == 6)
    check("① 老侧车连 pageid 都没有才退回内部坐标",
          SI.part_true_page({"page": 9, "seg": 0}, {"page": 9, "segs": []}) == 9)
    check("① 缺页记录不抛异常",
          SI.part_true_page({"page": 9, "seg": 0}, None) == 9)

    # ---- ② 原文上下文: 字形要**还原后**再切片, 不能切出半截占位符 ----
    ctx = SI.glyph_context(o["segs"][0]["raw"], o["vars"], "29")
    check("② 上下文里字形已还原", "include 3D fabrication" in ctx, ctx)
    check("② 上下文不是半截占位符", "{v2" not in ctx and "{v" not in ctx, ctx)
    check("② 找不到字形时返回空串",
          SI.glyph_context(o["segs"][0]["raw"], o["vars"], "99") == "")
    # 长段才截断: 前后都够长时两端打省略号
    long_ctx = SI.glyph_context("A" * 80 + "{v0}" + "B" * 80, {"0": "x"}, "0", width=10)
    check("② 长段两端打省略号",
          long_ctx.startswith("…") and long_ctx.endswith("…") and "x" in long_ctx, long_ctx)

    # ---- ②b [v28.80] 不可见字符也在这里剥(与 seg_export.restore 同源) ----
    # 口径与载荷侧一致, 否则返工单里引用的原文与译者收到的载荷对不上号。
    # 必须**逐片**剥: spans 是按剥后文本算的, 末尾整串剥会让下标全漂。
    ZWSP = "\u200b"
    iv_raw, iv_vv = "AA" + ZWSP + "{v0}" + ZWSP + "BB", {"0": "x" + ZWSP + "y"}
    iv_txt, iv_spans = SI.restore_with_spans(iv_raw, iv_vv)
    check("②b 原文与字形值两侧都剥", iv_txt == "AAxyBB", repr(iv_txt))
    check("②b 字形位置按剥后的文本算(不漂)",
          iv_txt[iv_spans["0"][0][0]:iv_spans["0"][0][1]] == "xy", iv_spans)
    check("②b 切片结果里不再残留零宽",
          SI.glyph_context(iv_raw, iv_vv, "0") == "AAxyBB",
          repr(SI.glyph_context(iv_raw, iv_vv, "0")))

    with tempfile.TemporaryDirectory(prefix="p2z_seg_rework_") as tmp:
        # ---- ③ FAIL: 落返工单, 报真实页码 + 段号 + 缺的字符 + 原文线索 ----
        root, sp, mp = sandbox(tmp)
        rc, out, err, note = run_import(root, sp, mp, "#S1\n三维制造是 alpha 的未来方向。\n")
        check("③ 缺字形判 FAIL", rc == 1, (rc, out, err))
        check("③ 返工单已落盘", os.path.exists(note), note)
        note_txt = read(note) if os.path.exists(note) else ""
        check("③ 单子点名真实页 + #S编号", "### #S1（第 6 页）" in note_txt, note_txt[:400])
        check("③ 单子不拿内部坐标当页码",
              "第 9 页" not in note_txt.split("## 一")[-1], note_txt[:400])
        check("③ 单子写明缺哪个字符", "`3`" in note_txt, note_txt[:600])
        check("③ 单子给出原文线索(字形已还原)",
              "include 3D fabrication" in note_txt, note_txt[:600])
        check("③ 单子要求只改点到的段", "只改下面点到的段" in note_txt)
        check("③ 单子写明交件名与段号范围",
              "demo2026.webai.txt" in note_txt and "#S1–#S1" in note_txt, note_txt[-300:])
        check("③ 单子判 FAIL 计数正确",
              "需返工 1 段 / 1 处字符没有落点" in note_txt, note_txt[:300])
        check("③ 没有别的小节时不空编号",
              "## 一、必须改" in note_txt and "## 二、" not in note_txt, note_txt)
        # [v28.83] 分栏: 本段载荷里 {v29}(='3') 之后还有 {v0}(='alpha') 锚上了,
        # 说明句子本身译了、只是这一串没写出来 -> 乙栏; 甲栏"回载荷补译"那套不该出现。
        check("③ 只差一串字符的段落落在「乙」栏",
              "**乙栏" in note_txt and "**甲栏" not in note_txt, note_txt[:900])
        check("③ 控制台改成真实页口径",
              "第6页" in out and "p9#" not in out, out)
        check("③ 控制台点出返工单路径", "返工单:" in out and note in out, out)
        check("③ 控制台结论 FAIL", "结论: FAIL" in out, out)

        # ---- ④ 重交改了 -> PASS, 且把过期单子删掉 ----
        rc, out, err, note = run_import(root, sp, mp, "#S1\n3D 制造是 alpha 的未来方向。\n")
        check("④ 改对后 PASS", rc == 0, (rc, out, err))
        check("④ 结论 PASS", "结论: PASS" in out, out)
        check("④ 过期返工单被删掉", not os.path.exists(note), note)
        check("④ PASS 时不再提返工单", "返工单:" not in out, out)

        # ---- ⑤ 非回锚类 FAIL(缺段) 与"提示"要分节呈现, 且不误算返工段数 ----
        # 用户读单子时要能一眼分清"必须改"和"工具已处理, 别动"。
        man2 = {"name": NAME, "items": [
            {"key": "S1", "merged": False,
             "parts": [{"page": 9, "seg": 0, "true_page": 6}]},
            {"key": "S2", "merged": False,
             "parts": [{"page": 9, "seg": 0, "true_page": 6}]}]}
        side2 = [{"page": 9, "pageid": 5, "vars": {"29": "3", "7": "-"},
                  "segs": [{"raw": "Exciting {v29}D work {v7} done."}]}]
        root2, sp2, mp2 = sandbox(tmp, manifest=man2, sidecar=side2)
        rc, out, err, note2 = run_import(root2, sp2, mp2, "#S1\n3D work done.\n")
        check("⑤ 缺段仍判 FAIL", rc == 1, (rc, out, err))
        n2 = read(note2) if os.path.exists(note2) else ""
        check("⑤ 缺段列在「其他门禁失败」", "其他门禁失败" in n2 and "FAIL 缺段: [2]" in n2, n2)
        check("⑤ 纯标点丢弃列在「不必改」",
              "不必改" in n2 and "纯标点字形丢弃" in n2, n2)
        check("⑤ 无返锚失败时不报数、也不空一节",
              "需返工 0 段" not in n2 and "## 一、其他门禁失败" in n2, n2[:300])
        check("⑤ 没点到的段仍要求照抄", "逐字符照抄上一版" in n2, n2)

        # ---- ⑥ 「必须改」与「不必改」同时出现: 编号 一 / 二 仍连续 ----
        man3 = {"name": NAME, "items": [
            {"key": "S1", "merged": False,
             "parts": [{"page": 9, "seg": 0, "true_page": 6}]}]}
        root3, sp3, mp3 = sandbox(tmp, manifest=man3, sidecar=side2)
        rc, out, err, note3 = run_import(root3, sp3, mp3, "#S1\n三维 work done.\n")
        n3 = read(note3) if os.path.exists(note3) else ""
        check("⑥ 两类小节同时在且编号连续",
              "## 一、必须改" in n3 and "## 二、不必改" in n3, n3)

        # ---- ⑧ 「不必改」一节瘦身: 提示超过 HINT_MAX 条时只列前 N 条 + 报计数 ----
        # 一篇几千段的稿子「纯标点字形丢弃」能有几百条, 逐条列会把「必须改」淹没掉。
        # 判据仍是同一份实现(这些段工具已自动处理), 少列几条不影响译者照抄。
        man4 = {"name": NAME, "items": [
            {"key": "S%d" % i, "merged": False,
             "parts": [{"page": 9, "seg": 0, "true_page": 6}]}
            for i in range(1, HINT_N + 2)]}          # 末段故意不交, 逼出返工单
        root4, sp4, mp4 = sandbox(tmp, manifest=man4, sidecar=side2)
        deliver4 = "".join("#S%d\n3D work done.\n" % i for i in range(1, HINT_N + 1))
        rc, out, err, note4 = run_import(root4, sp4, mp4, deliver4)
        check("⑧ 缺段仍判 FAIL", rc == 1, (rc, out, err))
        n4 = read(note4) if os.path.exists(note4) else ""
        check("⑧ 提示超限时只列前 %d 条" % HINT_MAX,
              n4.count("纯标点字形丢弃") == HINT_MAX,
              n4.count("纯标点字形丢弃"))
        check("⑧ 其余条数报计数",
              "同上提示共 %d 条，其余 %d 条不再逐条列出" % (HINT_N, HINT_N - HINT_MAX)
              in n4, n4[-500:])
        check("⑧ 截断不影响「必须改」分节",
              "## 一、其他门禁失败" in n4 and "## 二、不必改" in n4, n4[:300])

        # ---- ⑩ [v28.83] 「必须改」按**修法**分栏: 段尾没译 -> 甲(补译) ----
        # 判据见 seg_import.fail_where: 载荷里本字形**之后**还有没有别的字形锚上了。
        # 用 9 篇真译文的 26 处真 FAIL 逐条对账验证过 -> 20/21 例与人工判读一致(见
        # 改动记录 9.7.3); 这里锁的是"渲染出来确实分了栏、且甲栏带载荷后半句"。
        man5 = {"name": NAME, "items": [
            {"key": "S1", "merged": False,
             "parts": [{"page": 9, "seg": 0, "true_page": 6}]}]}
        side5 = [{"page": 9, "pageid": 5, "vars": {"0": "ok", "1": "3"},
                  "segs": [{"raw": "Alpha {v0} done, and the tail carries {v1} yet."}]}]
        root5, sp5, mp5 = sandbox(tmp, manifest=man5, sidecar=side5)
        # 交付只译了前半句: {v0} 锚上了, 段尾的 {v1} 落在没译的那半截里
        rc, out, err, note5 = run_import(root5, sp5, mp5, "#S1\nAlpha ok 完成，\n")
        n5 = read(note5) if os.path.exists(note5) else ""
        check("⑩ 段尾没译的段落进「甲」栏(不混进乙栏)",
              rc == 1 and "**甲栏" in n5 and "**乙栏" not in n5, (rc, n5[:600]))
        check("⑩ 甲栏附载荷后半句(照它把没译的补上)",
              "载荷这一段的后半" in n5 and "the tail carries" in n5, n5)
        check("⑩ 甲栏的旁证是位置/长度, 不是判据",
              "位置对照" in n5 and "本字形在载荷" in n5, n5)

    # ---- ⑦ 名单为空(取不到论文名)时不写文件、不抛异常 ----
    check("⑦ 取不到 name 就不写单子",
          SI.write_rework_note({"items": []}, ".manifest.json", [], []) == "")

    # ---- ⑨ [v28.82] 回锚埋点端到端: 真入口(main)把异常字形按家族记进台账 ----
    # 埋点若永不触发就等于没有, 故这里走**真 subprocess 入口**, 断言三件事: 台账真的
    # 落盘且分得清 fail/drop、跨次**追加**而不是覆盖、以及**不改退出码**(记账与判据
    # 解耦 —— 私用区那条 rc 仍是 0, 因为私用区字形的丢弃本来就是设计行为)。
    PUA = "\uf0b3"                          # 私用区: isalnum() 为假 -> 走 drop
    MATH = "\U0001D45B"                     # 数学斜体 n -> 走 fail(它是"高价值字形")
    side_drop = [{"page": 9, "pageid": 5, "vars": {"0": PUA, "1": "alpha"},
                  "segs": [{"raw": "Glyph {v0} in {v1} text."}]}]
    side_fail = [{"page": 9, "pageid": 5, "vars": {"0": "x", "1": MATH},
                  "segs": [{"raw": "{v0} and {v1}"}]}]
    with tempfile.TemporaryDirectory() as tmp2:
        rootd, spd, mpd = sandbox(tmp2, sidecar=side_drop)
        rc1, out1, _e1, _n1 = run_import(rootd, spd, mpd, "#S1\nGlyph in alpha text.\n")
        led = os.path.join(rootd, "logs", "reanchor_ledger.jsonl")
        rows1 = [json.loads(ln) for ln in read(led).splitlines()] if os.path.exists(led) else []
        det1 = [r for r in rows1 if r["kind"] != "summary"]
        summ1 = next((r for r in rows1 if r["kind"] == "summary"), {})
        check("⑨ 私用区 drop 落进台账, 且不改退出码",
              rc1 == 0 and [r["fams"] for r in det1] == [["pua"]]
              and det1[0]["kind"] == "drop", (rc1, rows1))
        check("⑨ 每次回锚都留一条分类总账",
              summ1.get("fam_drop") == {"pua": 1} and summ1.get("n_plain_drop") == 0, summ1)
        check("⑨ 控制台当场报一句埋点(不必回头翻文件)", "埋点:" in out1, out1[-400:])

        rootf, spf, mpf = sandbox(tmp2, sidecar=side_fail)
        rc2, _out2, _e2, _n2 = run_import(rootf, spf, mpf, "#S1\nx and\n")
        ledf = os.path.join(rootf, "logs", "reanchor_ledger.jsonl")
        rows2 = [json.loads(ln) for ln in read(ledf).splitlines()] if os.path.exists(ledf) else []
        check("⑨ 数学字母真缺 -> fail 也记下(与 drop 分得清)",
              rc2 == 1 and [r["fams"] for r in rows2 if r["kind"] != "summary"] == [["math"]]
              and rows2[-1]["kind"] == "fail", (rc2, rows2))

        run_import(rootd, spd, mpd, "#S1\nGlyph in alpha text.\n")
        lines = [ln for ln in read(led).splitlines() if ln]
        check("⑨ 台账跨次追加, 不是覆盖(每轮一条总账 + 明细)",
              len(lines) == 4, lines)
        check("⑨ 台账落在本篇 PROJ 内(不写死本机路径)", led.startswith(rootd), led)

    print(f"\n结果: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
