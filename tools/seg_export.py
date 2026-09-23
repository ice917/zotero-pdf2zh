# -*- coding: utf-8 -*-
"""seg_export.py — 导出"编号段落包"给豆包翻译 (M1 工具)

两条路线(由引擎画像决定, 见 engine.py):
  pdf2zh 1.x  侧车 -> 载荷
  1. 读 C:\\Users\\<user>\\.cache\\pdf2zh\\segflow\\latest.jsonl
     (每行 = 一次 receive_layout 回调: page/pageid/segs/vars。**不是**"每行一页":
      图形对象也各占一行, 那行的 pageid 就是它所在的真实页)
  2. 过滤纯字形段 (页眉/页码/整页表格 = {vN} 组成, 无可译文字)
  3. 还原字形: {vN} -> vars[str(N)], 让豆包看到真实数字/拉丁名
  4. 检测跨页续接 -> 合并为一条, 原断点插 ⋮。两档判据(详见文件头 B 类判据):
     档一 紧邻项: 上段尾无句末标点 + 下段首小写且页码紧邻;
     档二 非邻项: 上段尾有**断词证据**(硬 `词-` / 软 `…xxo`)时, 允许跳过图注/致谢等
          不构成续接的项去找真正的另一半(下段首小写 + 首词"从未在正常词位出现")。
  5. 输出 payload 文件 (#S 编号行) + manifest.json (编号 -> 页/段映射;
     每条 part 同时记 page=侧车内部坐标 与 true_page=真实 PDF 页码)

  next / BabelDOC  translate_tracking.json -> 载荷 (v28.40)
  1. 段表 = <translation.working_dir>/<篇名>/translate_tracking.json, 用 --tracking 指
     (v28.45: 上游 2.9.0 只在 debug 下写段表, 而 debug 会污染产物; 靠 pdf2zh_next 配置层
      补丁把 working_dir 变成 config 键, 段表根与第一公里读同一份 config —— 见 engine.py 节头)
  2. 正文取 `input` **原样**: {vN}/<style> 是引擎原生协议, 载荷带着它发出去, 收回来
     的译文里它照样落回原公式/原字体 —— 所以**不回锚、不还原**, 与 1.x 正好相反
     (把 pdf_unicode 那套"显示形态"发出去 = 豆腐块, 见 engine.py 节头)
  3. 不注入 ⋮: 上游把跨页段落整段并成一次调用, 每段都是完整段
  4. cross_page 池(上页末段+下页首段, 32 段那种)必须并进来, 用 --pdf 锚定页码后
     插回阅读序; 锚不到只影响报告里的页码, 不影响正文
  5. 输出同样格式的 payload 与 manifest, 另加 engine/source/pool/batch 字段

用法 (--pages 是**真实 PDF 页码**, 与质检/体检报告同一口径; v28.10 起):
  python tools/seg_export.py --pages 2-4 --name payload_p2_p4 \
      --doc "Reproductive Biology of Cactaceae (Desert Plants, 2009)"
产出:
  D:\\zotero-pdf2zh\\inbox\\payload_p2_p4.txt
  D:\\zotero-pdf2zh\\inbox\\payload_p2_p4.manifest.json

文档抬头与术语表都可参数化 (v28.1):
  --doc   抬头文本。缺省只写页码 —— 不再内置某篇论文的抬头, 否则导出别的论文
          时会给豆包一个错误的文档先验 (旧版硬编码 Cactaceae, 导出任何一篇都带它)。
  --terms 术语表 csv (english,chinese, 无表头)。缺省 server/glossary/terms.csv;
          传空串则退化为"按学科惯例统一译名", 不注入任何具体术语。
第 4 条(术语)无论走哪条分支, 末尾都带一段**术语查证子项**(2026-09-20): 表里没有的专业
术语要求先用 search_term 查证(Zotero 库 PDF 原文 + OpenAlex 学术文献)、查不到证据才按学科
惯例定名并首次括注原文, 禁止凭直觉生造。子项里明写"手边没有该工具就直接进入定名" ——
规则文本会随载荷发给任何人的豆包, 不能假设桥一定注册了。
"""
import argparse
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# [v28.39] 引擎画像: 侧车路径随 P2Z_ENGINE 走(adopt 会把该变量传给子进程); 缺省画像
# pdf2zh 1.x —— 与引入本层之前逐字节一致。next 画像不产侧车, 此时为 ""(下面 main 会拦)。
import engine as _ENG                                     # noqa: E402
import lessons as _LES                                    # noqa: E402
import text_clean as _TC                                  # noqa: E402 不可见字符剥离(两侧同源)
_PROF = _ENG.active()
SIDECAR = _PROF.sidecar or ""
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
INBOX = os.environ.get("P2Z_INBOX", os.path.join(PROJ, "inbox"))
TERMS_CSV = os.path.join(PROJ, "server", "glossary", "terms.csv")

