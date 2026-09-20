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

    print(f"\n结果: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
