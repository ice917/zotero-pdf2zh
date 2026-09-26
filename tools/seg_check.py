# -*- coding: utf-8 -*-
"""seg_check.py — 段表口径的「局部漏译」门禁（只读、零 API 成本）

【为什么要有这一件（缺口来源，别重新起头诊断）】
  post_check 的第一断言在 v28.46 改成了**全篇口径**（WHOLE_DOC_CJK_FAIL=0.18），
  代价写在那一节的 §五：「全篇口径实等价于**整篇零翻译闸门**，**局部漏译的自动
  监控能力丧失**（检出率 ≤ 10.9%）」。原因是 whole = Σcjk/Σ(cjk+alnum) 对
  「翻了多少页」**非单调** —— 未翻页的 alnum 特别大，把分母带跑，只翻 10% 的页
  读数（0.5829）甚至比翻满全篇（0.5117）还高。
  v28.46 §五 把真解登记为**引擎自述段表口径**（侧车 segs[].raw/trans），
  但当时未采纳。本工具就是那条路。

【与 post_check 的分工（互补，不重叠）】
  post_check  看**产物 PDF**：全篇汉化率 / 引用 / 占位符 / 文献区 / 残渣
  seg_check   看**段表**  ：逐段「这段进过 LLM 吗、回来了吗」
  产物侧分不清「引擎按规定不翻」与「卡死漏译」（两者在 PDF 文本层都表现为与原文
  逐字相同），段表侧分得清 —— 段表直接记着每段喂进去的原文与收回来的译文。

【判据】
  前置门槛（「这段有没有该翻的东西」）: 剥掉 {vN} 后存在**连续 4+ 个字母** —— 滤掉
      侧车里成堆的**数学字形段**（`t`、`x`、`u u`、`Pi j`：译文与原文逐字相同，是
      引擎「公式不译」的正确行为，实测占首版候选的 49%）。刻度依据见 WORD 常量处。
  判定级（决定退出码）: 段有上述可译词面，但译文**零汉字** → 该段没被翻译。
      豁免四档：① 无「可译词面」的段（前置门槛就挡掉了：纯 `{vN}` 公式段、
                   数学字形段 `t`/`x`/`Pi j` —— 不占判定，也不计入参与段）
                ② 文献页段（复用 post_check.is_ref_page 的**页级密度**判据 ——
                   文献条目规定整条保留英文，「零汉字」是预期结果，反向由
                   post_check 断言 4 把关）
                ③ **页内文献区块**（复用 post_check.is_ref_heading 的**区域**
                   判据）：段序里出现「文献区标题」段（LITERATURE CITED /
                   REFERENCES…）→ 该标题段及其后各段豁免。页级密度判据抓不到
                   「正文末页 + 标题 + 条目」这种**半页文献块** —— Johnson 第3页
                   5834 字符里文献只占末尾 5 行，三种密度读数全在阈值之下，而
                   条目本身是第三种体例（`GOLD, H. S. 1959. …`：行首无 [n]，
                   年份也不在括号里），REF_ENTRY / REF_AY 两臂都抓不到；
                   三种体例唯一共同的锚点就是那一行标题，故区域判据改锚标题。
                ④ 任务 skipLastPages 豁免的末尾页（与 post_check 同口径）
      ②③ 与 post_check 判据**同源**（同一个模块的同一个常量），只是粒度从
      「页」降到「段」—— 刻意不在这里另写一套文献判据。
  提示级（只出读数、不判定）: 段的汉化率 0 < r < CJK_FAIL(0.05) —— 段回来了但
      基本还是英文（「翻了半段」）。与 v28.46「判定与读数分离、零误报优先」同源。
  接缝切点（与上面几档**正交**的第二个读数，同样只提示、不进判定）: 相邻两段的接缝
      处**没有空白**、两侧都是字母 —— 引擎把**版面检测框的边缘**当成了段落边界，切点
      落在词内。根因与实测取证（两篇五处切点全落在框边缘上）见 SEAM_L 常量处注释；
      清单在报告的「接缝切点清单」一节。与 tools/seams_report.py **不重叠**：那件管
      **跨页**接缝（`a.page != b.page`，走缓存手术），本读数管**页内**词内切点。
      **两道口径，缺一不可**：① 文本口径 `seam_of()` 圈出候选；② **几何核验**
      `geom_seam()` 拿**原文 PDF 坐标**定性，只留「强/中」、丢弃行/块边界。侧车不记
      坐标，「词内切点」与「正文正常换行/换栏」在段文本上长得一模一样 —— 实测全语料
      文本口径 921 处，几何核验后只剩 35 处（强 16 / 中 19），**噪音率 96%**（Johnson
      3 页 23 → 0）。故文本口径**单用等于把行边界全报成切点**；只有原文 PDF 不在手
      （`--no-geo`）时才退回纯文本读数，且报告里会明写「几何未核验」。
  **但接缝口径有一个天生的盲区（v30.4 补）**：它读的是**段表文本**，只能看见**两半都成了
  文字段**的类①切点。类②（半词落进 `cls<=0` 保留区，那半被记成 `{vN}`；或残片短到过不了
  4 字母词面门槛，如 `gy`）在段表里**不留痕**，接缝读数与几何核验**都取不到**。实测产物里
  的可见残留正是这一类 —— Zhang p9 `seco` 落成 `最佳与|seco每个类别中的次优结果…`、
  Padmaprabhan p5 页眉落成 `TOSoFiT：拓扑gy Optimization of Hydraulic…`。故新增
  `--layout-cuts`（**默认关**）：转调 `tools/layout_probe.py --cuts`，按**生产同一采样口径**
  （字符 bbox 左下角一像素）在**原文坐标**上找「相邻两字母、同基线、水平相接却 `cls` 不同」
  的断段处 —— 与侧车怎么记那两半无关，故类②也现形。代价是探针要加载版面 onnx 模型、
  整篇逐页推理，慢（数十秒级），故只作**显式开关**；探针或模型缺失时只出一行「不可用」，
  **绝不影响**本门禁的判定与其他读数。

  【为什么判定级只认「零汉字」这一个刻度】它是最**紧**的刻度：译文里连一个汉字都
  没有的段，不可能是「译者有意保留英文」—— 有意保留的（文献条目/拉丁学名）要么在
  被豁免的页上，要么没有可译词面；而任何更细的刻度（「英文残留多少算多」）都要跟
  「拉丁学名/单位/引用体例本该保留」打架，本工具不去踩这个坑。

  【但「最紧」不等于零误报（存量实测，别把读数当确诊）】13 篇有归档侧车的存量产物
  里，同轮的 11 篇剩 14 段候选，逐条核对**全是已知良性类**：
      · 页1 的作者/单位/邮箱/URL 行（人名机构按规定不翻）—— 8 段
      · 代码/CLI 片段（`python server.py --transport …`、`_data/….csv`）—— 3 段
      · **断段半词残片**（段被引擎切在词中间：`otion`、`seco`、`TO SoFiT: T`）—— 3 段
  故本工具的输出定位是「**候选清单 + 人工确认**」，不是自动放行/拦截：退出码 1 的
  含义是「有段回来是英文，去看一眼」，不是「这篇没翻」。真正的判还要看候选段本身
  （人/豆包），这也是放行权归门禁与人的原因。另外 2 篇（Lee/Rupchin）段表与 mono
  **不同轮**，由段表新鲜度警告拦在外面（见下）。

【漏译段怎么重译（本工具只出清单，不写回写码）】
  清单给的是**真实 PDF 页码**，直接喂既有已接线的第二公里管线：
      tools/seg_export.py --pages <清单页码> --pdf <原文> --name payload_rework
      →（豆包/网页 AI 译）→ tools/seg_import.py --manifest … → tools/seg_inject.py
  刻意不在这里自己改缓存：改译文属**内容工作**，角色边界上归豆包 + 人确认；
  本工具留在代码侧（门禁 / 台账）。

【页数口径（与 backfill_pages / seg_export 同一口径，别用 page 当页码）】
  侧车的 page 是 receive_layout 的**回调计数**：页面回调 + 该页的图形回调各占
  一号，同一页会占多行（实测 Zhang 15 页占 26 行）。真实页码只由 pageid（0 基）
  说得准，故一律取 pageid + 1；老侧车没有 pageid 时回落 page（与旧行为一致）。

【侧车身份】
  latest.jsonl 是**全局单文件**（换论文就覆盖）。所以优先用按**原文内容 md5[:16]**
  归档的那一份（<segflow>/pdf-<md5>.jsonl，见 converter._segflow_paths）；拿不到
  归档件时才回落 latest.jsonl，并在来源行里**明写「身份未核验」** —— 静默按别的篇的
  段表判本篇，比不判更坏。

【段表新鲜度（身份之外的第二道核验：同一篇也会有多轮）】
  同名归档件在**重翻**时会被同一路径复写，所以「身份对上了」不等于「是这一轮」。
  同一轮里段表逐页追写、比 mono 早 -0.6~-11.8 秒落盘（实测 13 篇）；实测两篇异常
  （Lee +3373 秒、Rupchin +362 秒）都是**段表属于更晚一轮**，拿它判这份 mono 会
  得出与产物相反的结论（Rupchin 实测 mono 汉化率 59.4% PASS，旧口径按那份段表会
  报 129 段漏译）。故 |段表 mtime - mono mtime| > LAG_TOL 时给**中**级警告（不判
  FAIL）—— 读数照出，但明确告诉人「先复核是不是同一轮」。

  用法:
    python tools/seg_check.py --mono <译版mono.pdf>              # 自动推原文 / 找归档侧车
    python tools/seg_check.py --pdf <原文.pdf> --sidecar <x.jsonl>
    python tools/seg_check.py --engine next --name <篇名>
    python tools/seg_check.py --mono <m.pdf> --skip-last 2
    python tools/seg_check.py --mono <m.pdf> --no-geo        # 接缝只出文本口径（离线对照）
    python tools/seg_check.py --mono <m.pdf> --layout-cuts    # 加跑版面切点（覆盖类②，慢）
  退出码: 无未译段 0 / 发现未译段 1 / 基础设施（拿不到段表等）2
  """
