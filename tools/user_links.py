# -*- coding: utf-8 -*-
"""user_links.py — 用户自定义概念链接: 把"选中的那段字 -> 你自己给的网址"装进成品

为什么需要:
  原版超链接(引文锚 -> 文献表, 拉丁学名 -> 附录)是**继承来的** —— 读者只能点原书给的
  那几个。而"读到这里我需要一点背景知识"这类需求, 原书不会替你想到; 位置该由读者定。
  本工具把链接从"继承物"变成**可定稿资产**: 锚文本与 URL 都由用户给, 机械地装回原版面。
  与译文同一层级 —— 引文锚保留、学名锚保留、**新增**概念锚、**覆盖**原版链接(同矩形时
  用户优先)。

顺序(不可换): relink_pages -> resolve_links -> **本工具** -> style_links -> verify_links
  必须在 **style_links 之前**: 新锚要跟着一起变蓝, 否则读者看不出能点, 功能等于没做;
  必须在 **resolve_links 之后**: 新链接是 URI, 不参与 NAMED->GOTO 那一步。
  双语版**不必**另跑一遍: dual_links 的译文侧会把 mono 上的 URI 原样搬过去。

两层规格(user_links.json):
  global —— 跨篇通用的阅读偏好(如"基因漂变 -> 某百科")。`occurrence` 是**全篇第几次出现**
            (默认语义即"让概念首次出现处有个入口"), **不许带 page**。
  tasks  —— 单篇的(如"图 3 里那个概念")。`page` 必填(1 基), `occurrence` 是**该页第几次出现**。
            `occurrence` **留空 ≡ 第 1 处**, 但只在"确实只命中一处"时成立 —— 命中多处却不写
            就是门禁 FAIL(见下)。面板的「第几处」就是靠这条口径自动填的(--count-hits)。
  撞锚以**本篇为准**: tasks 里出现同一个锚, 该锚的 global 条目被跳过并记账。

"第几次出现"按**阅读顺序**(先上后下、先左后右)数, 且碎片命中先并成"一次出现"的整框 ——
直接复用 relink_pages.merge_fragments, 不并框会把 `[5]` 数成三个字, "第 3 次"跟人看到的对不上。

门禁(全部在**落盘之前**判完; 有一条不过 -> 一个字都不写):
  锚空 / URL 非 http(s) / global 带 page / tasks 缺 page / 该页(或全篇)0 命中 /
  命中 >1 却没给 occurrence / occurrence 越界 / 两条新锚抢同一块矩形
  —— 一律 FAIL 并点名, 不挑"最像的那一处"。
压住**原有**引文锚不算错(用户手写的意图优先于继承来的), 但删了哪一条要记账、报告点名。

用法:
  python tools/user_links.py --target <成品.pdf> [--spec <user_links.json>] [--task <任务名>]
                             [--dual] [--report <报告>]
  python tools/user_links.py --target <成品.pdf> --check [...]          只预检, 不落盘(面板提示的来源)
  python tools/user_links.py --target <成品.pdf> --page 7 --list-lines  列本页文字(面板选字)
  python tools/user_links.py --target <成品.pdf> --sidecar <段表.jsonl> --page 7 --list-segs
                                          列第 7 页逐段「原文↔译文」; --page 0 = 全篇(面板滚动联动用)
  python tools/user_links.py --target <成品.pdf> --count-hits --anchor <锚> --page 7 [--scope 本篇|全局]
                                          只数这条锚命中几处(只读; 面板的「第几处」靠它自动填)
                                          另回带 hits_pos = 每一处的 {page, rect}(阅读序),
                                          面板的 ◀▶ 靠它跳到那一处并涂亮
  python tools/user_links.py --target <成品.pdf> --render-page --page 7 --out <PNG> [--rect x0,y0,x1,y1]
                                          把第 7 页渲成 PNG(给了 --rect 就在**这份预览**上
                                          把那一块涂成荧光黄; 只读, 成品不动 —— 面板点 ◀▶ 时的定位标记)

选锚的数据源为什么改成**段表**而不是成品页文字(2026-09-25):
  成品页的文本层是**排版后的行**、中英混排且不标语言 —— 在整页无译文的文献页/回填页上
  取字, 取到的必然是英文; 而读者点的是**中文**, 锚就该落在中文上。段表(侧车)自带
  raw/trans 逐段对照, 一份文件就有 页码+原文+译文, 且 trans 是**渲染时的最终态**。
  {vN} 一律走 seg_export.restore() 还原 —— 与导出端/回锚端同一份实现, 不另写一套解析
  (另写迟早分叉成两种"还原后"的字符串, 面板选出来的锚就与页面对不上)。
"""
import argparse, io, json, os, re, sys, unicodedata

# 用 reconfigure 而不是 `sys.stdout = io.TextIOWrapper(...)`: 后者每执行一次就多包一层,
# 上一层包装器被回收时会**关掉同一个底层 buffer**, 于是"本工具 + 它 import 的
# relink_pages"这种链式 import 会让 stdout 变成 closed file(实测: 打印第二行就崩)。
# reconfigure 就地改编码, 重复执行是幂等的 —— table_pipe/* 一直用这个写法。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relink_pages import merge_fragments      # 同一把尺子: "一次出现"= 并框之后的一个框

norm = lambda s: re.sub(r"\s+", "", s or "")
URL_OK = re.compile(r"^https?://\S+$")

