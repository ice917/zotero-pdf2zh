# -*- coding: utf-8 -*-
"""post_check 门禁单元测试（第四断言: 文献区禁汉化 / 第五断言: 渲染残渣）

运行: venv python test_post_check.py, 退出码 0=全过
不触网/不读盘: 用合成 feats 字典直接调 post_check.run_checks():
  - 只测断言逻辑, 不构造真实 PDF
  - 断言1(汉化率)只在原文页 chars>=MIN_SUBSTANTIAL 时参与, 故本测试
    按 EgoPhys 实测的页规模给 chars(正文页 2000, 文献页 3000)以贴近现场
  - 判据口径(2026-09-28 起, 声明式): 第 4 断言以 seg_export 的 DNT 声明
    (manifest 的 dnt_declared / 逐段 dnt) 为准, 核验声明禁翻页的条目是否被
    汉化(存在性, 门槛 REF_ZH_MIN); 另用独立判据 is_ref_page(密度臂)做覆盖审计。
    无声明 -> 判"未验证"。本文件多数**非第 4 断言**用例用 ISO(空声明)把第 4 断言
    隔离成"空真通过", 专测第 4 断言的用例另传 DECL(...) 显式声明。
  - 第五断言(渲染残渣)是**差分**判据: 只报"译文有、原文整篇没有"的标签形态,
    故测试同时覆盖"官方正样本"与"我方负样本(正文合法的 <EOS>/<pad>)"
"""
import json
import os
import shutil
import sys
import tempfile

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import post_check

# [本质修 2026-09-28] 第 4 断言从"启发式反推"改为"比对 DNT 声明": 无声明即判
# 「未验证」。本组多数用例只关心**其他断言**的行为, 故用空声明 ISO 把第 4 断言
# 隔离成"空真通过" —— 声明为空、管辖范围也为空, 任何文献页都落进"管辖外(提示)",
# 不进 verdict(提示级)。专测第 4 断言的用例另传显式声明。
ISO = {"dnt_pages": set(), "covered_pages": set()}


def DECL(*pages):
    """显式 DNT 声明: 禁翻页 = 管辖页 = pages（供专测第 4 断言的用例）。"""
    return {"dnt_pages": set(pages), "covered_pages": set(pages)}


def feat(page, chars=2000, cjk=0, alnum=2000, cites=0,
         ref_entries=0, ref_zh_blocks=0, placeholders=0, error=None,
         ref_ay_entries=0, ref_zh_blocks_ay=0, residues=None,
         ref_heading=False):
    """构造一页质检特征(默认: 正文页, 无实质内容, 非文献页)

    ref_density / ref_ay_density 与 page_features() 同式推导, 保证测试与
    生产同一判据。residues 取"归一化后的标签形态"列表, 与
    post_check.page_residues() 的产出同形。
    """
    density = ref_entries * 1000.0 / chars if chars else 0.0
    ay_density = ref_ay_entries * 1000.0 / chars if chars else 0.0
    return {"page": page, "chars": chars, "cjk": cjk, "alnum": alnum,
            "cites": cites, "ref_entries": ref_entries,
            "ref_density": density, "ref_heading": ref_heading,
            "ref_zh_blocks": ref_zh_blocks,
            "ref_ay_entries": ref_ay_entries, "ref_ay_density": ay_density,
            "ref_zh_blocks_ay": ref_zh_blocks_ay,
            "placeholders": placeholders, "residues": list(residues or []),
            "error": error}


def ref_finding(findings):
    """只取与第四断言(文献区)相关的结论"""
    return [f for f in findings if "文献区" in f[2]]


