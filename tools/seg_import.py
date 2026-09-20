# -*- coding: utf-8 -*-
"""seg_import.py — 收豆包译文: 解析/对账/回锚 (M1 工具, 与 seg_export.py 配对)

做什么:
  1. 取译文: --clip 读剪贴板, 或 --text 指定文件 (豆包回复的全文)
  2. 解析: 按 #S编号 行切块 (容忍 markdown 加粗/代码围栏)
  3. 对账 manifest: 编号集合一致; 合并段 ⋮ 恰好 1 个且两侧非空; 普通段禁止 ⋮
  4. 回锚: 对每段按侧车 raw 的 {vN} 原序, 在译文中定位字形值 -> 还原为 {vN}
     高价值字形 (含字母/数字) 找不到 -> FAIL; 纯标点找不到 -> 丢弃并记提示
     (定位口径: 标点半/全角等价 + 上标数字·OHM 号等 NFKC 同字写法 + 译文自己补的空格)
  5. 产出 out/<name>.imported.json: {(page,seg): 带{vN}的译文} + 校验报告
  6. FAIL 时落一份**给豆包的返工单**到 inbox/<name>.rework.md(桥的 list_inbox /
     get_payload 直接读得到): 每条写明 真实页码 + #S编号 + 缺的字符 + 原文上下文,
     用户不必去理解控制台里的内部坐标; PASS 时把旧单子删掉, 免得读到过期结论。

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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SIDECAR = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "segflow", "latest.jsonl")
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
OUTDIR = os.path.join(PROJ, "out")
INBOX = os.environ.get("P2Z_INBOX", os.path.join(PROJ, "inbox"))

KEY_LINE = re.compile(r"^\s*#?\**\s*S(\d+)\s*\**\s*$")
V_TOKEN = re.compile(r"\{v(\d+)\}")

REWORK_SUFFIX = ".rework.md"
GLYPH_CTX = 60          # 返工单里原文上下文的半宽(按**还原后**字符数)


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
        out.append(raw[i:m.start()])
        cur += m.start() - i
        val = str((vv or {}).get(m.group(1), m.group(0)))
        spans.setdefault(m.group(1), []).append((cur, cur + len(val)))
        out.append(val)
        cur += len(val)
        i = m.end()
    out.append(raw[i:])
    return "".join(out), spans


def glyph_context(raw, vv, vn, width=GLYPH_CTX):
    """原文里字形 vn 所在处的前后文(字形已还原), 供返工单引用。

    同一字形号在段里可能出现多次, 取第一处 —— 返工单是给人和豆包的**线索**,
    不是判据本身。
    """
    txt, spans = restore_with_spans(raw, vv)
    hit = spans.get(str(vn))
    if not hit:
        return ""
    s, e = hit[0]
    lo, hi = max(0, s - width), min(len(txt), e + width)
    return ("…" if lo > 0 else "") + txt[lo:hi] + ("…" if hi < len(txt) else "")


# 返工单的固定段落。放在模块级是为了让 test 能直接断言措辞(它是**给豆包看的合同**)。
REWORK_INTRO = """# 返工单 —— {name}

这份单子是 `tools/seg_import.py` 自动生成的, 用来替代门禁控制台里的原始报错:
控制台报的是工具内部坐标(如 `p9#4`), 这里报的是**真实页码 + 段落编号**。

**只改下面点到的段**; 没点到的段请逐字符照抄你上一版, 不要顺手改动 ——
整篇重译会重新掷一次公式块与编号的骰子(v28.6 实测: 266 段里 206 段被动过)。

门禁判定: FAIL —— {count}
"""

REWORK_WHY = """## 一、必须改