# ---- [v28.92] 标点折叠: 只说"全/半角写法不同", 不说"内容不同" ------------------------
# 为什么非有: 侧车的 trans 用 ASCII 标点(`[13].` / `(1)`), 而渲染后的成品页是全角
# (`[13]。` / `（1）`) —— 实测 Lee 篇 8/117 段点整段报"找不到", 逐段断点比对全是这一类,
# 与跨行无关。
#
# [v34] **只折宽度变体, 不折内容**(收紧). 折叠是"两侧走同一把尺子", 尺子越粗, 两个
# **不同**的串越容易被折成同一个 -> 命中到错的地方; 而错链是**静默**的(门禁过、链接装上、
# 读者点开才发现在别处). 故:
#   · NFKC 只对它确实是宽度变体的那些字用 —— 按 Unicode 兼容分解的**类型**放行, 只收
#     `<wide>`(全角->半角) 与 `<narrow>`(半角->全角). 其余类型都**改内容**:
#     `①`(<circle>)->`1`、`Ⅰ`(<compat>)->`I`、`²`(<super>)->`2`、`…`(<compat>)->`...`、
#     `½`(<fraction>)->`1⁄2`. 一旦折了这些, 页面上随便一个 `1`/`I` 都能"证明"锚在页面上。
#   · 表里**不收** `、`(0x3001) 与 `·`(0x00B7): 顿号与间隔号都不是"逗号/句点的宽写法",
#     它们各有分工(并列 vs 停顿 / 人名分隔 vs 句读); 折了会让 `模型、遗留` 命中 `模型，遗留`。
#   · 句号 `。`(0x3002) **必须保留**: 它没有兼容分解(实测 decomposition 为空), 而实测的
#     错位恰恰是 `[13]。` vs `[13].`。破折号/连接号/弯引号同理(都无分解), 表里手列。
# 为什么不折《》〈〉这类没有 ASCII 对应的中文标点: 折成什么都是编的, 等于把"内容不同"
# 混进"宽度不同"。
# 实测(2026-09-26, Lee 篇 168 段, 逐段比对命中数): 按上述收紧后**一段都没变**
# (既没多命中, 也没丢命中) —— 收紧只砍假阳性, 不动真阳性。
_FOLD_TBL = {
    0x3002: ".",    # 。 句号(无兼容分解, 只能手列)
    0x2014: "-",    # — 破折号
    0x2013: "-",    # – 连接号
    0x2018: "'", 0x2019: "'",
    0x201C: '"', 0x201D: '"',
}
# 只有这两类兼容分解是"同一个字的宽窄写法"; 其余(<circle>/<compat>/<super>/<fraction>/…)改内容
_FOLD_NFKC_KINDS = ("<wide>", "<narrow>")


def _fold_char(c):
    """一个字 -> 折叠后的串(**只折宽度变体**: 宽窄兼容分解 + 上表)。"""
    if unicodedata.decomposition(c).startswith(_FOLD_NFKC_KINDS):
        c = unicodedata.normalize("NFKC", c)
    return c.translate(_FOLD_TBL)


def _fold(s):
    """**逐字**折叠 —— 两侧必须走同一个函数: 一边逐字、一边整串, NFKC 在有些组合上结果
    不同, 那就成了两种"折叠后", 与前面"锚与页面必须同一把尺子"是同一条纪律。"""
    return "".join(_fold_char(c) for c in s)


def _fold_stream(chars):
    """字符流 -> (折叠后的串, 每个折叠字对应的**原字下标**)。

    为什么带下标表: 折叠会改长度(如 `…` -> `...`), 命中位置必须能映射回原始字符,
    否则包围盒会盖错字。一个原字折出多个折叠字时, 每个都记同一个原下标。
    """
    out, idx = [], []
    for i, (c, _, _) in enumerate(chars):
        for fc in _fold_char(c):
            out.append(fc)
            idx.append(i)
    return "".join(out), idx


def _chars_of(page):
    """页面字符流 [(字, 框, 行键)] —— 按阅读序(块 → 行 → 段 → 字 的自然顺序)。

    行键直接用 rawdict 自带的 (块下标, 行下标), **不靠 y 坐标四舍五入**去凑"同一行":
    上下标与公式那几处基线会抖, 一抖就把一行劈成两行, 并框跟着错位。
    """
    out = []
    for bi, blk in enumerate((page.get_text("rawdict") or {}).get("blocks") or []):
        if blk.get("type") != 0:                # 1 = 图片块, 没有 chars
            continue
        for li, line in enumerate(blk.get("lines") or []):
            for sp in line.get("spans") or []:
                for ch in sp.get("chars") or []:
                    c = ch.get("c")
                    if not c or c.isspace():    # 与 norm() 同一口径: 空白不参与匹配
                        continue
                    out.append((c, pymupdf.Rect(ch["bbox"]), (bi, li)))
    return out


