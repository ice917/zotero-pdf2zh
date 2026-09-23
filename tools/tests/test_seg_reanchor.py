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

㉑ 节 [v28.80] 另锁一条: 不可见字符(零宽/bidi/tag 类)的**剥离两侧必须同源**。
它防的不是错译, 是"好端端的段被判 FAIL": 一个 U+200B 就能让字形定位(str.find)
落空, 而它在人眼里不存在。只剥一侧(译文或字形值)等于自己制造落空 —— 比不剥更糟,
故 ㉑a 把"值净/值脏 × 译文净/译文脏"四种组合全摆出来, 逐个都必须命中。

㉒ 节 [v28.82] 锁回锚**埋点**: fails/drops 按字符家族(私用区/数学字母/组合标记/
控制符)归档落盘, 且**只记账不判定** —— reanchor 是纯函数, 源码里不引用台账,
开关台账不改它的任何返回值。私用区字形 `isalnum()` 为假 -> 走 drop 而非 fail,
"只看 FAIL 数"会把它整个漏掉, 故两类都得记。

运行: venv python test_seg_reanchor.py, 退出码 0=全过
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import seg_import as SI  # noqa: E402  (导入时已把 stdout 包成 utf-8, 勿再包)
import text_clean as TC  # noqa: E402 ㉑ 节要断言"两侧同一个函数", 而非"各写一份"


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
    #    尾部句点也被吸收: 字形值结尾是 `.`, 译文写 `。`(. ≡ 。 同等价类) —— 不吸收
    #    就是渲染层写回 `.` 之后再让 `。` 留在原地, 页面出现 `.。` 两个句号。
    res, fails, _d, _n = run(
        "{v0} 的序列, 有 {v1}",
        {"0": "u(s)→0,", "1": "Pk−1s=0Γ(k,s+1)c(s)u(s)→0."},
        "有 Pk−1s=0Γ(k,s+1)c(s)u(s)→0。对 u(s)→0 的序列")
    check("⑫ 长公式不被短字形抢位", not fails, fails)
    check("⑫ 短字形退到自己的空位",
          res == "有 {v1}对 {v0} 的序列", res)

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

    # ⑲ 字形首尾标点残留 (v28.35, 2026-09-20 SILAGE 篇实测 30 处 `{vN}]` / 20 段):
    #    引用类字形值自带 `[` `]` `,`(侧车实测 `[16,20],`), 译文里自己写的那套标点
    #    必须**全部**被吞进占位符 —— 漏一个, 渲染写回原值就多一个字, post_check 的
    #    引用完整性随即虚高(69 vs 88, 差 19 顶出容差)。
    #    实况里还有第二种情形: 译文 `SCSG [22] 和` 省掉了字形尾部的 `,`(中文语序改
    #    用空格分隔), 只对得上 `]` —— 这时必须吃掉能对上的那部分, 不能因为"整段没
    #    对上"就一个都不吃, 残留的 `]` 一样会把计数顶出去。
    res, fails, _d, _n = run(
        "SVRG {v0}, SCSG {v1}, and SSRGD {v2},",
        {"0": "[16,20],", "1": "[22],", "2": "[24],"},
        "这包括 SVRG [16,20]、SCSG [22] 和 SSRGD [24] 等方法")
    check("⑲ 三处引用全回锚", not fails and res.count("{v") == 3, (res, fails))
    check("⑲ 无字形标点残留在占位符外", "]" not in res and "、" not in res, res)
    check("⑲ 顿号与右括号被吞净", "SCSG {v1} 和 SSRGD" in res, res)

    # ⑳ 数学字母不是"纯符号" (v28.38, 2026-09-20 NUL 定性):
    #    侧车把数学斜体存成**字面文本**(实测 SILAGE p1#4 `vars[4]='𝑁=𝑛𝑚'`), 而
    #    "含字母/数字"的判据写成了 ASCII-only 的 `[A-Za-z0-9]` —— 𝑁/𝑛/𝑚 全被判成
    #    纯符号丢弃, 于是从不回锚成 {vN}, 渲染时只能当普通文本排进**中文字体**;
    #    中文字体 cmap 里没有 U+1D400–U+1D7FF, 落到 CID 0/.notdef —— 页面豆腐块,
    #    提取出 NUL(SILAGE 全篇 1518 处 / 第 1 页 32 处, 同引擎对照版 0 处: 那边走
    #    pdf2zh 原生公式保护, 这些字符由原文字体 CMMI10 画)。
    res, fails, drops, _n = run(
        "structure{v0} where {v1} total samples are partitioned into "
        "{v2} blocks of size {v3}",
        {"0": ",", "1": "𝑁=𝑛𝑚", "2": "𝑛", "3": "𝑚("},
        "结构，其中总样本数 𝑁=𝑛𝑚 在逻辑上或物理上被划分为 𝑛 个大小为 𝑚( 的块")
    check("⑳ 数学字母参与回锚", res.count("{v") == 3, res)
    check("⑳ 数学字母不再算纯符号",
          not any(d[0] in ("1", "2", "3") for d in drops), drops)
    check("⑳ 逗号仍按设计丢弃", [d[0] for d in drops] == ["0"], drops)
    check("⑳ 无 FAIL", not fails, fails)

    # ㉑ [v28.80, 2026-09-23] 不可见字符: 回锚侧剥离, 与载荷侧**走同一个函数**
    #    零宽/bidi/tag 类字符人眼看不见, 却会让字形定位(str.find)落空 —— 由它产生的
    #    FAIL 事后无从解释。它防的**不是错译**, 正是"好端端的段被判 FAIL"。
    #    关键不在"要不要剥", 在**两侧必须同源**: 只剥译文不剥字形值(或反过来), 等于
    #    自己制造落空 —— 比不剥更糟。故下面把四种组合全摆出来, 逐个都必须命中。
    ZWSP = "\u200b"
    UR = "u(s)" + ZWSP + "\u21920,"          # 字形值的"脏"写法(夹了零宽)
    URC = "u(s)\u21920,"                     # 同值的干净写法
    ZR = "见 u(s)" + ZWSP + "\u21920, 成立"   # 译文的"脏"写法
    ZC = "见 u(s)\u21920, 成立"               # 同译文的干净写法

    #    先证明价值: 不剥的话, 这条**确实**会落空(str.find 的口径, 与 _core_pattern 同源)
    check("㉑ 不剥确实会落空(它在防的就是这个)",
          SI._core_pattern(URC).search(ZR) is None)

    # ㉑a 四种组合(值净/值脏 × 译文净/译文脏)全部必须命中
    for vname, val in (("值净", URC), ("值脏", UR)):
        for zname, zh in (("译文净", ZC), ("译文脏", ZR)):
            res, fails, _d, _n = run("{v0}", {"0": val}, zh)
            check("㉑a %s + %s -> 命中" % (vname, zname),
                  not fails and res == "见 {v0} 成立", (res, fails))

    # ㉑b 剥下来的字符不许进译文: 零宽字符排到纸上就是豆腐块/CID 0
    res, _f, _d, _n = run("{v0}", {"0": UR}, ZR + ZWSP)
    check("㉑b 译文里的零宽不残留", ZWSP not in res, repr(res))

    # ㉑c 不改判据: 真缺的字形仍然 FAIL —— 剥离是归一化, 不是放宽
    _res, fails, _d, _n = run("{v0} and {v1}", {"0": UR, "1": "i6=j,"}, ZC)
    check("㉑c 真缺的字形仍 FAIL", [f[0] for f in fails] == ["1"], fails)

    # ㉑d 两侧真的是**同一个函数**(不是"各写一份差不多的正则")
    check("㉑d 回锚用的是 text_clean.strip", SI._TC.strip is TC.strip)

    # ㉒ [v28.82, 2026-09-23] 回锚埋点: fails/drops 按**字符家族**归档, 只记账不判定
    #    它要回答的问题 —— "哪个字符家族真在真实翻译里捣乱" —— **不能从段表快照
    #    反推**: 快照里的 trans 是回锚**之前**的模型输出(改动记录 9.7.1), 据此算的
    #    FAIL 率会把大量好段算成坏的。唯一可信的口径就是在真实回锚现场记一笔。
    #    本节锁三件事: 家族分得对(私用区走 drop 而非 fail —— 只看 FAIL 数会把它整个
    #    漏掉) / 纯 ASCII 标点不入账 / **埋点与判据解耦**(台账开关不动 reanchor 结果)。
    import inspect as _insp
    import json as _json
    import tempfile as _tf

    PUA = "\uf0b3"                                  # 私用区: Symbol 字体字形常这么存
    MATH = "\U0001D45B"                             # 数学斜体 n (U+1D45B)
    COMB = "n\u0303"                                # n + 组合波浪线 (Mn)

    check("㉒a 私用区归 pua", SI.char_families(PUA) == {"pua": PUA}, SI.char_families(PUA))
    check("㉒b 数学字母归 math", SI.char_families(MATH) == {"math": MATH},
          SI.char_families(MATH))
    check("㉒c 组合标记归 combining", SI.char_families(COMB) == {"combining": "\u0303"},
          SI.char_families(COMB))
    check("㉒d 纯 ASCII 与汉字不入账", SI.char_families("A1 汉字 /") == {},
          SI.char_families("A1 汉字 /"))

    #    私用区的 isalnum() 为假 -> 它走的是 **drop**(按设计丢弃)而不是 fail。
    #    这正是"只看 FAIL 数会把私用区整个漏掉"的现场, 故埋点必须两类都记。
    _res, fails, drops, _n = run("见 {v0} 与 {v1}", {"0": PUA, "1": "x"}, "见 与 x")
    check("㉒e 私用区字形落 drops 而非 fails",
          [d[0] for d in drops] == ["0"] and not fails, (fails, drops))
    rows = SI.ledger_rows("demo", "#S1（第1页）", 1, 0, "drop", drops)
    check("㉒f drop 也被埋点记下(家族=pua, kind=drop)",
          len(rows) == 1 and rows[0]["fams"] == ["pua"] and rows[0]["kind"] == "drop", rows)
    check("㉒g 纯标点丢弃不入账(别让噪声淹掉真信号)",
          SI.ledger_rows("demo", "loc", 1, 0, "drop", [("2", "/")]) == [])

    # ㉒l 分类总账(每次必记一条): 四类家族 + 「其余」那一桶(= 纯标点丢弃 / OCR 碎片)。
    #     这一桶最值得盯: 实测 Johnson 篇 7 处 FAIL **全是** ASCII 的 OCR 碎片
    #     (`011`/`III`/`340/00Ill`), 没有一处是这些异体字符家族。
    _summ = SI.ledger_summary("demo", [("9", "0i1")], drops, rows)
    check("㉒l 总账把未回锚与丢弃分开计数",
          _summ["n_fail"] == 1 and _summ["n_drop"] == 1, _summ)
    check("㉒l 有家族的那部分单独归到 pua", _summ["fam_drop"] == {"pua": 1}
          and _summ["n_plain_drop"] == 0, _summ)
    check("㉒l 「其余」那一桶收 ASCII 碎片", _summ["n_plain_fail"] == 1
          and _summ["fam_fail"] == {}, _summ)

    old_led = SI.LEDGER
    try:
        SI.LEDGER = os.path.join(_tf.mkdtemp(prefix="reanchor_led_"), "led.jsonl")
        p1 = SI.append_ledger(rows)
        p2 = SI.append_ledger(rows)
        with open(p1, encoding="utf-8") as _f:
            lines = [ln for ln in _f.read().splitlines() if ln]
        check("㉒h 落盘返回台账路径", p1 == SI.LEDGER and p2 == p1, (p1, p2))
        check("㉒i 追加而非覆盖(记两次 -> 两行)",
              len(lines) == 2 and _json.loads(lines[0])["fams"] == ["pua"], lines)
        check("㉒j 一条都没记就不建文件", SI.append_ledger([]) == "")

        # ㉒m 但**总账**必须每次都落: 否则"埋点没触发"与"这次确实干净"分不出来
        check("㉒m 干净的回锚也留一条总账",
              SI.append_ledger([SI.ledger_summary("demo", [], [], [])]) == SI.LEDGER)

        # ㉒k 台账是**纯记账**: reanchor 自己不写盘, 也不引用埋点(判据与埋点解耦)
        ghost = os.path.join(os.path.dirname(SI.LEDGER), "ghost.jsonl")
        SI.LEDGER = ghost
        run("{v0}", {"0": "x"}, "见 x")
        check("㉒k reanchor 不落盘(台账文件没被创建)", not os.path.exists(ghost))
        _re_src = _insp.getsource(SI.reanchor)
        check("㉒k reanchor 源码里不出现埋点", "LEDGER" not in _re_src
              and "ledger_rows" not in _re_src)
    finally:
        SI.LEDGER = old_led

    print(f"\n结果: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
