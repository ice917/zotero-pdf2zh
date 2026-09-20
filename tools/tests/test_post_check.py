# -*- coding: utf-8 -*-
"""post_check 门禁单元测试（第四断言: 文献区禁汉化 / 第五断言: 渲染残渣）

运行: venv python test_post_check.py, 退出码 0=全过
不触网/不读盘: 用合成 feats 字典直接调 post_check.run_checks():
  - 只测断言逻辑, 不构造真实 PDF
  - 断言1(汉化率)只在原文页 chars>=MIN_SUBSTANTIAL 时参与, 故本测试
    按 EgoPhys 实测的页规模给 chars(正文页 2000, 文献页 3000)以贴近现场
  - 判据口径: 文献页 = 原文"行首 [n] 密度 >= REF_DENSITY(2.5 条/千字)"(编号制)
    或"作者-年份制条目密度 >= REF_DENSITY"(无行首 [n] 的 "姓名 (年)" 体例);
    事故 = 该页被汉化的"条目块"数 >= REF_ZH_MIN(2)
  - 第五断言(渲染残渣)是**差分**判据: 只报"译文有、原文整篇没有"的标签形态,
    故测试同时覆盖"官方正样本"与"我方负样本(正文合法的 <EOS>/<pad>)"
"""
import os
import sys

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