# ---- [v34] 版面连续性守卫: "流里相邻" != "版面上挨着" ---------------------------------
# 第③档是把**整页字符按 rawdict 的块/行顺序串成一条流**再找子串 —— 它把"流里相邻"
# 当成了"版面上挨着"。可 rawdict 的块序只是**大体**阅读序, 遇到"整页被切成几十个块"的
# 图形页/表格页就会跳: 流里紧接着的两个字, 版面上一个在页眉、一个在页脚。
# 实测(2026-09-26, Lee 篇 116 个走③档的段): 2 个是这种**假拼接**, 都是 100+ 字的整页
# dump 锚(p3 的 501 字 / p6 的 166 字), 拿到的框纵向跨 132.7 / 215.6 pt(≈6~10 个行高)。
# 判据: 相邻两字必须**要么同一行**(y 区间重叠 —— 覆盖"同一行的几个单元格横排"),
# **要么是下一行的行首**(b 比 a 低、b 起笔不比 a 收笔更右、且行距不超过 3 倍字高)。
# 违反者**整条 run 弃用** —— 方向与 seg_check 模块头 ③ 的取舍一致: 宁可不认这个锚
# ("找不到"是看得见的失败, 用户改个锚就好), 也不给一个满页热区(把链接**静默**绑到
# 错的地方, 读者点开才知道)。
# 阈值取宽(RISE_MAX=3 倍字高): 正常行距 1.2~2 倍, 表格行距更大, 3 倍是"绝不可能是
# 相邻两行"的下界 —— 宁可放过(多认一处)也不误杀(丢一处)。
_X_TOL = 2.0          # 水平容差: 字间微调(±2pt)不算"起笔更右"
_Y_TOL = 1.0          # 同行判定/行距判定的容差
_RISE_MAX = 3.0       # 换行时允许的最大行距(倍字高)


def _reading_contiguous(ch, run):
    """run(升序的原字下标) 在**版面上**是否真的连成一串(判据见上面注释)。

    为什么按"相邻两字"逐对判而不是只看包围盒: 包围盒只看**首末**, 中间怎么绕它不管 ——
    而假拼接的特征恰恰是"中间某一步跳了"(p3 那条 501 字锚里跳了 6 步)。逐对判才拦得住。
    """
    for a, b in zip(run, run[1:]):
        ra, rb = ch[a][1], ch[b][1]
        if rb.y0 < ra.y1 - _Y_TOL and rb.y1 > ra.y0 + _Y_TOL:
            continue                                    # y 区间重叠 = 同一行(含跨单元格横排)
        ha = max(1.0, ra.y1 - ra.y0)
        if (rb.y0 >= ra.y1 - _Y_TOL                     # 在下一行
                and rb.x0 <= ra.x1 + _X_TOL             # 回到左边续排
                and (rb.y0 - ra.y1) <= _RISE_MAX * ha):  # 行距正常
            continue
        return False
    return True


def find_across_lines(page, anchor):
    """字符级兜底: 把**跨行**的锚也找出来 —— 交给 search_for 必然失败, 它不跨行。

    为什么非有这一档(实测 2026-09-25, Lee 篇 p1 标题):
      『Architecture Carbon Tool v3：实现可持续性感知的硅系统设计探索』44 字 -> 0 命中;
      连中文尾巴『实现可持续性感知的硅系统设计探索』16 字也是**全篇 0** —— 它自己就折了行;
      而短的『可持续性感知』6 字命中 6 处。面板默认动作恰是"点右边中文取整段", 正落在
      最差那一档 —— 用户看到的是"我点的是页面上明摆着的字, 它却说找不到"。

    返回: **每次出现一个框**(整条锚所有字符的包围盒)。
    为什么不按行返回多个框: 下游 merge_fragments 的合并判据是"同一行(y 重叠)且水平相邻",
    跨行的框它**不会**并 —— 一次出现会被数成两处, occurrence 的语义跟着就错了。
    代价: 折行锚的热区是包围矩形, 会多盖住两行之间的空白; 比"只有第一行能点"更接近
    用户预期, 且不破坏"一处 = 一个框"这条口径。两个**不同**出现的包围盒若在纵向上交叠
    (罕见: 同一条锚两次出现且各自折行、行区间互相交错), merge_fragments 会把它们并成一个
    —— 属"少数被并成多数"一侧, 与既有 '[5][6]' 并框同一性质, 不新增机制。

    **实测效果(2026-09-25, Lee 篇 117 个可选段, 判据 = merge_fragments 后 ≥1 框)**:
      · 只有前两档: 40 段命中 = 34.2%
      · 加上本档:   109 段命中 = 93.2%(救回 69 段)
      · 本档 + [v28.92] 标点折叠: **117 段 = 100%**
      · 前两档已命中的段, 计数**一格未变**(本档只在①②皆空时才走) —— 无过度计数
    剩下的 8 段(6.8%)已逐一查明病因并一并修掉(见下面的标点折叠): 它**不是**跨页、**不是**分块
    交错, 而是锚与页面**标点宽度不一致** —— 侧车 trans 里是 ASCII `.`/`(1)`, 渲染后的页面是全角
    `。`/`（1）`, 而 norm() 只去空白。实测断点: 页 `模型：（1）遗留` vs 锚 `模型：(1)遗留`;
    页 `[13]。，提供` vs 锚 `[13].，提供`。

    [v34] **版面连续性守卫(见 _reading_contiguous 上方)**: 本档的"命中"只是流里连着, 不是
    版面上挨着 —— 整页切碎成几十块的图形页上会拼出**页面上并不存在**的串。实测该语料
    116 个走本档的段里 2 个是假拼接(框跨 132.7/215.6 pt), 加守卫后它们改判"找不到",
    其余 114 段计数一格未变。
    """
    a = norm(anchor)
    if len(a) < 2:
        return []
    ch = _chars_of(page)
    text = "".join(c for c, _, _ in ch)
    runs = []                       # 每次出现 -> 原字符下标(升序、去重)
    if a in text:
        i = text.find(a)
        while i >= 0:
            runs.append(tuple(range(i, i + len(a))))
            i = text.find(a, i + 1)
    else:
        # [v28.92] 精确找不到 -> 折标点再找。**先精确、失败才折叠**是为了把影响面压到最小:
        # 本来就能命中的段走的仍是原路径, 计数一格不变; 折叠只服务于"只差全/半角"的那几段。
        ftext, fidx = _fold_stream(ch)
        fa = _fold(a)
        i = ftext.find(fa) if len(fa) >= 2 else -1
        while i >= 0:
            run = tuple(sorted(set(fidx[i:i + len(fa)])))
            if run not in runs:     # 折叠可能把相邻两处映射到同一批原字, 那仍算一处
                runs.append(run)
            i = ftext.find(fa, i + 1)
    out = []
    for run in runs:
        # [v34] 假拼接要在这里拦: 下标连续**只说明流里连续**, 版面上可能一个是页眉、
        # 一个是页脚(判据与实测见 _reading_contiguous 上方)。弃用整条 run。
        if not _reading_contiguous(ch, run):
            continue
        r = pymupdf.Rect(ch[run[0]][1])
        for k in run[1:]:
            r |= ch[k][1]
        out.append(r)
    return out