import argparse
import datetime
import glob
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import warnings

# [v34] 用 reconfigure 而不是 `sys.stdout = io.TextIOWrapper(...)`: 后者每执行一次就多包
# 一层, 而上一层的包装器被回收时会**关掉同一个底层 buffer** —— 于是"谁 import 本模块"
# 谁就在之后打印时报 closed file(实测形态与 user_links.py 头注释里记的同一例)。
# reconfigure 就地改编码, 重复执行是幂等的。本模块目前没人 import, 但把它当**库**用
# (例如单元测 `scan()`)是迟早的事, 这个坑不该留着。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
warnings.filterwarnings("ignore")
logging.getLogger("pypdf").setLevel(logging.ERROR)

import engine as _ENG                       # noqa: E402  引擎接缝（1.x 侧车 / next 段表）
import post_check as _PC                    # noqa: E402  CJK 判据 / 文献页判据 / review 目录

try:
    import pymupdf as _FITZ                 # 接缝切点的几何核验；缺了退化成文本口径
except Exception:
    _FITZ = None

PH = re.compile(r"\{v\d+\}")
ALNUM = re.compile(r"[A-Za-z0-9]")
# 「可译词面」判据：连续 4+ 个**字母**（`[^\W\d_]` = 任意文字系统的字母、去掉数字）。
# 刻度切在「有无 4+ 连续字母」而非「有几个词」：本机全语料 517 段候选呈**天然双峰**
# （≥1 个 4+ 字母串 vs 0 个）—— 252 段（49%）一个都没有、几乎全是数学字形段
# （`t` / `x` / `u u` / `t t X X` / `Pi j`），反之中间那 38 段（1~3 个）逐条核对
# 全是真内容（章节标题、数学散文残片）。故刻度只能切在「有无」。
# 刻意不用 ASCII 专属的 [A-Za-z]：数学斜体/西里尔/重音拉丁也是正文（v28.38 的教训，
# 与 engine._skip_para 同一取舍）；同时排除数字，纯年份/编号段（`1970`）不算词面。
WORD = re.compile(r"[^\W\d_]{4,}")
# 段表比 mono 新/旧多少秒就认定「不是同一轮」。实测本机 13 篇: 同一轮里段表比 mono
# **早** -0.6~-11.8 秒落盘（段表逐页追写、mono 要等翻译收尾才生成）; 不同轮的只有
# 两篇, Lee +3373 秒 / Rupchin +362 秒。60 秒对同轮最大读数留 5 倍余量、距最小异常 6 倍。
LAG_TOL = 60.0


# ---------------------------------------------------------------- 判据（纯函数）
def literal(s):
    """剥掉 {vN} 占位符 -> 裸文字。判据用，不改任何东西。"""
    return PH.sub(" ", s or "").strip()


def has_translatable(raw):
    """这一段有没有「该翻的东西」：剥掉 {vN} 后存在**连续 4+ 个字母**（WORD）。

    [自研补丁 2026-09-24] 为什么不是「有字母数字」就够（本工具首版口径）：
      侧车里有一大批**数学字形段** —— 单个变量/符号被引擎拆成独立段（`t`、`x`、
      `u u`、`Pi j`）。译文与原文逐字相同，这是引擎的**正确**行为（公式不译），但
      按「有字母数字 + 译文零汉字」会被全数误报成漏译；本机全语料实测这类占候选
      49%。刻度依据见文件头部 WORD 常量处（天然双峰）。
    代价：极短的英文残段（`Fig. 1.`、`1970`）也会被滤掉，方向是**少报**；这是
      唯一能把近半噪音清零的刻度，且这类残段的可读性影响微乎其微。
    """
    t = literal(raw)
    return bool(t) and bool(WORD.search(t))


def seg_ratio(trans):
    """段内汉化率 = 汉字 / (汉字 + 字母数字)，{vN} 先剥掉。

    占位符必须先剥：{v0} 里的数字 0 会被算成 alnum，一段纯公式译文会因此拿到非零
    分母。剥掉之后与 post_check.cjk_ratio 是**同一个量**，只是粒度从「页」换成
    「段」—— 口径同源是刻意的，两个门禁给的读数要能互相解释。
    """
    t = literal(trans)
    cjk = len(_PC.CJK.findall(t))
    alnum = len(ALNUM.findall(t))
    tot = cjk + alnum
    return (cjk / tot) if tot else 0.0


def judge(raw, trans):
    """一段 -> skip(无可译词面) | untranslated | half | ok。

    skip 的两类：纯 {vN} 段（公式/字形），和**有内容但没有 4+ 连续字母**的段
    （数学字形段 `t`/`x`/`Pi j` —— 译文与原文逐字相同属预期，见 has_translatable）。
    """
    if not has_translatable(raw):
        return "skip"
    r = seg_ratio(trans)
    if r == 0.0:
        return "untranslated"
    if r < _PC.CJK_FAIL:
        return "half"
    return "ok"


