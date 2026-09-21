# -*- coding: utf-8 -*-
"""豆包实测教训台账 —— 门禁踩过的坑, 自动回流进下一篇的翻译提示词。

为什么要有它: 规则文本(seg_export.RULES)是**静态**的, 只能写成"不许把整块拆开"这种
抽象话; 而豆包真正反复踩的是**具体形态**(把 `C∈{2,4}` 拆成两截并插中文、把 `(0.69/0.63`
丢掉块首左括号)。实测表明具体反例比抽象规则有效, 但靠人事后一条条补既慢又会漏。
故: 门禁判 FAIL 时把"踩过什么"记进台账(写), 导出载荷时把最近几条注入提示词(读)。

三条硬约束(不满足就会反噬):
  1. **只记译者侧的错**: 台账由 seg_import.write_rework_note 调用, 那是回锚失败/编号
     断点失败才走的路径 —— 工具/环境故障压根到不了这里, 不会被记成"豆包的教训"。
  2. **只记"正确形态"不记错法**: 记的是原文里**该原样出现**的字形串(门禁的 ground
     truth), 不是豆包写成的样子 —— 把错法写进提示词等于教它照着错。
  3. **限量**: 提示词每加一行都在稀释前面的规则。故文件上限 MAX_KEEP 条、注入上限
     MAX_INJECT 条 / MAX_BLOCK_CHARS 字符, 超了新条目顶掉旧的(台账是"最近的教训"
     而非"全部历史", 历史仍在 改动记录.md 与返工单里)。

台账是人可读可改的 TSV: `类型<TAB>证据<TAB>上下文<TAB>日期`。删掉某行即不再注入;
清空文件即回到纯静态规则。写入失败一律吞掉(台账是辅助, 不能反过来卡住门禁)。
"""
import io
import os
import time

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lessons.tsv")

MAX_KEEP = 50           # 台账最多留几条(超出丢最旧)
MAX_INJECT = 6          # 注入提示词最多几条
MAX_BLOCK_CHARS = 700   # 注入块总长上限(含表头)
CTX_MAX = 70            # 单条上下文截断长度

HEADER = ("# 豆包实测教训台账 —— 门禁 FAIL 时自动追加, 导出载荷时自动注入提示词。\n"
          "# 格式: 类型<TAB>证据<TAB>上下文<TAB>日期\n"
          "# 类型: GLYPH=字形整块 / MOVE=断点两侧错位 / OTHER=编号与空段类\n"
          "# 删掉某行即不再注入; 清空本文件即回到纯静态规则。\n")

KIND_LABEL = {
    "GLYPH": "字形整块",
    "MOVE": "断点错位",
    "OTHER": "编号与空段",
}

# 注入到载荷提示词里的说法。刻意写成"该原样出现"而不是"你曾写错成X" —— 见模块头第 2 条。
LINE_GLYPH = "- 字形串 `{ev}` 必须**逐字符原样**出现在译文里（实测这一串曾被改写或拆开）{ctx}"
LINE_OTHER = "- 曾出现: {ev} —— 见上面对应规则，按它办"


def _rows():
    """读台账 -> [(kind, ev, ctx, date)]; 文件不在/读不动 -> []。"""
    if not os.path.exists(PATH):
        return []
    out = []
    try:
        with io.open(PATH, encoding="utf-8") as f:
            for ln in f:
                ln = ln.rstrip("\n")
                if not ln.strip() or ln.lstrip().startswith("#"):
                    continue
                p = ln.split("\t")
                if len(p) < 2:
                    continue
                out.append(tuple((p + ["", ""])[:4]))
    except OSError:
        return []
    return out


def _write(rows):
    tmp = PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER)
        for r in rows:
            f.write("%s\t%s\t%s\t%s\n" % r)
    os.replace(tmp, PATH)


def record(kind, evidence, context=""):
    """追加一条教训(按 (类型,证据) 去重, 已存在则只刷新日期并移到末尾)。

    返回 True 表示台账真的变了。任何异常都吞掉: 记不上教训不该让门禁失败。
    """
    ev = (evidence or "").strip().replace("\t", " ").replace("\n", " ")
    if not ev:
        return False
    ctx = (context or "").strip().replace("\t", " ").replace("\n", " ")
    if len(ctx) > CTX_MAX:
        ctx = ctx[:CTX_MAX] + "…"
    today = time.strftime("%Y-%m-%d")
    try:
        rows = [r for r in _rows() if not (r[0] == kind and r[1] == ev)]
        rows.append((kind, ev, ctx, today))
        _write(rows[-MAX_KEEP:])
        return True
    except Exception:
        return False


def block():
    """渲染注入提示词的「实测反例」块; 台账为空 -> ""。"""
    rows = _rows()
    if not rows:
        return ""
    seen, picked = set(), []
    for kind, ev, ctx, _d in reversed(rows):          # 从最新往回挑
        if (kind, ev) in seen:
            continue
        seen.add((kind, ev))
        picked.append((kind, ev, ctx))
        if len(picked) >= MAX_INJECT:
            break
    lines = ["[实测反例] 本管线在真实回包里**踩过**下面这些错, 同样的写法务必避免:"]
    used = len(lines[0])
    for kind, ev, ctx in picked:
        if kind == "GLYPH":
            s = LINE_GLYPH.format(ev=ev, ctx=("（原文处: %s）" % ctx) if ctx else "")
        elif kind == "MOVE":
            s = ("- 曾出现: 分页断点 ⋮ 两侧的译文被互换 —— "
                 "⋮ 左边只写左边原文的译文, 右边同理, 不许把某侧短语提到另一侧")
        else:
            s = LINE_OTHER.format(ev=ev)
        if used + len(s) > MAX_BLOCK_CHARS:
            break
        used += len(s) + 1
        lines.append(s)
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n"
