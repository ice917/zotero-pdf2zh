# -*- coding: utf-8 -*-
"""seg_import 回锚匹配器单元测试 (v26.22, 2026-09-19)

覆盖 Wang 篇(豆包链首跑)撞出的两类结构性缺陷:
  ① 中文语序重排 —— 旧实现按原文 token 顺序推进单调游标, 一旦译文把符号
     提前/挪后, 游标就越过它, 该 token 及其后全部判 FAIL。
     (战例: "each nonzero eigenvalue λi of L with λ=λi" 被译成
      "对 L 的每个非零特征值 λi，取 λ=λi 时" —— L 被提到 λi 之前)
  ② core 内部标点全角化 —— 中文排版把 2.1,(20) 写成 2.1，(20)、把
     (26),(27) 写成 (26)、(27); 旧实现 str.find 字面匹配直接 FAIL。

修法: 两阶段定位(长 core 唯一落点优先 / 语序推进) + 标点等价类 + 占用区间防重叠。
本测试锁死这三条语义, 防止回退。

运行: venv python test_seg_reanchor.py, 退出码 0=全过
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import seg_import as SI  # noqa: E402  (导入时已把 stdout 包成 utf-8, 勿再包)


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

    def run(raw, vars_, zh):
        return SI.reanchor(zh, raw, vars_)

    # ① 语序一致: 旧行为不变(seq 通道)
    res, fails, _d, _n = run("{v0} is {v1}", {"0": "x", "1": "y"}, "x 是 y")
    check("① 顺序一致回锚", res == "{v0} 是 {v1}", res)
    check("① 无 FAIL", not fails, fails)

    # ② 语序重排 + 全局唯一 -> reorder 救回 (Wang p7#3 战例, 旧实现 3 连 FAIL)
    raw = "each nonzero eigenvalue {v0} of {v1} with {v2}"
    vv = {"0": "λi,", "1": "L", "2": "λ=λi."}
    zh = "对 L 的每个非零特征值 λi，取 λ=λi 时 (15) 成立"
    res, fails, _d, notes = run(raw, vv, zh)
    check("② 重排后全部回锚", not fails, fails)
    check("② L 落点正确", "对 {v1} 的每个非零特征值 {v0}" in res, res)
    check("② 唯一候选不算歧义", not notes, notes)

    # ③ 半角逗号 -> 全角逗号
    res, fails, _d, _n = run("{v0}", {"0": "2.1,(20)"}, "见 2.1，(20) 式")
    check("③ 全角逗号命中", not fails and res == "见 {v0} 式", (res, fails))

    # ④ 并列逗号 -> 顿号
    res, fails, _d, _n = run("{v0}", {"0": "26),(27"}, "综合 (26)、(27) 与 (28)")
    check("④ 顿号命中", not fails, (res, fails))

    # ⑤ 半角句点 -> 句号
    res, fails, _d, _n = run("{v0}", {"0": "1N=1.(2)"}, "有 π⊤1N=1。(2) 成立")
    check("⑤ 全角句号命中", not fails, (res, fails))

    # ⑥ 数字千分位弹性: 2,000 与 2000 视为同位
    res, fails, _d, _n = run("{v0}", {"0": "2,000"}, "共 2000 次")
    check("⑥ 千分位弹性命中", not fails and res == "共 {v0} 次", (res, fails))

    # ⑦ 同值两处: 占用区间防重叠, 各自回锚
    res, fails, _d, _n = run("{v0} 与 {v1}", {"0": "A,", "1": "A,"}, "A 与 A")
    check("⑦ 同值两处各自回锚", res == "{v0} 与 {v1}", (res, fails))

    # ⑧ 译文里真的没有 -> 仍然严格 FAIL (不可放宽)
    res, fails, _d, _n = run("{v0}", {"0": "i6=j,"}, "任意 i≠j，")
    check("⑧ 找不到仍 FAIL", len(fails) == 1, fails)

    # ⑨ 全局多候选 -> 取最近 + 记台账(不误判 FAIL)
    res, fails, _d, notes = run("{v0}{v1}", {"0": "A", "1": "B"}, "B B A")
    check("⑨ 歧义不误判 FAIL", not fails, fails)
    check("⑨ 歧义记台账", len(notes) == 1 and notes[0][0] == "1", notes)

    # ⑩ 纯标点字形 -> 按设计丢弃
    res, fails, drops, _n = run("a{v0}b", {"0": "-"}, "a中b")
    check("⑩ 纯标点丢弃", not fails and len(drops) == 1, (fails, drops))

    # ⑪ 未回锚者绝不出现在译文里(渲染契约: 占位符多重集 ⊆ raw)
    raw = "{v0} and {v1} and {v2}"
    res, fails, _d, _n = run(raw, {"0": "x", "1": "zz", "2": "y"}, "x、y")
    check("⑪ 未回锚者不入译文", "{v1}" not in res, res)
    check("⑪ 已回锚者在译文里", "{v0}" in res and "{v2}" in res, res)
    check("⑪ 缺一个必报 FAIL", len(fails) == 1, fails)

    # ⑫ 长 core 优先: 短字形不得抢走长公式唯一的落点
    #    (Wang p5#11 / p6#14 / p3#31 战例: 旧实现短字形先落点 -> 长公式判 FAIL)
    res, fails, _d, _n = run(
        "{v0} 的序列, 有 {v1}",
        {"0": "u(s)→0,", "1": "Pk−1s=0Γ(k,s+1)c(s)u(s)→0."},
        "有 Pk−1s=0Γ(k,s+1)c(s)u(s)→0。对 u(s)→0 的序列")
    check("⑫ 长公式不被短字形抢位", not fails, fails)
    check("⑫ 短字形退到自己的空位",
          res == "有 {v1}。对 {v0} 的序列", res)

    # ⑬~⑯ [v28.34] 定位口径两档放宽 (2026-09-20 SILAGE 篇 21 处 FAIL 逐条定性而来):
    #    12 处不是"译文缺字", 是译文用了**同一个字的另一种写法**, 或自己补了空格。
    #    放宽的只有**定位**, 命中后写回的仍是字形原值; 真缺字一律仍然 FAIL。
    # 下面涉及码点的地方一律写转义 —— 这组用例要断言的正是"码点不同、字是同一个字",
    # 直接敲字符的话看不出自己敲的是哪个(Ω 的两种写法肉眼看完全一样)。
    OHM, OMEGA, TILDE = "\u2126", "\u03a9", "\u02dc"     # OHM SIGN / 希腊大写 Ω / 小波浪号
    MB, MT, MI, MN, MINUS = "\U0001d44f", "\U0001d461", "\U0001d456", "\U0001d45b", "\u2212"

    # ⑬ 上标数字: 版面里 '4' 是独立字形, 中文排版写成 '⁴'(U+2074)
    res, fails, _d, _n = run(
        "Then the iteration complexity{v0} of SILAGE to reach",
        {"0": "4"}, "则 SILAGE 达到 𝜖-近似平稳点的迭代复杂度\u2074为")
    check("⑬ 上标数字（4 vs ⁴）命中", not fails and "{v0}" in res, (res, fails))

    # ⑭ OHM SIGN(U+2126) 与希腊大写 Ω(U+03A9) 是同一个字的两种码点(NFKC 同字)
    res, fails, _d, _n = run(
        "Using {v0} and {v1}, we have",
        {"0": "|" + OHM + MT + "|=" + MB + "grp" + MINUS + "1",
         "1": "|" + TILDE + OHM + MT + "|=" + MB + "grp,"},
        "使用 |" + OMEGA + MT + "|=" + MB + "grp" + MINUS + "1 和 |"
        + TILDE + OMEGA + MT + "|=" + MB + "grp，我们有")
    check("⑭ OHM SIGN vs 希腊 Ω 命中", not fails, (res, fails))

    # ⑮ 空白弹性: 译文按可读性补了空格 (字形值里没有); 全角括号与句读仍被吸收进字形块
    res, fails, _d, _n = run(
        "with exact initialization {v0}, we have",
        {"0": "(" + MB + "0" + MI + "=∇" + MB + MI + "(𝑥0)∀" + MI + "∈[" + MN + "]),"},
        "在精确初始化下（" + MB + "0" + MI + "=∇" + MB + MI + "(𝑥0) ∀" + MI + "∈["
        + MN + "]），有 Ψ0=∆0")
    check("⑮ 译文补的空格不影响命中", not fails, (res, fails))
    check("⑮ 括号与逗号被吸收进字形块", res == "在精确初始化下{v0}有 Ψ0=∆0", res)

    # ⑯ 真缺字仍须 FAIL —— 放宽不能变成"什么都算命中"
    res, fails, _d, _n = run(
        "minimizing Φ({v0}) over {v1}, i.e.,",
        {"0": MB + "grp", "1": MB + "grp∈[" + MN + "],"},
        "等价于最小化一维函数 Φ(" + MB + "grp)。")
    check("⑯ 译文真缺的字符仍 FAIL", [f[0] for f in fails] == ["1"], fails)

    # ⑰ 合并段 ⋮ 被挪: 本侧没落点、另侧有 -> 判"挪位"而不是"漏字"
    #    (SILAGE #S22 实况: 译文把「算法 1 和算法 2 提供下降方向」提到了 ⋮ 左侧)
    hits = SI.sibling_hits([("12", "1"), ("13", "2" + "\u2014")],
                           ["即为算法 1 和算法 2 提供下降方向的运行聚合",
                            "——SILAGE 仅需 𝒪(" + MN + ") 内存\u00b3"],
                           1)
    check("⑰ 另侧命中 -> 认出 ⋮ 挪位", sorted(hits) == ["12", "13"], hits)

    # ⑱ 两侧都没有 -> 不误判成挪位(那是真缺字)
    check("⑱ 两侧都没有 -> 不算挪位",
          SI.sibling_hits([("12", "1")], ["无数字的一侧", "另一侧也没有"], 1) == {})

    print(f"\n结果: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