def ref_anchor_cut(segs):
    """页内段列表 -> 文献区起始下标（第一条「文献区标题」段）；无则 None。

    文献条目规定整条保留英文（post_check 断言 4 反向把关），故标题之后的段
    「译文零汉字」是预期结果而非漏译。判据本身在 post_check.is_ref_heading
    （与页级 is_ref_page 同源），这里只负责**在段序里定位**它。

    标题段本身也算文献区（含在豁免内）：标题常常同样保持英文（`LITERATURE
    CITED` 原样回显），不该因此报"漏译"。

    [v34] **弱形式加上下文守卫**：光杆 `Reference(s)` 是一条**整段**判据，而它同时是
    表格里常见的表头单元格（Melhani 第4页 `Reference | Dim. | Filter type | …` 被拆成
    单段就只剩 `Reference`）。命中它的代价是**其后整页段落全部豁免漏译判定** ——
    实测族 3「本该 FAIL 却 PASS」正是这条通道。故弱形式再要求一条客观证据：
    它**后面**得跟着像文献条目的段（含 4 位年份）。守卫只收紧"是不是锚点"，
    判据文本仍只有 post_check 一处（谁算弱形式由那里定义，这里不另写词表）。
    方向性：真文献区若一条年份都没有，守卫会失手 -> **多报**一个漏译段（噪音，看得见），
    而不是少报（危险）—— 与模块头 ③ 的取舍一致。页级 is_ref_page（密度）不经过这里。

    只往后看（`segs[i+1:]`）而不是全页找年份：锚点**之前**的段属于正文，正文里出现
    年份是常态；把"之前有年份"当证据等于没守卫（表头那页照旧被豁免）。
    """
    for i, s in enumerate(segs):
        raw = literal(s.get("raw"))
        if not _PC.is_ref_heading(raw):
            continue
        if _PC.REF_HEADING_WEAK.match(raw or "") and not any(
                _PC.REF_ENTRY_YEAR.search(literal(t.get("raw")) or "")
                for t in segs[i + 1:]):
            continue                     # 更像表头单元格: 不当锚点
        return i
    return None


# ------------------------------------------------- 接缝切点（与漏译判定正交的读数）
# 【这是什么】相邻两段的接缝处**没有空白**、两侧都是字母 —— 引擎把**版面检测框的
#   边缘**当成了段落边界，切点落在词内/行首几字符内留下的痕迹。
# 【根因（2026-09-24 取证，venv 侧 converter.py，探针 tools/layout_probe.py）】
#   断段的**唯一**条件是 `cls == xt_cls ? 追加 : sstk.append("") 开新段`（L469/L475），
#   而 cls 是**字符 bbox 左下角一个像素**在 YOLO 版面检测框图上采样的类别
#   （L411-416：`cls = layout[int(y0), int(x0)]`）。检测框边缘是像素级的 —— 回归误差
#   ±几像素，也可能比文字边界整体偏十几像素。一旦框边缘落在词内，逐字符 cls 就跳变
#   → 词被切开。实测两篇五处切点**全部**落在框边缘上：
#     · Padmaprabhan p3 页眉 y0=724.7：`abandon` 框(88,61,335,70) → 切在 `T|opology`
#       （x 84.2 cls=1 / x 87.5 cls=0，框左边缘=87）；框右边缘=336 → 切在
#       `undulating|locomotion`（331.8 cls=0 / 337.0 cls=1）。
#     · Zhang p9 行首 y0=432.2：`table_caption` 框左边缘 x=67，而文字左边界 x≈49 ——
#       **该 caption 每一行的头 3~4 个字符都被判成背景(cls=1)**，于是切出 `TAB|LE I:`
#       （x 61.3 cls=1 / x 68.0 cls=12）与次行 `seco|nd-best`。
# 【为什么只读不判定】切点是否造成可见危害取决于切在哪：
#   ① 一半落进保留区(cls<=0) → 那半按公式留原文 → 中英夹花（`拓扑gy`、`undulating 运动`）
#   ② 两半都是文字段 → 两半都送翻 → 译文被拆开/重复（Zhang p9 产物里那个重复的
#      `表 / 表 I：`）
#   ③ 少数是**正文正常换行/断词**，属预期 —— 故必须人工确认，绝不进判定（与
#      「最紧刻度也不等于零误报」同一条纪律）。
SEAM_L = re.compile(r"[A-Za-z]$")       # 前段接缝处直接以字母收尾（两侧之间没有空白）
SEAM_R_LOWER = re.compile(r"[a-z]")     # 后段以小写起头 → 强信号（几乎必然是切在词内）
SEAM_R_UPPER = re.compile(r"[A-Z]")     # 后段以大写起头 → 弱信号（如 `TAB|LE I:`）


def seam_of(raw_a, raw_b):
    """相邻两段 -> 接缝切点 {"left","right","upper"}，或 None（不是切点 / 是噪音）。

    判定：剥掉 {vN} 后前段以字母收尾、后段以字母起头。**「无空白」是自动成立的** ——
    引擎在同一段内遇到间距会在段文本里补空格（converter.py L470-474），所以正常词/句
    边界的前段会以空格收尾、后段的起首字符不是第 0 位，两种情形都落不进本判据。

    噪音门槛沿用漏译那套 `has_translatable`：数学字形段两两相邻（`t`/`x`/`u u`）也
    满足「字母接字母」，但**接缝两侧凑不出 4+ 连续字母**，据此滤掉。
    """
    if not (has_translatable(raw_a) or has_translatable(raw_b)):
        return None
    a = PH.sub("", raw_a or "")
    b = PH.sub("", raw_b or "")
    if not a or not b or not SEAM_L.search(a):
        return None
    if not (SEAM_R_LOWER.match(b) or SEAM_R_UPPER.match(b)):
        return None
    return {"left": a[-24:], "right": b[:24], "upper": bool(SEAM_R_UPPER.match(b))}


# ------------------------------------------------- 接缝切点: 几何核验（第二道，滤噪）
# 【为什么文本口径不能单用】`seam_of` 只看**段文本**，而**侧车不记坐标** —— 「页内词内
#   切点」与「正文正常换行/换栏」在文本上长得一模一样。本机全语料实测：文本口径 921 处，
#   几何口径只剩 **35 处**（强 16 / 中 19），**噪音率 96%**（Johnson 3 页更是 23 → 0）。
#   故第二道拿**原文 PDF 的坐标**定性；原文不在手（`--no-geo`）时才退回纯文本读数。
RUN2 = re.compile(r"[A-Za-z]{2,}")      # 接缝两侧各取「末一个/头一个字母串」去原文里找


def geom_seam(page, left, right):
    """原文 PDF 页对象 -> (级别, 依据)。级别 ∈ {"强","中",None}；None = 行/块边界（噪音）。

    强 = 两侧字母串在原文里**连成一个串**（`a+b` 能被 search_for 命中）—— 原文里根本
         没有这个接缝，词被逐字符 cls 跳变生生判成两段；
    中 = 两串分别在原文里找到、**同基线且水平相接**（-2..6 px）—— 同一行内被切开，
         中间可能还夹着被吞成 `{vN}` 的字符（类②：那半在段表里根本不是文字，任何段文本
         规则都看不见它，只有坐标能看出来）；
    None = 都不是 → 正文正常换行/换栏 → 丢弃。

    刻度**原样复刻**取证脚本 `_seam_geo.py`（921→35 那个读数就是它量的），不另调参。
    """
    lr, rr = RUN2.findall(left or ""), RUN2.findall(right or "")
    if not lr or not rr:
        return None, "两侧凑不出 2+ 字母串"
    a, b = lr[-1], rr[0]
    if page.search_for(a + b):
        return "强", "%s+%s 原文连续" % (a, b)
    for ra in page.search_for(a):
        for rb in page.search_for(b):
            if abs(ra.y1 - rb.y1) < 3 and -2 <= (rb.x0 - ra.x1) <= 6:
                return "中", "%s|%s 同基线相接" % (a, b)
    return None, "%s|%s 行/块边界" % (a[-8:], b[:8])


class GeoVerifier:
    """接缝切点的几何核验器（`scan(verify=…)` 的实参）：原文只开一次、页按需取。

    **核验不了就不过滤**（返回 "未核验" 而不是 None）：拿不到原文、页码越界、页面读崩
    —— 一律退回文本口径并在依据里写明原因。宁可多报让人看一眼，也不要静默少报。
    """

    def __init__(self, pdf):
        if _FITZ is None:
            raise RuntimeError("未安装 pymupdf，无法做几何核验")
        self.doc = _FITZ.open(pdf)

    def __call__(self, page_no, left, right):
        try:
            if not (1 <= page_no <= self.doc.page_count):
                return "未核验", "页码 %d 超出原文范围" % page_no
            return geom_seam(self.doc[page_no - 1], left, right)
        except Exception as exc:                 # 单页读崩不该让门禁整体倒下
            return "未核验", "核验异常: %s" % exc

    def close(self):
        try:
            self.doc.close()
        except Exception:
            pass