V_TOKEN = re.compile(r"\{v(\d+)\}")
PURE_GLYPH = re.compile(r"^(?:\{v\d+\})+$")
TERMINAL = tuple(".!?:;)】」”']")  # 句末/收尾标点 (含引括号收口)

RULES = """[文档] {doc}第{pages}页
[任务] 把下列每个 #S 段落译成简体中文（学术书排版用），只输出译文，不要任何解释。
[规则]
1. 每段独立翻译；译文前先写一行原样的 #S编号；不许合并、拆分、增删段落。
2. 数字、拉丁学名、人名、单位、[n] 引用标号、化学式：原样保留，不译不改不移动位置。
   数字与字母连写的记号（如 3D、2D、4×4、P1、COVID-19）是一整块，整体照抄，
   不得改写成「三维」「二维」「四乘四」这类中文说法 —— 原文里的每个数字字符
   都必须在译文中原样出现，一个都不能少。
   这一整块的边界比"数字和字母"更宽，块内**原有字符逐个照抄**：
   (a) 空格不加不减：字母与数字直接相邻处不得插入空格（如 A100 不得写成「A 100」）；
   (b) 标点不换形状：块内原有的半角标点（. , / - _ % ~ = : * 等）照抄，
       不得换成全角或中文标点（如 0.5、3,000、a/b 里的 . , / 不得写成 。，／）。
   自查办法：把这一小段从原文**逐字符**抄下，只要有一个字符被"顺手改通顺"了，就算错。
3. ⋮ 是原文分页断点，**只属于"跨页续接"被合并的那一段**：
   (a) 正例：载荷段里若出现 ⋮（说明它在原 PDF 里跨了页），译文在语义对应的断点处
       原样保留同样个数的 ⋮ —— 载荷里有 n 个，译文就写 n 个，一个不多一个不少；
   (b) 反例：载荷段里**没有** ⋮ 的（普通单页段），译文里就**一个 ⋮ 都不许有** ——
       哪怕你觉得"这里该分段了"、或想用 ⋮ 当省略号/项目符号，都不行；
   (c) 不许自己发明断点：标点、换行、空行、栏末都不算分页断点，只有载荷写着 ⋮ 的那处才算。
   (d) ⋮ 的**位置也不能挪**：⋮ 左边只写 ⋮ 左边原文的译文，右边只写右边原文的译文 ——
       不许把右边（下一页开头）的短语提到左边来，也不许反过来。
       版面上这些字符是独立的字形对象、分属不同的页，只认得落在自己那一页的译文；
       挪了位置渲染时就找不到落点。同侧**内部**仍可按中文习惯调整语序。
   自查办法：数一数载荷该段的 ⋮ 个数，再数一数你译文里的 ⋮ 个数，两个数必须相等；
   再确认 ⋮ 左右两侧的内容与载荷 ⋮ 左右两侧一一对应。
{terms}5. 中文通顺为学术散文，不要翻译腔；段内语序可按中文习惯调整，但事实与数字不得增减。
6. 参考文献区不得汉化：行首 [n] 的编号制条目，或行首"姓名 (年份)"的作者-年份制条目，
   整条原样保留 —— 人名/标题/期刊名/年份/卷期页/DOI/URL 一律不译、不改写、不重排、不换标点；
   正文中的行内引用照常处理。若某段整段都是文献条目，原样输出该段即可。
7. 公式片段（变量、符号、运算符、上下标及其紧邻的标点或编号连成的一串）是一整块：
   整块逐字符照抄，块内不得插入中文、不得增删或改动任何一个字符；要调整语序就把中文放在块外。
   宁可读起来别扭、甚至略有冗余，也不要拆开这块去"理顺"。
   反例（实测反复踩，两种都算错）：把 `C∈{{2,4}}` 拆成 `C∈{{2}}` 与 `C∈{{4}}` 两截、
   并在中间插上「旋转任务…滑动任务…」这类说明；或把 `(0.69/0.63` 写成 `0.69/0.63`
   （丢掉了块首左括号）。正确做法：原串**整块**照抄（括号、斜杠、花括号、关系符一个都不动），
   要说明就写在块外。
   自查办法：把每段的公式串从载荷**逐字符**抄一遍，与你译文里的对应串比 —— 只要长度、
   括号个数或字符形状有一处不同，就是错。
8. 残句碎片照译不补全：载荷里有些段**本身就是残句** —— 以半个词开头、以半个词或连字符
   结尾，或一句话被拦腰截断。这是 PDF 分栏/分页切断的正常结果，不是你漏看了内容。
   这类段**只译它现有的字面**：
   (a) 不补全 —— 不要把半个词补成一个完整的词，也不要把半句话补成完整的句子；
   (b) 不臆测 —— 被切掉的那部分不在本段里，不要按上下文猜一个意思填进去；
   (c) 不拼接 —— 不要把它和相邻段合起来译，渲染层会按原文位置把碎片拼回去。
   补全 = 译文凭空多出内容，拼接处会重复、与原文对不上号。
9. 整段译完，不许只译开头：载荷每一段（长段尤其）都要**从头译到尾**，不能译完头一句
   就收尾。载荷结尾若本身就是半个句子（见规则 8），译到那儿为止即可；但**只要这一段
   还有完整句子没译，就必须补上** —— 规则 8 管的是"碎片不许补全"，不是"完整句可以漏译"。
   自查办法：逐段比长度 —— 中文译文通常约为载荷字数的三到六成，某段远低于这个比例
   （如载荷 400 字而译文只有 60 字）几乎一定是只译了开头，回该段重译。
"""