def residue_finding(findings):
    """只取与第五断言(渲染残渣)相关的结论"""
    return [f for f in findings if "渲染残渣" in f[2]]


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

    # ---------- ① 文献条目被汉化 → 高危 FAIL ----------
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    t = [feat(9, chars=3000, cjk=900, alnum=2100, ref_entries=13,
              ref_zh_blocks=5)]
    fs, v = post_check.run_checks(o, t, **DECL(9))
    hits = ref_finding(fs)
    check("① 汉化文献页判高危", len(hits) == 1 and hits[0][0] == "高", str(fs))
    check("① 门禁判 FAIL", v == "FAIL", v)
    check("① 结论含汉化条数", bool(hits) and "5条" in hits[0][2],
          hits[0][2] if hits else "")

    # ---------- ② 文献页保持原样 → 通过 ----------
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    t = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    fs, v = post_check.run_checks(o, t, **DECL(9))
    hits = ref_finding(fs)
    check("② 原样文献页判通过", len(hits) == 1 and hits[0][0] == "通过", str(fs))
    check("② 门禁不误判 FAIL", v == "PASS", v)
    check("② 通过文案含声明覆盖页数",
          bool(hits) and "禁翻声明覆盖 1 个页面" in hits[0][2],
          hits[0][2] if hits else "")

    # ---------- ③ 非文献页(密度低于阈值)有汉字 → 不误伤 ----------
    # 正文页每千字行首 [n] 仅 1.0 条, 远低于 REF_DENSITY
    o = [feat(3, chars=2000, alnum=2000, ref_entries=2)]
    t = [feat(3, chars=2000, cjk=1000, alnum=1000, ref_entries=2,
              ref_zh_blocks=9)]
    fs, v = post_check.run_checks(o, t, **ISO)
    check("③ 非文献页不产出文献结论",
          not any(f[0] in ("高", "未验证") for f in ref_finding(fs)), str(fs))
    check("③ 门禁不误判 FAIL", v == "PASS", v)

    # ---------- ④ 回归 EgoPhys 第9页: 正文末页 + 文献开头 ----------
    # 实测密度 1.77 条/千字 < 2.5 → 不判文献页, 同页正文的中译(450 字)
    # 不得被当成"文献被汉化"(旧兜底口径曾在此误报)
    o = [feat(9, chars=3392, alnum=3392, ref_entries=6)]
    t = [feat(9, chars=2246, cjk=450, alnum=1796, ref_entries=6,
              ref_zh_blocks=2)]
    fs, v = post_check.run_checks(o, t, **ISO)
    check("④ 正文末页+文献开头不触发第四断言",
          not any(f[0] in ("高", "未验证") for f in ref_finding(fs)), str(fs))
    check("④ 门禁判 PASS", v == "PASS", v)

    # ---------- ⑤ 回归 EgoPhys 第10/11/12页: 纯文献页译文无汉字 ----------
    # 条目整条保留英文 → 译文 0 汉字是预期, 断言1 不得判"翻译缺失"
    o = [feat(10, chars=3477, alnum=3477, ref_entries=16)]
    t = [feat(10, chars=3499, alnum=3499, ref_entries=16)]
    fs, v = post_check.run_checks(o, t, **ISO)
    highs = [f for f in fs if f[0] == "高"]
    check("⑤ 纯文献页不判翻译缺失", len(highs) == 0, str(highs))
    check("⑤ 门禁判 PASS", v == "PASS", v)
    check("⑤ 产出文献保留通过项",
          any("文献页条目原样保留" in f[2] for f in fs), str(fs))

    # ---------- ⑥ 边界: 存在性口径 —— 只译 1 条也判事故 ----------
    # [V7] 原判据"该页被汉化条目数 >= REF_ZH_MIN(2)"会静默放过"只译了 1 条"的页;
    #      文献条目整条原样保留是硬要求, 不该给数量配额。
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    t = [feat(9, chars=3000, cjk=100, alnum=2900, ref_entries=13,
              ref_zh_blocks=1)]
    fs, v = post_check.run_checks(o, t, **DECL(9))
    hits = [f for f in ref_finding(fs) if f[0] == "高"]
    check("⑥ 文献区恰被汉化 1 条 -> 触发(存在性口径)",
          len(hits) == 1 and "第9页" in hits[0][1], str(fs))

    # ⑥b 一条都没被汉化 -> 不误判
    t = [feat(9, chars=3000, alnum=3000, ref_entries=13, ref_zh_blocks=0)]
    fs, v = post_check.run_checks(o, t, **DECL(9))
    check("⑥b 文献区 0 条被汉化 -> 不触发",
          all(f[0] != "高" for f in ref_finding(fs)), str(fs))

    # ---------- ⑥c [V9 2026-09-28] 页级第三臂: 标题锚 + 行首条目 >= 4 ----------
    # 背景: 首/半文献页的密度被同页正文摊薄(语料实测 6 页密度 1.29~2.18 全在
    # 2.5 之下), 靠"文献表标题就在本页"兜底; 全语料无"正文页带标题锚且>=4条目"
    # 的误收候选。阈值 4 = 实测最小值(Vaswani p10)。
    _p = feat(9, chars=4000, alnum=4000, ref_entries=4, ref_heading=True)
    check("⑥c 标题锚+4条目判文献页", post_check.is_ref_page(_p), _p)
    check("⑥c 无标题锚不判(条目再多也不豁免正文页)",
          not post_check.is_ref_page(
              feat(9, chars=4000, alnum=4000, ref_entries=4)), "第三臂缺锚不命中")
    check("⑥c 标题锚+3条目不判(阈值=实测最小值4)",
          not post_check.is_ref_page(
              feat(9, chars=4000, alnum=4000, ref_entries=3, ref_heading=True)),
          "低于 REF_HEADING_PAGE_MIN")
    check("⑥c 常量回归 REF_HEADING_PAGE_MIN=4",
          post_check.REF_HEADING_PAGE_MIN == 4, post_check.REF_HEADING_PAGE_MIN)

    # ⑥d [V9] 有标题无条目锚(Johnson 第三体例/Nature 上标等无编号体例) →
    #     刻意不收: 无条目锚则 zh_n 恒 0, 收进"通过"就是假查过。改为提示人工抽查。
    o = [feat(9, chars=3000, alnum=3000, ref_heading=True)]
    t = [feat(9, chars=3000, cjk=1000, alnum=2000, ref_heading=True)]
    fs, v = post_check.run_checks(o, t, **ISO)
    check("⑥d 无编号体例页不假称通过",
          not any(f[0] == "通过" and f[1] == "第9页" for f in fs),
          str(fs))
    check("⑥d 无判据提示人工抽查",
          any(f[0] == "提示" and "无判据" in f[2] and f[1] == "第9页" for f in fs),
          str(fs))
    check("⑥d 提示不阻断", v == "PASS", v)

    # ⑥e 已识别文献页的"通过"与 blind 页的提示并存, 各说各话
    o = [feat(3, chars=3000, alnum=3000, ref_entries=13),
         feat(9, chars=3000, alnum=3000, ref_heading=True)]
    t = [feat(3, chars=3000, alnum=3000, ref_entries=13),
         feat(9, chars=3000, cjk=1000, alnum=2000, ref_heading=True)]
    fs, v = post_check.run_checks(o, t, dnt_pages={3}, covered_pages={3, 9})
    check("⑥e 通过与无判据提示并存",
          any(f[0] == "通过" and "禁翻声明覆盖 1 个页面" in f[2] for f in fs)
          and any(f[0] == "提示" and f[1] == "第9页" for f in fs), str(fs))

    # ---------- ⑦ 提取失败的页 → 跳过, 不参与文献判定 ----------
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13),
         feat(10, chars=3000, ref_entries=13, error="broken")]
    t = [feat(9, chars=3000, alnum=3000, ref_entries=13),
         feat(10, chars=3000, cjk=900, ref_zh_blocks=9, error="broken")]
    fs, v = post_check.run_checks(o, t, **DECL(9))
    check("⑦ 报错页被跳过", len(ref_finding(fs)) == 1, str(fs))

    # ---------- ⑧ 多页混合: 只报被汉化的那页 ----------
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13),
         feat(10, chars=3000, alnum=3000, ref_entries=15)]
    t = [feat(9, chars=3000, cjk=900, alnum=2100, ref_entries=13,
              ref_zh_blocks=4),
         feat(10, chars=3000, alnum=3000, ref_entries=15)]
    fs, v = post_check.run_checks(o, t, **DECL(9, 10))
    hits = ref_finding(fs)
    check("⑧ 只报汉化页", len(hits) == 1 and "第9页" in hits[0][1], str(hits))
    check("⑧ 不牵连原样页", bool(hits) and "第10页" not in hits[0][1], str(hits))

    # ---------- ⑨ 串联: 全篇汉化率断言与第四断言同时触发 ----------
    # [v28.46] 断言1 改全篇口径后, 要让它出高危必须"参与页合计"低于阈值 ——
    # 第5页单独零汉字即足够(它是唯一参与页), 第12页是文献页被豁免、不参与合计。
    o = [feat(5, chars=2000, alnum=2000),
         feat(12, chars=3000, alnum=3000, ref_entries=14)]
    t = [feat(5, chars=2000, alnum=2000),
         feat(12, chars=3000, alnum=3000, ref_entries=14,
              ref_zh_blocks=6)]
    fs, v = post_check.run_checks(o, t, **DECL(12))
    highs = [f for f in fs if f[0] == "高"]
    check("⑨ 两条高危同时产出", len(highs) == 2, str(highs))
    check("⑨ 门禁判 FAIL", v == "FAIL", v)

    # ---------- ⑩ 常量口径回归(防阈值被误改) ----------
    check("⑩ CJK_FAIL=0.05(逐页读数)", post_check.CJK_FAIL == 0.05,
          post_check.CJK_FAIL)
    check("⑩ WHOLE_DOC_CJK_FAIL=0.18(全篇判定)",
          post_check.WHOLE_DOC_CJK_FAIL == 0.18, post_check.WHOLE_DOC_CJK_FAIL)
    check("⑩ zh_min=1(存在性口径, 取自契约)", post_check._DNT.zh_min() == 1,
          post_check._DNT.zh_min())
    check("⑩ REF_DENSITY=2.5", post_check.REF_DENSITY == 2.5,
          post_check.REF_DENSITY)
    check("⑩ REF_ZH_WINDOW=200", post_check.REF_ZH_WINDOW == 200,
          post_check.REF_ZH_WINDOW)

    # ---------- ⑪ 条目块切分与截断口径 ----------
    cb = post_check.count_zh_ref_blocks
    en = ("[1] K. Zhang, B. Li. EgoPhys. CVPR 2026.\n"
          "[2] J. Doe. Robotics. 2025.\n")
    check("⑪ 全英文条目不计数", cb(en) == 0, cb(en))
    zh = ("[1] K. 张，李四. EgoPhys. 计算机视觉. 2026.\n"
          "[2] J. Doe. Robotics. 2025.\n")
    check("⑪ 汉化条目计 1 条", cb(zh) == 1, cb(zh))
    both = ("[1] K. 张，李四. 2026.\n[2] 王五. 机器人. 2025.\n")
    check("⑪ 两条汉化计 2 条", cb(both) == 2, cb(both))
    # 末条之后同页正文的中文: 超过 REF_ZH_WINDOW 即被截断, 不吞进末条
    far = "[1] A. Smith. Title. 2024.\n" + "x" * 250 + "然后正文开始，中文段落。"
    check("⑪ 窗口截断不吞同页正文", cb(far) == 0, cb(far))
    check("⑪ 行中引用不算条目", cb("see [12] for details. 中文。") == 0)

    # ---------- ⑫ 报告渲染: 第四断言写入断言项说明 ----------
    fs, v = post_check.run_checks([feat(1, chars=100)], [feat(1, chars=100)], **ISO)
    md = post_check.build_report("t-mono.pdf", "t.pdf", 1, fs, v)
    check("⑫ 报告含第四断言说明", "文献区禁汉化" in md)
    check("⑫ 报告含门禁判定", "门禁判定: PASS" in md)
    check("⑫ 报告含断言项编号4", "4. **文献区禁汉化（声明式）**" in md)
    check("⑫ 报告含声明式判据", "dnt_declared" in md)

    # ---------- ⑬ 作者-年份制文献页: 无 [n] 也要被识别 ----------
    # 事故: Wang 2026 第15页 "R. Olfati-Saber and R. M. Murray (2004). ..."
    # 体例, 行首 [n] 密度恒 0 → 旧判据既不豁免断言1, 也不受断言4 保护。
    # 实测原文 (年)密度 3.58 (正文页最高 0.7)。
    o = [feat(15, chars=3916, alnum=3916, ref_ay_entries=14)]   # 3.58/千字
    t = [feat(15, chars=3607, cjk=107, alnum=2500, ref_ay_entries=14)]
    fs, v = post_check.run_checks(o, t, **DECL(15))
    check("⑬ 作者-年份制文献页不判翻译缺失",
          all(f[0] != "高" for f in fs), str(fs))
    check("⑬ 门禁判 PASS", v == "PASS", v)
    check("⑬ 计入文献页豁免项",
          any("文献页条目原样保留" in f[2] for f in fs), str(fs))
    check("⑬ 计入断言4 的文献页数",
          any("文献区禁汉化检查通过" in f[2] and "禁翻声明覆盖 1 个页面" in f[2]
              for f in fs), str(fs))

    # ---------- ⑭ 作者-年份制条目被汉化 → 高危 FAIL ----------
    # 注入前硅基译文实测: 标题被整条译成中文, 姓名与期刊名保留拉丁 → 26 条
    o = [feat(15, chars=3916, alnum=3916, ref_ay_entries=14)]
    t = [feat(15, chars=2600, cjk=330, alnum=1500, ref_ay_entries=0,
              ref_zh_blocks_ay=26)]
    fs, v = post_check.run_checks(o, t, **DECL(15))
    hits = ref_finding(fs)
    check("⑭ 汉化作者-年份条目判高危",
          len(hits) == 1 and hits[0][0] == "高", str(fs))
    check("⑭ 门禁判 FAIL", v == "FAIL", v)
    check("⑭ 结论含汉化条数", bool(hits) and "26条" in hits[0][2],
          hits[0][2] if hits else "")

    # ---------- ⑮ 编号制文献页 + 中文正文: 不得启用作者-年份臂 ----------
    # 回归 CLAP 第15-17页实测: 编号制文献页同页有中文正文, 姓名锚切块会
    # 误报 27/53/17 条 → 仅当该页确为作者-年份制文献页时才启用该臂。
    o = [feat(15, chars=3000, alnum=3000, ref_entries=10)]      # [n] 3.33/千字
    t = [feat(15, chars=3000, cjk=630, alnum=2300, ref_entries=10,
              ref_zh_blocks=0, ref_zh_blocks_ay=27)]
    fs, v = post_check.run_checks(o, t, **DECL(15))
    check("⑮ 编号制页忽略作者-年份臂",
          all(f[0] != "高" for f in ref_finding(fs)), str(ref_finding(fs)))
    check("⑮ 门禁判 PASS", v == "PASS", v)

    # ---------- ⑯ 姓名锚切块口径 ----------
    ca = post_check.count_zh_ay_blocks
    zh_case = ("差分隐私平均一致性：障碍、权衡与最优算法设计\n"
               "Automatica\nR. Olfati-Saber 与 R. M. Murray\n"
               "具有切换拓扑与时滞的智能体网络中的一致性问题\n"
               "IEEE Transactions on Automatic Control\n"
               "R. Olfati-Saber, J. A. Fax 与 R. M. Murray\n")
    check("⑯ 汉化条目（真实事故片段）计数>=2", ca(zh_case) >= 2, ca(zh_case))
    en_case = ("differentially private average consensus: Obstructions, "
               "trade-offs, and optimal algorithm design. Automatica 81, "
               "221-231. R. Olfati-Saber and R. M. Murray (2004). Consensus "
               "problems in networks of agents. IEEE Transactions on "
               "Automatic Control 49, 1520-1533.\n")
    check("⑯ 英文原样条目不计数", ca(en_case) == 0, ca(en_case))
    # 首锚之前（页眉/正文）与末锚之后（附录证明）的中文不属于条目内容
    edge = ("中文页眉标题\nR. A. Smith. Some English title. 2024.\n"
            "R. B. Jones. Another English title. 2025.\n中文附录证明如下。")
    check("⑯ 首锚前/末锚后的中文不计", ca(edge) == 0, ca(edge))
    # 两锚之间跨度过长 → 视为正文夹缝, 不计
    far = "R. A. Smith. Title. 2024.\n" + "x" * 500 + "\nR. B. Jones. T. 2025."
    check("⑯ 超长跨度不计", ca(far) == 0, ca(far))
    # 姓汉字化后仍能锚住（锚点不依赖姓氏保持拉丁）
    zh_name = ("具有时滞的多智能体系统一致性问题\nAutomatica\n"
               "R. 奥法蒂 与 R. 默里\n"
               "网络化系统的协作与一致性\nIEEE TAC\n"
               "W. 任 与 R. 比尔德\n")
    check("⑯ 姓名汉字化仍可锚定", ca(zh_name) >= 2, ca(zh_name))

    # ---------- ⑰ 作者-年份常数与报告文案 ----------
    check("⑰ REF_AY_SPAN_CAP=400", post_check.REF_AY_SPAN_CAP == 400,
          post_check.REF_AY_SPAN_CAP)
    check("⑰ REF_AY 命中原文条目体例",
          post_check.REF_AY.search(
              "R. Olfati-Saber and R. M. Murray (2004). Consensus problems")
          is not None)
    check("⑰ REF_AY 不认正文夹注",
          post_check.REF_AY.search(
              "as shown in (Olfati-Saber and Murray, 2004). we conclude")
          is None)
    fs, v = post_check.run_checks([feat(1, chars=100)], [feat(1, chars=100)], **ISO)
    md = post_check.build_report("t-mono.pdf", "t.pdf", 1, fs, v)
    check("⑰ 报告说明含声明式覆盖审计", "覆盖审计" in md)

    # ---------- ⑱ 第五断言: 渲染残渣（差分判据） ----------
    # 事故: 官方 BabelDOC 2.9.0 隔离评估产物第 2 页印出 `</style\x01id='25'>`
    # (IL 标签未回填)。我方 13 篇既有产物 0 处。
    # 注意: 本组 feats 用 chars=100(< MIN_SUBSTANTIAL) 让断言1 不参与,
    # 使结论只来自第五断言本身。
    RES = post_check.page_residues
    P = dict(chars=100, alnum=100)

    # ① 官方正样本: 原文整篇无标签形态, 译文第 2 页 1 处 → 高危 FAIL
    o = [feat(1, **P), feat(2, **P)]
    t = [feat(1, **P), feat(2, residues=["</styleid='25'>"], **P)]
    fs, v = post_check.run_checks(o, t)
    hits = residue_finding(fs)
    check("⑱ 官方残渣样本判高危", len(hits) == 1 and hits[0][0] == "高", str(fs))
    check("⑱ 门禁判 FAIL", v == "FAIL", v)
    check("⑱ 结论定位到第2页", bool(hits) and "第2页" in hits[0][1], str(hits))
    check("⑱ 结论回显残渣样本",
          bool(hits) and "</styleid='25'>" in hits[0][2],
          hits[0][2] if hits else "")

    # ② 我方负样本: 正文合法的 <EOS>/<pad> 原文里也有 → 不算残渣
    # (Vaswani 实测 原文 30 处 / 我方译文 2 处, 差分不增)
    o = [feat(13, residues=["<EOS>", "<pad>"], **P),
         feat(14, residues=["<EOS>"], **P)]
    t = [feat(13, residues=["<EOS>"], **P),
         feat(14, residues=["<EOS>", "<pad>"], **P)]
    fs, v = post_check.run_checks(o, t, **ISO)
    check("⑱ 原文同形标签不判残渣",
          all(f[0] != "高" for f in residue_finding(fs)), str(fs))
    check("⑱ 门禁判 PASS", v == "PASS", v)

    # ③ 译文出现原文没有的**新**形态 → 仍要报(差分不等于"有标签就放过")
    o = [feat(13, residues=["<EOS>", "<pad>"], **P)]
    t = [feat(13, residues=["<EOS>", "<pad>", "<spanclass='x'>"], **P)]
    fs, v = post_check.run_checks(o, t)
    hits = residue_finding(fs)
    check("⑱ 原文没有的新形态仍判高危",
          len(hits) == 1 and hits[0][0] == "高", str(fs))
    check("⑱ 只报新形态不牵连同形",
          bool(hits) and "<spanclass='x'>" in hits[0][2]
          and "<EOS>" not in hits[0][2], hits[0][2] if hits else "")

    # ④ 提取失败页 → 跳过, 不参与残渣判定
    o = [feat(2, residues=[], error="broken", **P)]
    t = [feat(2, residues=["</styleid='25'>"], error="broken", **P)]
    fs, v = post_check.run_checks(o, t)
    check("⑱ 报错页不参与残渣判定",
          all(f[0] != "高" for f in residue_finding(fs)), str(fs))

    # ⑤ 多页: 只报含残渣的那页
    o = [feat(1, **P), feat(2, **P), feat(3, **P)]
    t = [feat(1, **P), feat(2, residues=["</styleid='25'>"], **P),
         feat(3, **P)]
    fs, v = post_check.run_checks(o, t)
    hits = residue_finding(fs)
    check("⑱ 多页只报命中页",
          len(hits) == 1 and "第2页" in hits[0][1] and "第3页" not in hits[0][1],
          str(hits))

    # ⑥ 形态口径: 只认三类标签, 不认裸 token 名(后者在 NLP 论文正文里合法)
    check("⑱ 认闭合标签", RES("</style\x01id='25'>") == ["</styleid='25'>"],
          RES("</style\x01id='25'>"))
    check("⑱ 认带属性开标签", RES("<span class='x'>") == ["<spanclass='x'>"],
          RES("<span class='x'>"))
    check("⑱ 认自闭合标签", RES("<br/>") == ["<br/>"], RES("<br/>"))
    check("⑱ 不认裸 token 名 <EOS>", RES("<EOS>") == [], RES("<EOS>"))
    check("⑱ 不认裸 token 名 <pad>", RES("<pad>") == [], RES("<pad>"))
    # Vaswani 原文里被渲染换行的 "<EO\nS\n>": 无 = / 无斜杠, 不属三类
    check("⑱ 不认换行的 <EO\\nS\\n>", RES("<EO\nS\n>") == [], RES("<EO\nS\n>"))
    # 数学比较符不误伤
    check("⑱ 不认数学比较符", RES("p < 0.05, q > 0.1") == [],
          RES("p < 0.05, q > 0.1"))
    # 归一化: 属性里的空白/控制符差异视为同一形态(防渲染改写后绕过)
    check("⑱ 归一化抹平空白与控制符",
          RES("</style\x01id='25'>") == RES("</style  id='25'>"),
          (RES("</style\x01id='25'>"), RES("</style  id='25'>")))

    # ⑦ 报告: 第五断言写入断言项说明
    fs, v = post_check.run_checks([feat(1, **P)], [feat(1, **P)])
    md = post_check.build_report("t-mono.pdf", "t.pdf", 1, fs, v)
    check("⑱ 报告含第五断言说明", "5. **渲染残渣**" in md)
    check("⑱ 报告断言项编号齐 1-5",
          all(f"{i}. **" in md for i in range(1, 6)), md[-400:])

    # ---------- ⑲ 引用标号判据容忍提取空隙 ----------
    # [v28.35] 事故: SILAGE 逐页对账"原文 69 / 译文 88, 差 19"判 FAIL, 而页面
    # 已干净(无 `],]` 残留)。真根因是**提取形态**: 原文侧引用是公式字体字形,
    # pypdf 提取带空隙(实测第2页 `SVRG[ 16, 20],SCSG[ 22],` 8 处全 `[ ` 开头),
    # 译文侧回填字形原值无空隙(`[22]`)。旧判据 `\[\d{1,3}\]` 只认后者 → 虚高。
    # 放宽是**两侧同样放宽**(同一正则对原文与译文各跑一遍), 不是只放过译文。
    CITE = post_check.CITE_ANY
    check("⑲ 认无空隙引用", CITE.findall("[22]") == ["[22]"], CITE.findall("[22]"))
    check("⑲ 认前导空隙", CITE.findall("[ 22]") == ["[ 22]"],
          CITE.findall("[ 22]"))
    check("⑲ 认尾随空隙", CITE.findall("[22 ]") == ["[22 ]"],
          CITE.findall("[22 ]"))
    check("⑲ 认两侧空隙", CITE.findall("[ 22 ]") == ["[ 22 ]"],
          CITE.findall("[ 22 ]"))
    # [V6b 修 2026-09-28] 多引形态改按**组**计: 语料实测 22 篇里 14 篇带
    # `[n,m]`/`[n-m]` 形态(最高 38 处), 旧正则全漏计 → 断言2 对整组丢失失明;
    # 两侧同正则对称, 只提覆盖不产误报。超三位数([1234])与含非数字分隔仍不认
    check("⑲ 带逗号多引计一组", CITE.findall("[16, 20]") == ["[16, 20]"],
          CITE.findall("[16, 20]"))
    check("⑲ 连字符/en破折号范围计一组",
          CITE.findall("[1-3]") == ["[1-3]"]
          and CITE.findall("[12\u201315]") == ["[12\u201315]"],
          (CITE.findall("[1-3]"), CITE.findall("[12\u201315]")))
    check("⑲ 多引组内可带提取空隙",
          CITE.findall("[ 1, 2 , 3 ]") == ["[ 1, 2 , 3 ]"],
          CITE.findall("[ 1, 2 , 3 ]"))
    check("⑲ 非数字内容仍不计",
          CITE.findall("[a,b]") == [] and CITE.findall("[0.5]") == [],
          (CITE.findall("[a,b]"), CITE.findall("[0.5]")))
    check("⑲ 超三位数不计", CITE.findall("[1234]") == [], CITE.findall("[1234]"))
    # 同一段文本两侧形态不同(原文带空隙/译文无空隙) → 计数必须相等
    # (`[ 16, 20]` 1 组 + `[ 22]` + `[ 24]` = 3, 两侧同为 3)
    _o = post_check.text_features("SVRG[ 16, 20],SCSG[ 22], and SSRGD[ 24]", 1)
    _t = post_check.text_features("SVRG [16,20],SCSG [22], and SSRGD[ 24]", 1)
    check("⑲ 两侧形态不同但计数一致(多引对称)",
          _o["cites"] == _t["cites"] == 3, (_o["cites"], _t["cites"]))
    # 放宽后译文**真丢**引用仍要判 FAIL(容差外的差距不会被这条正则吃掉)
    fs, v = post_check.run_checks(
        [feat(2, chars=100, alnum=100, cites=8)],
        [feat(2, chars=100, alnum=100, cites=0)])
    check("⑲ 译文真丢引用仍判 FAIL", v == "FAIL", v)

    # ---------- ⑲b [V6a] o_total==0 → 断言2 不再静默蒸发 ----------
    # 语料实测 7/22 篇全文无 [n](上标数字/作者-年份/括号数字体例或提取受损):
    # 旧逻辑整条跳过、无产物无痕迹。现在出"未生效"提示, 且**不阻断** ——
    # 该篇确实无判据可拦, 提示级让支持侧看得见, 不制造误拦。
    fs, v = post_check.run_checks(
        [feat(3, chars=2000, alnum=2000)],
        [feat(3, chars=2000, cjk=1000, alnum=1000, cites=2)], **ISO)
    check("⑲b 无[n]体例断言2出提示不阻断",
          any(f[0] == "提示" and "引用完整性断言未生效" in f[2] for f in fs)
          and v == "PASS", (str(fs), v))
    check("⑲b 原文零标记而译文反有标记时如实报数",
          any(f[0] == "提示" and "译文却检出 2 个" in f[2] for f in fs),
          str(fs))

    # ---------- ⑳ 断言1 全篇口径（v28.46 起） ----------
    # 背景: Mandujano 第3,9,10,11,12,13,14 页是 Table 10.1/10.2(+continued)
    # 整页表格, 引擎按版面分类不翻 → 逐页口径判 FAIL 属假阳性; 且 PDF 文本层
    # 分不清"按规定不翻"与"卡死漏译"(实测 1.x 与 next 两引擎行为一致, 1.x 的
    # pdf2zh/high_level.py 也把 "table" 列入不译类 vcls), 故判定上移全篇,
    # 逐页零汉字降级为提示。定阈依据(蒙特卡洛 + 代价敏感)见 WHOLE_DOC_CJK_FAIL。
    # ① 局部整页表格零汉字 → 全篇达标 → PASS, 只出提示
    o = [feat(3, chars=1691, alnum=1691),
         feat(4, chars=2000), feat(5, chars=2000)]
    t = [feat(3, chars=1526, alnum=1526),          # 表格页, 与原文逐字相同
         feat(4, chars=1900, cjk=1000, alnum=900),
         feat(5, chars=1900, cjk=1000, alnum=900)]
    fs, v = post_check.run_checks(o, t, **ISO)
    check("⑳ 局部表格页不再判 FAIL", v == "PASS", str(fs))
    check("⑳ 零汉字页降级为提示",
          any(f[0] == "提示" and "第3页" in f[1] for f in fs), str(fs))
    check("⑳ 产出全篇读数",
          any(f[0] == "通过" and "参与页合计汉字占比" in f[2] for f in fs),
          str(fs))

    # ② 整篇零翻译 → 全篇 FAIL（这是该断言真正的监视目标）
    o = [feat(3, chars=2000), feat(4, chars=2000)]
    t = [feat(3, chars=2000), feat(4, chars=2000)]
    fs, v = post_check.run_checks(o, t, **ISO)
    check("⑳ 整篇零翻译判 FAIL", v == "FAIL", v)
    check("⑳ 全篇结论含阈值与参与页数",
          any(f[0] == "高" and "18%" in f[2] and "2 个参与页" in f[2]
              for f in fs), str(fs))

    # ③ 略高于阈值 → 仍 PASS（分界 0.18: 合计 400/2000 = 0.20）
    o = [feat(3, chars=2000), feat(4, chars=2000)]
    t = [feat(3, chars=2000, alnum=1000),
         feat(4, chars=2000, cjk=400, alnum=600)]
    fs, v = post_check.run_checks(o, t, **ISO)
    check("⑳ 略高于阈值判 PASS", v == "PASS", str(fs))
    check("⑳ 同篇仍保留零汉字提示",
          any(f[0] == "提示" and "第3页" in f[1] for f in fs), str(fs))

    # ④ 无参与页 → 提示跳过, 不误判 FAIL（文献页/跳页独占的短文档）
    o = [feat(3, chars=100, alnum=100)]
    t = [feat(3, chars=100, alnum=100)]
    fs, v = post_check.run_checks(o, t, **ISO)
    check("⑳ 无参与页不误判 FAIL", v == "PASS", v)
    check("⑳ 报明跳过原因",
          any("无参与汉化率断言的页" in f[2] for f in fs), str(fs))

    # ---------- ㉑ 本质修回归: 声明式契约(无声明→未验证/覆盖审计/三值) ----------
    # ㉑a 无声明(未传 --manifest) → 断言4 判"未验证", 绝不静默签发"通过"
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    t = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    fs, v = post_check.run_checks(o, t)
    check("㉑a 无声明判未验证",
          any(f[0] == "未验证" and "文献区" in f[2] for f in fs), str(fs))
    check("㉑a 无声明 verdict=UNVERIFIED(不静默通过)", v == "UNVERIFIED", v)

    # ㉑b 独立信号(密度臂)认定文献页却不在声明中, 且在管辖范围内 → 覆盖缺口=未验证
    fs, v = post_check.run_checks(o, t, dnt_pages=set(), covered_pages={9})
    check("㉑b 声明缺口判未验证",
          any(f[0] == "未验证" and "覆盖审计" in f[2] for f in fs), str(fs))
    check("㉑b 覆盖缺口 verdict=UNVERIFIED", v == "UNVERIFIED", v)

    # ㉑c 同一文献页落在声明管辖范围外 → 只提示, 不判未验证(声明没对它说话)
    fs, v = post_check.run_checks(o, t, dnt_pages=set(), covered_pages=set())
    check("㉑c 管辖外文献页仅提示",
          any(f[0] == "提示" and "不在本次导出范围内" in f[2] for f in fs)
          and not any(f[0] == "未验证" for f in fs), str(fs))

    # ㉑d 声明禁翻页, 但独立判据未认作文献页 → 提示留痕(信任声明), 不阻断
    o = [feat(3, chars=2000, alnum=2000)]
    t = [feat(3, chars=2000, cjk=500, alnum=1500)]
    fs, v = post_check.run_checks(o, t, **DECL(3))
    check("㉑d 声明页未被独立判据认可 → 提示留存",
          any(f[0] == "提示" and "未认作文献页" in f[2] for f in fs), str(fs))
    check("㉑d 该提示不阻断", v not in ("FAIL", "UNVERIFIED"), v)

    # ㉑e 空声明 + 导出范围内无文献页 → 空真通过(声明确实覆盖了"无文献区")
    fs, v = post_check.run_checks(o, t, **ISO)
    check("㉑e 空声明+无文献页 → 空真通过",
          any(f[0] == "通过" and "空声明" in f[2] for f in fs) and v == "PASS",
          (str(fs), v))

    # ㉑f 高危(违声明汉化)与未验证(覆盖缺口)并存 → FAIL 优先, 两者都可见
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13),
         feat(10, chars=3000, alnum=3000, ref_entries=14)]
    t = [feat(9, chars=3000, cjk=900, alnum=2100, ref_entries=13,
              ref_zh_blocks=5),
         feat(10, chars=3000, alnum=3000, ref_entries=14)]
    fs, v = post_check.run_checks(o, t, dnt_pages={9}, covered_pages={9, 10})
    check("㉑f 高危优先于未验证",
          v == "FAIL" and any(f[0] == "高" for f in fs)
          and any(f[0] == "未验证" for f in fs), (v, str(fs)))

    # ㉑g load_dnt_manifest 解析: 逐段 dnt 并集=禁翻页, 全部 true_page=管辖页;
    #     legacy(无 dnt_declared 字段) → 无声明(禁翻空), 由调用侧决定是否判未验证
    tmpd = tempfile.mkdtemp(prefix="dntman_")
    try:
        man = {"dnt_declared": True, "items": [
            {"key": "S1", "dnt": True, "parts": [{"true_page": 9}]},
            {"key": "S2", "parts": [{"true_page": 10}]},
            {"key": "S3", "dnt": True,
             "parts": [{"true_page": 9}, {"true_page": 11}]},
        ]}
        p1 = os.path.join(tmpd, "m1.json")
        with open(p1, "w", encoding="utf-8") as f:
            json.dump(man, f)
        dp, cp, decl = post_check.load_dnt_manifest(p1)
        check("㉑g manifest 解析禁翻页", dp == {9, 11}, dp)
        check("㉑g manifest 解析管辖页", cp == {9, 10, 11}, cp)
        check("㉑g manifest dnt_declared 读出", decl is True, decl)
        legacy = {"items": [{"key": "S1", "parts": [{"true_page": 9}]}]}
        p2 = os.path.join(tmpd, "m2.json")
        with open(p2, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        dp2, cp2, decl2 = post_check.load_dnt_manifest(p2)
        check("㉑g legacy 无 dnt_declared → 空禁翻",
              dp2 == set() and decl2 is False, (dp2, decl2))
        check("㉑g legacy 仍解析管辖页", cp2 == {9}, cp2)
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)

    # ㉑h 报告: UNVERIFIED 判定 + 橙标 + "需人工确认后交付"三处都在
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    t = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    fs, v = post_check.run_checks(o, t)
    md = post_check.build_report("t-mono.pdf", "t.pdf", 1, fs, v)
    check("㉑h 报告出 UNVERIFIED 与橙标与人工确认提示",
          "门禁判定: UNVERIFIED" in md and "🟠" in md
          and "需人工确认后交付" in md, md[:200])

    # ㉑i 退出码三值: 字面量已抽入契约(A1, tools/contracts/dnt.json), 源码不再内联;
    #     行为经 exit_code_for 仍为三值, 且未登记 verdict fail-closed 到 1(rc=2 保留)。
    _src = open(os.path.join(TOOLS, "post_check.py"), encoding="utf-8").read()
    check("㉑i 退出码字面量已抽出源码(改由契约承载)",
          '{"PASS": 0, "FAIL": 1, "UNVERIFIED": 3}' not in _src
          and "exit_code_for" in _src)
    check("㉑i exit_code_for 三值 0/1/3(随契约)",
          post_check.exit_code_for("PASS") == 0
          and post_check.exit_code_for("FAIL") == 1
          and post_check.exit_code_for("UNVERIFIED") == 3,
          "%s/%s/%s" % (post_check.exit_code_for("PASS"),
                        post_check.exit_code_for("FAIL"),
                        post_check.exit_code_for("UNVERIFIED")))
    check("㉑i 未登记 verdict fail-closed 到 1",
          post_check.exit_code_for("NOPE") == 1)

    print(f"\npost_check 门禁单元测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