# ------------------------------------------------- 版面切点（类② 的唯一覆盖面，opt-in）
# 探针路径默认取同目录的 layout_probe.py；`PDF2ZH_LAYOUT_PROBE` 可覆盖 —— 只是给测试
# 一个不依赖版面 onnx 模型的接缝（与探针自己认 `PDF2ZH_LAYOUT_MODEL` 是同一种做法）。
PROBE = (os.environ.get("PDF2ZH_LAYOUT_PROBE")
         or os.path.join(os.path.dirname(os.path.abspath(__file__)), "layout_probe.py"))


def probe_schema_err(obj):
    """探针 JSON 的形状核验 -> 不符的原因（符合返回 None）。

    [v34] 为什么非要核:**下游直接取字段** —— `layout_cut_finding` 取 `p["page"]`/`p["cuts"]`,
    main 取 `p["cuts"]`/`c.get("keep_side")`。探针一改字段名(或哪天 --json 改成吐对象),
    这里就抛**裸 KeyError/TypeError** —— main 不接这一类, 于是表现为「门禁自己崩了」:
    栈里全是 seg_check 的代码、退出码 1, 与「发现未译段」(也是退 1) 在**退出码上分不开**,
    而它其实是**基础设施故障**(该退 2)。核过形状再往回传, 把这种错钉在"探针读数不可用"
    这一档 —— 与本函数上方「拿不到就报一行不可用, 其他读数一字不动」同一条口径。
    """
    if not isinstance(obj, list):
        return "顶层不是数组（%s）" % type(obj).__name__
    for i, p in enumerate(obj):
        if not isinstance(p, dict) or "page" not in p or "cuts" not in p:
            return "第 %d 项缺 page/cuts" % i
        if not isinstance(p["cuts"], list):
            return "第 %d 项的 cuts 不是数组" % i
        for j, c in enumerate(p["cuts"]):
            if not isinstance(c, dict):
                return "第 %d 项的第 %d 个 cut 不是对象" % (i, j)
    return None


