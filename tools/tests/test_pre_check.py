# -*- coding: utf-8 -*-
"""pre_check 文献页判据单元测试 (v28.2, 2026-09-19)

被锁死的缺陷: decide() 的文献页判据只认"行首 [n]"(编号制)。作者-年份制
(Wang 2026 那类) 整张文献表没有 [n], 行首条目数恒为 0, 于是:
  · 翻前体检给不出 skipLastPages 建议 → 文献区照常送翻
  · 而翻后门禁 post_check 早已认作者-年份制(REF_AY) → 体检放行、门禁判死,
    两个口径打架; 跨类型实跑里 Petkevičius/CLAP/Padmaprabhan 三篇的
    "文献区汉化" FAIL 都是这一路的后果。

修法: 与 post_check 共享全套常量 —— REF_AY(行首姓名 + 80 字符内出现 (年份)) +
REF_DENSITY 双密度阈值, 判据一律取**原文页**; 在此之上再补一道"行首 [n] 占比"
闸门(体检可比门禁更敏感, 不可更宽)。

本测试锁五点:
  ① 正则口径: 认行首条目, 不误吃正文行内引用 "(Olfati-Saber and Murray, 2004)"
  ② 密度阈值: 2.5 是闭区间下界, 稍低即不算
  ③ decide() 对两种体例都给得出跳页建议, 且旧的编号制行为原地不动
  ④ 编号制两道闸门: 只数"行首 [n] 条数"会把正文页当文献页(Vaswani p10 行首
     4 条、CLAP p2 行首 3 条, 两页没有一条真文献条目), 故补"行首占全部 [n]
     的比例 >= 0.5"; 反过来长条目文献页密度会掉到 2.5 以下(Jiang p7=2.18 /
     Kim p9=1.77 / Melhani p42=1.43)被 post_check 整页漏判, 也要救回
  ⑤ [v28.80] 不可见字符记账: 零宽/bidi/tag 类逐页记账, **只报不拦**(级别"提示",
     不给跳页建议)。剥除由 tools/text_clean.py 在载荷侧与回锚侧**走同一个函数**
     做掉 —— 体检只记下"哪一页、什么码点、在第几个字符"供事后追查;

运行: venv python test_pre_check.py, 退出码 0=全过
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import pre_check as PC  # noqa: E402  (本模块不重包 stdout, 可直接导入)
import text_clean as TC  # noqa: E402 ⑤ 节要断言体检用的是同一份剥离实现

AY_ENTRY = ("R. Olfati-Saber and R. M. Murray (2004). Consensus problems in networks "
            "of agents with switching topology and time-delays. IEEE TAC, 49(9):1520-1533.")
AY_ENTRY2 = ("W. Ren and R. W. Beard (2005). Consensus seeking in multiagent systems "
             "under dynamically changing interaction topologies. IEEE TAC, 50(5):655-661.")
NUMBERED = "\n".join(
    "[%d] Author %d. Some title of the paper. Journal Name, 20%02d, 1(2):3-4." % (i, i, i)
    for i in range(1, 5))


def page(idx, text, **over):
    """按 pre_check 自己的函数造页特征, 保证与线上口径一致"""
    p = {
        "page": idx + 1,
        "chars": len(text.strip()),
        "ref_markers": PC.count_citation_markers(text),
        "ref_entries": PC.count_reference_entries(text),
        "ref_density": PC.ref_density(text),
        "ref_ay": PC.count_reference_ay_entries(text),
        "ref_ay_density": PC.ref_ay_density(text),
        "math": PC.count_math_symbols(text),
        "cjk_r": PC.cjk_ratio(text),
        "invis": PC._TC.phrase(PC._TC.scan(text)),
        "error": None,
    }
    p.update(over)
    return p


class FakePage:
    def __init__(self, text):
        self._t = text

    def extract_text(self):
        return self._t


class FakeReader:
    """只实现 analyze_page 用到的那一点接口（pages[idx].extract_text()）"""

    def __init__(self, texts):
        self.pages = [FakePage(t) for t in texts]


def body(n=1800):
    """一段不含行首姓名的正文(可指定长度)"""
    s = ("The consensus protocol is analysed under switching topology. "
         "As shown in (Olfati-Saber and Murray, 2004), the system converges. ") * 30
    return s[:n]


def find(findings, level):
    return [f for f in findings if f[0] == level]


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

    # ---- ① 正则口径 ----
    check("① 行首姓名条目命中", PC.count_reference_ay_entries(AY_ENTRY) == 1,
          PC.count_reference_ay_entries(AY_ENTRY))
    check("① 多条目计数", PC.count_reference_ay_entries(AY_ENTRY + "\n" + AY_ENTRY2) == 2)
    body_txt = "As shown in (Olfati-Saber and Murray, 2004), consensus is reached."
    check("① 正文行内引用不误吃", PC.count_reference_ay_entries(body_txt) == 0,
          PC.count_reference_ay_entries(body_txt))
    check("① 编号制行首 [n] 仍照旧", PC.count_reference_entries(NUMBERED) == 4)

    # ---- ② 密度阈值(闭区间下界 2.5) ----
    ay2 = AY_ENTRY + "\n" + AY_ENTRY2
    t250 = ay2 + "\n" + ("x" * (800 - len(ay2) - 1))     # 2 条 / 800 字 = 正好 2.5
    check("② 密度 2.5 恰在阈值上", abs(PC.ref_ay_density(t250) - 2.5) < 1e-6,
          PC.ref_ay_density(t250))
    t_low = ay2 + "\n" + ("x" * (1000 - len(ay2) - 1))    # 2 条 / 1000 字 = 2.0
    check("② 密度 2.0 落在阈值下", PC.ref_ay_density(t_low) < PC.REF_AY_DENSITY,
          PC.ref_ay_density(t_low))
    check("② 空文本不炸", PC.ref_ay_density("") == 0.0)

    # ---- ③ decide(): 作者-年份制尾巴 → 给出跳页建议 ----
    pages = [page(0, body()), page(1, body()), page(2, body()),
             page(3, body()), page(4, AY_ENTRY + "\n" + AY_ENTRY2 + "\n" + body(200)),
             page(5, AY_ENTRY2 + "\n" + body(200))]
    skip, findings = PC.decide(pages, len(pages))
    check("③ 作者-年份制尾巴推荐跳页", skip == 2, skip)
    hi = find(findings, "高")
    check("③ 结论里点明体例", any("作者-年份制" in f[2] for f in hi), hi)
    check("③ 页码区间正确", any("第5-6页" in f[1] for f in hi), hi)

    # ---- ③b 编号制行为原地不动 ----
    pages = [page(0, body()), page(1, body()), page(2, NUMBERED), page(3, NUMBERED)]
    skip, findings = PC.decide(pages, len(pages))
    check("③b 编号制尾巴推荐跳页不变", skip == 2, skip)
    check("③b 编号制体例标注", any("编号制" in f[2] for f in find(findings, "高")))

    # ---- ③c 正文页(行内引用密集但非条目)不得被误判 ----
    pages = [page(0, body()), page(1, body()), page(2, body())]
    skip, findings = PC.decide(pages, len(pages))
    check("③c 纯正文不推荐跳页", skip == 0, skip)
    check("③c 纯正文无文献结论", not [f for f in findings if "参考文献" in f[2]], findings)

    # ---- ③d 混体例: 同一后缀里两种体例 ----
    pages = [page(0, body()), page(1, NUMBERED), page(2, AY_ENTRY + "\n" + AY_ENTRY2)]
    skip, findings = PC.decide(pages, len(pages))
    check("③d 混体例仍推荐跳页", skip == 2, skip)
    hi = find(findings, "高")
    check("③d 两种体例都点名",
          any("编号制" in f[2] and "作者-年份制" in f[2] for f in hi), hi)

    # ---- ③e 中段文献页(后面还有正文) → 只提示不跳页 ----
    pages = [page(0, body()), page(1, AY_ENTRY + "\n" + AY_ENTRY2), page(2, body())]
    skip, findings = PC.decide(pages, len(pages))
    check("③e 中段文献页不自动跳页", skip == 0, skip)
    mid = find(findings, "中")
    check("③e 给中段提示", any("参考文献特征页" in f[2] for f in mid), mid)

    # ---- ③f 解析失败的页不参与判据 ----
    pages = [page(0, body()), page(1, "", error="boom")]
    skip, findings = PC.decide(pages, len(pages))
    check("③f 坏页不进文献判据", not [f for f in findings if "参考文献" in f[2]], findings)

    # ---- ④ 编号制两道闸门: 只数条数会把正文页当文献页 ----
    # 实机取证 Vaswani p10 = "Table 4 + 正文结尾": 行首 [n] 4 条却被判文献页,
    # 真文献页在 p11/p12; Zhang/CLAP p2(teaser 页)同理(行首 3 条)。
    # 判据补上"行首 [n] 占全部 [n] 的比例"(实测文献页 0.83~1.00, 正文页 <=0.25)。
    inline = "See [1], [2], [3], [4] for details in this study. " * 12
    mixed = inline + "\n" + ("y" * 3000) + "\n" + "\n".join(
        "[%d] x" % i for i in range(1, 5))
    mp = page(0, mixed)
    check("④ 正文页行首条目数达标", mp["ref_entries"] >= PC.REF_ENTRY_MIN,
          mp["ref_entries"])
    check("④ 正文页行首占比低", PC.ref_entry_ratio(mp) < PC.REF_ENTRY_RATIO,
          PC.ref_entry_ratio(mp))
    check("④ 正文页密度也低(排除密度分支)",
          mp["ref_density"] < PC.REF_DENSITY, mp["ref_density"])
    check("④ 正文页(行首 4 条/占比低)不判文献页", not PC.is_ref_page(mp), mp)

    # ---- ④b 长条目文献页: 密度掉到阈值下, 靠"条数+占比"救回 ----
    # 实机取证 Jiang p7(15 条/6891 字=2.18) / Kim p9(1.77) / Melhani p42(1.43)
    # 三页在 post_check 的纯密度判据下整页漏判。
    entries = "\n".join(
        "[%d] A. Author. Title of the paper. Journal Name, 20%02d, 1(2):3-4."
        % (i, i) for i in range(1, 16))
    long_ref = entries + "\n" + ("z" * 6000)
    lp = page(0, long_ref)
    check("④b 长条目页密度低于阈值",
          lp["ref_density"] < PC.REF_DENSITY, lp["ref_density"])
    check("④b 行首占比高", PC.ref_entry_ratio(lp) >= PC.REF_ENTRY_RATIO,
          PC.ref_entry_ratio(lp))
    check("④b 仍判文献页", PC.is_ref_page(lp), lp)

    # ---- ④c decide(): 长条目文献页构成后缀 → 推荐跳页 ----
    pages = [page(0, body()), page(1, body()), page(2, long_ref)]
    skip, findings = PC.decide(pages, len(pages))
    check("④c 长条目文献页推荐跳页", skip == 1, skip)
    hi = find(findings, "高")
    check("④c 体例标编号制", any("编号制" in f[2] for f in hi), hi)

    # ---- ④d decide(): 正文页里的行首 [n] 不得触发跳页 ----
    pages = [page(0, body()), page(1, body()), page(2, mixed)]
    skip, findings = PC.decide(pages, len(pages))
    check("④d 正文页不触发跳页", skip == 0, skip)
    check("④d 正文页无文献结论",
          not [f for f in findings if "参考文献" in f[2]], findings)

    # ---- ⑤ [v28.80, 2026-09-23] 不可见字符记账（只报不拦）----
    # 为什么记这一笔: 零宽/bidi/tag 类字符人眼看不见, 却会让回锚的字形定位(str.find)
    # 落空 —— 由它产生的 FAIL 事后无从解释。剥除由 tools/text_clean.py 在载荷侧与回锚侧
    # **走同一个函数**做掉; 体检这里只记账, 不改任何判据(既不给跳页建议, 也不拦翻译)。
    ZWSP = "\u200b"
    feat = PC.analyze_page(FakeReader(["Converges fast." + ZWSP + " See Fig." + ZWSP]), 0)
    check("⑤a 走真路径记到命中", "U+200B" in feat["invis"], feat["invis"])
    check("⑤a 点明次数与首次位置",
          "×2" in feat["invis"] and "首次第" in feat["invis"], feat["invis"])
    feat = PC.analyze_page(FakeReader(["plain ascii text"]), 0)
    check("⑤a 无命中不报", feat["invis"] == "", feat["invis"])

    # ⑤b 不剥的字符不得上报: 变体选择符(U+FE00) 与三种特殊空格有呈现/排版含义,
    #     剥了等于改内容(见 text_clean 模块头) —— 报出来只会教人误删。
    keep = "\u2211\ufe00 A\u00a0B\u202fC\u3000D"
    check("⑤b 变体选择符与特殊空格不剥", PC._TC.strip(keep) == keep,
          repr(PC._TC.strip(keep)))
    check("⑤b 它们也不算命中", PC._TC.scan(keep) == [], PC._TC.scan(keep))
    check("⑤b 体检与实现同源", PC._TC.strip is TC.strip)

    # ⑤c 只报不拦: 级别只是"提示", 也不给跳页建议
    pages = [page(0, body(), invis="U+200B ZERO WIDTH SPACE ×1(首次第 3 字符)"),
             page(1, body())]
    skip, findings = PC.decide(pages, len(pages))
    check("⑤c 不给跳页建议", skip == 0, skip)
    check("⑤c 级别只是提示",
          [f[0] for f in findings if "不可见字符" in f[2]] == ["提示"], findings)

    # ⑤d 报告单列一节, 并在页面特征表里点出该页
    rep = PC.build_report("x.pdf", 2, pages, 0, findings, False)
    check("⑤d 报告有「不可见字符」节", "## 不可见字符" in rep)
    check("⑤d 页面特征表里标出该页", "| 不可见字符 |" in rep)
    check("⑤d 记下码点与位置", "U+200B" in rep and "首次第 3 字符" in rep)
    check("⑤d 明写只记账不拦", "只记账" in rep)

    # ⑤e 没有命中就不许凭空加节(否则每篇报告都多一段噪声)
    rep2 = PC.build_report("x.pdf", 1, [page(0, body())], 0, [], False)
    check("⑤e 无命中不加节", "## 不可见字符" not in rep2)

    print("\n结果: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
