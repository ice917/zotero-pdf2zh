"""tools/text_clean.py —— 不可见字符体检的**单一剥离函数** (v28.80)

为什么单独一个模块
  这个函数**必须被两侧同时调用**: 原文侧(载荷 / 侧车字形值) 与 译文侧(豆包回包)。
  **只剥一侧 = 自己制造 str.find 落空**, 把好端端的段判成 FAIL —— 那比不剥更糟。
  独立成零依赖模块, 是为了让 seg_export / seg_import / pre_check 都能直接 import,
  又不给任何一个模块引入新耦合。

它防的不是"错译", 是**回锚 FAIL**
  一个 U+200B 就能让 str.find 落空(`_core_pattern` 找的是字形 core 串), 而它在人眼里
  不存在 —— 事后无从定位。少见但真实: AI 隐形水印、网页聊天的富文本粘贴、
  OCR/排版残留都可能带进来。

口径 = **归一化, 不是放行口径**
  与 seg_import 的 `_eq_class`(NFKC 单字符等价类) 同级: 只改变"拿什么去比",
  不改变任何 PASS/FAIL 判定, 不参与 adopt 的放行。

  剥离(23 个码点为 v28.81 全码点普查补齐, 现覆盖 141/170 个 Cf):
    零宽/格式字符 U+200B-U+200F(ZWSP/ZWNJ/ZWJ/LRM/RLM)、U+2060-U+2064(word joiner 等)、
    U+FEFF(BOM)、U+00AD(soft hyphen); bidi 控制 U+202A-U+202E、U+2066-U+2069、U+061C(ALM);
    U+180E(MVS); 废弃格式符 U+206A-U+206F; 行间注释 U+FFF9-U+FFFB;
    速记格式 U+1BCA0-U+1BCA3; 乐谱连接符 U+1D173-U+1D17A; tag 块 U+E0000-U+E007F
  不剥(明写, 免得日后被"顺手补全"):
    - Cf 里**有视觉含义**的那批 —— 阿拉伯数字符号 U+0600-U+0605 / U+06DD、叙利亚缩略号 U+070F、
      阿拉伯小额数字 U+0890-U+0891 / U+08E2、Kaithi 数字号 U+110BD / U+110CD、埃及象形连接符
      U+13430-U+1343F。它们**不是零宽字符**, 剥了等于删内容。
    - 变体选择符 U+FE00-U+FE0F —— 实测是**正当用法**: 数学符号字形变体(`∑︀` = U+2211+U+FE00)、
      emoji 呈现(`⚠️`)。它改变**前一个字符的呈现**, 剥了等于改内容。
    - 特殊空格 U+00A0 / U+202F / U+3000 等 —— 有宽度、有排版含义, 另案。
"""
import re
import unicodedata

# 剥离集(见模块头"剥离"一节)。用 \u 转义写, 源码保持 ASCII, 便于 review。
# [v28.81] 按 Unicode 全码点普查补齐 23 个 —— 原先只覆盖 118/170 个 Cf 码点, 漏的这批
# 同样是**纯零宽无视觉**的格式字符(普查数据见改动记录 9.7)。**这不是"全量 Cf"**:
# 有视觉含义的那批(阿拉伯数字符号 U+0600-U+0605 / U+06DD、叙利亚缩略号 U+070F、
# 埃及象形连接符 U+13430-U+1343F 等)一律**不剥**, 剥了等于删内容。
_INVIS_PAT = ("["
              "\u00ad"                  # SOFT HYPHEN
              "\u061c"                  # ARABIC LETTER MARK (bidi)
              "\u180e"                  # MONGOLIAN VOWEL SEPARATOR
              "\u200b-\u200f"           # ZWSP / ZWNJ / ZWJ / LRM / RLM
              "\u202a-\u202e"           # bidi embedding / override
              "\u2060-\u2064"           # word joiner / invisible times 等
              "\u2066-\u2069"           # bidi isolates
              "\u206a-\u206f"           # 废弃的格式控制符(对称交换抑制等)
              "\ufeff"                  # BOM / ZWNBSP
              "\ufff9-\ufffb"           # 行间注释锚 / 分隔 / 终止
              "\U0001bca0-\U0001bca3"   # 速记格式控制符
              "\U0001d173-\U0001d17a"   # 乐谱连接符(BEGIN/END 系列)
              "\U000e0000-\U000e007f"   # tag 块
              "]")
_INVIS_RE = re.compile(_INVIS_PAT)


def has_invisible(text):
    """有没有该剥的字符(不构造新串, 供快速体检)。"""
    return bool(text) and _INVIS_RE.search(text) is not None


def strip(text):
    """剥掉不可见字符。**这是唯一实现** —— 两侧都调它, 别各写一份。"""
    if not text:
        return text
    return _INVIS_RE.sub("", text)


def scan(text):
    """-> [(1 基字符序号, 字符)] —— 给体检报告定位用。"""
    return [(m.start() + 1, m.group(0)) for m in _INVIS_RE.finditer(text or "")]


def glyph(ch):
    """-> 'U+200B ZERO WIDTH SPACE' (码点名取不到就只给码点)。"""
    try:
        nm = unicodedata.name(ch)
    except ValueError:
        nm = "?"
    return "U+%04X %s" % (ord(ch), nm)


def group(hits, limit=4):
    """[(位置, 字符)] -> [(码点名, 次数, 首次位置)]，按首次出现排序。

    报告里一行说清"有几个、什么码点、第一次在哪", 不铺原始命中列表。
    """
    seen, out = {}, []
    for pos, ch in hits:
        key = glyph(ch)
        if key not in seen:
            seen[key] = len(out)
            out.append([key, 0, pos])
        out[seen[key]][1] += 1
    return [tuple(x) for x in out[:limit]]


def phrase(hits, limit=4):
    """-> 'U+200B ZERO WIDTH SPACE ×2(首次第 3 字符)、…' —— 报告可直接粘。"""
    g = group(hits, limit=limit)
    if not g:
        return ""
    txt = "、".join("%s ×%d(首次第 %d 字符)" % (nm, n, pos) for nm, n, pos in g)
    if len(group(hits, limit=10 ** 6)) > len(g):
        txt += " 等"
    return txt