def feat(page, chars=2000, cjk=0, alnum=2000, cites=0,
         ref_entries=0, ref_zh_blocks=0, placeholders=0, error=None,
         ref_ay_entries=0, ref_zh_blocks_ay=0, residues=None):
    """构造一页质检特征(默认: 正文页, 无实质内容, 非文献页)

    ref_density / ref_ay_density 与 page_features() 同式推导, 保证测试与
    生产同一判据。residues 取"归一化后的标签形态"列表, 与
    post_check.page_residues() 的产出同形。
    """
    density = ref_entries * 1000.0 / chars if chars else 0.0
    ay_density = ref_ay_entries * 1000.0 / chars if chars else 0.0
    return {"page": page, "chars": chars, "cjk": cjk, "alnum": alnum,
            "cites": cites, "ref_entries": ref_entries,
            "ref_density": density, "ref_zh_blocks": ref_zh_blocks,
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
    fs, v = post_check.run_checks(o, t)
    hits = ref_finding(fs)
    check("① 汉化文献页判高危", len(hits) == 1 and hits[0][0] == "高", str(fs))
    check("① 门禁判 FAIL", v == "FAIL", v)
    check("① 结论含汉化条数", bool(hits) and "5条" in hits[0][2],
          hits[0][2] if hits else "")

    # ---------- ② 文献页保持原样 → 通过 ----------
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    t = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    fs, v = post_check.run_checks(o, t)
    hits = ref_finding(fs)
    check("② 原样文献页判通过", len(hits) == 1 and hits[0][0] == "通过", str(fs))
    check("② 门禁不误判 FAIL", v == "PASS", v)
    check("② 通过文案含文献页数", bool(hits) and "1 个文献页" in hits[0][2],
          hits[0][2] if hits else "")

    # ---------- ③ 非文献页(密度低于阈值)有汉字 → 不误伤 ----------
    # 正文页每千字行首 [n] 仅 1.0 条, 远低于 REF_DENSITY
    o = [feat(3, chars=2000, alnum=2000, ref_entries=2)]
    t = [feat(3, chars=2000, cjk=1000, alnum=1000, ref_entries=2,
              ref_zh_blocks=9)]
    fs, v = post_check.run_checks(o, t)
    check("③ 非文献页不产出文献结论", len(ref_finding(fs)) == 0, str(fs))
    check("③ 门禁不误判 FAIL", v == "PASS", v)

    # ---------- ④ 回归 EgoPhys 第9页: 正文末页 + 文献开头 ----------
    # 实测密度 1.77 条/千字 < 2.5 → 不判文献页, 同页正文的中译(450 字)
    # 不得被当成"文献被汉化"(旧兜底口径曾在此误报)
    o = [feat(9, chars=3392, alnum=3392, ref_entries=6)]
    t = [feat(9, chars=2246, cjk=450, alnum=1796, ref_entries=6,
              ref_zh_blocks=2)]
    fs, v = post_check.run_checks(o, t)
    check("④ 正文末页+文献开头不触发第四断言",
          len(ref_finding(fs)) == 0, str(fs))
    check("④ 门禁判 PASS", v == "PASS", v)

    # ---------- ⑤ 回归 EgoPhys 第10/11/12页: 纯文献页译文无汉字 ----------
    # 条目整条保留英文 → 译文 0 汉字是预期, 断言1 不得判"翻译缺失"
    o = [feat(10, chars=3477, alnum=3477, ref_entries=16)]
    t = [feat(10, chars=3499, alnum=3499, ref_entries=16)]
    fs, v = post_check.run_checks(o, t)
    highs = [f for f in fs if f[0] == "高"]
    check("⑤ 纯文献页不判翻译缺失", len(highs) == 0, str(highs))
    check("⑤ 门禁判 PASS", v == "PASS", v)
    check("⑤ 产出文献保留通过项",
          any("文献页条目原样保留" in f[2] for f in fs), str(fs))

    # ---------- ⑥ 边界: 汉化条目数恰为阈值-1 → 不触发 ----------
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
    t = [feat(9, chars=3000, cjk=200, alnum=2800, ref_entries=13,
              ref_zh_blocks=1)]
    fs, v = post_check.run_checks(o, t)
    check("⑥ 未达阈值不误判",
          all(f[0] != "高" for f in ref_finding(fs)), str(fs))

    # ---------- ⑦ 提取失败的页 → 跳过, 不参与文献判定 ----------
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13),
         feat(10, chars=3000, ref_entries=13, error="broken")]
    t = [feat(9, chars=3000, alnum=3000, ref_entries=13),
         feat(10, chars=3000, cjk=900, ref_zh_blocks=9, error="broken")]
    fs, v = post_check.run_checks(o, t)
    check("⑦ 报错页被跳过", len(ref_finding(fs)) == 1, str(fs))

    # ---------- ⑧ 多页混合: 只报被汉化的那页 ----------
    o = [feat(9, chars=3000, alnum=3000, ref_entries=13),
         feat(10, chars=3000, alnum=3000, ref_entries=15)]
    t = [feat(9, chars=3000, cjk=900, alnum=2100, ref_entries=13,
              ref_zh_blocks=4),
         feat(10, chars=3000, alnum=3000, ref_entries=15)]
    fs, v = post_check.run_checks(o, t)
    hits = ref_finding(fs)
    check("⑧ 只报汉化页", len(hits) == 1 and "第9页" in hits[0][1], str(hits))
    check("⑧ 不牵连原样页", bool(hits) and "第10页" not in hits[0][1], str(hits))

    # ---------- ⑨ 串联: 汉化率断言与第四断言同时触发 ----------
    o = [feat(5, chars=2000, alnum=2000),
         feat(12, chars=3000, alnum=3000, ref_entries=14)]
    t = [feat(5, chars=2000, alnum=2000),
         feat(12, chars=3000, cjk=900, alnum=2100, ref_entries=14,
              ref_zh_blocks=6)]
    fs, v = post_check.run_checks(o, t)
    highs = [f for f in fs if f[0] == "高"]
    check("⑨ 两条高危同时产出", len(highs) == 2, str(highs))
    check("⑨ 门禁判 FAIL", v == "FAIL", v)

    # ---------- ⑩ 常量口径回归(防阈值被误改) ----------
    check("⑩ REF_ZH_MIN=2", post_check.REF_ZH_MIN == 2, post_check.REF_ZH_MIN)
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
    fs, v = post_check.run_checks([feat(1, chars=100)], [feat(1, chars=100)])
    md = post_check.build_report("t-mono.pdf", "t.pdf", 1, fs, v)
    check("⑫ 报告含第四断言说明", "文献区禁汉化" in md)
    check("⑫ 报告含门禁判定", "门禁判定: PASS" in md)
    check("⑫ 报告含断言项编号4", "4. **文献区禁汉化**" in md)
    check("⑫ 报告含密度判据", "条/千字" in md)

    # ---------- ⑬ 作者-年份制文献页: 无 [n] 也要被识别 ----------
    # 事故: Wang 2026 第15页 "R. Olfati-Saber and R. M. Murray (2004). ..."
    # 体例, 行首 [n] 密度恒 0 → 旧判据既不豁免断言1, 也不受断言4 保护。
    # 实测原文 (年)密度 3.58 (正文页最高 0.7)。
    o = [feat(15, chars=3916, alnum=3916, ref_ay_entries=14)]   # 3.58/千字
    t = [feat(15, chars=3607, cjk=107, alnum=2500, ref_ay_entries=14)]
    fs, v = post_check.run_checks(o, t)
    check("⑬ 作者-年份制文献页不判翻译缺失",
          all(f[0] != "高" for f in fs), str(fs))
    check("⑬ 门禁判 PASS", v == "PASS", v)
    check("⑬ 计入文献页豁免项",
          any("文献页条目原样保留" in f[2] for f in fs), str(fs))
    check("⑬ 计入断言4 的文献页数",
          any("文献区禁汉化检查通过" in f[2] and "1 个文献页" in f[2]
              for f in fs), str(fs))

    # ---------- ⑭ 作者-年份制条目被汉化 → 高危 FAIL ----------
    # 注入前硅基译文实测: 标题被整条译成中文, 姓名与期刊名保留拉丁 → 26 条
    o = [feat(15, chars=3916, alnum=3916, ref_ay_entries=14)]
    t = [feat(15, chars=2600, cjk=330, alnum=1500, ref_ay_entries=0,
              ref_zh_blocks_ay=26)]
    fs, v = post_check.run_checks(o, t)
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
    fs, v = post_check.run_checks(o, t)
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
    fs, v = post_check.run_checks([feat(1, chars=100)], [feat(1, chars=100)])
    md = post_check.build_report("t-mono.pdf", "t.pdf", 1, fs, v)
    check("⑰ 报告说明含作者-年份制", "作者-年份" in md)

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
    fs, v = post_check.run_checks(o, t)
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
    # 放宽不等于放开: 带逗号的多引(`[16, 20]`)与超三位数, 两侧都不认
    check("⑲ 带逗号多引不计", CITE.findall("[16, 20]") == [],
          CITE.findall("[16, 20]"))
    check("⑲ 超三位数不计", CITE.findall("[1234]") == [], CITE.findall("[1234]"))
    # 同一段文本两侧形态不同(原文带空隙/译文无空隙) → 计数必须相等
    _o = post_check.text_features("SVRG[ 16, 20],SCSG[ 22], and SSRGD[ 24]", 1)
    _t = post_check.text_features("SVRG [16,20],SCSG [22], and SSRGD [24]", 1)
    check("⑲ 两侧形态不同但计数一致", _o["cites"] == _t["cites"] == 2,
          (_o["cites"], _t["cites"]))
    # 放宽后译文**真丢**引用仍要判 FAIL(容差外的差距不会被这条正则吃掉)
    fs, v = post_check.run_checks(
        [feat(2, chars=100, alnum=100, cites=8)],
        [feat(2, chars=100, alnum=100, cites=0)])
    check("⑲ 译文真丢引用仍判 FAIL", v == "FAIL", v)

    print(f"\npost_check 门禁单元测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