TERMS_FALLBACK = "4. 术语统一：按学科惯例统一译名，同一术语全文译法一致。\n"

# [自研补丁 2026-09-20] 术语查证子项: 挂在第 4 条下(缩进子项), **不动编号** ——
# 第 4 条本就是"术语"条, 查证是它的下一条动作, 单列成第 9 条会离术语表太远。
# 判据来自豆包侧反馈"术语拿不准时凭直觉生造译名"; 项目已有查证能力, 就是
# doubao_bridge 的 search_term(Zotero 库 PDF 原文上下文 + OpenAlex 学术文献)。
# (a) 明写"没有工具也无妨, 跳过查证直接做 (b)": 别的用户不注册桥也不会被这条卡住 ——
# 规则文本会随载荷发给任何人的豆包, 不能假设工具一定在。
# **组名要点名**: 规则 2/3/4 各有一组 (a)(b)(c), 裸引一句"进入 (b)"会串到上一条去
# (用户当场就是这么问的: "(b)是什么"), 故开头写死"下述 (a)(b)(c) 三步"、引用处写"下面 (b)"。
TERMS_LOOKUP = """   术语表里**没有**的专业术语不要凭直觉定名 —— 按下述 (a)(b)(c) 三步定，并保证同一术语全文一致：
   (a) 先查证：用 search_term 工具查该词（命中 Zotero 库 PDF 原文上下文 + OpenAlex 学术文献），
       中文学术文献里用过的译名优先照它；手边没有该工具也无妨，跳过查证、直接做下面 (b) 的定名；
   (b) 再定名：查不到证据的，按学科惯例给出**一个**译名；首次出现处可在译名后括注原文
       （如「热带气旋(tropical cyclone)」），之后不再括注；
   (c) 不得生造：不认识的词不要硬凑一个像术语的中文词，也不要原样留英文蒙混过去 ——
       宁可先用 (b) 的括注形式写上，也不要猜一个"看着像术语"的说法。
   自查办法：把译文里所有像术语的中文词列一遍，逐个答出依据（术语表 / 查证结果 / 学科惯例）；
   答不出依据的，回原句重译。
"""

# [自研补丁 2026-09-20] next 画像专用第 10 条: 占位符与标签原样保留。
# 为什么只有 next 需要: 1.x 的载荷在导出时就把 {vN} 还原成真字形(豆包根本看不到占位符),
# 而 next 的载荷**故意**带引擎原生形态发出去 —— {vN}/<style> 是引擎的替换协议, 少一个
# 渲染时就少一个公式/少一段样式。这条属"载荷规则", 是开发者侧的杠杆(见摊派边界: 内容
# 质量交给豆包, 但"怎么把引擎记号完好交回"得由我们把规则写进载荷)。
# 单独成条第 10 条、**不动 1-9 的编号**: 那些编号被别处(术语子项、返工单)引用过。
_PH_RULE = """10. 段里的花括号占位符与 <style> 标签一律**原样照抄**：
    {v1}、{v2}… 这类占位符，以及 <style id='1'>…</style> 这类标签，个数、编号、
    位置、尖括号与引号形状都不许改，不许删、不许自己添，也不许把它们展开成实际
    内容或换个写法（如写成「公式」「v1」、全角花括号、去掉 id）。标签**里面**的
    可读文字照常翻译，标签本身不动。
    自查办法：把译文里的 {vN} 与 <style ...> 逐个抄出来与载荷逐字符比对；载荷里有
    n 个占位符，译文里就得有同样 n 个、编号一一对应。
"""


