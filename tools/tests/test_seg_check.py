# -*- coding: utf-8 -*-
"""seg_check 段表口径「局部漏译」门禁单元测试 (v30.2 起, 2026-09-24)

被锁死的缺口与两处误报 (都来自真实语料, 不是构造出来的):
  · 缺口: v28.46 把 post_check 第一断言改成**全篇口径**后, 残留的局部漏译无人把关
    (post_check §五自陈检出率 ≤10.9%)。补法是看**段表**(侧车 segs[].raw/trans)——
    产物侧分不清「引擎按规定不翻」与「卡死漏译」(在 PDF 文本层都表现为与原文逐字
    相同), 段表侧分得清。
  · 误报一(占首版候选 49%): 侧车里成堆**数学字形段**(`t`/`x`/`u u`/`Pi j`), 译文
    与原文逐字相同是引擎「公式不译」的**正确**行为。实测 Yoshioka 一篇 234 段候选,
    加「连续 4+ 字母」词面门槛后归零。
  · 误报二(Johnson 第3页): 「正文末页 + LITERATURE CITED + 条目」这种**半页文献块**
    页级密度必然低于阈值(is_ref_page 判 False), 条目按第三种体例写成
    `GOLD, H. S. 1959. …`(行首无 [n]、年份不带括号)两臂都抓不到 —— 故按**区域**
    判据(文献区标题锚点)豁免标题段及其后各段。

[v30.3] 追加「接缝切点」的两道口径 —— 文本口径圈候选, **几何核验**(原文坐标)滤噪音:
  · 侧车**不记坐标**, 「页内词内切点」与「正文正常换行/换栏」在段文本上长得一模一样。
    实测全语料 13 篇: 文本口径 921 处 → 几何口径只剩 35 处(强 16 / 中 19), **噪音率 96%**
    (Johnson 3 页 23 → 0)。故几何那一道不可省, 且**核验不了就不过滤**(宁可多报)。

本测试锁十段语义, 全部走 CLI (沙箱 + 合成侧车 + pymupdf 合成 PDF):
  ① 真漏译注入(某段 trans 原样留英文) -> 退出码 1, 且该段进候选清单
  ② 数学字形段(`Pi j`)与纯 `{vN}` 段 -> 不报, 且**不计入参与段**
  ③ 页内文献区块: 标题段(`LITERATURE CITED`)及其后条目段豁免; 标题**之前**的
     零汉字段照样报(豁免不得越界)
  ④ 页级文献页(原文页行首 `[n]` 密度 >= REF_DENSITY)整页豁免
  ⑤ 半译段(0 < 段内汉化率 < CJK_FAIL)只作**提示**, 不进判定
  ⑥ `--skip-last N` 豁免末尾页(与 post_check 同口径), 且早于文献页判定
  ⑦ 段表新鲜度: |段表 mtime - mono mtime| > LAG_TOL -> 中级警告但**不判 FAIL**
  ⑧ 接缝切点几何核验: 段文本**不在原文里**(合成侧车与 PDF 文本脱钩) -> **丢弃**;
     段文本在原文里**连续**(`Topol`|`ogy` → `Topology`) -> **强**; 同基线相接
     (`component`|`error`, 原文里就隔一个空格) -> **中**; 保留数 <= 文本口径数
  ⑨ `--no-geo`: 跳过几何核验 -> 全部候选都留(标注「未核验」), **不过滤**(不静默少报)
  ⑩ `--layout-cuts`(v30.4, 类② 的唯一覆盖面): 转调 `tools/layout_probe.py --cuts`。
     用 `PDF2ZH_LAYOUT_PROBE` 把探针换成受控件, 从而**不依赖版面 onnx 模型**:
       · 假探针吐 2 处切点 -> 进 stats/报告/JSON, 且**判定与其他读数一字不动**;
       · 探针路径不存在 -> 只报一行「版面切点读数不可用」, stats 不写、判定不动。

运行: venv python test_seg_check.py, 退出码 0=全过
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOLS, "seg_check.py")

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

import pymupdf  # noqa: E402

BODY1 = "Introduction This study investigates the growth of the fungus in culture."
BODY2 = "Discussion Our findings agree with those reported earlier for related species."
REF_LINES = ["[%d] Smith J. 19%02d. A study of aquatic fungi in culture. Journal of Mycology."
             % (i, 80 + i) for i in range(1, 7)]

# 页 4 (⑧): 原文里 `Topology` / `component` 各是**一个词**, 侧车却把它们切开 ——
# 这正是生产里「检测框边缘落在词内 → 逐字符 cls 跳变 → 断段」的形态。
L4_LINE1 = "Topology optimization for the locomotion task"
L4_LINE2 = "the component error is bounded"

ZH1 = "引言 本研究考察了这种真菌在培养中的生长情况。"          # 段内汉化率 1.0 -> ok
ZH2 = "讨论 我们的发现与早先的报道一致。"                      # 同上
LEAK = "Materials and methods were described previously by the same authors"

# 页 -> [(raw, trans), ...]  (侧车记录; 与 PDF 文本无关, 判据只看段表)
SEGS = {
    1: [
        ("Introduction This study investigates the growth of the fungus", ZH1),
        (LEAK, LEAK),                                   # ← ① 真漏译(原样回显)
        ("Pi j", "Pi j"),                               # ← ② 数学字形段
        ("{v0}{v1}", "{v0}{v1}"),                       # ← ② 纯占位符段
        ("Results showed significant differences among treatments",   # ← ⑤ 半译
         "结果 showed significant differences among treatments"),
    ],
    2: [
        (BODY2, ZH2),
        ("LITERATURE CITED", "LITERATURE CITED"),       # ← ③ 区域锚(标题段自身也豁免)
        ("GOLD H S 1959 Distribution of some aquatic fungi in North Carolina",
         "GOLD H S 1959 Distribution of some aquatic fungi in North Carolina"),
    ],
    3: [
        (REF_LINES[0], REF_LINES[0]),                   # ← ④ 页级文献页
    ],
    # ⑧ 接缝切点: 相邻四段 —— 三个候选里两个能过几何核验, 一个不行。
    # 注意 `ogy` / `error` 都是 **2+ 字母串**才被 geom_seam 取到(判据用 RUN2 = {2,})。
    4: [
        ("We study Topol", "我们研究拓扑"),              # + 下一段 -> `Topology` 连续 -> 强
        ("ogy optimization for the locomotion task", "ogy 优化运动任务"),
        # ↑ 它与再下一段在原文里**不相邻**(行1 的 `task` 后面没有 `the`) -> 丢弃
        ("the component", "该分量"),                     # + 下一段 -> 同一行隔一空格 -> 中
        ("error is bounded", "误差有界"),
    ],
}


def make_pdf(path, pages_lines):
    """pymupdf 合成 PDF: 每页若干行文本。"""
    doc = pymupdf.open()
    for lines in pages_lines:
        page = doc.new_page(width=420, height=700)
        for k, ln in enumerate(lines):
            page.insert_text((40, 60 + k * 14), ln, fontsize=9)
    doc.save(path)
    doc.close()
    return path


def make_sidecar(path, segs):
    with open(path, "w", encoding="utf-8") as f:
        for pg, items in segs.items():
            for raw, tr in items:
                f.write(json.dumps(
                    {"page": pg, "pageid": pg - 1,
                     "segs": [{"raw": raw, "trans": tr}]}, ensure_ascii=False) + "\n")
    return path


def run(root, *args, env=None, report=False):
    """跑一次 seg_check CLI。`env` 叠加在 os.environ 上（⑩ 用它换掉版面探针）；
    `report=False`（缺省）加 `--no-report` —— 不给真实 review 目录留垃圾。"""
    e = dict(os.environ)
    if env:
        e.update(env)
    cmd = ([sys.executable, SCRIPT, "--json"] + ([] if report else ["--no-report"])
           + list(args))
    proc = subprocess.run(cmd, cwd=root, capture_output=True, env=e)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def payload(stdout):
    """取 --json 那一块（print(json.dumps(..., indent=1)) 从行首 { 开始）。

    用 rfind 而不是 find: Windows 的 stdout 是 CRLF, 且正文里可能出现别的花括号;
    JSON 块是判定行之前最后一段, raw_decode 会自己跳过行首空白并忽略尾随文本。
    """
    i = stdout.rfind("\n{")
    if i < 0:
        return None
    obj, _ = json.JSONDecoder().raw_decode(stdout[i + 1:])
    return obj


def main():
    tmp = tempfile.mkdtemp(prefix="pdf2zh_segcheck_")
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    # 沙箱: t.pdf(原文) + t-mono.pdf(译版, 只用来推原文/核新鲜度) + a.jsonl(段表)
    # 页 4 的 PDF 文本是**原件**(`Topology` / `component` 各是一个词), 而侧车把它切开
    # —— ⑧ 的几何核验就是拿这份原文坐标去对段表。
    orig = make_pdf(os.path.join(tmp, "t.pdf"),
                    [[BODY1], [BODY2], REF_LINES, [L4_LINE1, L4_LINE2]])
    mono = make_pdf(os.path.join(tmp, "t-mono.pdf"),
                    [[BODY1], [BODY2], ["translated page three"]])
    sc = make_sidecar(os.path.join(tmp, "a.jsonl"), SEGS)
    t_mono = os.path.getmtime(mono)

    # ---- ① + ② + ③ + ④ + ⑤: 一段真漏译, 其余各档豁免 ----
    rc, so, se = run(tmp, "--mono", mono, "--sidecar", sc)
    js = payload(so)
    check("① 真漏译 -> 退出码 1", rc == 1, "%d %s" % (rc, se[-200:]))
    check("① 真漏译 -> 进候选清单", js and len(js["untranslated"]) == 1
          and LEAK[:20] in js["untranslated"][0]["head"], so.strip()[-400:])
    check("② 字形段/占位符段不计入参与段 (页1 5段-2段 + 页2 1段 + 页4 4段 = 8)",
          js and js["stats"]["segments"] == 8 and js["stats"]["nontext_segments"] == 2,
          js and js["stats"])
    check("② 字形段不进候选", js and all("Pi j" not in c["head"] for c in js["untranslated"]),
          js and js["untranslated"])
    check("③ 页内文献区块豁免 2 段(标题段 + 条目段)",
          js and js["stats"]["ref_segments"] == 2, js and js["stats"])
    check("③ 豁免不越界: 标题之前的零汉字段照样报(候选只在第 1 页)",
          js and {c["page"] for c in js["untranslated"]} == {1}
          and "第2页" not in so and "第3页" not in so, so.strip()[-400:])
    check("④ 页级文献页整页豁免", js and js["stats"]["exempt_pages"] == 1
          and "文献页豁免 [3]" in so, so.strip()[-400:])
    check("⑤ 半译段只作提示, 不进候选", js and js["stats"]["half"] == 1
          and len(js["untranslated"]) == 1, js and js["stats"])

    # ---- ⑧ 接缝切点: 文本口径圈候选, 几何核验(原文坐标)滤噪音 ----
    # 文本口径 5 处 = 页1 两处(合成侧车与 PDF 文本脱钩) + 页4 三处
    # 几何保留 2 处 = 页4 的 `Topol|ogy`(强) 与 `component|error`(中)
    check("⑧ 文本口径 5 处 → 几何保留 2 处(强 1 / 中 1), 丢弃 3 处行/块边界",
          js and js["stats"]["seam_raw"] == 5 and js["stats"]["seam_cuts"] == 2
          and js["stats"]["seam_geo_strong"] == 1 and js["stats"]["seam_geo_mid"] == 1
          and js["stats"]["seam_unverified"] == 0, js and js["stats"])
    check("⑧ 靠原文坐标定性的两处: `Topol`+`ogy` 判强, `component`|`error` 判中",
          js and sorted(x["grade"] for x in js["seam_cuts"]) == ["中", "强"]
          and any(x["detail"] == "Topol+ogy 原文连续" for x in js["seam_cuts"])
          and any(x["detail"] == "component|error 同基线相接"
                  for x in js["seam_cuts"]), js and js["seam_cuts"])
    check("⑧ 段文本不在原文里的候选被丢弃(页1 的 `fungus`|`Materials` 不进清单)",
          js and all("fungus" not in x["left"] for x in js["seam_cuts"]),
          js and js["seam_cuts"])
    check("⑧ 两道读数都写进报告(保留/文本)",
          js and js["stats"]["seam_cuts"] <= js["stats"]["seam_raw"]
          and "文本口径 5 处 → 几何保留 2 处（强 1 / 中 1）" in so, so.strip()[-500:])

    # ---- ⑨ --no-geo: 不过滤(核验不了宁可多报, 不静默少报) ----
    rc9, so9, se9 = run(tmp, "--mono", mono, "--sidecar", sc, "--no-geo")
    js9 = payload(so9)
    check("⑨ --no-geo 不过滤: 5 处候选全留且标注「未核验」",
          js9 and js9["stats"]["seam_raw"] == 5 and js9["stats"]["seam_cuts"] == 5
          and js9["stats"]["seam_unverified"] == 5
          and all(x["grade"] == "未核验" for x in js9["seam_cuts"]),
          js9 and js9["stats"])
    check("⑨ --no-geo 明写「几何未核验」, 判定不受影响",
          rc9 == 1 and "几何未核验" in so9 and js9 and js9["verdict"] == "FAIL",
          so9.strip()[-300:])

    # ---- ⑥ --skip-last 2: 末尾两页豁免, 且早于文献页判定(页3 既在末尾又是文献页) ----
    rc6, so6, se6 = run(tmp, "--mono", mono, "--sidecar", sc, "--skip-last", "2")
    js6 = payload(so6)
    check("⑥ --skip-last 2 -> 末尾页计入跳页而非文献页",
          js6 and js6["stats"]["skipped_pages"] == 2
          and js6["stats"]["exempt_pages"] == 0, js6 and js6["stats"])

    # ---- ⑦ 新鲜度: 同轮(段表比 mono 早 1 秒) -> 无警告 ----
    os.utime(sc, (t_mono - 1, t_mono - 1))
    rc7, so7, se7 = run(tmp, "--mono", mono, "--sidecar", sc)
    check("⑦ 同轮 -> 不出「不同轮」警告", rc7 == 1 and "不同轮" not in so7
          and "段表新鲜度: 比 mono 旧 1.0 秒" in so7, so7.strip()[-300:])

    # ---- ⑦ 新鲜度: 段表比 mono 新 1 小时 -> 中级警告, 但不判 FAIL ----
    os.utime(sc, (t_mono + 3601, t_mono + 3601))
    rc8, so8, se8 = run(tmp, "--mono", mono, "--sidecar", sc, "--skip-last", "4")
    js8 = payload(so8)
    check("⑦ 段表新 3601 秒 -> 中级警告", "[中]" in so8 and "不同轮" in so8
          and "新 3601 秒" in so8, so8.strip()[-400:])
    check("⑦ 新鲜度警告不判 FAIL (全页跳页 -> PASS, 退出码 0)",
          rc8 == 0 and js8 and js8["verdict"] == "PASS", "%d %s" % (rc8, so8.strip()[-300:]))

    # ---- ⑩ --layout-cuts: 转调 tools/layout_probe.py --cuts (类② 的唯一覆盖面) ----
    # 不依赖版面 onnx 模型: 用 PDF2ZH_LAYOUT_PROBE 把探针换成受控件 —— 一份吐受控 JSON
    # 的假探针(锁「读数进 stats/报告/JSON 且判定不动」), 一条不存在的路径(锁「拿不到只
    # 报一行不可用, 判定与其他读数一字不动」)。
    fake_probe = os.path.join(tmp, "fake_probe.py")
    with open(fake_probe, "w", encoding="utf-8") as f:
        f.write(
            "# -*- coding: utf-8 -*-\n"
            "import json\n"
            "print(json.dumps([\n"
            "    {\"page\": 1, \"chars\": 9, \"cuts\": [\n"
            "        {\"page\": 1, \"left\": \"o\", \"right\": \"g\", \"x_left\": 100.0,\n"
            "         \"x_right\": 103.5, \"y0\": 50.0, \"cls_left\": 1, \"cls_right\": 0,\n"
            "         \"keep_side\": \"右\", \"edge\": \"abandon#3 左边 x=104\"}]},\n"
            "    {\"page\": 2, \"chars\": 7, \"cuts\": [\n"
            "        {\"page\": 2, \"left\": \"m\", \"right\": \"o\", \"x_left\": 348.8,\n"
            "         \"x_right\": 354.4, \"y0\": 60.0, \"cls_left\": 0, \"cls_right\": 1,\n"
            "         \"keep_side\": \"左\", \"edge\": None}]},\n"
            "], ensure_ascii=False))\n")
    os.utime(sc, (t_mono - 1, t_mono - 1))     # 回到「同轮」(⑦ 把它改成了 +3601)
    rc_b, so_b, _ = run(tmp, "--mono", mono, "--sidecar", sc)
    base = payload(so_b)
    rc_a, so_a, se_a = run(tmp, "--mono", mono, "--sidecar", sc, "--layout-cuts",
                           env={"PDF2ZH_LAYOUT_PROBE": fake_probe})
    js_a = payload(so_a)
    check("⑩ --layout-cuts 把探针读数写进 stats(2 处 / 2 处进保留区 / 2 页)",
          js_a and js_a["stats"].get("layout_cuts") == 2
          and js_a["stats"].get("layout_keep") == 2
          and js_a["stats"].get("layout_pages") == 2, js_a and js_a["stats"])
    check("⑩ --layout-cuts 的判定与其他读数一字不动",
          rc_a == rc_b == 1 and js_a and base
          and js_a["verdict"] == base["verdict"]
          and all(js_a["stats"][k] == base["stats"][k] for k in (
              "segments", "untranslated", "half", "nontext_segments",
              "seam_raw", "seam_cuts", "seam_geo_strong", "seam_geo_mid")),
          js_a and base and (js_a["stats"], base["stats"]))
    check("⑩ 版面切点进控制台读数 + 提示级发现(含样本)",
          "版面切点（类②覆盖，cls 跳变）: 2 处" in so_a and "中英夹花" in so_a
          and "版面切点 2 处" in so_a and "p1 o|g" in so_a, so_a.strip()[-500:])

    # 报告那一节单独跑(要落盘): 生成后立刻删掉本测试自己那份, 不给 review 目录留垃圾。
    rc_c, so_c, _ = run(tmp, "--mono", mono, "--sidecar", sc, "--layout-cuts",
                        report=True, env={"PDF2ZH_LAYOUT_PROBE": fake_probe})
    rep = ""
    for ln in so_c.splitlines():
        if ln.startswith("📝 报告: "):
            rp = ln[len("📝 报告: "):].strip()
            if os.path.isfile(rp):
                with open(rp, encoding="utf-8") as f:
                    rep = f.read()
                os.remove(rp)
            break
    check("⑩ 版面切点清单进报告(逐列 + 保留侧 + 框边缘)",
          "## 版面切点清单" in rep and "| 1 | o | g | 100.0\\|103.5 |" in rep
          and "| 2 | m | o |" in rep and "abandon#3 左边 x=104" in rep
          and "保留侧" in rep and "- 版面切点(类②): 2 处（其中 2 处半词落进保留区）" in rep,
          rep[-400:] or so_c.strip()[-300:])
    check("⑩ JSON 带上逐页读数(2 页 / 2 处切点)",
          js_a and len(js_a["layout_cuts"]) == 2
          and all(len(p["cuts"]) == 1 for p in js_a["layout_cuts"]),
          js_a and js_a["layout_cuts"])

    rc_d, so_d, se_d = run(tmp, "--mono", mono, "--sidecar", sc, "--layout-cuts",
                           env={"PDF2ZH_LAYOUT_PROBE": os.path.join(tmp, "no_such_probe.py")})
    js_d = payload(so_d)
    check("⑩ 探针拿不到 -> 只报「不可用」, stats 不写、判定与读数不动",
          rc_d == rc_b == 1 and js_d and "layout_cuts" not in js_d["stats"]
          and js_d["layout_cuts"] == [] and js_d["verdict"] == base["verdict"]
          and "版面切点读数不可用" in so_d
          and all(js_d["stats"][k] == base["stats"][k] for k in (
              "segments", "untranslated", "seam_raw", "seam_cuts")),
          so_d.strip()[-400:])

    print("\nseg_check 段表漏译门禁单元测试: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