def find_hits(page, anchor):
    """本页按锚找框, 三档递进: ①照原样 ②去掉所有空白 ③**字符级跨行兜底**([v28.91])。

    为什么不直接照 relink_pages 那样去空白: 锚是用户从**本页文字**里点来的
    (见 --list-lines), 页面上明明写着 "GelSight Mini" —— 一去空白就成了页面上
    不存在的字符串。实测(2026-09-22): 『GelSight Mini』去空白后 0 命中, 面板
    把用户自己点的那一行判成"全篇找不到", 只能改成只选单个词。
    relink_pages 那边非去不可, 是因为它的锚从**另一份文件**的文本层抽出来,
    两边的空格排版本就不同; 这里锚与页面同源, 没有那个问题。
    保留去空白这一档是为了**手工敲**进来的锚(空格与页面排版不一致时靠它兜)。

    第③档为什么排在最后: 前两档走 search_for, 命中框是**引擎给的精确框**; ③档只能给
    包围盒, 是退而求其次。故只在①②都落空时才启用 —— 换来的好处是短锚的框不变,
    长锚(整段/折行标题)从"搜不到"变成"找得到"。
    """
    hits = page.search_for(anchor)
    if not hits and norm(anchor) != anchor:
        hits = page.search_for(norm(anchor))
    if not hits:
        hits = find_across_lines(page, anchor)
    return hits


PROJ = os.environ.get("P2Z_PROJ") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------- 逐段「原文↔译文」(面板选锚, [v28.85] 2026-09-25)
def _seg_tools():
    """取 seg_export 的 restore / true_page。

    为什么借而不重写: 那是**同一把尺子** —— 导出端还原 {vN}、回锚端建 pattern 都走
    seg_export.restore(); 这里另写一套, 迟早分叉成两种"还原后"的字符串。

    导入期陷阱: seg_export 在**模块层**把 sys.stdout 重包成 utf-8 wrapper, 而被丢掉的旧
    wrapper 在 GC 时会**关掉底层 buffer** -> 本工具之后的 print 全报 closed file。故导入
    期间把 sys.stdout 换成一次性替身(与 verify/v33/payload_fresh.py 同一手法)。
    """
    import io as _io
    real, sink = sys.stdout, _io.TextIOWrapper(_io.BytesIO(), encoding="utf-8")
    sys.stdout = sink
    try:
        import seg_export
    finally:
        sys.stdout = real
    return seg_export.restore, seg_export.true_page, seg_export.PURE_GLYPH