def load_terms(path):
    """读术语表 csv (无表头, 'english,chinese') -> [(en, zh), ...]; 缺文件即空表"""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",", 1)
            if len(parts) != 2:
                continue
            en, zh = parts[0].strip(), parts[1].strip()
            if en and zh:
                out.append((en, zh))
    return out


def terms_line(path):
    """术语表 -> RULES 第 4 条整块(术语行 + 术语查证子项); 空表/缺文件退化为通用要求(不撒谎不误导向)"""
    terms = load_terms(path)
    if not terms:
        return TERMS_FALLBACK + TERMS_LOOKUP
    return ("4. 术语统一：%s。\n" % "；".join("%s=%s" % (en, zh) for en, zh in terms)
            + TERMS_LOOKUP)


def true_page(o):
    """侧车记录的**真实 PDF 页码**(1 基); 记录里没有 pageid 时回落 None。

    侧车的 `page` 是 receive_layout 的**回调计数**, 不是页码: 图形对象也会各占
    一号 (end_figure -> receive_layout(fig)), 所以图多的论文整体漂移。真实页码
    只有 `pageid` 说得准 (LTPage.pageid, 0 基; 图形继承所在页的 pageid)。
    """
    pid = o.get("pageid")
    return None if pid is None else int(pid) + 1