def layout_cuts(pdf, timeout=1800):
    """转调 `tools/layout_probe.py --cuts --json` -> (逐页读数, None) 或 (None, 原因)。

    **走子进程而不是 import**：探针要加载版面 onnx 模型、整篇逐页推理；把它拖进门禁
    进程既慢，又会把「模型缺失」变成门禁自己的故障。这里只要求它把 JSON 吐回来，
    拿不到就报一行「不可用」，门禁的其他读数与判定一字不动。
    """
    if not pdf or not os.path.isfile(pdf):
        return None, "没给原文 PDF"
    if not os.path.isfile(PROBE):
        return None, "没有 %s" % PROBE
    cmd = [sys.executable, "-X", "utf8", PROBE, "--cuts", pdf, "--json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except Exception as exc:                 # 探针起不来 / 超时，都不该让门禁倒下
        return None, "探针跑不动: %s" % exc
    if p.returncode != 0:
        tail = (p.stderr or "").strip().splitlines()
        return None, "探针退出码 %d%s" % (p.returncode, ("：" + tail[-1]) if tail else "")
    for line in reversed((p.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("["):             # 模型加载会往 stdout 吐杂音，只认 JSON 那行
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            bad = probe_schema_err(obj)      # [v34] 形状不符 -> 当"读数不可用"，不带进下游
            if bad:
                return None, "探针 JSON 形状不符（%s）" % bad
            return obj, None
    return None, "探针没有吐出可解析的 JSON"


def layout_cut_finding(pages):
    """逐页读数 -> 一条「提示」级发现；一处切点都没有则 None（不占版面）。"""
    cuts = [(p["page"], c) for p in pages for c in p["cuts"]]
    if not cuts:
        return None
    n_keep = sum(1 for pg, c in cuts if c.get("keep_side"))
    pages_hit = sorted({pg for pg, c in cuts})
    sample = "；".join("p%d %s|%s" % (pg, c.get("left"), c.get("right"))
                      for pg, c in cuts[:5])
    return ("提示", "第%s页" % ",".join(map(str, pages_hit)),
            "版面切点 %d 处（其中 %d 处有半词落进保留区 → 产物里表现为**中英夹花**）：按"
            "生产同一采样口径（字符 bbox 左下角一像素）逐字符算 cls，相邻两字母同基线、"
            "水平相接却 cls 不同 = 引擎在此断段且切点落在词内。**这是类②的唯一覆盖面** ——"
            "段表口径看不见它（那半被记成 `{vN}`，或残片短到过不了 4 字母词面门槛）。"
            "**是候选不是确诊**：只读数、不判定。样本：%s" % (len(cuts), n_keep, sample))


# ---------------------------------------------------------------- 段表读取
def md5_16(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def archived_sidecar(pdf):
    """原文 PDF -> 按内容散列归档的侧车路径（不存在则 None）。

    身份取 PDF **内容**散列（不取文件名：改名/重下同内容仍是同一篇），与
    converter._segflow_paths 的写入端同一口径。
    """
    if not pdf or not os.path.isfile(pdf):
        return None
    p = os.path.join(_ENG.PROFILES["pdf2zh"].sidecar_dir, "pdf-%s.jsonl" % md5_16(pdf))
    return p if os.path.isfile(p) else None


def load_sidecar(path, n_pages=None):
    """侧车 jsonl -> 逐页记录 [{"page": 真实页码, "segs": [...]}]（按页归并，保序）。

    · 真实页码 = pageid + 1（见模块头「页数口径」）；无 pageid 时回落 page。
    · 同一页的多行（图形回调）并进同一条记录 —— 逐行当页会把同页文字回调误判成
      另起一页（backfill_pages 踩过这个坑，实测 Zhang 漂 11 页）。
    · 同页内**完全相同的 (raw, trans) 对去重**：图内文字会被回调两次
      （end_figure 与 end_page 各一次），不去重会让同一段被数两次。
    · [v34] pageid 越界（`n_pages` 给了才算）只**告警不丢弃** —— 见下方推导。
    """
    order, seen, out = {}, {}, []
    n_bad, samples = 0, []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        pid = o.get("pageid")
        # [v34] pageid 是**唯一**权威页码口径（见模块头），但它一直没有边界核验, 且回落路线
        # 的 `page` 还得是整数才能当排序键 —— 侧车里一条坏记录有两种形态:
        #   ① pageid 写成越界值(0 基负数 -> 页 0/-1, 或错位 -> 页数百): 凭空多出一"页",
        #      既不在文献页豁免集里(文献条目会按未译报出), 又会抬高 main 里 n_side =
        #      max(page) —— pdf 读不到页数时这条就是跳页窗口的上界, **窗口整段前移**,
        #      末尾真页的真漏译被静默放行。
        #   ② page 缺失/非整数: 记录 page=None, 末尾 out.sort 拿 None 与 int 比 -> TypeError
        #      （裸崩, 与"发现未译段"同为退 1, 分不出来）。
        # 处置:**只告警、不丢弃**。丢了是往"少报"方向走(漏译被放行), 而这条口径一贯是
        # "宁多报不漏报"; 留着最坏也只是报告里多几行看着不对的页码 —— 且下面点名说清了。
        try:
            pg = (int(pid) + 1) if pid is not None else int(o.get("page"))
        except (TypeError, ValueError):
            n_bad += 1
            if len(samples) < 3:
                samples.append("pageid=%r page=%r" % (pid, o.get("page")))
            continue
        if n_pages and not (1 <= pg <= n_pages):
            n_bad += 1
            if len(samples) < 3:
                samples.append("pageid=%r -> 页 %d（原文 %d 页）" % (pid, pg, n_pages))
        if pg not in order:
            order[pg] = len(out)
            out.append({"page": pg, "segs": []})
            seen[pg] = set()
        slot = out[order[pg]]["segs"]
        for s in (o.get("segs") or []):
            if not isinstance(s, dict):
                continue
            raw, tr = s.get("raw") or "", s.get("trans") or ""
            if (raw, tr) in seen[pg]:
                continue
            seen[pg].add((raw, tr))
            slot.append({"raw": raw, "trans": tr})
    out.sort(key=lambda r: r["page"])
    if n_bad:
        extra = "（另有 %d 条同类未列出）" % (n_bad - len(samples)) if n_bad > len(samples) else ""
        print("⚠ 侧车有 %d 条页记录的页码不可信：%s%s\n"
              "     · 页码非整数（pageid/page 都缺或不是数）的**已丢弃** —— 它连排序键都不成立；\n"
              "     · 页码越界的**已保留**（本口径宁多报不漏报）：它们可能让「文献页豁免」与\n"
              "       「跳页窗口」失准 —— 先核对这份侧车是不是这一篇/这一轮，再读下面的清单。"
              % (n_bad, "；".join(samples), extra), file=sys.stderr)
    return out


def _mtime(path):
    """文件 mtime（秒）；拿不到返回 None（新鲜度核验用，缺了只影响那条警告）。"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def load_pages(sidecar="", pdf="", engine_name="", name=""):
    """按引擎画像取段表 -> (逐页记录, 来源说明, 段表文件 mtime)。拿不到一律抛 IOError。

    第三个返回值供 main() 做**新鲜度核验**（见模块头「段表新鲜度」）：身份（md5）说得
    清「是不是这一篇」，mtime 才说得清「是不是这一轮」。
    """
    prof = _ENG.select(engine_name)
    if prof.seg_source == "tracking_json":
        path = prof.tracking_json(name)
        if not path:
            raise IOError("工作根下没有 translate_tracking.json（next 段表根 = config.toml "
                          "的 [translation].working_dir: %s）" % prof.working_root)
        by_page = {}
        for s in _ENG.read_tracking_segments(path):
            by_page.setdefault(s["page"], []).append(
                {"raw": s["src"], "trans": s["dst"], "err": s["err"]})
        recs = [{"page": pg, "segs": by_page[pg]} for pg in sorted(by_page)]
        # [v34] 与下面侧车路线同一条口径: 空段表不是"通过", 是"判不了"(见那里的推导)
        if not recs:
            raise IOError("段表是空的（%s）—— 一条页记录都没有, 本口径无从判定。"
                          "别当通过看" % path)
        return recs, "段表 %s" % path, _mtime(path)

    arch = archived_sidecar(pdf)
    src = sidecar or arch or prof.sidecar
    if not src or not os.path.isfile(src):
        raise IOError("拿不到侧车（--sidecar 没给、按原文 md5 也找不到归档件: %s）"
                      % (sidecar or pdf or "(空)"))
    how = "侧车 %s" % src
    if not sidecar and not arch:
        how += ("  ⚠ 身份未核验: latest.jsonl 是单文档假设, 换论文即覆盖; 本次没能按原文"
                "内容散列认领归档件 —— 若刚翻过别的篇, 下面的读数可能是别的篇的")
    recs = load_sidecar(src, n_pages=pdf_page_count(pdf))
    # [v34] **空段表不许当通过**。侧车是**逐页追写**的：翻译刚开始(或刚开始就崩)时文件
    # 已存在却一行没有 —— 旧口径下 scan([]) 会给出「提示: 段表为空」+ verdict PASS + 退 0,
    # 也就是**最后一道门禁对着一份什么都没判的段表举手放行**。门禁的语义是"判过了没问题",
    # 不是"没东西可判也算没问题"；判不了就得说判不了(退 2)，与"拿不到段表"同一档。
    if not recs:
        raise IOError("侧车是空的（%s）—— 一条页记录都没有, 本口径无从判定。"
                      "侧车是逐页追写的: 这通常意味着翻译刚起步就中断了, 别当通过看"
                      % src)
    return recs, how, _mtime(src)


# ---------------------------------------------------------------- 扫描
def scan(pages, exempt_pages=(), skip_last=0, n_pages=None, verify=None):
    """逐页记录 -> (findings, verdict, cands, stats, seams)。

    exempt_pages  文献页（**真实页码**）集合：由 post_check.is_ref_page 在原文页上判定
    skip_last     任务跳页数：末尾连续 skip_last 页豁免（与 post_check 同一口径）
    n_pages       总页数（判跳页用；None 时取段表里的最大页）
    verify        接缝切点的**几何核验器**（GeoVerifier 实例，或任何同签名的可调用）：
                  `(真实页码, 前段接缝, 后段接缝) -> (级别, 依据)`。级别为 None → 丢弃
                  （行/块边界）；"未核验" → 保留但标注。**传 None 则不过滤**（纯文本口径，
                  实测 96% 噪音，只作对照/离线用）。

    页内文献区块（post_check.is_ref_heading 定位的标题段及其后各段）在这里豁免 ——
    页级密度判据抓不到「正文末页 + 文献标题 + 条目」的半页块（见模块头 ③）。
    """
    findings, cands, halves, errs = [], [], [], []
    seams, n_seam_raw = [], 0
    n_seg = n_skip = n_exempt = n_refseg = n_notext = 0
    last = n_pages or max((r["page"] for r in pages), default=0)
    for rec in pages:
        pg = rec["page"]
        if skip_last > 0 and pg > last - skip_last:
            n_skip += 1
            continue
        if pg in exempt_pages:
            n_exempt += 1
            continue
        cut = ref_anchor_cut(rec["segs"])
        segs = rec["segs"]
        # 接缝切点（读数，不判定）：只在**参与判定**的段区间里找 —— 文献区块内
        # 的段（i >= cut）是整条保留英文的条目，接缝不具可比性。
        limit = len(segs) if cut is None else cut
        for i in range(max(0, limit - 1)):
            j = seam_of(segs[i].get("raw"), segs[i + 1].get("raw"))
            if not j:
                continue
            n_seam_raw += 1
            j["page"] = pg
            if verify is None:
                j["grade"], j["detail"] = "未核验", "未启用几何核验（--no-geo 或缺原文）"
            else:
                # 第二道：拿原文坐标定性。「词内切点」与「正常换行」在段文本上同形，
                # 过不了这一道的（行/块边界）直接丢弃 —— 实测这一步滤掉 96% 噪音。
                j["grade"], j["detail"] = verify(pg, j["left"], j["right"])
                if j["grade"] is None:
                    continue
            seams.append(j)
        for i, s in enumerate(segs):
            if cut is not None and i >= cut:
                n_refseg += 1
                continue
            v = judge(s["raw"], s["trans"])
            if v == "skip":
                n_notext += 1          # 无可译词面：数学字形段 / 纯 {vN} 段
                continue
            n_seg += 1
            if s.get("err"):
                errs.append((pg, literal(s["raw"])[:60]))
            if v == "untranslated":
                cands.append({"page": pg, "raw": s["raw"], "trans": s["trans"],
                              "head": literal(s["raw"])[:90]})
            elif v == "half":
                halves.append({"page": pg, "ratio": seg_ratio(s["trans"]),
                               "head": literal(s["raw"])[:70]})

    if cands:
        hit = sorted({c["page"] for c in cands})
        sample = "；".join(c["head"] for c in cands[:3])
        findings.append((
            "高", "第%s页" % ",".join(map(str, hit)),
            "段表漏译候选：%d 段有可译词面但译文零汉字（分布在 %d 页）—— 这些段喂进"
            "了引擎却没有中文回来，读者看到的是英文。**是候选不是确诊**：已知良性类"
            "（页1作者/单位/邮箱行、代码/CLI 片段、断段半词残片）也会落在这里，"
            "需逐条确认。样本：%s" % (len(cands), len(hit), sample)))
    elif n_seg:
        findings.append((
            "通过", "全文",
            "段表漏译检查通过：%d 个参与段，无一「有可译词面但译文零汉字」" % n_seg))
    else:
        findings.append(("提示", "全局", "没有参与判定的段（全被豁免，或段表为空）"))

    if seams:
        n_s = sum(1 for s in seams if s["grade"] == "强")
        n_m = sum(1 for s in seams if s["grade"] == "中")
        sample = "；".join("%s|%s" % (s["left"][-10:], s["right"][:10]) for s in seams[:3])
        findings.append((
            "提示", "第%s页" % ",".join(map(str, sorted({s["page"] for s in seams}))),
            "接缝切点 %d 处（几何核验：强 %d = 两串在原文里连续 / 中 %d = 同基线相接；"
            "文本口径原报 %d 处、按原文坐标丢弃 %d 处行/块边界）：相邻两段之间**没有"
            "空白**且两侧都是字母 —— 引擎把**版面检测框的边缘**当成了段落边界（根因见本"
            "工具「接缝切点」注释）。切点落进保留区的那半会按公式留原文（中英夹花），两半"
            "都是文字段的则会各自送翻（译文被拆开/重复）。**是候选不是确诊**：正文正常"
            "断词也会留在「中」档，需逐条确认。样本：%s"
            % (len(seams), n_s, n_m, n_seam_raw, n_seam_raw - len(seams), sample)))
    if halves:
        findings.append((
            "提示", "第%s页" % ",".join(map(str, sorted({h["page"] for h in halves}))),
            "段内汉化率 <%.0f%% 的段（%d 段）：段回来了但基本仍是英文，可能是"
            "「翻了半段」，也可能是有意保留（学名/单位/引用体例）—— 读数交人工，"
            "不参与判定" % (_PC.CJK_FAIL * 100, len(halves))))
    if errs:
        findings.append((
            "中", "第%s页" % ",".join(map(str, sorted({e[0] for e in errs}))),
            "段表标记上游报错的段（%d 段）：样本 %s"
            % (len(errs), "；".join(e[1] for e in errs[:3]))))
    if n_skip:
        findings.append(("通过", "全文",
                         "跳过 %d 页记录（任务 skipLastPages=%d 豁免的末尾页）"
                         % (n_skip, skip_last)))
    if n_exempt:
        findings.append(("通过", "全文",
                         "跳过 %d 页记录（原文文献页：条目规定整条保留英文，零汉字属"
                         "预期；是否被汉化由 post_check 断言 4 把关）" % n_exempt))
    if n_refseg:
        findings.append(("通过", "全文",
                         "跳过 %d 段（页内文献区块：文献区标题之后的条目段，整条保留"
                         "英文属预期；是否被汉化由 post_check 断言 4 把关）" % n_refseg))
    if n_notext:
        findings.append(("通过", "全文",
                         "跳过 %d 段（无可译词面：数学字形段 `t`/`x`/`Pi j` 与纯 "
                         "`{vN}` 公式段 —— 译文与原文逐字相同是引擎「公式不译」的"
                         "预期行为，实测占首版候选 49%%）" % n_notext))

    verdict = "FAIL" if any(f[0] == "高" for f in findings) else "PASS"
    stats = {"segments": n_seg, "untranslated": len(cands), "half": len(halves),
             "errored": len(errs), "skipped_pages": n_skip, "exempt_pages": n_exempt,
             "ref_segments": n_refseg, "nontext_segments": n_notext,
             "seam_raw": n_seam_raw, "seam_cuts": len(seams),
             "seam_geo_strong": sum(1 for s in seams if s["grade"] == "强"),
             "seam_geo_mid": sum(1 for s in seams if s["grade"] == "中"),
             "seam_unverified": sum(1 for s in seams if s["grade"] == "未核验")}
    return findings, verdict, cands, stats, seams


def freshness_finding(lag):
    """段表与 mono 的 mtime 差（秒；段表 - mono）-> 「中」级发现，或 None（在容差内）。

    同一轮里段表比 mono **早**落盘（逐页追写 vs 翻译收尾后生成），实测 -0.6~-11.8 秒；
    |差| 超 LAG_TOL 说明两者不是同一轮，读数可能不反映这份 mono（见模块头）。
    只给「中」（提醒复核），不判 FAIL —— 判 FAIL 会逼人为了消警告去做无谓的重翻。
    """
    if lag is None or abs(lag) <= LAG_TOL:
        return None
    return ("中", "全局",
            "段表与这份 mono 疑似**不同轮**：段表比 mono %s %.0f 秒（同一轮里段表应"
            "**早于** mono 落盘，本机实测同轮为 -0.6~-11.8 秒）。下面的读数可能不"
            "反映这份 mono 的翻译实况 —— 先确认是不是同一轮，再据此出返工单。"
            % ("新" if lag > 0 else "旧", abs(lag)))


def exempt_pages_from(original, n_pages):
    """原文 PDF -> 文献页（真实页码）集合。判据复用 post_check（同源，不重写）。"""
    if not original or not os.path.isfile(original):
        return set()
    try:
        from pypdf import PdfReader
        r = PdfReader(original)
        return {i + 1 for i in range(min(n_pages, len(r.pages)))
                if _PC.is_ref_page(_PC.page_features(r, i))}
    except Exception as exc:
        # [v34] 不吞: 拿不到原文时**返回空集**是安全的(方向是"多报"——文献条目会按
        # 未译段报出来, 是看得见的噪音), 但"为什么豁免没生效"必须说出来 —— 静默的
        # except 会让人以为"这篇没有文献页", 于是拿噪音当故障查。
        print("⚠ 文献页豁免不可用（%s）—— 文献条目段会按未译段报出（噪音, 非故障）"
              % exc, file=sys.stderr)
        return set()


def pdf_page_count(pdf):
    """原文 PDF -> 真实总页数（拿不到/读不了返回 None）。"""
    if not pdf or not os.path.isfile(pdf):
        return None
    try:
        from pypdf import PdfReader
        return len(PdfReader(pdf).pages)
    except Exception:
        return None


# ---------------------------------------------------------------- 报告
def build_report(title, source, findings, verdict, stats, ref_pages, skip_last,
                 lag=None, seams=(), layout=None):
    lines = [
        "# 段表漏译质检报告（门禁 · 局部漏译）",
        "",
        "- 篇目: `%s`" % os.path.basename(title or "(未指定)"),
        "- 段表: %s" % source,
        "- 质检时间: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 跳页豁免: %s" % ("末尾 %d 页" % skip_last if skip_last > 0 else "无"),
        "- 文献页豁免: %s" % (",".join(map(str, sorted(ref_pages))) or "无"),
        "- 段表新鲜度: %s" % (
            "未核验（没给 --mono，或段表无 mtime）" if lag is None
            else "段表比译文%s %.1f 秒%s" % (
                "新" if lag > 0 else "旧", abs(lag),
                "（⚠ 超容差 %.0f 秒，疑不同轮）" % LAG_TOL if abs(lag) > LAG_TOL else "")),
        "- **门禁判定: %s**" % (verdict if verdict != "PASS"
                              else "PASS（本口径内无未译段）"),
        "- 版面切点(类②): %s" % (
            "%d 处（其中 %d 处半词落进保留区）"
            % (stats["layout_cuts"], stats["layout_keep"])
            if "layout_cuts" in stats
            else "未跑（加 `--layout-cuts`；类②切点只有它能看见）"),
        "",
        "## 读数",
        "",
        "| 参与段 | 未译段(判定) | 半译段(提示) | 上游报错段 | 无词面段 | 跳页记录 | 文献页记录 | 文献段记录 | 接缝切点(保留/文本) |",
        "|---|---|---|---|---|---|---|---|---|",
        "| %d | %d | %d | %d | %d | %d | %d | %d | %d/%d |" % (
            stats["segments"], stats["untranslated"], stats["half"],
            stats["errored"], stats["nontext_segments"], stats["skipped_pages"],
            stats["exempt_pages"], stats["ref_segments"],
            stats["seam_cuts"], stats["seam_raw"]),
        "",
        "## 断言结果",
        "",
    ]
    icon = {"高": "🔴", "中": "🟡", "提示": "🔵", "通过": "✅"}
    for level, loc, desc in findings:
        lines.append("- %s **[%s]** %s：%s" % (icon.get(level, "⚪"), level, loc, desc))
    if seams:
        n_s, n_m = stats["seam_geo_strong"], stats["seam_geo_mid"]
        if stats["seam_unverified"]:
            geo = ("⚠ **几何未核验**（原文 PDF 不在手或 --no-geo）：%d 处全是文本口径，"
                   "实测噪音率 96%%，请只当线索看。\n" % stats["seam_unverified"])
        else:
            geo = ("几何核验：文本口径 %d 处 → 保留 **%d** 处（强 %d = 两串在原文里连续 / "
                   "中 %d = 同基线相接），按原文坐标丢弃 %d 处行/块边界。\n"
                   % (stats["seam_raw"], stats["seam_cuts"], n_s, n_m,
                      stats["seam_raw"] - stats["seam_cuts"]))
        lines += [
            "",
            "## 接缝切点清单（提示级，需人工确认）",
            "",
            geo,
            "| 页 | 前段接缝处 | 后段接缝处 | 几何 | 依据 |",
            "|---|---|---|---|---|",
        ]
        for s in seams:
            lines.append("| %d | …%s | %s… | %s | %s |"
                         % (s["page"], s["left"], s["right"],
                            s["grade"], s["detail"]))
    if layout:
        cuts = [(p["page"], c) for p in layout for c in p["cuts"]]
        if cuts:
            lines += [
                "",
                "## 版面切点清单（cls 跳变；类②的唯一覆盖面，需人工确认）",
                "",
                "按**生产同一采样口径**（字符 bbox 左下角一像素）逐字符算 `cls`：相邻两"
                "字母同基线、水平相接却 `cls` 不同 = 引擎在此断段且切点落在**词内**。"
                "与段表无关，故类②（半词进保留区被记成 `{vN}`、或残片太短过不了 4 字母"
                "词面门槛）也在这里现形。「保留侧」标出哪一半落进 `cls<=0` 保留区 ——"
                "那一半会按公式留原文，产物里就是中英夹花（`拓扑gy`）。\n",
                "| 页 | 左 | 右 | x | 基线 y | cls | 保留侧 | 相关框边缘 |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for pg, c in cuts:
                lines.append("| %d | %s | %s | %.1f\\|%.1f | %.1f | %d→%d | %s | %s |"
                             % (pg, c.get("left"), c.get("right"),
                                c.get("x_left", 0), c.get("x_right", 0), c.get("y0", 0),
                                c.get("cls_left", 0), c.get("cls_right", 0),
                                c.get("keep_side") or "—",
                                (c.get("edge") or "—").replace("|", "\\|")))
    lines += [
        "",
        "## 口径说明",
        "",
        "1. **判定级 = 段有可译词面但译文零汉字**。词面门槛 = 剥掉 `{vN}` 后存在连续"
        " 4+ 个字母（滤掉侧车里成堆的数学字形段 `t`/`x`/`Pi j`，实测占首版候选 49%）。"
        "「零汉字」是最**紧**的刻度，但**不是零误报**：实测已知良性类也会落在候选里 ——"
        " 页1 作者/单位/邮箱/URL 行、代码/CLI 片段、断段半词残片（段被切在词中间 ——"
        " 这类自 v30.3 起另有「接缝切点」读数专项列出，见口径说明 6）。"
        "故本表是**候选清单**，需人工确认后才算漏译；放行权归门禁与人。",
        "2. **豁免四档**：① 无「可译词面」的段（纯 `{vN}` 公式段、数学字形段）；"
        "② 原文文献**页**（页级密度判据，复用 post_check.is_ref_page）；③ **页内文献"
        "区块**（区域判据，复用 post_check.is_ref_heading：文献区标题段及其后各段 ——"
        " 抓「正文末页 + 标题 + 条目」这类半页文献块，页级密度在它身上必然低于阈值）；"
        "④ 任务 skipLastPages 末尾页。②③ 的「文献条目保留英文属预期」由 post_check"
        " 断言 4 反向把关。",
        "3. **段表新鲜度**：同一轮里段表比 mono 早落盘（逐页追写 vs 收尾生成），实测"
        " -0.6~-11.8 秒；|差| > 60 秒即判「不同轮」并给中级警告 —— 此时段表读数可能"
        "不反映这份 mono（实测 Rupchin 已交付 mono 汉化率 59.4% PASS，而它的段表属更"
        "晚一轮、会报 127 段候选）。警告解决前不要把候选当漏译。",
        "4. **与 post_check 互补**：post_check 看产物 PDF 的全篇汉化率（v28.46 起为"
        "全篇口径，对局部漏译不敏感，检出率 ≤10.9%）；本工具看段表，逐段判"
        "「进过 LLM 吗、回来了吗」。",
        "5. 未译段的重译走既有第二公里管线（seg_export → 译者 → seg_import → "
        "seg_inject），本工具只出清单，不自己改缓存。",
        "6. **接缝切点（第二个维度，只出读数不判定）**：相邻两段之间**没有空白**且两侧"
        "都是字母 —— 引擎把**版面检测框的边缘**当成了段落边界。根因：断段的唯一条件是"
        " `cls == xt_cls ? 追加 : 新段`（venv 侧 converter.py L469/L475），而 cls 是"
        " **字符 bbox 左下角一个像素**在 YOLO 版面检测框图上采样的类别（L411-416）；"
        "检测框边缘是像素级的，落在词内就逐字符跳变、词被切开。实测两篇五处切点全部"
        "落在框边缘上（Padmaprabhan p3 页眉 `T|opology` 切在 abandon 框左边缘 x=87；"
        "Zhang p9 `TAB|LE I:` 切在 table_caption 框左边缘 x=67，而文字左边界 x≈49 ——"
        "**每一行**头几字符都被判成背景）。危害：切进保留区的那半按公式留原文（中英夹"
        "花），两半都是文字段的各自送翻（译文被拆开/重复）。**判据分两道**：① 文本口径"
        " `seam_of()`（侧车段文本，无空白 + 两侧字母）；② **几何核验** `geom_seam()` 拿"
        "**原文 PDF 坐标**定性 —— 强 = 两串在原文里连续、中 = 同基线相接、丢弃 = 行/块"
        "边界。第二道不可省：侧车不记坐标，「词内切点」与「正文正常换行/换栏」在段文本"
        "上**长得一模一样**，全语料实测文本口径 921 处、几何口径只剩 35 处（**噪音率"
        " 96%**，Johnson 3 页 23 → 0）。**与 tools/seams_report.py 不重叠** —— 那件管"
        "**跨页**接缝（`a.page != b.page`，走缓存手术），本读数管**页内**词内切点。"
        "注意「中」档里仍可能混着正文正常断词，故只提示、不进判定。",
        "7. **版面切点（类②的唯一覆盖面，`--layout-cuts`，默认关）**：接缝口径读的是"
        "**段表文本**，只能看见**两半都成了文字段**的类①切点；类②（半词落进 `cls<=0`"
        " 保留区被记成 `{vN}`，或残片短到过不了 4 字母词面门槛，如 `gy`）在段表里不留痕。"
        "故 `--layout-cuts` 转调 `tools/layout_probe.py --cuts`，按**生产同一采样口径**"
        "（字符 bbox 左下角一像素、`cls_single`）在**原文坐标**上找「相邻两字母、同基线、"
        "水平相接却 `cls` 不同」的断段处，与侧车怎么记那两半无关。实测产物里的可见残留"
        "正是这一类：Zhang p9 `seco` → `最佳与|seco每个类别中的次优结果…`；Padmaprabhan"
        " p5 页眉 → `TOSoFiT：拓扑gy Optimization of Hydraulic…`。代价是要加载版面 onnx"
        " 模型并整篇逐页推理（数十秒级），故只作显式开关；探针/模型缺失时只出一行"
        "「不可用」，**判定与其他读数一字不动**。",
        "",
        "---",
        "",
        "> 由 tools/seg_check.py 自动生成（只读、零 API 成本）。",
    ]
    return "\n".join(lines)


def save_report(content, title):
    os.makedirs(_PC.review_dir(), exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = re.sub(r"[^\w\-]+", "_",
                  os.path.splitext(os.path.basename(title or "seg"))[0])[:40]
    path = os.path.join(_PC.review_dir(), "段表漏译质检_%s_%s.md" % (stamp, base))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def find_latest_mono():
    d = os.path.join(_PC.project_root(), "server", "translated")
    monos = glob.glob(os.path.join(d, "*-mono.pdf"))
    return max(monos, key=os.path.getmtime) if monos else None


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="段表口径的局部漏译门禁（只读、零 API）")
    ap.add_argument("--sidecar", default="", help="侧车 jsonl（1.x）；缺省按 --pdf 的 md5 找归档件")
    ap.add_argument("--pdf", default="", help="原文 PDF；用来定位归档侧车 + 判文献页")
    ap.add_argument("--mono", default="", help="译版 mono.pdf；用来推原文并定位归档侧车")
    ap.add_argument("--engine", default="", help="pdf2zh|next；缺省读 P2Z_ENGINE")
    ap.add_argument("--name", default="", help="next: working 下的篇名目录")
    ap.add_argument("--skip-last", type=int, default=0, metavar="N",
                    help="任务实际配置的 skipLastPages（由服务端传入；手工运行按任务填）")
    ap.add_argument("--json", action="store_true", help="把未译段清单以 JSON 打到 stdout")
    ap.add_argument("--no-report", action="store_true", help="不落报告文件")
    ap.add_argument("--no-geo", action="store_true",
                    help="接缝切点跳过几何核验（只出文本口径；实测噪音率 96%%，仅供离线对照）")
    ap.add_argument("--layout-cuts", action="store_true",
                    help="额外转调 tools/layout_probe.py --cuts 扫「版面切点」"
                         "（类②的唯一覆盖面；要加载版面 onnx 模型、整篇推理，慢）")
    args = ap.parse_args()
    if args.skip_last < 0:
        print("❌ --skip-last 不能为负数", file=sys.stderr)
        return 2

    pdf = args.pdf
    mono = args.mono or (find_latest_mono() if not args.pdf and not args.sidecar else "")
    if not pdf and mono:
        pdf = _PC.derive_original(mono) or ""
    if not pdf and not mono and not args.sidecar and not args.name:
        print("❌ 至少给 --pdf / --mono / --sidecar / --name 之一", file=sys.stderr)
        return 2

    try:
        pages, source, seg_mtime = load_pages(sidecar=args.sidecar, pdf=pdf,
                                              engine_name=args.engine, name=args.name)
    except (IOError, ValueError) as exc:
        print("❌ 拿不到段表: %s" % exc, file=sys.stderr)
        return 2

    n_side = max((r["page"] for r in pages), default=0)
    # [v34] 跳页豁免窗口必须按**原文真实页数**算, 不能用"段表里的最大页"代替:
    # 段表只记**有文字回调**的页, 而末尾被 skipLastPages 跳掉的那几页本来就一个字都没送翻
    # -> 不在段表里。拿段表最大页当总页数, 整个窗口就**往前挪**(实测形态: 原文真 11 页、
    # 段表末页 9, 该豁免 10/11 却去豁免 8/9), 于是正文章节里真正的漏译**在窗口内被静默
    # 放行** —— 门禁放过。post_check 的同一参数用的是译文页特征数(= 原文真实页数), 这里对齐它。
    n_pdf = pdf_page_count(pdf)
    n_pages = n_pdf or n_side
    if args.skip_last > 0 and n_pdf is None:
        print("⚠ 读不到原文页数（--pdf 没给或读不了）—— 跳页窗口按段表最大页 %d 算, "
              "末尾被跳的页若不在段表里, 窗口会前移。要精确请给 --pdf" % n_side,
              file=sys.stderr)
    ref_pages = exempt_pages_from(pdf, n_pages)
    # 接缝切点的几何核验器（第二道）：拿原文坐标把「行/块边界」从候选里滤掉。
    # 拿不到原文 / 没装 pymupdf → verifier 为 None → 退回纯文本口径（报告里会写明）。
    verifier = None
    if not args.no_geo and pdf and os.path.isfile(pdf):
        try:
            verifier = GeoVerifier(pdf)
        except Exception as exc:
            print("⚠ 接缝几何核验不可用（%s）—— 接缝读数退化为文本口径（实测 96%% 噪音）"
                  % exc, file=sys.stderr)
    try:
        findings, verdict, cands, stats, seams = scan(
            pages, exempt_pages=ref_pages, skip_last=args.skip_last,
            n_pages=n_pages, verify=verifier)
    finally:
        if verifier is not None:
            verifier.close()
    # 版面切点（类②的唯一覆盖面，opt-in）：转调探针；拿不到只报「不可用」，不影响判定。
    layout = None
    if args.layout_cuts:
        print("🔎 版面切点扫描中（探针要加载版面模型并整篇推理，慢）…", file=sys.stderr)
        layout, layout_err = layout_cuts(pdf)
        if layout is None:
            findings.append((
                "提示", "全局",
                "版面切点读数不可用（%s）—— 类②切点本轮无覆盖；段表口径的其他读数与判定"
                "不受影响。手工补跑：python tools/layout_probe.py --cuts <原文.pdf>"
                % layout_err))
        else:
            _cuts = [c for p in layout for c in p["cuts"]]
            stats["layout_cuts"] = len(_cuts)
            stats["layout_keep"] = sum(1 for c in _cuts if c.get("keep_side"))
            stats["layout_pages"] = len(layout)
            _lf = layout_cut_finding(layout)
            if _lf:
                findings.append(_lf)
    # 新鲜度核验（身份之外的第二道）：段表 mtime vs 这份 mono 的 mtime
    lag = None
    if seg_mtime is not None and mono and os.path.isfile(mono):
        lag = seg_mtime - _mtime(mono)
    fresh = freshness_finding(lag)
    if fresh:
        findings.insert(0, fresh)

    title = pdf or mono or args.name or "(未指定)"
    print("📋 段表: %s" % source)
    print("   真实页 %d 页 / 参与段 %d 段 / 无词面段 %d 段 / 文献页豁免 %s / 文献段豁免 %d 段"
          % (n_pages, stats["segments"], stats["nontext_segments"],
             sorted(ref_pages) or "无", stats["ref_segments"]))
    print("   接缝切点（页内词内切点，提示级）: %s"
          % ("文本口径 %d 处 → 几何保留 %d 处（强 %d / 中 %d）"
             % (stats["seam_raw"], stats["seam_cuts"],
                stats["seam_geo_strong"], stats["seam_geo_mid"])
             if stats["seam_unverified"] == 0 or not stats["seam_cuts"]
             else "文本口径 %d 处（⚠ 几何未核验，实测 96%% 噪音）" % stats["seam_cuts"]))
    if "layout_cuts" in stats:
        print("   版面切点（类②覆盖，cls 跳变）: %d 处（其中 %d 处半词落进保留区 → 中英夹花）"
              % (stats["layout_cuts"], stats["layout_keep"]))
    print("   段表新鲜度: %s" % ("未核验（缺 --mono）" if lag is None
                                else "比 mono %s %.1f 秒" % ("新" if lag > 0 else "旧",
                                                             abs(lag))))
    for level, loc, desc in findings:
        mark = {"高": "🔴", "中": "🟡", "提示": "🔵", "通过": "✅"}.get(level, "⚪")
        print("%s [%s] %s: %s" % (mark, level, loc, desc))
    if cands:
        print("")
        print("未译段清单（重译入口: tools/seg_export.py --pages %s --pdf <原文>）"
              % ",".join(map(str, sorted({c["page"] for c in cands}))))
        for c in cands:
            print("  p%-4d %s" % (c["page"], c["head"]))
    if args.json:
        print(json.dumps({"verdict": verdict, "source": source, "stats": stats,
                          "untranslated": cands, "seam_cuts": seams,
                          "layout_cuts": layout or []},
                         ensure_ascii=False, indent=1))
    if not args.no_report:
        print("📝 报告: %s" % save_report(
            build_report(title, source, findings, verdict, stats,
                         ref_pages, args.skip_last, lag, seams, layout), title))
    print("🟢 门禁判定: PASS" if verdict == "PASS"
          else "🔴 门禁判定: FAIL（存在未译段）")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