def list_segs(sidecar, page):
    """段表 -> (全篇页号集合, [{page, seg, orig, zh, same}])。

    页号走 true_page(**真实 PDF 页码**): 侧车的 `page` 是回调计数, 图多的论文会整体漂移
    (理由见 seg_export.true_page 的 docstring)。面板的页码框与质检报告同一口径。
    `vars` 是**该条记录(该页)局部**的 {vN}->字形 图例, 同号跨页含义可不同, 故逐条取用、不合并。

    `page` 给 0/None = **全篇**(每段自带 page 字段, 面板按页分组渲染、滚动时页码跟着走);
    给 N = 只列第 N 页。[v28.87] 起每段都带 page —— 单页模式多这一个字段无副作用, 形状统一。

    **纯字形段要按导出端同一口径滤掉**(PURE_GLYPH: raw 全部由 {vN} 组成)。这类段是页眉/
    页码/整页表格, 提取期被压成单个字形 token —— 实测 Lee 的 p10 seg0 raw 就是 `{v0}`,
    而 vars["0"] 是整行页眉 "ACT3TechnicalWhitepaper,2026VincentT.Lee,…"(**空格本就不在
    值里**, 不是还原丢了)。列给用户既无可译文字、也当不了锚(页面上搜不到), 纯噪声;
    文献页(p11)整页如此 -> 过滤后该页为 0 段, 面板会显示"本页没有可取的段", 正合期望。
    """
    restore, true_page, pure_glyph = _seg_tools()
    out, pages = [], set()
    with io.open(sidecar, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            o = json.loads(ln)
            tp = true_page(o)
            if tp is None:
                tp = o.get("page")
            pages.add(tp)
            if page and tp != page:
                continue
            vs = o.get("vars") or {}
            for i, s in enumerate(o.get("segs") or []):
                raw = s.get("raw") or ""
                if pure_glyph.match(raw.strip()):
                    continue
                orig = restore(raw, vs)
                zh = restore(s.get("trans") or "", vs)
                out.append({"page": tp, "seg": i, "orig": orig, "zh": zh,
                            "same": bool(orig) and zh == orig})
    return pages, out


# ----------------------------------------------------------------- 规格装载
def load_spec(path, task):
    """规格 -> (entries, notices)。entry 带 scope; 撞锚时本篇覆盖全局。

    `--task` 缺席时只装 global —— 但若规格里确实有 tasks 段, 必须**说出来**,
    否则用户会以为"怎么没生效"却查不出原因(静默失效是这套工具最怕的形态)。
    """
    with io.open(path, encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, dict):
        raise ValueError("规格根必须是对象: {\"global\": [...], \"tasks\": {...}}")

    ent, notices = [], []
    for e in (spec.get("global") or []):
        ent.append(dict(e, scope="全局"))
    tasks = spec.get("tasks") or {}
    own = []
    if task:
        own = [dict(e, scope="本篇") for e in (tasks.get(task) or [])]
    elif tasks:
        notices.append("NOTICE 规格里有 %d 个任务的条目, 但没给 --task, 这些条目本次未生效: %s"
                       % (len(tasks), ", ".join(sorted(tasks))))
    own_anchors = {norm(e.get("anchor") or "") for e in own}
    kept = []
    for e in ent:
        if norm(e.get("anchor") or "") in own_anchors:
            notices.append("本篇覆盖全局: 『%s』" % e["anchor"])
            continue
        kept.append(e)
    return kept + own, notices


# ------------------------------------------------------------ 页映射(dual)
def page_index(p_mono, dual):
    """规格里的页码一律是 **mono 坐标(1 基)**; dual 时同一页的译文侧是 2p-1(0 基奇数)。

    与 style_links --dual 同一换算口径 —— 不换算就会去绑原版页(英文侧), 锚自然找不到。
    """
    return 2 * p_mono - 1 if dual else p_mono - 1


def doc_pages(n, dual):
    """按 mono 页序给出的**目标页 0 基索引**序列(dual 只取译文侧)。"""
    return list(range(1, n, 2)) if dual else list(range(n))


def mono_page(pno, dual):
    """page_index 的**逆**: 目标页 0 基索引 -> mono 页码(1 基)。

    面板拿到的位置必须回写成**用户看到的页码**(规格里也一律是 mono 坐标), 否则双语版
    会报出一个用户翻不到的两倍页号。
    """
    return (pno + 1) // 2 if dual else pno + 1


# ------------------------------------------------------------------- 定位
def occ_list(doc, pages, anchor):
    """全篇按阅读顺序的 [(页0基, 框)] —— "第 N 次出现"的判据就是这份列表。"""
    out = []
    for pno in pages:
        hits = merge_fragments(find_hits(doc[pno], anchor))
        for b in sorted(hits, key=lambda r: (round(r.y0, 1), round(r.x0, 1))):
            out.append((pno, b))
    return out


def plan_entry(doc, pages, e, dual):
    """一条规格 -> 计划(含 FAIL 的判据)。返回 dict(status, detail, pno, rect, ...)。"""
    d = dict(e)
    d["status"], d["detail"], d["pno"], d["rect"] = "fail", "", None, None
    # raw 用来**搜**, anchor_n 用来**认**(撞锚/报告里的键) —— 两者不能混: 一开头就把
    # raw 归一化再拿去搜, find_hits 里的"原样"那一档就永远走不到(实测: 传进来的
    # 已经是去空白的 "GelSightMini", 页面上没这个串, 于是照旧判"全篇找不到")。
    raw = (e.get("anchor") or "").strip()
    anchor = norm(raw)
    d["anchor_n"] = anchor
    if not anchor:
        d["detail"] = "锚文本为空"
        return d
    url = (e.get("url") or "").strip()
    if not URL_OK.match(url):
        d["detail"] = "URL 必须以 http:// 或 https:// 开头: %r" % url
        return d

    scope = e["scope"]
    if scope == "全局":
        if e.get("page") not in (None, ""):
            d["detail"] = "全局条目不许带 page(它要跨篇用) —— 要在某一页生效请改成本篇条目"
            return d
        cand = occ_list(doc, pages, raw)
        where = "全篇"
    else:
        pg = e.get("page")
        if pg in (None, ""):
            d["detail"] = "本篇条目必须给 page(1 基)"
            return d
        try:
            pg = int(pg)
        except (TypeError, ValueError):
            d["detail"] = "page 不是整数: %r" % (e.get("page"),)
            return d
        n_mono = len(pages) if not dual else len(doc) // 2
        if not (1 <= pg <= n_mono):
            d["detail"] = "page %d 越界(本篇 mono 共 %d 页)" % (pg, n_mono)
            return d
        pno = page_index(pg, dual)
        cand = [(pno, b) for b in sorted(merge_fragments(find_hits(doc[pno], raw)),
                                        key=lambda r: (round(r.y0, 1), round(r.x0, 1)))]
        where = "第 %d 页" % pg

    if not cand:
        # 报**用户原样写的**锚, 不报去空白后的 anchor_n —— 报后者会让人对着
        # "GelSightMini" 发懵(他自己写的是 "GelSight Mini")。
        d["detail"] = "%s找不到『%s』—— 锚要连续出现在同一行里, 空格会被忽略" % (
            where, (e.get("anchor") or "").strip())
        return d
    occ = e.get("occurrence")
    if occ in (None, ""):
        if len(cand) > 1:
            d["detail"] = "%s命中 %d 处, 请给 occurrence 指定第几处" % (where, len(cand))
            return d
        occ = 1
    try:
        occ = int(occ)
    except (TypeError, ValueError):
        d["detail"] = "occurrence 不是正整数: %r" % (e.get("occurrence"),)
        return d
    if not (1 <= occ <= len(cand)):
        d["detail"] = "%s只命中 %d 处, occurrence=%d 越界" % (where, len(cand), occ)
        return d

    pno, rect = cand[occ - 1]
    d.update(status="ok", pno=pno, rect=pymupdf.Rect(rect), occurrence=occ,
             hits=len(cand), where=where)
    d["detail"] = "%s命中 %d 处, 取第 %d 处" % (where, len(cand), occ)
    return d


def check_overlaps(plans):
    """两条新锚抢同一块矩形 -> 都点名。用户自己写重的两条, 不该由机器挑一条执行。"""
    ok = [p for p in plans if p["status"] == "ok"]
    bad = []
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            a, b = ok[i], ok[j]
            if a["pno"] == b["pno"] and a["rect"].intersects(b["rect"]):
                bad.append((a, b))
                b["status"] = "fail"
                b["detail"] = "与另一条新锚抢同一块矩形: 『%s』(%s) vs 『%s』(%s)" % (
                    a["anchor_n"], a["where"], b["anchor_n"], b["where"])
                a["status"] = "fail"
                a["detail"] = "与另一条新锚抢同一块矩形: 『%s』(%s) vs 『%s』(%s)" % (
                    a["anchor_n"], a["where"], b["anchor_n"], b["where"])
    return bad


# --------------------------------------------------------------------- 主
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--spec", default=os.path.join(PROJ, "user_links.json"))
    ap.add_argument("--task", default="", help="本篇任务名(规格 tasks 段的键)")
    ap.add_argument("--dual", action="store_true",
                    help="成品是双语版(规格页码按 mono 坐标, 落到译文侧)")
    ap.add_argument("--check", action="store_true", help="只预检, 不落盘")
    ap.add_argument("--page", type=int, default=0,
                    help="--list-lines/--count-hits: 列/数第几页(mono 坐标); "
                         "--list-segs: 0=全篇(每段自带真实页码), N=只列第 N 页")
    ap.add_argument("--list-lines", action="store_true", help="列本页文字(面板选字用)")
    ap.add_argument("--sidecar", default="", help="段表(侧车 jsonl); 配 --list-segs")
    ap.add_argument("--list-segs", action="store_true",
                    help="列本页逐段「原文↔译文」(真实 PDF 页码; 面板选锚用)")
    ap.add_argument("--count-hits", action="store_true",
                    help="只数这条锚命中几处(配 --anchor/--scope/--page); 只读, 不写任何文件")
    ap.add_argument("--anchor", default="", help="配 --count-hits: 要数的锚文本")
    ap.add_argument("--scope", default="本篇", choices=("本篇", "全局"),
                    help="配 --count-hits: 本篇=只在这一页找 / 全局=全篇找")
    ap.add_argument("--render-page", action="store_true",
                    help="把第 N 页渲成 PNG(配 --page/--out, 可选 --rect 把目标处涂成荧光黄); 只读")
    ap.add_argument("--out", default="", help="配 --render-page: PNG 落盘路径")
    ap.add_argument("--rect", default="",
                    help="配 --render-page: 要涂亮的矩形 'x0,y0,x1,y1'(PDF 点坐标)")
    ap.add_argument("--zoom", type=float, default=2.0,
                    help="配 --render-page: 渲染倍率(1.0=72dpi, 默认 2.0≈144dpi)")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    doc = pymupdf.open(args.target)

    # ---- 面板选字: 列本页文字(不改文件) ----
    if args.list_lines:
        if not args.page:
            print("FAIL --list-lines 需要 --page")
            return 1
        pno = page_index(args.page, args.dual)
        if not (0 <= pno < len(doc)):
            print("FAIL 第 %d 页越界(共 %d 页)" % (args.page, len(doc)))
            return 1
        lines, seen = [], set()
        for ln in doc[pno].get_text("text").splitlines():
            t = ln.strip()
            if len(t) < 2 or t in seen:
                continue
            seen.add(t)
            lines.append(t[:120])
        # pages 一并回带: 面板要它来**限住页码输入框**, 否则用户填到第 99 页才被告知越界。
        n_mono = len(doc) // 2 if args.dual else len(doc)
        print(json.dumps({"page": args.page, "pages": n_mono, "total": len(doc),
                          "lines": lines[:400]}, ensure_ascii=False))
        return 0

    # ---- [v28.85] 面板选锚: 列逐段「原文↔译文」(读段表, 不改任何文件) ----
    # [v28.87] --page 0 = 全篇: 面板把所有页一次载进对照列表、按页分组, 滚动时页码
    # 跟着走到正看的页(滚动联动), 不再"一页一拉"。给 N 仍只列第 N 页(调试/单页用)。
    if args.list_segs:
        if not (args.sidecar and os.path.exists(args.sidecar)):
            print("FAIL 拿不到段表(侧车): %r —— 先出稿(⑤), 段表才带最终译文"
                  % args.sidecar)
            return 1
        try:
            pages, segs = list_segs(args.sidecar, args.page)
        except Exception as e:
            print("FAIL 段表读不了(%s): %s" % (args.sidecar, e))
            return 1
        n = max(pages) if pages else 0
        if args.page and not (1 <= args.page <= n):
            print("FAIL 第 %d 页越界(段表共 %d 页)" % (args.page, n))
            return 1
        print(json.dumps({"page": args.page, "pages": n, "segs": segs},
                         ensure_ascii=False))
        return 0

    if args.dual and len(doc) % 2:
        print("FAIL --dual 但页数是奇数(%d), 这不是双语版成品" % len(doc))
        return 1

    # ---- [v35] 面板定位标记: 把第 N 页渲成 PNG(只读; 不碰成品, 不写任何数据) ----
    # 为什么这一件在工具里而不在面板里: 面板至今**零渲染层**, 而"那一处落在页面的什么
    # 位置"是 **PDF 层的读数** —— 与"命中几处"同源。放在这里, 页码换算(page_index)与
    # 矩形坐标(hits_pos 给的那四个数)与 occ_list 是同一把尺子; 面板只把这张 PNG 显示出来。
    # 画的是**这份预览**上的荧光黄块(在内存里 draw_rect), 成品一个字都不动 —— 逐字纪律同 --check。
    if args.render_page:
        if not args.page:
            print("FAIL --render-page 需要 --page")
            return 1
        n_mono = len(doc) // 2 if args.dual else len(doc)
        if not (1 <= args.page <= n_mono):
            print("FAIL 第 %d 页越界(本篇共 %d 页)" % (args.page, n_mono))
            return 1
        if not args.out:
            print("FAIL --render-page 需要 --out(PNG 落盘路径)")
            return 1
        rect = None
        raw_rect = (args.rect or "").strip()
        if raw_rect:
            try:
                vals = [float(v) for v in re.split(r"[,\s]+", raw_rect) if v]
                if len(vals) != 4:
                    raise ValueError("要 4 个数 x0,y0,x1,y1")
                rect = pymupdf.Rect(*vals)
            except Exception as e:
                print("FAIL --rect 解析不了(%r): %s" % (raw_rect, e))
                return 1
        pno = page_index(args.page, args.dual)
        pg = doc[pno]
        if rect is not None:
            # [v35] 荧光笔: 半透明黄**盖在字上**(overlay 缺省为真), 不是描四条边框 ——
            # 框只圈出范围, 读者还得自己把眼睛挪进去找那一行; 实心黄底直接把那段字涂亮,
            # 一眼就是它(用户口径: 不用红框, 用荧光标记)。
            # color=None 是必须的: draw_rect 的 color **缺省是 (0,)**(1pt 黑描边),
            # 不显式关掉就会在黄块外圈多出一道黑框。
            pg.draw_rect(rect, color=None, fill=(1, 1, 0), fill_opacity=0.4)
        try:
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(args.zoom, args.zoom))
        except Exception as e:
            print("FAIL 第 %d 页渲不出来: %s" % (args.page, e))
            return 1
        d = os.path.dirname(os.path.abspath(args.out))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        pix.save(args.out)
        print(json.dumps({"page": args.page, "pno": pno, "out": args.out,
                          "w": pix.width, "h": pix.height, "pages": n_mono,
                          "rect": None if rect is None else
                                  [round(rect.x0, 2), round(rect.y0, 2),
                                   round(rect.x1, 2), round(rect.y1, 2)]},
                         ensure_ascii=False))
        return 0

    # ---- [v28.86] 面板选锚: 这条锚命中几处(只读; 判据与门禁同一套) ----
    # 为什么要这一问: 面板要让「第几处」不再手填。命中 1 处 -> 留空(工具自己的默认语义
    # 就是第 1 处, 等价); 命中 >1 处 -> 才要人选, 且只能选 1..N, 选不到界外去。
    # 判据**一字不改**: 走的就是 plan_entry 那两行(本篇 = 本页 find_hits 并框; 全局 =
    # occ_list 全篇)。这里另写一套 counting, 迟早与门禁数出两个数。
    if args.count_hits:
        raw = (args.anchor or "").strip()
        if not raw:
            print("FAIL --count-hits 需要 --anchor")
            return 1
        n_mono = len(doc) // 2 if args.dual else len(doc)
        if args.page and not (1 <= args.page <= n_mono):
            print("FAIL 第 %d 页越界(本篇共 %d 页)" % (args.page, n_mono))
            return 1
        cand_doc = occ_list(doc, doc_pages(len(doc), args.dual), raw)
        n_doc = len(cand_doc)
        cand_page = None
        if args.page:
            # 与 plan_entry 同一行写法(同排序键): "第 N 处"在两个工具里必须是同一处。
            pno = page_index(args.page, args.dual)
            cand_page = [(pno, b) for b in sorted(
                merge_fragments(find_hits(doc[pno], raw)),
                key=lambda r: (round(r.y0, 1), round(r.x0, 1)))]
        n_page = len(cand_page) if cand_page is not None else None
        if args.scope == "全局":
            n, where, cand = n_doc, "全篇", cand_doc
        else:
            if not args.page:
                print("FAIL --count-hits 的「本篇」口径需要 --page")
                return 1
            n, where, cand = n_page, "第 %d 页" % args.page, cand_page
        # [v28.89] hits 仍是"当前口径"的命中数(前端逻辑不变); 另回 hits_page/hits_doc
        # 两个口径, 面板才能区分"全篇真就一处"和"本页一处、别页还有" —— 用户拿着只
        # 命中一次的锚来问「第几处为什么点不动」时, 这正是要答的那句话。
        # [v35] hits_pos: 当前口径下**每一处的位置**(mono 页码 + 矩形), 顺序就是阅读序,
        # 与 occ_list/plan_entry 的 cand 逐项对应 —— 面板的 ◀▶ 据此跳到第 N 处并在页面上
        # 画框。这里只把**本来就算出来、只是被 len() 丢掉**的位置回带, 判据一字未改。
        hits_pos = [{"page": mono_page(pno, args.dual),
                     "rect": [round(b.x0, 2), round(b.y0, 2),
                              round(b.x1, 2), round(b.y1, 2)]}
                    for pno, b in cand]
        print(json.dumps({"anchor": raw, "scope": args.scope, "page": args.page,
                          "pages": n_mono, "hits": n, "hits_page": n_page,
                          "hits_doc": n_doc, "where": where,
                          "hits_pos": hits_pos},
                         ensure_ascii=False))
        return 0

    if not os.path.exists(args.spec):
        print("FAIL 规格文件不存在: %s" % args.spec)
        return 1

    try:
        entries, notices = load_spec(args.spec, args.task)
    except Exception as e:
        print("FAIL 规格无法解析(%s): %s" % (args.spec, e))
        return 1

    pages = doc_pages(len(doc), args.dual)
    plans = [plan_entry(doc, pages, e, args.dual) for e in entries]
    check_overlaps(plans)

    fails = [p for p in plans if p["status"] != "ok"]

    # ---- 预检: 到此为止, 一个字都不写 ----
    if args.check:
        for p in plans:
            mark = "✓" if p["status"] == "ok" else "✗"
            scope = "%s%s" % (p["scope"], ("/p%d" % p["page"]) if p["scope"] == "本篇" else "")
            print("  %s [%s] 『%s』 -> %s" % (mark, scope, p["anchor_n"], p.get("url", "")))
            print("      %s" % p["detail"])
        for n in notices:
            print("  " + n)
        print("预检: 可装 %d 条 | 不通过 %d 条%s"
              % (len(plans) - len(fails), len(fails), "" if not fails else " —— 修好再跑"))
        return 1 if fails else 0

    if fails:
        print("FAIL %d 条不通过, 成品未改动:" % len(fails))
        for p in fails:
            print("  『%s』(%s) %s" % (p["anchor_n"], p["scope"], p["detail"]))
        for n in notices:
            print("  " + n)
        return 1

    # ---- 应用: 用户手写的意图优先于继承来的 -> 压住原有链接就删掉它, 但要记账 ----
    stat, log = [], []
    for p in plans:
        page = doc[p["pno"]]
        for l in page.get_links():
            if pymupdf.Rect(l["from"]).intersects(p["rect"]):
                kind = {pymupdf.LINK_GOTO: "引文锚(GOTO)", pymupdf.LINK_URI: "原有超链接(URI)",
                        pymupdf.LINK_NAMED: "命名链接(NAMED)"}.get(l["kind"], "链接%d" % l["kind"])
                page.delete_link(l)
                stat.append(("覆盖", p["pno"] + 1, p["anchor_n"], l["kind"]))
                log.append("p%d 覆盖原有 %s -> %s" % (p["pno"] + 1, kind, p["anchor_n"]))
        page.insert_link({"kind": pymupdf.LINK_URI, "from": p["rect"],
                          "uri": p["url"]})
        stat.append(("新装", p["pno"] + 1, p["anchor_n"], None))

    tmp = args.target + ".tmp"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, args.target)

    # ---- 落盘后重开核对: 计划里的每一条都必须在成品里找得到(不信内存) ----
    chk = pymupdf.open(args.target)
    placed = 0
    for p in plans:
        hit = any(l["kind"] == pymupdf.LINK_URI and (l.get("uri") or "") == p["url"]
                  and pymupdf.Rect(l["from"]).intersects(p["rect"])
                  for l in chk[p["pno"]].get_links())
        if hit:
            placed += 1
        else:
            log.append("落盘后核对失败: p%d 『%s』没找到对应 URI 链接" % (p["pno"] + 1, p["anchor_n"]))
    chk.close()

    n_cov = sum(1 for s in stat if s[0] == "覆盖")
    n_glob = sum(1 for p in plans if p["scope"] == "全局")
    print("概念链接: 新装 %d 条(全局 %d / 本篇 %d) | 覆盖原有链接 %d 条 | 落盘后核对 %d/%d"
          % (len(plans), n_glob, len(plans) - n_glob, n_cov, placed, len(plans)))
    for n in notices:
        print("  " + n)
    print("  顺序提醒: 本工具必须跑在 style_links 之前 —— 接着跑 "
          "`python tools/style_links.py --target <成品>%s`, 新锚才会变蓝。"
          % (" --dual" if args.dual else ""))
    if args.report:
        with io.open(args.report, "w", encoding="utf-8") as f:
            f.write("动作\t页\t锚\t原链接kind\n")
            for s in stat:
                f.write("%s\t%d\t%s\t%s\n" % (s[0], s[1], s[2], s[3] if s[3] is not None else ""))
            for ln in log:
                f.write("\t\t%s\t\n" % ln)
        print("  报告:", args.report)
    # 覆盖原有链接是**正常动作**(用户手写的意图优先于继承来的), 只报不拦 ——
    # 拦的只有"落盘后核对不上": 那是文件真出问题了。两者混在一个 log 里看过一次,
    # 结果是"每跑一次正常覆盖就退 1", 面板会把它当失败。
    for ln in log:
        print("  " + ln)
    return 1 if placed != len(plans) else 0


if __name__ == "__main__":
    sys.exit(main())