def load_pages(pages_want, sidecar):
    """按**真实 PDF 页码**选页 -> (记录表, 缺页列表, 全量记录)。

    为什么按真实页码: 质检/体检报告说的都是真实页码, 用户在报告里读到
    "第43,44页"再照抄到 `--pages` 上是最自然的用法。而 v28.10 之前这里按回调
    计数匹配, 对图多的论文会整体漂移 —— 实测 Melhani 真实第43,44页对应侧车
    第55,56条(漂 12), `--pages 43-44` 取回的是第31,32页正文, 且库内照样命中
    12 行、段号照样连续, 一路"看起来正常"(静默错)。

    返回的记录表仍以侧车原 `page` 为键 —— 那是**载荷内部坐标**
    (manifest / imported.json / seg_inject 沿用它), 不随本函数改变;
    `pageid` 缺失的老侧车退化为按 `page` 匹配(与旧行为一致, 不误伤归档件)。

    第三个返回值是**全量**解析结果: 跨段断词续接要拿整篇的"正常词位"词表
    (见 normal_words)。只看所选页的话, "这个词在本篇别页正常出现过"就看不见,
    会把独立段的段首词误判成"从未出现"的残片, 于是把它当续接吞掉。
    """
    got, seen, all_recs = {}, set(), []
    with open(sidecar, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            all_recs.append(o)
            tp = true_page(o)
            if tp is None:                      # 老侧车没有 pageid
                if o["page"] in pages_want:
                    got[o["page"]] = o
                    seen.add(o["page"])
                continue
            if tp in pages_want:
                got[o["page"]] = o
                seen.add(tp)
    return got, [p for p in sorted(pages_want) if p not in seen], all_recs


def restore(raw, vars_):
    def sub(m):
        return vars_.get(m.group(1), m.group(0))
    # [v28.80] 不可见字符体检: 剥离**必须在**下面的断词正则**之前** —— 夹在 `-` 与空格
    # 之间的 U+200B 会让断词判据当场失明(`obser-<ZWSP> vation` 认不出该接)。
    # 剥的是**还原后的整串**, 藏在字形值里的不可见字符同批去掉 —— 回锚侧拿字形值
    # 建 pattern 时走同一个 text_clean.strip, 两侧同源; 只剥一侧 = 自造 str.find 落空。
    text = _TC.strip(V_TOKEN.sub(sub, raw)).strip()
    # PDF 换行断词, 两种情况区分处理:
    #   音节断词 (后随小写): "obser- vation" -> "observation", "Astera- ceae" -> "Asteraceae"
    #   复合名断词 (后随大写): "Lovett- Doust" -> "Lovett-Doust", 保留连字符只删空格
    text = re.sub(r"([A-Za-z])- (?=[a-z])", r"\1", text)
    text = re.sub(r"([A-Za-z])- (?=[A-Z])", r"\1-", text)
    return text


# ---- [v28.74] B 类: 跨段断词续接的判据 ----
# 缘起(实测 Johnson): 侧车把被 PDF 分栏/分页截断的一个词切成两段, 但这两段在段表里
# **不一定相邻** —— 中间可能夹着图注、致谢、误命名段。旧判据只比对 items[i] 与
# items[i+1], 于是 S6(p1#7 尾 `water par-`)→S9(p1#12 头 `tially…`, 中间夹两段) 与
# S13(p1#16 尾 `Infection was reo`)→S18(p2#7 头 `corded…`, 中间夹 Fig.1 图注)
# 永远合不上: 半个词被两家各自照译, 渲染拼接处重复、与原文对不上号(规则 8 想防的
# 正是这件事, 但导出端根本没把碎片标出来)。
#
# 修法: 允许**跳过**不构成续接的项去找 B, 但两侧都要有硬证据, 否则会把独立段吞掉:
#   A 侧(断词证据)
#     H 段尾 `词-`             连字符断词("water par-")
#     S 段尾 `…xxo` 且末词≤4字符 —— 本类 PDF 的字形图层把连字符抽成了字母 'o':
#                    "Infection was reo" 实为 "…was re-" + "corded…"(源文缺陷)
#     不放宽成"任何非句末标点结尾": `…of` / `…the` 这类完整收尾满篇都是, 一放宽整篇连坐。
#   B 侧(三条同时成立)
#     1 与 A **同页或紧邻下一页** —— 跨页续接的物理前提(与 v28.10 的页码口径一致)
#     2 段首小写字母      —— 半词续接不会以大写开头(大写只可能是新句子/专名)
#     3 首词"在整篇从未出现在正常词位" —— 防误吞独立段的关键, 见 normal_words
BREAK_HARD = re.compile(r"[A-Za-z]-$")
BREAK_SOFT = re.compile(r"[a-z]{2,3}o$")
WORD_RE = re.compile(r"[A-Za-z]{2,}")


def normal_words(records):
    """整篇里在"正常词位"出现过的词集(小写)。

    正常词位 = 词前面是**单个**空白(空白前一字符不是空白), 即它是由正常字距分开的
    独立词。断词残片不会落在这种位置:
      - 后半截(tially/corded/nd-best)必然是某个框/行的**段首**, 前面没东西;
      - 被版面断开的半个词, 前半截与后半截之间是**双空格**(源文缺陷: 实测本篇
        p1#2 里 "essen  tially" 就是这么来的), 故"空白前还是空白"要排除掉。
    两个条件缺一不可 —— 实测教训:
      - 只要求"空格前是字母"(早先的写法): `, where` / `(3) fouled` 这类**跟在标点
        后**的常用词全被算成"从未出现", 于是 pdf-31e0fcbb 的 `Grounded-` 跳到 4 项
        之外的 `where kij…` 上合出一个假续接;
      - 只要求"前面是空白": 又把 `essen  tially` 的 `tially` 收进来, 于是 Johnson
        S6(`water par-`)那个**真**续接反倒被自己的"残片"判据挡住了。
    """
    good = set()
    for o in records:
        vv = o.get("vars") or {}
        for s in o["segs"]:
            t = restore(s.get("raw") or "", vv)
            for m in WORD_RE.finditer(t):
                st = m.start()
                if st >= 2 and t[st - 1] in " \t" and t[st - 2] not in " \t":
                    good.add(m.group(0).lower())
    return good


def break_hint(text):
    """A 侧断词证据: 'H' 连字符断词 / 'S' 字形缺陷断词 / '' 无。"""
    if BREAK_HARD.search(text):
        return "H"
    if BREAK_SOFT.search(text):
        m = re.search(r"([A-Za-z][A-Za-z\-]*)$", text)
        if m and len(m.group(1)) <= 4:
            return "S"
    return ""


def first_word(text):
    m = re.match(r"([A-Za-z][A-Za-z\-]*)", text)
    return m.group(1) if m else ""


def _emit(args, items, merged_log, warnings, pages_str, source=None):
    """载荷 + manifest 落盘与报告 —— **两个引擎共用**(免得两边的编号/manifest 慢慢漂移)。

    items 每项: {"parts": [(页, 段序, 正文, 真实页)], "merged": bool}; next 路线另带
    "pool"/"batch"/"src"。source 非空 = next 路线: manifest 记 engine/source/pool/batch/src/fp,
    载荷末尾附第 10 条占位符规则。
    """
    for n, it in enumerate(items, 1):
        it["key"] = "S%d" % n

    doc = args.doc.strip()
    if args.terms == TERMS_CSV and not os.path.exists(TERMS_CSV):
        # 沙箱/换机时 P2Z_PROJ 一改, 默认术语表就跟着落空; 静默退化会让人以为术语生效了
        warnings.append("默认术语表不存在: %s —— 本次只写通用术语要求, 用 --terms 指定" % TERMS_CSV)
    # [v28.58] 实测反例回流: 门禁踩过的坑(lessons 台账)逐条注进提示词, 抽象规则变成
    # 具体反例。台账为空则为 ""(与引入本层之前逐字节一致)。注入条数受 lessons.MAX_INJECT
    # 与 MAX_BLOCK_CHARS 限流, 不会把前面的规则挤掉。
    lessons_block = _LES.block()
    lines = [RULES.format(doc=(doc + " ") if doc else "",
                          pages=pages_str,
                          terms=terms_line(args.terms))
             + (_PH_RULE if source else "")
             + (("\n" + lessons_block) if lessons_block else "")]
    for it in items:
        lines.append("#%s" % it["key"])
        body = ""
        for j, (_pg, _idx, text, _tp) in enumerate(it["parts"]):
            if j:
                body += "⋮"
            body += text
        lines.append(body)
    manifest = {"name": args.name, "pages": pages_str, "items": []}
    if args.pdf:
        # [v28.53] 载荷锚定原文: 面板据此把"最新 payload"自动对到它自己的原文 PDF
        # (换论文不用再手设 P2Z_BODY_PDF)。旧载荷没这字段时面板回落服务器 history。
        manifest["pdf"] = os.path.abspath(args.pdf)
    if source:
        # 下游(报告/回写)要能一眼看出这是 next 的载荷: 页号是**真实 PDF 页码**,
        # 不是 1.x 那种"回调计数"坐标。
        manifest["engine"] = _PROF.key
        manifest["source"] = source
    for it in items:
        rec = {
            "key": it["key"],
            "merged": it["merged"],
            # page 是**侧车内部坐标**(回调计数), imported.json / seg_inject 沿用它;
            # true_page 是**真实 PDF 页码**(给人看、给报告用)。两者都留着, 是为了让
            # 下游(seg_import 的报错与返工单)不必再自己重算一遍 pageid 口径。
            # next 路线没有侧车坐标, 两者同值(见 manifest.source)。
            "parts": [{"page": pg, "seg": idx, "true_page": tp}
                      for pg, idx, _t, tp in it["parts"]],
        }
        if source:
            rec["pool"] = it.get("pool", "page")
            # 定位键 + 正文指纹: 回写侧拿它把 #S 对回段记录(见 engine.tracking_segment)。
            # 不落它们的话, 回写只能"拿当前段表重推一遍再比坐标" —— 而重推依赖导出时的
            # --pdf 锚定, 换一次参数就整列错位(真样本 129 段里 #S11 起全对不上)。
            rec["src"] = list(it.get("src") or [])
            rec["fp"] = _ENG.text_fp(it["parts"][-1][2])
            if (it.get("batch") or (None,))[0] is not None:
                rec["batch"] = list(it["batch"])
        manifest["items"].append(rec)

    os.makedirs(INBOX, exist_ok=True)
    p_txt = os.path.join(INBOX, args.name + ".txt")
    p_man = os.path.join(INBOX, args.name + ".manifest.json")
    with open(p_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(p_man, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # ---- 报告 ----
    total = sum(len(p[2]) for it in items for p in it["parts"])
    n_cross = sum(1 for it in items if it.get("pool") == "cross_page")
    print("payload: %s (%d 段 / %d 原文字符%s)" % (
        p_txt, len(items), total, ("; 其中跨页池 %d 段" % n_cross) if source else ""))
    for m in merged_log:
        print("  " + m)
    for w in warnings:
        print("  [警告] " + w)
    n_les = sum(1 for ln in lessons_block.splitlines() if ln.startswith("- "))
    if n_les:
        # 让"提示词其实被改过"可见: 否则换台机器/换了台账, 载荷差异无从解释
        print("  [教训库] 提示词已附带 %d 条实测反例(台账 tools/lessons.tsv, 可人工删改)" % n_les)
    for it in items:
        loc = "+".join("p%d#%d" % (tp, idx) for _pg, idx, _t, tp in it["parts"])
        t = it["parts"][-1][2] if not it["merged"] else it["parts"][-1][2][:25] + "…"
        print("  %-5s %-12s %s %5dch  %s" % (
            "#" + it["key"], loc,
            {"cross_page": "跨页"}.get(it.get("pool"), "    "),
            sum(len(p[2]) for p in it["parts"]),
            ("[合并] 头: " + it["parts"][0][2][:20] + "… 尾: " + t) if it["merged"]
            else "尾: …" + t[-25:]))
    return 0


def export_next(args, pages_want):
    """next 画像: translate_tracking.json -> 载荷条目(阅读序) -> _emit。"""
    tk = args.tracking or (_PROF.tracking_json("") or "")
    if not tk:
        print("FAIL: 拿不到 next 的段表 translate_tracking.json —— 段表只在 config 的 "
              "[translation].working_dir 设了(v28.45 配置键)或 debug=true 时才落盘; "
              "用 --tracking 显式指一份")
        return 1
    if not os.path.exists(tk):
        # 这是**业务错**不是接线错(rc=1 而非 2): 换篇论文后工作根下的旧段表会被覆盖,
        # 报错必须告诉人怎么修 —— 不然拿到 rc=1 只会去怀疑引擎没接好。
        print("FAIL: 段表不存在: %s\n      用 --tracking 显式指一份 translate_tracking.json "
              "(段表根 = config 的 [translation].working_dir, v28.45 配置键)" % tk)
        return 1
    try:
        items, warnings = _ENG.tracking_payload_items(tk, pdf=args.pdf,
                                                      pages=pages_want or None)
    except (IOError, ValueError) as e:
        print("FAIL: %s" % e)
        return 1
    if not items:
        print("FAIL: 段表 %s 里没有落在 --pages %s 的段" % (tk, args.pages))
        return 1
    print("段表: %s%s" % (tk, "" if args.pdf else "  (未给 --pdf: 跨页池页码按序猜)"))
    for it in items:                      # 归一成 _emit 的 4 元组(页, 段序, 正文, 真实页)
        it["parts"] = [(pg, idx, txt, pg) for pg, idx, txt in it["parts"]]
    warnings.append("next 无侧车: 页号是**真实 PDF 页码**(不是 1.x 那种回调计数); 回写"
                    "(seg_import/seg_inject)按 manifest 的定位键对回段表, 不重推页号")
    return _emit(args, items, [], warnings, args.pages,
                 source={"kind": "tracking_json", "path": tk})


def export_sidecar(args, pages_want):
    """1.x 画像: 侧车 -> 载荷条目(逐页 + 跨页续接合并) -> _emit。"""
    pages, missing, all_recs = load_pages(pages_want, args.sidecar)
    if missing:
        print("侧车缺页: %s" % missing)
        return 1

    # ---- 收集可译段 (顺序: 页升序, 段升序) ----
    items = []   # {"parts": [(侧车坐标页, 段序, 文本, 真实页码)], "merged": bool}
    warnings = []
    for pg in sorted(pages):
        o = pages[pg]
        tp = true_page(o)
        vv = o.get("vars") or {}
        for i, s in enumerate(o["segs"]):
            raw = (s.get("raw") or "").strip()
            if not raw or PURE_GLYPH.match(raw):
                continue
            text = restore(raw, vv)
            if V_TOKEN.search(text):
                warnings.append("p%d#%d 仍有未还原占位符: %s" % (pg, i, V_TOKEN.findall(text)[:5]))
            if not re.search(r"[A-Za-z0-9]", text):
                continue
            items.append({"parts": [(pg, i, text, tp if tp is not None else pg)],
                          "merged": False})

    # ---- 跨页续接检测与合并 ----
    def tail(t):
        return t[-1]

    def head(t):
        return t[0]

    # 整篇的"正常词位"词表: B 侧判据 3 用它把独立段的段首词与断词残片分开
    # (只用所选页算会漏掉"该词在别页正常出现过", 见 load_pages 节头)
    good_words = normal_words(all_recs)

    def continuation(k):
        """a=items[k] 之后**第一个**可续接项的下标; 无则 None。

        中间的项只有"本身不构成候选"才允许被跳过(超窗/段首非小写/首词是正常词);
        否则说明 A 的断词在下游没有对应物, 不该硬接一个。
        """
        a = items[k]
        pa = a["parts"][-1][3]
        # 跨页续接只在"A 是该页最后一项"时允许: 紧随其后的一项若还在 A 自己这页, 说明
        # A 不在页末, 真正的续接只可能在同一页里(多栏版面上就是隔壁那栏, 见 Johnson
        # p1#7→p1#12)。不设这条就会跨到下一页去抢别人的残片 —— 实测合成夹具里
        # `masks with Grounded-`(p2)去接了下页的 `seco`, 而 `seco` 自己还等着 `nd-best`。
        same_page_only = items[k + 1]["parts"][0][3] == pa
        for j in range(k + 1, len(items)):
            pb = items[j]["parts"][0][3]
            if pb not in (pa, pa + 1) or (same_page_only and pb != pa):
                continue
            tb = items[j]["parts"][0][2]
            if not head(tb).islower():
                continue
            if first_word(tb).lower() in good_words:
                continue
            return j
        return None

    merged_log = []
    i = 0
    while i < len(items) - 1:
        a = items[i]
        ta = a["parts"][-1][2]
        # 相邻性按**真实页码**判: 侧车坐标页是回调计数, 用它判会把"同页的图形记录"
        # 当成隔页, 也会把"隔着一页"当成相邻。
        pa = a["parts"][-1][3]
        b = items[i + 1]
        tb = b["parts"][0][2]
        pb = b["parts"][0][3]
        if pb == pa + 1 and tail(ta) not in TERMINAL and (head(tb).islower() or head(tb).isdigit()):
            # 档一: 紧邻的下一项(v28.10 起的旧判据, 逐字节不动)
            a["parts"].append(b["parts"][0])
            a["merged"] = True
            items.pop(i + 1)
            merged_log.append("合并: p%d 段尾 + p%d 段头 (断点⋮)" % (pa, pb))
            continue
        # 档二: 有断词证据时, 允许跳过不构成续接的项去找真正的另一半(见文件头 B 类判据)
        hint = "" if tail(ta) in TERMINAL else break_hint(ta)
        j = continuation(i) if hint else None
        if j is not None:
            pb2 = items[j]["parts"][0][3]
            a["parts"].extend(items[j]["parts"])
            a["merged"] = True
            items.pop(j)
            merged_log.append("合并(%s, 跳%d项): p%d 段尾 + p%d 段头 (断点⋮)"
                              % (hint, j - i - 1, pa, pb2))
            continue
        if hint:
            # 有证据却找不到另一半: 不硬接, 但要说出来 —— 否则这类漏合并(本次修的就是它)
            # 只会在成品里表现为"半个词被译了两遍", 导出这一步看着毫无异常。
            warnings.append("跨段断词未配对(%s): p%d#%d 尾 …%s"
                            % (hint, pa, a["parts"][-1][1], ta[-24:]))
        i += 1

    return _emit(args, items, merged_log, warnings, args.pages)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", required=True,
                    help="真实 PDF 页码(与质检/体检报告同一口径), 如 2-4 或 2,3,4")
    ap.add_argument("--name", required=True, help="payload 文件名(不含扩展名)")
    ap.add_argument("--sidecar", default=SIDECAR, help="1.x: 侧车路径; 新论文请先归档 latest.jsonl 再用")
    ap.add_argument("--tracking", default="",
                    help="next: translate_tracking.json 路径; 缺省取工作根下最新一份(换论文会被覆盖)")
    ap.add_argument("--pdf", default="",
                    help="next: 原文 PDF 绝对路径; 跨页配对段靠它锚定页码(不给只影响报告的页码)")
    ap.add_argument("--doc", default="", help='文档抬头, 如 "标题 (期刊, 年份)"; 缺省只写页码')
    ap.add_argument("--terms", default=TERMS_CSV,
                    help="术语表 csv (english,chinese 无表头); 缺省 %s; 传空串则不注入具体术语" % TERMS_CSV)
    ap.add_argument("--force", action="store_true", help="续接检测失败也照常导出(逐段独立)")
    args = ap.parse_args()

    # 引擎接线门禁: 未接线的引擎在这里拦下, 免得下游报一个看不懂的"侧车不存在: "。
    _ok, _why = _PROF.stage_ok("export")
    if not _ok:
        print("FAIL: 引擎 %s 上未接线: %s" % (_PROF.key, _why))
        return 2

    pages_want = _ENG.parse_pages(args.pages)
    if _PROF.seg_source == "tracking_json":
        return export_next(args, pages_want)
    return export_sidecar(args, pages_want)


if __name__ == "__main__":
    sys.exit(main())