**共同原因**：版面里这些字符是独立的字形对象，译文里不出现它就**没有落点**，
渲染这一处会出错，所以门禁直接判 FAIL。
**共同修法**：把它**原样写回**（数字/字母照抄，不要改写成「三维」「二维」这类中文说法）；
紧邻的字母/数字一起照抄；公式片段整块照抄、中文放在块外（见任务包规则 2、7）。
"""


def write_rework_note(man, src, report, detail):
    """FAIL 时写"给豆包的返工单" -> inbox/<name>.rework.md, 返回路径(不写返回 "")。

    `report` 是控制台那串校验行(FAIL/提示), `detail` 是回锚失败的结构化明细。
    两类分开渲染: 回锚失败能给出"缺哪个字符 + 原文哪一处"(豆包看不见字形占位符
    背后的东西, 这正是它需要的线索); 其余 FAIL 原样转述。
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
        for d in detail:
            L.append("")
            L.append("### #S%d（第 %d 页）" % (d["key"], d["tp"]))
            L.append("- 缺的字符: %s" % "、".join("`%s`" % v for _vn, v in d["fails"][:8]))
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
        sections.append(("不必改（工具已自动处理，别动）",
                         ["- %s" % r for r in hints]))
    for n, (title, lines) in enumerate(sections, 1 if not detail else 2):
        L.append("")
        L.append("## %s、%s" % ("一二三四五"[n - 1], title))
        L.extend(lines)
    L.append("")
    L.append("## 交件")
    L.append("- 交件名: `%s.doubao*.txt`（递增，别覆盖上一版）" % name)
    L.append("- 段号必须 #S1–#S%s 连续无缺。" % (max(keys) if keys else "?"))
    L.append("- 没点到的段逐字符照抄上一版。")
    L.append("")

    os.makedirs(INBOX, exist_ok=True)
    path = os.path.join(INBOX, name + REWORK_SUFFIX)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
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
         整块消失(豆包把 2,000 写成 2000 时仍能命中);
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


def reanchor(seg_zh, raw, vars_):
    """只回锚高价值字形(核心=字母/数字), 纯标点字形按设计丢弃。
    核心命中后向两侧吸收与字形值一致的标点(全角兼容), 避免渲染时双重标点。

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
    toks = []
    for m in V_TOKEN.finditer(raw):
        vn = m.group(1)
        val = vars_.get(vn)
        if val is None:
            continue
        pre, core, post = split_value(val)
        if not core or not re.search(r"[A-Za-z0-9]", core):
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
        if pre:
            k = s
            while k > 0 and seg_zh[k - 1] in PUNCT_CHARS and _fw(seg_zh[k - 1]) in pre:
                k -= 1
            if k < s and _fw(seg_zh[k:s]) == _fw(pre):
                s = k
        if post:
            k = e
            while k < len(seg_zh) and seg_zh[k] in PUNCT_CHARS and _fw(seg_zh[k]) in post:
                k += 1
            if k > e and _fw(seg_zh[e:k]) == _fw(post):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--clip", action="store_true", help="从剪贴板取译文")
    ap.add_argument("--text", default="", help="从文件取译文")
    ap.add_argument("--sidecar", default=SIDECAR, help="侧车路径; 与导出时一致")
    args = ap.parse_args()

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
    imported = {}
    for key_s, it in sorted(want.items()):
        key = int(str(key_s).lstrip("S"))
        zh = got.get(key, "")
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
                               "raw": raw, "vv": vv, "fails": fails, "moved": moved})
            if drops:
                report.append("提示 %s 纯标点字形丢弃 %d 个: %s" % (
                    loc, len(drops), [d[1] for d in drops[:6]]))
            if notes:
                report.append("提示 %s 歧义锚定 %d 个(取最近, 需复查): %s" % (
                    loc, len(notes),
                    ["{v%s}=%r x%d" % n for n in notes[:6]]))

    os.makedirs(OUTDIR, exist_ok=True)
    out_json = os.path.join(OUTDIR, os.path.basename(args.manifest).replace(".manifest.json", "") + ".imported.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(imported, f, ensure_ascii=False, indent=1)

    # 返工单: FAIL 时写一份给豆包(桥读得到), PASS 时把旧单子删掉 ——
    # 留着过期单子比没有更糟: 豆包会照它改已经改好的段。
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
    print("回锚段数: %d" % len(imported))
    print("产出: %s" % out_json)
    if not ok:
        print("返工单: %s" % note)
        print("        (给豆包的: 让它 list_inbox / get_payload 读这个文件, 按上面点到的段改)")
    print("结论: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
