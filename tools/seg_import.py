# -*- coding: utf-8 -*-
"""seg_import.py — 收译者译文: 解析/对账/回锚 (M1 工具, 与 seg_export.py 配对)

"译者"= 网页版 AI 在浏览器里做的整篇翻译(本项目当前用的是 **DeepSeek 网页版**;
`豆包`/`doubao*` 只是桥与交件文件名的**历史代号**, 不是译者本身)。它与门禁是**两个
不同的 AI 供应商**(服务端强制), 返工单就是发给它的合同。

两条路线(由引擎画像决定, 见 engine.py):
  pdf2zh 1.x  侧车路线
  1. 取译文: --clip 读剪贴板, 或 --text 指定文件 (译者回复的全文)
  2. 解析: 按 #S编号 行切块 (容忍 markdown 加粗/代码围栏)
  3. 对账 manifest: 编号集合一致; 合并段 ⋮ 恰好 1 个且两侧非空; 普通段禁止 ⋮
  4. 回锚: 对每段按侧车 raw 的 {vN} 原序, 在译文中定位字形值 -> 还原为 {vN}
     高价值字形 (含字母/数字) 找不到 -> FAIL; 纯标点找不到 -> 丢弃并记提示
     (定位口径: 标点半/全角等价 + 上标数字·OHM 号等 NFKC 同字写法 + 译文自己补的空格)
  5. 产出 out/<name>.imported.json: {(page,seg): 带{vN}的译文} + 校验报告
  6. 埋点(v28.82): 每次回锚的 fails/drops 按**字符家族**(私用区/数学字母/组合标记/
     控制符)追加到 logs/reanchor_ledger.jsonl(P2Z_LEDGER 可改), 并**每次**另留一条
     分类总账(四类家族计数 + 「纯标点·OCR 碎片」那一桶), 控制台同时报一句。**只记账**,
     不改判据、不参与 PASS/FAIL —— 用来回答"哪个字符家族真在真实翻译里捣乱", 这个
     问题从段表快照反推不出来(见 改动记录.md 9.7.1)。

  next / BabelDOC  tracking 路线 (v28.41)
  1. 取译文 / 解析 / 编号对账 —— 与上面同 (共用同一份实现)
  2. **不回锚**: 载荷正文本来就是引擎原生形态({vN}/<style>), 收回来直接就能写回
     缓存。1.x 那套"显示字形回锚成 {vN}"在这里没有对应物, 也不需要
  3. 改判**守恒校验**: {vN} 与 <style> 标签的多重集逐段相等(上游自己的判据就是
     full match), 译文里不许出现 ⋮
  4. 产出 out/<name>.imported.json: {"S5": "译文"} (按 #S 编号; 段身份由 manifest
     与段表共同确定, inject 侧再解析)

  两条路线 FAIL 时都落一份**给译者的返工单**到 inbox/<name>.rework.md(桥的
  list_inbox / get_payload 直接读得到): 每条写明 真实页码 + #S编号 + 缺的字符 +
  原文上下文, 用户不必去理解控制台里的内部坐标; PASS 时把旧单子删掉, 免得读到
  过期结论。

用法:
  python tools/seg_import.py --manifest inbox\\payload_p2_p4.manifest.json --clip
退出码: 全过 0 / 有 FAIL 1
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# [v28.39] 引擎画像: 侧车路径随 P2Z_ENGINE 走(adopt 会把该变量传给子进程); 缺省画像
# pdf2zh 1.x —— 与引入本层之前逐字节一致。
import engine as _ENG                                     # noqa: E402
import result_naming as RN                                # noqa: E402  交件命名契约(后缀族)
import text_clean as _TC                                  # noqa: E402  不可见字符剥离(两侧同源)
_PROF = _ENG.active()
SIDECAR = _PROF.sidecar or ""
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
OUTDIR = os.path.join(PROJ, "out")
INBOX = os.environ.get("P2Z_INBOX", os.path.join(PROJ, "inbox"))

KEY_LINE = re.compile(r"^\s*#?\**\s*S(\d+)\s*\**\s*$")
V_TOKEN = re.compile(r"\{v(\d+)\}")

REWORK_SUFFIX = ".rework.md"
GLYPH_CTX = 60          # 返工单里原文上下文的半宽(按**还原后**字符数)
PAYLOAD_TAIL = 90       # 返工单「段尾没译完」类里引用载荷末段的字数
HINT_MAX = 20           # 「不必改」一节最多逐条列几条提示(超出只报计数)

# ---- [v28.82] 回锚埋点: 把 fails / drops 按**字符家族**归档落盘 ------------------
# 它存在的理由是一条方法论教训(改动记录 9.7.1): 「哪个字符家族在真实翻译里真的
# 造成回锚失败」**不能从段表快照反推** —— ~/.cache/pdf2zh/segflow/*.jsonl 里的
# trans 是回锚**之前**的模型输出, 拿它算 FAIL 率会把大量好段算成坏的(实测差 3 倍
# 以上)。唯一可信的口径是**在真实回锚的现场记一笔**。
#
# 它只**记账**: 不动判据、不改 reanchor 的返回值、不参与任何 PASS/FAIL, 写盘失败
# 也绝不拦流程(埋点把正常导入搞崩是最坏的结果)。
LEDGER = os.environ.get("P2Z_LEDGER",
                        os.path.join(PROJ, "logs", "reanchor_ledger.jsonl"))

_MATH_RANGES = ((0x1D400, 0x1D7FF),)      # 数学字母数字符号(Mathematical Alphanumeric)


def char_families(text):
    """一串字符 -> {家族: "命中的字符"}, 只收**有家族**的字符(ASCII/汉字不入账)。

    家族轴直接对应 9.7 那张体检表 —— 私用区(Co) / 数学字母(U+1D400 段) /
    组合标记(Mn/Mc/Me) / 其余格式控制符(C*)。这四类正是"最可能让回锚定位落空、
    又最不容易被校对的人眼发现"的那批(它们要么在人眼里不存在, 要么长得像正常字)。
    """
    fam = {}
    for ch in text:
        cp, cat = ord(ch), unicodedata.category(ch)
        if cat == "Co":                                   # 私用区
            k = "pua"
        elif any(a <= cp <= b for a, b in _MATH_RANGES):
            k = "math"
        elif cat in ("Mn", "Mc", "Me"):                   # 组合标记
            k = "combining"
        elif cat.startswith("C"):                         # 其余控制/格式符
            k = "control"
        else:
            continue
        fam[k] = fam.get(k, "") + ch
    return fam


def ledger_rows(name, loc, pg, seg, kind, items):
    """把一批 fails/drops 变成埋点行。**只记有家族的** —— 纯 ASCII 标点"按设计
    丢弃"是正常行为, 记它只会往台账里灌噪声, 把真正要看的那几条淹掉。

    kind: "fail"(高价值字形在译文里没有落点 -> 调用方判 FAIL) /
          "drop"(纯符号字形, 设计上就不回锚)。
    分清这两者是本埋点的要点之一: 私用区字符 `isalnum()` 为假, 会走 "drop" 而不是
    "fail" —— 只看 FAIL 数会把私用区整个漏掉。
    """
    rows = []
    for vn, val in items:
        fam = char_families(val)
        if not fam:
            continue
        rows.append({
            "man": name, "loc": loc, "page": pg, "seg": seg, "kind": kind,
            "vn": vn, "val": val, "cps": [hex(ord(c)) for c in val],
            "fams": sorted(fam), "chars": fam,
        })
    return rows


def ledger_summary(name, fails, drops, rows):
    """一次 import 的**分类总账**(只此一条)。四类 = PUA / 数学字母 / 组合标记 / 其余控制符,
    外加「都没有的」那一桶(= 纯标点丢弃 + OCR 碎片)。

    为什么**纯标点**只进总数不逐条记: 一篇论文的"纯标点字形丢弃"动辄上百条(实测 Johnson
    篇 44 条提示、单段最多 22 个), 逐条落盘会把真正要看的那几类淹掉。有家族的那几类逐条
    记(见 ledger_rows), 这里只给计数。

    家族计数**从 rows 统计**(只此一份口径): rows 已经是 ledger_rows 算好的"有家族"明细,
    另起一遍分类就会漂。n_plain_* = 总数减掉有家族的那部分 —— 这一桶最值得盯: 实测 Johnson
    篇的 7 处 FAIL **全是** ASCII 的 OCR 碎片(`011`/`III`/`340/00Ill`), 没有一处是这些
    异体字符家族。
    """
    fam_fail = Counter(f for r in rows if r["kind"] == "fail" for f in r["fams"])
    fam_drop = Counter(f for r in rows if r["kind"] == "drop" for f in r["fams"])
    return {
        "man": name, "kind": "summary",
        "n_fail": len(fails), "n_drop": len(drops),
        "fam_fail": dict(fam_fail), "fam_drop": dict(fam_drop),
        "n_plain_fail": len(fails) - sum(fam_fail.values()),
        "n_plain_drop": len(drops) - sum(fam_drop.values()),
    }


def append_ledger(rows):
    """追加落盘(跨次翻译累积成一份台账)。返回台账路径; 一条没记则返回 ''。

    追加而不是覆盖: 单篇论文的样本量太小, 判"PUA 到底有没有害"要靠多篇累积。
    """
    if not rows:
        return ""
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError:
        return ""
    return LEDGER


def load_sidecar_pages(sidecar):
    pages = {}
    with open(sidecar, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            pages[o["page"]] = o
    return pages


def part_true_page(part, o):
    """这一段的**真实 PDF 页码**(1 基)。

    导出时已把 true_page 算好写进清单(与 seg_export.true_page 同一口径); 老清单
    没有这个字段就退回落 pageid(LTPage.pageid, 0 基); 老侧车连 pageid 都没有, 才
    退回侧车的回调计数 —— 那是**内部坐标**, 拿它当页码会指向不存在的页(实测
    Padmaprabhan 全文 6 页, 侧车坐标却是 9, 门禁报"p9#4"用户根本找不到)。
    """
    if part.get("true_page") is not None:
        return int(part["true_page"])
    if o is not None and o.get("pageid") is not None:
        return int(o["pageid"]) + 1
    return int(part["page"])


def restore_with_spans(raw, vv):
    """raw -> (还原后文本, {字形号: [(起, 止), ...]})。

    还原口径与 seg_export 一致({vN} -> vars[str(N)]), 但额外记下每个字形在**还原后**
    文本里的位置 —— 返工单要按还原后的文字切片引用原文, 直接切 raw 会把 {v29}
    切成 {v2 这种半截货。
    """
    out, spans, cur, i = [], {}, 0, 0
    for m in V_TOKEN.finditer(raw):
        # [v28.80] 与 seg_export.restore 同源: 两侧都剥不可见字符。**逐片**剥而不是
        # 末尾整串剥 —— spans 是按剥离后的文本算的, 整串剥会让下标全部漂掉。
        head = _TC.strip(raw[i:m.start()])
        out.append(head)
        cur += len(head)
        val = _TC.strip(str((vv or {}).get(m.group(1), m.group(0))))
        spans.setdefault(m.group(1), []).append((cur, cur + len(val)))
        out.append(val)
        cur += len(val)
        i = m.end()
    out.append(_TC.strip(raw[i:]))
    return "".join(out), spans


def glyph_context(raw, vv, vn, width=GLYPH_CTX):
    """原文里字形 vn 所在处的前后文(字形已还原), 供返工单引用。

    同一字形号在段里可能出现多次, 取第一处 —— 返工单是给人和译者的**线索**,
    不是判据本身。
    """
    txt, spans = restore_with_spans(raw, vv)
    hit = spans.get(str(vn))
    if not hit:
        return ""
    s, e = hit[0]
    lo, hi = max(0, s - width), min(len(txt), e + width)
    return ("…" if lo > 0 else "") + txt[lo:hi] + ("…" if hi < len(txt) else "")


def fail_where(raw, vv, fixed, zh, vn):
    """本字形"为什么没回锚"的处境 -> ((其后整片没译?, 位置%, 交付/载荷%), 载荷末段)。

    判据: 载荷里本字形**之后**还有没有别的字形**成功回了锚** —— 回锚只在译文里能定位到
    该字形时才成功, 所以"后面一个锚点都没有"就等于"本字形之后那半句在交付里没有落点"。
    这是**直接信号**, 不是"位置%"或"长度比"那类代理指标(那两个都会把已验证的案例分错,
    实测见 改动记录 9.7.3)。

    用 9 篇真译文的 26 处真 FAIL 逐条对账验证过: 20/21 例与人工判读一致。唯一一例
    (#S595 的第二个 `PŁ` —— 整句都译了, 只有这个字母没写出来)按"补译"处理**同样能改对**:
    译者回载荷对一眼就看出缺的是那半句里的 `PŁ`。反过来把 #S491/#S520/#S694 那类
    "整片没译"当"写回一串字符"处理则**改不动** —— 所以信号偏向"没译完"这一侧。
    """
    if not raw or not fixed:
        return None, ""
    try:
        res, spans = restore_with_spans(raw, vv)
        own = spans.get(str(vn)) or []
        if not own:
            return None, ""
        k = own[0][0]
        last_anchor = -1
        for m in V_TOKEN.finditer(raw):
            g = m.group(1)
            _p, core, _q = split_value(_TC.strip((vv or {}).get(g) or ""))
            # 与 reanchor 同一口径: 纯符号字形按设计丢弃, 不算落点; 没锚上的也不算。
            if not (core and has_alnum(core)) or ("{v%s}" % g) not in fixed:
                continue
            sp = spans.get(g) or []
            if sp:
                last_anchor = max(last_anchor, sp[0][0])
        tail = res[-PAYLOAD_TAIL:]
        return ((last_anchor <= k), 100.0 * k / max(1, len(res)),
                100.0 * len(zh) / max(1, len(res))), \
            ("…" if len(res) > PAYLOAD_TAIL else "") + tail
    except Exception:
        return None, ""


# 返工单的固定段落。放在模块级是为了让 test 能直接断言措辞(它是**给译者看的合同**)。
REWORK_INTRO = """# 返工单 —— {name}

这份单子是 `tools/seg_import.py` 自动生成的, 用来替代门禁控制台里的原始报错:
控制台报的是工具内部坐标(如 `p9#4`), 这里报的是**真实页码 + 段落编号**。

**只改下面点到的段**; 没点到的段请逐字符照抄你上一版, 不要顺手改动 ——
整篇重译会重新掷一次公式块与编号的骰子(v28.6 实测: 266 段里 206 段被动过)。

门禁判定: FAIL —— {count}
"""

REWORK_WHY = """## 一、必须改

**共同原因**：这些段里的字符是版面里**独立的字形对象**，译文里不出现它就**没有落点**，
渲染这一处会出错。但"为什么没出现"分两类，修法不一样，下面分开列了，照着做：

- **甲、载荷这一段没译完**（这批里最常见）：整句/从句/段尾那半截压根没进交付。
  回载荷把缺的部分**补译完** —— 包括段尾那个"看起来没写完"的公式残块，它属于原文，照译，
  不要因为"看着不完整"就丢掉（任务包规则 9）。
- **乙、句子译了，只是这一串字符没写出来**：把它**原样写回**（数字/字母照抄，不要改写成
  「三维」「二维」这类中文说法）；紧邻的字母/数字一起照抄；公式片段整块照抄、中文放在块外
  （任务包规则 2、7）。
"""


def write_rework_note(man, src, report, detail):
    """FAIL 时写"给译者的返工单" -> inbox/<name>.rework.md, 返回路径(不写返回 "")。

    `report` 是控制台那串校验行(FAIL/提示), `detail` 是回锚失败的结构化明细。
    两类分开渲染: 回锚失败能给出"缺哪个字符 + 原文哪一处"(译者看不见字形占位符背后的
    东西, 这正是它需要的线索); 其余 FAIL 原样转述。

    回锚失败再按**修法**分两栏(见 REWORK_WHY, 判据见 fail_where): 甲"段尾没译完 -> 补译" /
    乙"句子译了 -> 把这串写回"。混在一栏里点名"缺的字符: `3`"会把人引到"补一个字符"上去,
    而实测这批 FAIL 里绝大多数是整句没译 —— 补一个字符根本改不动(见 改动记录 9.7.3)。
    """
    name = man.get("name") or os.path.basename(src).replace(".manifest.json", "")
    if not name:
        return ""
    fails_anchor = {d["line"] for d in detail}
    other_fails = [r for r in report if r.startswith("FAIL") and r not in fails_anchor]
    hints = [r for r in report if r.startswith("提示")]
    keys = [int(str(it["key"]).lstrip("S")) for it in man.get("items", [])]

    # 计数行只在**真有回锚失败**时才报数; 缺段/断点类 FAIL 没有"处数"可数, 硬写
    # "需返工 0 段"会让人以为单子发错了(实测反复出现)。
    if detail:
        count = "需返工 %d 段 / %d 处字符没有落点" % (
            len(detail), sum(len(d["fails"]) for d in detail))
    else:
        count = "见下面「其他门禁失败」一节"

    L = [REWORK_INTRO.format(name=name, count=count).rstrip()]
    if detail:
        L.append("")
        L.append(REWORK_WHY.rstrip())
        # 逐段归栏: 一段里只要有一处属于"段尾没译完", 就整段按甲处理 —— 甲那句
        # "把缺的补译完"对乙那种"只差一串字符"也成立, 反过来不成立。
        grp = {"甲": [], "乙": []}
        for d in detail:
            infos = [(fail_where(d["raw"], d.get("vv"), d.get("fixed"), d.get("zh"), vn), vn, val)
                     for vn, val in d["fails"]]
            grp["甲" if any(w and w[0] and w[0][0] for w, _vn, _v in infos) else "乙"].append((d, infos))
        for tag, title in (("甲", "载荷这一段没译完 —— 回载荷把缺的补译完"),
                           ("乙", "句子译了，只是这一串字符没写出来 —— 原样写回")):
            if not grp[tag]:
                continue
            L.append("")
            L.append("**%s栏（%d 段）· %s**" % (tag, len(grp[tag]), title))
            for d, infos in grp[tag]:
                L.append("")
                L.append("### #S%d（第 %d 页）" % (d["key"], d["tp"]))
                L.append("- 缺的字符: %s" % "、".join("`%s`" % v for _vn, v in d["fails"][:8]))
                w = next((w for w, _vn, _v in infos if w), None)
                if tag == "甲" and w:
                    # 位置/长度是**给人核对的旁证**, 不是判据: 交付明显短于载荷时它印证
                    # "没译完"; 但长度比正常也不能反证(实测 #S400 比值 50% 仍整句没译)。
                    L.append("- 位置对照: 本字形在载荷 %d%% 处，交付只写到载荷的 %d%%"
                             % (round(w[0][1]), round(w[0][2])))
                    if w[1]:
                        L.append("- 载荷这一段的后半（照它把没译的补齐）: %s" % w[1])
                for vn, _val in d["fails"][:4]:
                    ctx = glyph_context(d["raw"], d["vv"], vn)
                    if ctx:
                        L.append("- 原文里它在哪: %s" % ctx)
                if d.get("moved"):
                    L.append("- **这一处的修法不同**: 上面这些字符的译文被写到了 ⋮ 的**另一侧**。"
                             "本段是「跨页续接」合并来的（⋮ 左右各对应原 PDF 的一页），"
                             "版面上这些字符属于那一页。请把 ⋮ 摆回原文那一处："
                             "⋮ 左边只写 ⋮ 左边原文的译文，右边只写右边原文的译文，"
                             "**不要把某一侧的短语提到另一侧去**（同侧内部可以按中文习惯调语序）。")
    # 小节编号随实际出现的小节走: 只写"一、必须改"和"三、不必改"、中间空着"二"，
    # 读的人会以为单子缺了一节。
    sections = []
    if other_fails:
        sections.append(("其他门禁失败（编号 / 分页断点 / 空段）",
                         ["- %s" % r for r in other_fails]))
    if hints:
        # 瘦身: 一篇几千段的稿子里「纯标点字形丢弃」一类提示能有几百条(实测 640 行的
        # 单子), 而它们**一条都不用改**(工具已自动丢弃并继续)。逐条列出来只会把
        # 「必须改」淹没掉, 所以只留前 HINT_MAX 条看形态, 其余报计数。
        lines = ["- %s" % r for r in hints[:HINT_MAX]]
        if len(hints) > HINT_MAX:
            lines.append("- …（同上提示共 %d 条，其余 %d 条不再逐条列出 —— "
                         "这些段**不用改**，逐字符照抄你上一版即可）"
                         % (len(hints), len(hints) - HINT_MAX))
        sections.append(("不必改（工具已自动处理，别动）", lines))
    for n, (title, lines) in enumerate(sections, 1 if not detail else 2):
        L.append("")
        L.append("## %s、%s" % ("一二三四五"[n - 1], title))
        L.extend(lines)
    L.append("")
    L.append("## 交件")
    L.append("- 交件名: `%s`（递增，别覆盖上一版；旧的 `.doubao*` 名字也认）"
             % RN.normalize(name))
    L.append("- 段号必须 #S1–#S%s 连续无缺。" % (max(keys) if keys else "?"))
    L.append("- 没点到的段逐字符照抄上一版。")
    L.append("")

    os.makedirs(INBOX, exist_ok=True)
    path = os.path.join(INBOX, name + REWORK_SUFFIX)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # [v28.58] 把这次踩的坑记进教训台账 -> 下一篇导出的提示词自动带上(见 lessons.py)。
    # 只记**该原样出现的字形串**(门禁的 ground truth), 不记译者写成的样子 —— 见模块头。
    try:
        import lessons as _LES
        for d in detail:
            for vn, val in d["fails"][:3]:
                _LES.record("GLYPH", val, glyph_context(d["raw"], d["vv"], vn))
            if d.get("moved"):
                _LES.record("MOVE", "⋮ 两侧译文互换")
        for r in other_fails[:3]:
            _LES.record("OTHER", r)
    except Exception:
        pass                      # 台账是辅助, 记不上不能反过来卡住门禁
    return path


def get_clipboard():
    ps = ("powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw")
    return subprocess.run(ps, capture_output=True, check=True).stdout.decode("utf-8", "replace")


def parse_blocks(text):
    """返回 {int_key: 译文正文}, 忽略编号行之前的引导文字。"""
    blocks, cur = {}, None
    buf = []
    for line in text.splitlines():
        m = KEY_LINE.match(line)
        if m:
            if cur is not None:
                blocks[cur] = "\n".join(buf).strip()
            cur, buf = int(m.group(1)), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        blocks[cur] = "\n".join(buf).strip()
    return blocks


PUNCT_CHARS = set(".,;:!?()[]{}<>\"'“”‘’（）《》【】、，。；：？！…—·-–‐‑‒―")
_FW = str.maketrans("（）：；，", "():;,")


def _fw(ch):
    return ch.translate(_FW)


# [自研补丁 2026-09-19] 标点等价类。
# 合格的中文译者会按中文排版规范把 core 内部的半角标点写成全角
# (2.1,(20) -> 2.1，(20)), 或把并列逗号写成顿号 ((26),(27) -> (26)、(27))。
# 旧实现用 str.find 做字面匹配 -> 这类段落全被判"高价值字形未回锚"。
# Wang 篇实测: 99 处 FAIL 中约 20 处纯由此产生。
PUNCT_EQUIV = {
    ",": ",，、",
    ".": ".。",
    "(": "(（",
    ")": ")）",
    ";": ";；",
    ":": ":：",
    "?": "?？",
    "!": "!！",
    "[": "[【",
    "]": "]】",
}


# [自研补丁 2026-09-20] 兼容等价类 + 空白弹性。
# 来源: SILAGE 篇 21 处 FAIL 逐条定性(用码点逐字符比对), 其中 12 处根本不是
# "译文缺字", 而是译文用了**同一个字的另一种写法**, 字面匹配认不出来:
#   - 上标数字: 版面里 '4'/'5'/'3' 是独立字形, 中文排版写成 '⁴'(U+2074)/'⁵'/'³'(U+00B3);
#   - OHM SIGN: 字形值是 'Ω'(U+2126), 译文写同义的希腊大写 'Ω'(U+03A9)
#     —— NFKC 把前者折成后者, Unicode 认定二者是同字符的两种码点;
#   - 空格: 译文按可读性补空格(公式与 ∀ 之间、'𝑏grp∈[𝑛]2:' 里 ' 2' 之前), 字形值里没有。
# 判据收口: 只并入「NFKC 折成同一个**单字符**、且折出来是字母/数字」的写法
# (³/³/₃/３ 同类), 多字符折叠(Ⅲ -> III、℃ -> °C)一律不收 —— 否则会把无关字符
# 拉进等价类, 制造错锚。空白只在字与字**之间**放宽为 \s*, 两端不放, 免得匹配区间外溢。
_EQ_RANGES = ((0x00A0, 0x2FFF), (0xFB00, 0xFB4F), (0xFF00, 0xFFEF))
_EQ_INDEX = None


def _eq_index():
    """{NFKC 折出的单字符: {其它写法}} —— 懒建一次(约 12k 字符, 毫秒级)。"""
    global _EQ_INDEX
    if _EQ_INDEX is None:
        idx = {}
        for lo, hi in _EQ_RANGES:
            for cp in range(lo, hi + 1):
                c = chr(cp)
                n = unicodedata.normalize("NFKC", c)
                if len(n) == 1 and n != c:
                    idx.setdefault(n, set()).add(c)
        _EQ_INDEX = idx
    return _EQ_INDEX


def _eq_class(ch):
    """ch 的兼容等价类(含自身)。折不出单字符字母/数字的, 只返回自己。

    取法是「**所有**折到同一个正体的写法」: ch 自己 + 索引里那些异体 + 正体本身
    (仅当正体本身就是范围里的字符, 即非 ASCII)。

    正体必须补进来 —— 索引是按"'正体' -> {异体}"建的, 只查 idx[key] 拿到的是
    **异体**; 当 ch 本身就是异体时(如 OHM SIGN U+2126, 正体是希腊 Ω U+03A9),
    查出来只有它自己, 恰好漏掉译文实际会写的那一个字 (v28.34 测试 ⑭ 逮到)。

    正体是 ASCII 时**不**并进来 —— 否则数学斜体字母(U+1D400 段, 在范围外)会被
    折成 ASCII, "𝑏" 去匹配译文里任意一个 'b', 把短 core 的全局唯一性打散。
    实测需要的就是范围里那几类(上标数字 U+2074/U+00B3、OHM SIGN U+2126),
    范围外的一律按原样匹配。
    """
    key = unicodedata.normalize("NFKC", ch)
    if len(key) != 1 or not key.isalnum():
        return {ch}
    cls = {ch} | _eq_index().get(key, set())
    if not key.isascii():
        cls.add(key)
    return cls


def _punct_eq(a, b):
    """a 与 b 是"同一个标点的两种写法"吗 —— 供**吸收**用。

    [自研补丁 2026-09-20] 口径必须与定位(_char_pattern)同源。原先吸收只认
    `_fw(a) == _fw(b)`(_FW 只折 `（）：；，` 六个), 而定位认 PUNCT_EQUIV 整类 ——
    两者不一致的地方就会**漏吸**: 字形值 `[16,20],` 定位时 core=`16,20`、
    pre=`[`、post=`],`; 译文 `SVRG [16,20]、SCSG` 里 `]`(_FW 折得出)被吞进占位符,
    `、`(≡ `,`, _FW 折不出)留在译文外。而占位符渲染时会把字形原值**整个**写回,
    页面上于是出现 `[16,20],]、` —— 多一个 `]`, post_check 引用计数随之虚高。
    SILAGE 实测 30 处残留 / 20 段(其中 19 处正好把"引用完整性 69 vs 88"顶出容差)。
    """
    if a == b:
        return True
    if a in PUNCT_EQUIV or b in PUNCT_EQUIV:
        return a in PUNCT_EQUIV.get(b, b) or b in PUNCT_EQUIV.get(a, a)
    return _fw(a) == _fw(b)


def _char_pattern(ch):
    """单字符 -> 正则片段(标点优先走 PUNCT_EQUIV, 其余走兼容等价类)。"""
    if ch in PUNCT_EQUIV:
        return "[%s]" % re.escape(PUNCT_EQUIV[ch])
    eq = _eq_class(ch)
    if len(eq) == 1:
        return re.escape(ch)
    return "[%s]" % re.escape("".join(sorted(eq)))


def _core_pattern(core):
    """core -> 正则。三条放宽都只是**定位**口径, 命中后仍写回字形原值:
      ① 标点等价类: 半/全角等价 (2.1,(20) -> 2.1，(20)); 数字间的逗号额外允许
         整块消失(译者把 2,000 写成 2000 时仍能命中);
      ② 兼容等价类: 上标数字 / OHM SIGN / 全角数字等 NFKC 同字写法;
      ③ 空白弹性: 字与字之间允许译文自己补的空格。"""
    parts = []
    for k, ch in enumerate(core):
        if ch.isspace():
            parts.append(r"\s*")
            continue
        cls = _char_pattern(ch)
        if (ch == "," and 0 < k < len(core) - 1
                and core[k - 1].isdigit() and core[k + 1].isdigit()):
            cls += "?"      # 千分位弹性
        parts.append(cls)
        if k < len(core) - 1:
            parts.append(r"\s*")
    return re.compile("".join(parts))


def _spans(pat, text):
    return [(m.start(), m.end()) for m in pat.finditer(text)]


def _overlaps(s, e, taken):
    return any(s < te and ts < e for ts, te in taken)


def _free_spans(pat, seg_zh, taken):
    """pat 在译文里全部未被占用的落点(按出现序)。"""
    return [sp for sp in _spans(pat, seg_zh) if not _overlaps(sp[0], sp[1], taken)]


def split_value(val):
    """'(1793),' -> ('(', '1793', '),'); '-' -> ('', '', '')"""
    i = 0
    while i < len(val) and val[i] in PUNCT_CHARS:
        i += 1
    j = len(val)
    while j > i and val[j - 1] in PUNCT_CHARS:
        j -= 1
    return val[:i], val[i:j], val[j:]


def sibling_hits(fails, parts_zh, idx):
    """合并段里, 本侧没落点的字形是否出现在**别侧**的译文里 -> {字形号: 值}。

    [自研补丁 2026-09-20] 跨页续接合并成的段, ⋮ 左右各对应原 PDF 的一页; 回锚是
    **按侧**做的(缓存行也按页), 所以译文只要把断点位置挪了(把下一页开头的短语提到
    这一侧), 本侧就找不到那些字形。这类 FAIL 让译者"把它原样写回"是**无解的** ——
    他那侧的译文本来就不该有这段内容, 照着改只会来回打转(实测 SILAGE #S22: 原文
    断点落在 "…the running aggregate⋮that supplies the descent direction in
    Algorithms 1 and 2—…", 译文把「算法 1 和算法 2 提供下降方向」提到了 ⋮ 左侧,
    右侧那侧的 '1'/'2—' 两个字形于是没落点)。
    判"挪了位置"而不是"漏了内容", 报错才有可执行的修法: 把 ⋮ 摆回原文那一处。
    """
    out = {}
    for vn, val in fails:
        _pre, core, _post = split_value(val)
        pat = _core_pattern(core)
        for j, zh in enumerate(parts_zh):
            if j != idx and pat.search(zh):
                out[vn] = val
                break
    return out


def has_alnum(text):
    """字形 core 里含"字母/数字"吗 —— 高价值字形的判据(Unicode 口径)。

    [v28.38] 原实现是 `re.search(r"[A-Za-z0-9]", core)`, **ASCII-only**: 侧车把
    数学斜体存成**字面文本**(SILAGE p1#4 `vars[4]='𝑁=𝑛𝑚'`), 而 `𝑁`/`𝑛`/`𝑚` 是
    U+1D400–U+1D7FF 的数学字母 —— 判据认不出, 它们就被当成"纯标点/纯符号"丢弃,
    **从不回锚成 {vN}**。渲染时它们只能当普通文本排进**中文字体**(LXGW Neo
    ZhiSong), 而中文字体的 cmap 里没有这些码位 → CID 0/.notdef: 视觉是豆腐块,
    提取是 NUL(SILAGE 全篇 1518 处、第 1 页 32 处; 同引擎对照版 0 处 —— 那边走
    pdf2zh 原生公式保护, 同一批字符由原文字体 CMMI10 画)。改成 Unicode 口径后
    这类字形才像数字/字母一样参与回锚、由 var[i] **原字体**回填(与 `_eq_class`
    的判据同源, 那里本来就用 `isalnum()`)。
    """
    return any(ch.isalnum() for ch in text)


def reanchor(seg_zh, raw, vars_):
    """只回锚高价值字形(核心=字母/数字), 纯标点字形按设计丢弃。
    核心命中后向两侧吸收与字形值一致的标点(等价类同 _char_pattern, 见 _punct_eq),
    避免渲染时双重标点: 占位符写回的是字形**原值**(含首尾标点), 译文里已写出的那套
    标点必须删掉, **漏一个就多一个字** —— 实测 `SVRG [16,20], SCSG [22],` 这类引用
    字形, `],` 里的 `]` 若留在占位符外, 页面就出现 `[16,20],]、` 两个 `]`, 引用完整性
    断言随之虚高(SILAGE 篇 30 处残留 / 20 段, 把 69 vs 88 顶出容差)。译文省掉字形
    尾部标点时同理: 写出的那部分(如 `[22]` 的 `]`)照样要吞。

    两阶段定位 (2026-09-19 Wang 篇实测: 单遍单调游标 99 处 FAIL; 两遍后 21,
    其中 7 处是短字形抢位造成的假 FAIL):
      ① 独特性 -- 长 core 优先, 只占"全局唯一落点"的 token。长公式更长更独特,
         必须先行; 否则短字形(k→∞Ekxi(k)− / ak,s / s)会先抢走长公式唯一的
         落点, 反过来把长公式判 FAIL。
      ② 语序   -- 剩下的按原文顺序推进单调游标。译文语序与原文一致时最准;
         游标之后没有候选(中文把符号提前了)才退到"离游标最近"。

    返回 (译文, fails, drops, notes):
      fails -- 高价值字形在译文里定位不到 -> 调用方按 FAIL 处理
      drops -- 纯标点字形, 按设计丢弃
      notes -- (vn, val, 候选数) 歧义锚定台账, 供人工复查
    """
    fails, drops, notes = [], [], []
    # [v28.80] 不可见字符体检 —— 回锚是**唯一**做字符串定位的地方, 所以剥离必须成对
    # 落在这里: 搜索串(字形值 val) 与 被搜索串(译文 seg_zh) **走同一个 text_clean.strip**。
    # 只剥一侧 = 自己制造 str.find 落空 —— 那反而会把好端端的段判成 FAIL。
    # 剥离属**归一化**(与 `_eq_class` 那套定位口径同级), 不参与任何 PASS/FAIL 判定。
    seg_zh = _TC.strip(seg_zh)
    toks = []
    for m in V_TOKEN.finditer(raw):
        vn = m.group(1)
        val = vars_.get(vn)
        if val is None:
            continue
        val = _TC.strip(val)
        pre, core, post = split_value(val)
        if not core or not has_alnum(core):
            # 纯标点/纯符号字形(/ 等)按设计丢弃, 不参与搜索
            drops.append((vn, val))
            continue
        toks.append((vn, val, pre, core, post))

    taken, placed, rest = [], {}, []
    for t in sorted(toks, key=lambda x: -len(x[3])):    # ① 长 core 优先
        free = _free_spans(_core_pattern(t[3]), seg_zh, taken)
        if len(free) == 1:
            placed[t[0]] = free[0]
            taken.append(free[0])
        else:
            rest.append(t)

    cursor = 0
    for t in rest:                                      # ② 原文顺序 + 单调游标
        vn, val, core = t[0], t[1], t[3]
        free = _free_spans(_core_pattern(core), seg_zh, taken)
        if not free:
            fails.append((vn, val))
            continue
        ahead = [sp for sp in free if sp[0] >= cursor]
        hit = ahead[0] if ahead else min(free, key=lambda sp: abs(sp[0] - cursor))
        if len(free) > 1:
            notes.append((vn, val, len(free)))
        placed[vn] = hit
        taken.append(hit)
        cursor = hit[1]

    pieces = []
    for vn, _val, pre, _core, post in toks:
        if vn not in placed:
            continue
        s, e = placed[vn]
        os_, oe_ = s, e
        # 两侧都按**字形值原序**逐字对账(pre 从右往左、post 从左往右), 每个位置用
        # _punct_eq 判"同一个标点的两种写法"。**能吃多少吃多少**, 不要求整段对上 ——
        # 只要译文写了字形尾部的一部分标点, 那部分就必须吞掉: 占位符渲染时写回的是
        # 字形**原值**, 漏掉的部分会与译文自己写的标点并排出现(实测 SILAGE `[22],`
        # + 译文 `[22] ` -> 页面 `[22],]`)。逐位对账保证首位不等价即停, 不会误吞
        # 译文里字形并不覆盖的标点。
        if pre:
            k, pi = s, len(pre)
            while (k > 0 and pi > 0 and seg_zh[k - 1] in PUNCT_CHARS
                   and _punct_eq(seg_zh[k - 1], pre[pi - 1])):
                k -= 1
                pi -= 1
            s = k
        if post:
            k, pi = e, 0
            while (k < len(seg_zh) and pi < len(post)
                   and seg_zh[k] in PUNCT_CHARS
                   and _punct_eq(seg_zh[k], post[pi])):
                k += 1
                pi += 1
            e = k
        if _overlaps(s, e, [x for x in taken if x != (os_, oe_)]):
            s, e = os_, oe_     # 吸收越界(语序重排后) -> 退回裸匹配, 不侵占用区间
        pieces.append((s, e, "{v%s}" % vn))
    pieces.sort()
    res, last = [], 0
    for s, e, rep in pieces:
        res.append(seg_zh[last:s])
        res.append(rep)
        last = e
    res.append(seg_zh[last:])
    return "".join(res), fails, drops, notes


def _ms(items):
    """多重集 -> "a x2, b x1" 这种人读得懂的写法(返工单里要逐字点出缺了哪个)。"""
    return ", ".join("%s x%d" % (k, n) for k, n in sorted(items.items()))


def _ph_ms(s):
    """`{vN}` 的多重集 —— **连花括号一起**(返工单要能逐字点出缺的是哪一个:
    "少了 1 x1" 这种写法, 读的人还得自己回头想那是哪个占位符)。"""
    return Counter(re.findall(r"\{v\d+\}", s or ""))


def _fails_of(lack):
    """不守恒的"缺"项 -> 返工单的 `fails` 字段 [(定位键, 显示值)]。

    显示值 = 直接印给人看的东西(`{v1}` / `<style id='2'>`); 定位键给 glyph_context 用
    —— 1.x 那边是字形号, 这里取 `{vN}` 的编号; 样式标签没有可查询的编号, 取标签本身,
    查不到只是不打印"原文里它在哪"那一行, 不影响"缺的字符"。
    """
    out = []
    for k in sorted(lack):
        m = re.fullmatch(r"\{v(\d+)\}", k)
        out.append((m.group(1) if m else k, k))
    return out


def _tag_ms(s):
    """`<style id='n'>` / `</style>` 的多重集(连 id 一起比: id 变了就落到另一份样式上)。"""
    return Counter(re.findall(r"</?style[^>]*>", s or ""))


def import_next(args, man, blocks):
    """next 路线: 收译者译文 -> 守恒校验 -> out/<name>.imported.json (**无回锚**)。

    与 1.x 的**本质差别**: 载荷正文本来就是引擎原生形态({vN}/<style>), 收回来直接
    就能写回缓存 —— 不需要把"显示字形"回锚成 {vN}(那是 1.x 专有的: 它把还原过的
    真字形喂给了译者, 收回来必须再还原回占位符)。

    于是这里只剩**守恒校验**。判据与引擎同源, 不是我们自己发明的严格度:
      1. 段号集合 = manifest(缺段/多段都 FAIL)
      2. 每段 {vN} 的**多重集**守恒 —— 少一个 = 版面上少一处公式, 多一个 = 凭空多
         出一处; 上游自己的判据就是 full match(段表里那个 `placeholder_full_match`)。
         用集合而不是多重集就会漏掉"{v1} 出现两次却只写回一次"这类错, 所以按计数比。
      3. `<style id='n'>`/`</style>` 多重集守恒 —— 标签丢了 = 那一小段的字体/样式
         回落, 成品里表现为"某几个词突然换了字体"; id 也在比较范围内(id 变了就
         落到了另一份样式上)。
      4. 译文里不许出现 ⋮ —— next 的载荷不注入断点, 出现即译者自己加的, 会原样
         印到纸上。
    """
    want = {it["key"]: it for it in man["items"]}
    got = {int(k): v for k, v in blocks.items()}
    report, detail, ok = [], [], True

    # 源正文从**载荷本身**取 —— next 的 manifest 只落坐标不落正文(正文与载荷逐字相同,
    # 再存一份只是重复), 而载荷正文就是 {vN}/<style> 原生形态, 与段表 input 同值。
    # 拿载荷当判据还多一层好处: 译者改的就是它, 两边是同一件东西。
    pay = os.path.join(os.path.dirname(os.path.abspath(args.manifest)),
                       (man.get("name") or "") + ".txt")
    if not os.path.isfile(pay):
        print("FAIL: 找不到载荷原文 %s —— 守恒校验要拿它当判据(manifest 只落坐标); "
              "请与 manifest 一起保留 inbox/<name>.txt" % pay)
        return 1
    with open(pay, encoding="utf-8-sig") as f:
        src_blocks = parse_blocks(f.read())

    want_ids = {int(str(k).lstrip("S")) for k in want}
    miss = sorted(want_ids - set(got))
    extra = sorted(set(got) - want_ids)
    if miss:
        ok = False
        report.append("FAIL 缺段: %s" % miss)
    if extra:
        ok = False
        report.append("FAIL 多段: %s" % extra)

    imported = {}
    for key_s, it in sorted(want.items(), key=lambda kv: int(str(kv[0]).lstrip("S"))):
        key = int(str(key_s).lstrip("S"))
        # [v28.80] 不可见字符体检: 只剥**译文**。本路线的载荷原文是引擎自己的 prompt
        # (它就是缓存主键 original_text, manifest.fp 也是按它算的) —— 动载荷 = 自造
        # fp 对不上, 当场被判"这份载荷不是从当前段表导出的"。译文侧没有这个约束。
        zh = _TC.strip((got.get(key) or "").strip())
        pg = (it.get("parts") or [{}])[0].get("page")
        loc = "#S%d（第%d页%s）" % (key, pg, "·跨页" if it["pool"] == "cross_page" else "")
        if not zh:
            ok = False
            report.append("FAIL %s 译文为空" % loc)
            continue
        if "\u22ee" in zh:
            ok = False
            report.append("FAIL %s 出现了 ⋮ —— 本路线不分页断点, 这是多写的, 会原样印到纸上" % loc)
            zh = zh.replace("\u22ee", "")
        src = src_blocks.get(key, "")
        if not src:
            ok = False
            report.append("FAIL %s 载荷里没有这一段(编号对不上, 无法校验守恒)" % loc)
            continue
        for what, a, b in (("占位符", _ph_ms(src), _ph_ms(zh)),
                           ("样式标签", _tag_ms(src), _tag_ms(zh))):
            if a == b:
                continue
            ok = False
            lack = {k: n for k, n in (a - b).items()}
            more = {k: n for k, n in (b - a).items()}
            why = []
            if lack:
                why.append("少了 %s" % _ms(lack))
            if more:
                why.append("多了 %s" % _ms(more))
            line = ("FAIL %s %s不守恒: %s（载荷里 %s；译文里 %s）"
                    % (loc, what, "; ".join(why), _ms(a), _ms(b)))
            report.append(line)
            if lack:
                # 只把"缺的"编进返工单的"必须改"一节(与 1.x 回锚失败同一节 —— 那里能
                # 指出原文位置)。"多了"没有原文位置可指, 原样留在「其他门禁失败」转述。
                detail.append({"line": line, "key": key, "tp": pg,
                               "fails": _fails_of(lack), "raw": src,
                               "vv": {}, "moved": False})
        imported[key_s] = zh

    os.makedirs(OUTDIR, exist_ok=True)
    out_json = os.path.join(
        OUTDIR, os.path.basename(args.manifest).replace(".manifest.json", "") + ".imported.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(imported, f, ensure_ascii=False, indent=1)

    name = man.get("name") or os.path.basename(args.manifest).replace(".manifest.json", "")
    note = os.path.join(INBOX, name + REWORK_SUFFIX)
    if ok:
        if os.path.exists(note):
            os.remove(note)
    else:
        note = write_rework_note(man, args.manifest, report, detail) or note

    print("--- 校验报告 ---")
    for r in report:
        print(r)
    print("收下段数: %d" % len(imported))
    print("产出: %s" % out_json)
    if not ok:
        print("返工单: %s" % note)
        print("        (给译者的: 让它 list_inbox / get_payload 读这个文件, 按上面点到的段改)")
    print("结论: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--clip", action="store_true", help="从剪贴板取译文")
    ap.add_argument("--text", default="", help="从文件取译文")
    ap.add_argument("--sidecar", default=SIDECAR, help="侧车路径; 与导出时一致")
    args = ap.parse_args()

    # 引擎接线门禁: 回锚口径(1.x 的 var[]/sstk 吸收)在新引擎里没有对应物, 单跑本工具
    # 时同样要拦 —— adopt 拦的是它自己派发的调用。
    _ok, _why = _PROF.stage_ok("import")
    if not _ok:
        print("FAIL: 引擎 %s 上未接线: %s" % (_PROF.key, _why))
        return 2

    with open(args.manifest, encoding="utf-8") as f:
        man = json.load(f)

    if args.clip:
        text = get_clipboard()
        src = "剪贴板"
    else:
        with open(args.text, encoding="utf-8-sig") as f:
            text = f.read()
        src = args.text

    # 去掉 markdown 代码围栏
    text = re.sub(r"^```[a-z]*\s*$", "", text, flags=re.M).strip()
    blocks = parse_blocks(text)
    print("来源: %s (%d 字符) -> 解析出 %d 段" % (src, len(text), len(blocks)))

    # 两条路线(由引擎画像决定): next 的载荷本来就是原生形态 -> 只做守恒校验, 无回锚。
    if _PROF.seg_source == "tracking_json":
        return import_next(args, man, blocks)

    want = {it["key"]: it for it in man["items"]}
    got = {int(k): v for k, v in blocks.items()}
    report, ok, detail = [], True, []

    # ---- 编号对账 (统一为整数) ----
    want_ids = {int(str(k).lstrip("S")) for k in want}
    miss = sorted(want_ids - set(got))
    extra = sorted(set(got) - want_ids)
    if miss:
        ok = False
        report.append("FAIL 缺段: %s" % miss)
    if extra:
        ok = False
        report.append("FAIL 多段: %s" % extra)

    pages = load_sidecar_pages(args.sidecar)
    name = man.get("name") or os.path.basename(args.manifest).replace(".manifest.json", "")
    imported, led, all_fails, all_drops = {}, [], [], []
    for key_s, it in sorted(want.items()):
        key = int(str(key_s).lstrip("S"))
        # [v28.80] 与回锚同源: 先剥不可见字符再拆 ⋮ —— 否则 parts_zh(要喂给
        # sibling_hits 做 pattern.search)是原串, 而 pattern 来自剥过的字形值,
        # 又是一次"两侧不同源"。剥离属归一化, 不参与 PASS/FAIL 判定。
        zh = _TC.strip(got.get(key, ""))
        n_parts = len(it["parts"])
        parts_zh = zh.split("⋮")
        # ⋮ 对账
        if it["merged"]:
            if len(parts_zh) != n_parts:
                ok = False
                report.append("FAIL #S%d ⋮ 数量 %d != 预期 %d" % (key, len(parts_zh) - 1, n_parts - 1))
                continue
        else:
            if "⋮" in zh:
                ok = False
                report.append("FAIL #S%d 普通段出现了 ⋮" % key)
            parts_zh = [zh.replace("⋮", "")]
        if not zh.strip():
            ok = False
            report.append("FAIL #S%d 译文为空" % key)
            continue
        # 回锚
        for i, (p, seg_zh) in enumerate(zip(it["parts"], parts_zh)):
            pg, seg = p["page"], p["seg"]
            o = pages[pg]
            vv = o.get("vars") or {}
            raw = o["segs"][seg].get("raw") or ""
            # 报错一律用**真实页码 + #S编号**: 控制台是给人看的, 而内部坐标(p9#4)
            # 在一篇 6 页的论文上指向不存在的页, 用户读不懂也搜不到。
            loc = "#S%d（第%d页）" % (key, part_true_page(p, o))
            if not V_TOKEN.search(raw):
                imported["%d#%d" % (pg, seg)] = seg_zh
                continue
            fixed, fails, drops, notes = reanchor(seg_zh, raw, vv)
            imported["%d#%d" % (pg, seg)] = fixed
            # [v28.82] 埋点: 把两类"异常字形"按字符家族记下来(模块头 LEDGER 一节)。
            # **只记不判** —— 下面那几行 report/ok 才是判据, 这里一个字节都不动它们。
            led += ledger_rows(name, loc, pg, seg, "fail", fails)
            led += ledger_rows(name, loc, pg, seg, "drop", drops)
            all_fails += fails
            all_drops += drops
            if fails:
                ok = False
                moved = sibling_hits(fails, parts_zh, i) if it["merged"] else {}
                if moved:
                    line = ("FAIL %s 合并段的 ⋮ 位置被挪: %s 的译文出现在 ⋮ 另一侧"
                            "（本段由跨页续接合并而来: ⋮ 左只写左页内容、右只写右页内容）"
                            % (loc, sorted(set(moved.values()))))
                else:
                    line = "FAIL %s 高价值字形未回锚: %s" % (
                        loc, ["{v%s}=%r" % f for f in fails[:8]])
                report.append(line)
                detail.append({"key": key, "tp": part_true_page(p, o), "line": line,
                               "raw": raw, "vv": vv, "fails": fails, "moved": moved,
                               # 返工单要按修法分栏(见 write_rework_note): 分栏判据要看
                               # "这一处字形之后有没有别的字形锚上了", 得把回锚结果与
                               # 交付原文都带上。
                               "fixed": fixed, "zh": seg_zh})
            if drops:
                report.append("提示 %s 纯标点字形丢弃 %d 个: %s" % (
                    loc, len(drops), [d[1] for d in drops[:6]]))
            if notes:
                report.append("提示 %s 歧义锚定 %d 个(取最近, 需复查): %s" % (
                    loc, len(notes),
                    ["{v%s}=%r x%d" % n for n in notes[:6]]))

    # [v28.82] 埋点落盘 + 当场报一句(用户跑真实翻译时就能看见, 不必回头翻文件)。
    # 这一步**在判据之外**: 台账写不进去也不改退出码、不改 ok。总账**每次都记**(哪怕一个
    # 异常都没有)—— 否则"埋点没触发"和"这次真的干净"分不出来。
    summ = ledger_summary(name, all_fails, all_drops, led)
    led_file = append_ledger([summ] + led)
    _fam = sorted(set(list(summ["fam_fail"]) + list(summ["fam_drop"])))
    print("埋点: 回锚异常 %d 处 (未回锚 %d, 纯符号丢弃 %d) -> %s"
          % (summ["n_fail"] + summ["n_drop"], summ["n_fail"], summ["n_drop"],
             led_file or "(写盘失败)"))
    print("      字符家族 未回锚/丢弃: %s; 其余(纯标点·OCR 碎片): %d/%d"
          % (", ".join("%s %d/%d" % (k, summ["fam_fail"].get(k, 0),
                                     summ["fam_drop"].get(k, 0)) for k in _fam) or "无",
             summ["n_plain_fail"], summ["n_plain_drop"]))

    os.makedirs(OUTDIR, exist_ok=True)
    out_json = os.path.join(OUTDIR, os.path.basename(args.manifest).replace(".manifest.json", "") + ".imported.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(imported, f, ensure_ascii=False, indent=1)

    # 返工单: FAIL 时写一份给译者(桥读得到), PASS 时把旧单子删掉 ——
    # 留着过期单子比没有更糟: 译者会照它改已经改好的段。
    note = os.path.join(INBOX, name + REWORK_SUFFIX)
    if ok:
        if os.path.exists(note):
            os.remove(note)
    else:
        note = write_rework_note(man, args.manifest, report, detail) or note

    print("--- 校验报告 ---")
    for r in report:
        print(r)
    print("回锚段数: %d" % len(imported))
    print("产出: %s" % out_json)
    if not ok:
        print("返工单: %s" % note)
        print("        (给译者的: 让它 list_inbox / get_payload 读这个文件, 按上面点到的段改)")
    print("结论: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
