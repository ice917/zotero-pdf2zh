# -*- coding: utf-8 -*-
"""
tools/post_check.py —— 翻译后质检门禁（零 API 成本，只读）

模块职责：
  翻译完成后，将 mono 译文与原始 PDF 逐页对比，执行五项自动断言，
  把"翻车但没人发现"变成"翻车自动报警"。断言项（参照 BabelDOC
  ACL 2026 论文的 Untranslated Blocks 质检指标设计）：

    1. 汉化率断言    全篇口径（v28.46 起）：参与页（原文有实质内容、非文献
                     页、非跳页）的译文**合计**汉字占比低于 WHOLE_DOC_CJK_FAIL
                     → 整篇翻译缺失/大段失败。逐页汉字占比过低不再直接判失败，
                     而是作为"零汉字页"清单以**提示**级列出供人工核对。
                     原因：PDF 文本层分不清"引擎按规定不翻的整页表格/公式页"
                     与"卡死漏译页"（两者在译文中都表现为与原文逐字相同），
                     故自动判定上移到全篇，逐页异常交人工 —— 这与原始参照的
                     BabelDOC "Untranslated Blocks **Count**"（计数而非判定）
                     以及 CAT 业界把 "Target same as Source" 默认关闭同源。
                     定阈的蒙特卡洛依据见 WHOLE_DOC_CJK_FAIL 注释
    2. 引用完整性    原文与译文的 [n] 引用标号数量逐页对账，
                     偏差超限 → 版面错乱/文献混排（参考文献页事故
                     的典型特征就是标号丢失或重复）
    3. 占位符残留    译文中残留 {vN} 公式占位符 → 公式回填失败
    4. 文献区禁汉化  原文的参考文献页（编号制行首 [n] 条目密集，或
                     作者-年份制条目密集，判据见 REF_DENSITY）
                     在译文中被中文化（人名/期刊名/标题被翻成中文）
                     → 学术文献表不可用。文献条目必须整条原样保留。
    5. 渲染残渣      译文中出现"原文整篇都没有的"标签残渣（如渲染层
                     内部标记 </style…>、<span class=…> 被当页面文字
                     画了出来）→ 读者会直接在版面上看到这些字符。
                     判据见 RESIDUE_TAG / check_residue：**差分**判定，
                     正文里合法出现的 <EOS> 这类裸 token 名不算残渣。

  特别处理：参考文献条目按规定整条保持英文，所以"文献页译文汉字为零"
  是预期结果而非翻译失败——断言4 反向把关（条目里出现汉字才算事故），
  断言1 对该类页面直接豁免（且不参与全篇合计）。skipLastPages 跳过的
  末尾页同理豁免。

  结论写入 server/translated/review/翻译后质检_*.md，并给出
  总体门禁判定：PASS（可交付）/ FAIL（存在高危问题需处理）。

设计原则：
1. **只读**：不修改任何 PDF 与翻译产物，仅输出报告文件。
2. **页对齐假设**：pdf2zh 的 mono 输出与原 PDF 页数一致
   （skipLastPages 跳过的页保留原文仍在输出中，已实测验证）。
   页数不一致时按较小者对比并在报告中注明。
3. **零依赖增量**：只用 venv 里已有的 pypdf。
4. **退出码语义**：PASS=0，FAIL=1，供脚本/自动化联动判断。

用法：
  python tools/post_check.py                        # 自动配对最新 mono 与原文
  python tools/post_check.py <mono译文.pdf>         # 指定译文（自动推导原文）
  python tools/post_check.py <译文> --original <原文.pdf>
"""

import argparse
import datetime
import glob
import logging
import os
import re
import sys
import warnings

from pypdf import PdfReader

# 老 PDF 字体表(CMap)损坏的解析警告对质检无贡献，静音
logging.getLogger("pypdf").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

# ---------------------------------------------------------------- 常量
REVIEW_DIRNAME = "review"

CJK_FAIL = 0.05          # [自研补丁 2026-09-20] 降级为**逐页读数**阈值: 参与页
                         # 译文汉字占比低于该值 → 该页记入"零汉字页"清单
                         # (提示级), 不再直接判 FAIL —— 判定改用 WHOLE_DOC_CJK_FAIL
# [自研补丁 2026-09-20] 第一断言改为**全篇口径**的阈值。
# 读数依据 (D:\zotero-pdf2zh-eval\logs\pilot_tbl\threshold_opt.py, 蒙特卡洛 +
# 代价敏感决策): H0 = 15 篇实测全篇读数 0.3644~0.8082 (min=Mandujano, 该篇 33 个
# 参与页里 7 页整页表格零汉字, 全篇仍 0.3644); H1 = 故障模型两臂 (页级截断
# "卡死/跳页" 与乘性完成度)。结论: 该统计量对"局部漏译"检出率≈0 —— τ 推到零
# 误报上限 0.3644 时"翻一半卡死"的检出率也只有 10.9%, 要检出 50% 需 τ≈0.44 而
# FPR 达 26.7% (分母被未翻页的大 alnum 主导, 故 whole 对"翻了多少页"不敏感)。
# 因此本断言的语义是"整篇零翻译闸门", 局部异常一律降级为提示交人工。
# 阈值取 H0 最小值的一半 = 2 倍样本外余量, 代价只比严格最优 (τ=0.35) 差 17%。
WHOLE_DOC_CJK_FAIL = 0.18
MIN_SUBSTANTIAL = 500    # 原文页可提取字符数超过该值才算"实质内容页"
CITE_TOL_ABS = 5         # 引用标号数量对账的绝对容差
CITE_TOL_REL = 0.2       # 引用标号数量对账的相对容差（20%）
# [自研补丁 2026-09-19] 第四断言(文献区禁汉化)口径
REF_ZH_MIN = 2           # 单页"被汉化的文献条目"数达到该值 → 该页文献被翻成中文
REF_DENSITY = 2.5        # 文献页判据: 原文每千字的**文献条目**数达到该值。
                         # 条目按引用体例取其一: 编号制数行首 [n], 作者-年份制
                         # 数"姓名 (年)"(见 REF_AY) —— 两种体例同阈值。
                         # 实测 EgoPhys: 纯文献页 4.31/4.46/4.60, 而"正文末页
                         # + 文献开头"的第 9 页只有 1.77 —— 用密度而不是"条目数
                         # >= 3"才能把那半页正文排除在文献判据之外(否则正文的
                         # 中文会被当成文献被汉化)。
                         # 不变量: 判据一律取**原文页**的密度。译文页不可用 ——
                         # 条目一旦被汉化/换成中文标点, 行首 [n] 会被 pypdf 拆散,
                         # 实测 CLAP 第15页原文密度 3.18 而译文只剩 0.40。
REF_ZH_WINDOW = 200      # 条目块截断长度(字符): 一个文献条目极少超过 200 字符,
                         # 截断可防止"文献区末尾 + 同页后续正文"被算进最后一条。
# [自研补丁 2026-09-19] 作者-年份制文献页(整表无行首 [n])的判据与汉化计数。
# 事故: Wang 2026 第15页是"R. Olfati-Saber and R. M. Murray (2004). ..."
# 体例, 行首 [n] 密度恒为 0 → 既不被识别为文献页(断言1 误判"翻译缺失"),
# 也不受断言4 保护(硅基首轮把整表条目汉化仍 PASS)。
# 实测分离度: Wang 文献页 (年)密度 2.93/3.58, 其余 11 篇 PDF 正文页最高 0.7,
# 编号制文献页恒为 0.00 —— 与 REF_DENSITY 同阈值即可分离。
REF_AY = re.compile(r"(?m)^\s*(?:[A-Z]\.\s*){1,4}[A-Z][A-Za-z\-'\u4e00-\u9fff]+"
                    r"[\s\S]{0,80}?\((?:19|20)\d{2}[a-z]?\)")
                         # 原文侧: "R. Olfati-Saber and R. M. Murray (2004)."
                         # 行首姓名 + 其后 80 字符内出现年份 → 一个条目。
                         # 正文引用"(Olfati-Saber and Murray, 2004)"不在此列
                         # (它不是行首姓名), 实测正文页密度 ≤0.7。
AY_AUTHOR = re.compile(r"(?:[A-Z]\.\s*){1,4}[A-Z\u4e00-\u9fff]"
                       r"[A-Za-z\-'\u4e00-\u9fff]+")
                         # 译文侧姓名锚: LLM 改写文献条目时姓名(首字母缩写+姓)
                         # 最常原样保留(实测 "R. Olfati-Saber 与 R. M. Murray"),
                         # 故用它切块。姓首字允许汉字(姓被汉化后仍能锚住
                         # "R. 奥法蒂"), 但不允许小写拉丁开头 —— 否则英文正文
                         # 里的 "R. and"、"a. b" 之类会被误当姓名。
REF_AY_SPAN_CAP = 400    # 两姓名锚之间的跨度上限(字符): 超长视为正文夹缝,
                         # 不是条目内容, 不计入(防止正文中文被当文献汉化)。
PLACEHOLDER = re.compile(r"\{v\d+\}")   # pdf2zh 公式占位符 {v1} {v2} ...
REF_ENTRY = re.compile(r"(?m)^\s*\[\d{1,3}\]")
# [自研补丁 2026-09-24] 文献区**区域**判据（第三种体例 / 半页文献块）。
# 事故: Johnson 第3页是「正文末段 + LITERATURE CITED 标题 + 5 条条目」——页级
# 密度判据（REF_DENSITY）在此必然失效: 该页 5834 字符里文献只占末尾 5 行,
# 编号制 0.00 / 作者-年份制 0.00 / 连"姓, 名首字母"密度也只有 1.03, 全在
# 阈值 2.5 之下 → is_ref_page 判 False。而这一页的条目形态是**第三种体例**:
#   GOLD, H. S. 1959. Distribution of some ... Jour. Elisha Mitchell Sci. Soc.
# 行首无 [n], 年份也不在括号里（在作者名之后、无括号）→ REF_ENTRY 与 REF_AY
# 两臂都抓不到。三种体例的共同点是**都有一行标题**（LITERATURE CITED /
# REFERENCES / BIBLIOGRAPHY…）, 故区域判据改用「标题锚点」定位, 不再依赖密度。
#
# 只认「整段就是标题」且标题之后不得紧跟小写字母 —— 后者是最主要的误报源:
#   "References to earlier work show..." / "Bibliography of the genus" 这类
# 正文句子会以标题词开头, 但后面跟的是小写词。
# 收得**很紧**（整段只剩标题，可带一个句点/冒号）是实测逼出来的: 放宽到
# "标题词打头即可"时, 全语料出了两类误报, 而误报的代价是**静默藏住漏译**
# （标题之后的正文段全被豁免）:
#   · 表头行 `Reference | Dim. | Filter type | ...`（Melhani 第4页，行位 6%）
#   · 图例行 `ReferenceEKF estimate`（Melhani 第32页；"Reference" 后面接的是
#     字母 E, 连词边界都没有）
# 收紧后这两类都落空。代价: 若某引擎把标题与首条条目并成一段
# （`LITERATURE CITED GOLD, H. S. ...`），该段不被认作锚点 —— 结果是**多报**
# 一个漏译段（噪音，看得见），而不是少报（危险）。本机 16 篇侧车实测无此形态。
# 刻意**不收**光板的 LITERATURE —— "LITERATURE REVIEW" 是正文章节标题,
# 把它当文献区锚点会把该页后半的正文段全部豁免掉。
REF_HEADING = re.compile(
    r"^\s*(?i:LITERATURE\s+CITED|REFERENCES?\s+CITED|REFERENCES?|BIBLIOGRAPHY"
    r"|WORKS\s+CITED|参考文献|引用文献)(?![A-Za-z\u4e00-\u9fff])\s*[.:：]?\s*$")
# 注: 本判据只给**区域级**消费者用（tools/seg_check.py 的页内文献段豁免）。
# 刻意不接进 is_ref_page —— 那是**页级**判据, 供断言1/断言4 用: 一个"正文占
# 九成、末尾挂文献块"的页一旦被当成文献页, 断言4 会在该页正文的中文上跑
# count_zh_ay_blocks（姓名锚切块）而误报"文献区被汉化", 断言1 也会整页豁免。
# 页级与区域级是同一件事的两个粒度, 判据文本同源, 阈值语义各自成立。

# [自研补丁 2026-09-20] 引用标号判据要容忍**提取空隙**: 同一份 PDF 的两侧
# (原文 vs 译文)提取形态可以不一样 —— 原文里 `[22]` 是公式字体里的字形对象,
# 提取出来常带空格(`[ 22]`, 实测 SILAGE 第2页 8 处全是 `[ ` 开头), 而回填后的
# 译文写的是字形原值 `[22],`(无空格), 提取出来是 `[22]`。旧判据只认后者, 于是
# 逐页对账变成"原文 69 / 译文 88, 差 19 判 FAIL"—— 差的不是引用, 是空格。
# 放宽的是**两侧同样放宽**(同一正则对原文与译文各跑一遍), 不是只放过译文;
# 两侧形态一旦一致(都不带空隙)行为与旧判据完全相同。
CITE_ANY = re.compile(r"\[\s*\d{1,3}\s*\]")
CJK = re.compile(r"[\u4e00-\u9fff]")
# [自研补丁 2026-09-19] 第五断言(渲染残渣)口径。
# 事故: 官方 BabelDOC 2.9.0 的产物第 2 页真印出了 `</style\x01id='25'>`
# (16 个码点, 中间夹控制符 U+0001) —— IL(行间注)标签没被回填, 就被当成
# 页面文字画了出来。我方 13 篇既有产物(含 34 页专著)扫描 0 处, 故判据可上线。
#
# 只认三类"标签形态", **刻意排除** <EOS>/<pad> 这类裸 token 名 —— 后者在
# NLP 论文正文里合法出现(Vaswani 原文 30 处), 算成残渣就是误伤。
# 又因为"原文自己就有"的标签形态同样可能是正文内容(讲标记语言的论文),
# 故最终判据是**差分**: 只报"译文有、而原文整篇没有"的形态。
# 归一化后比对(去空白与控制符, 见 RESIDUE_BLANK), 以防渲染把属性里的空白/
# 控制符换掉后绕过同形判断。
#
# 反面教训(实测): "控制符出现在译文即 FAIL"这条更简单的判据**不可用** ——
# 控制符是字体子集/编码的抽取产物, 原文自己就有(Proximity 原文 576 处;
# Zhang 原文 73 处 = 译文 73 处; Wang 原文 0 → 译文 18), 会把我方合格产物
# 大面积误判为 FAIL。
RESIDUE_TAG = re.compile(r"</[A-Za-z][A-Za-z0-9]{0,24}[^<>]{0,60}>"
                         r"|<[A-Za-z][A-Za-z0-9]{0,24}[^<>]{0,60}=[^<>]*>"
                         r"|<[A-Za-z][A-Za-z0-9]{0,24}[^<>]{0,60}/>")
RESIDUE_BLANK = re.compile(r"[\s\x00-\x1f\x7f]+")


def project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def review_dir():
    return os.path.join(project_root(), "server", "translated", REVIEW_DIRNAME)


# ---------------------------------------------------------------- 页面特征
def count_zh_ref_blocks(text):
    """统计"被汉化的文献条目"条数。

    [自研补丁 2026-09-19] 按行首 [n] 切块: 每块从 [n] 起, 到下一个 [n] 或
    块首 + REF_ZH_WINDOW 截断为止, 块内含汉字即该条被汉化。截断是必需的
    —— 文献区末尾那条后面若紧跟同页正文, 不截断会把正文的中文算进最后
    一条(EgoPhys 第9页: 文献开头 + 正文末尾同页)。
    """
    hits = list(REF_ENTRY.finditer(text))
    n = 0
    for k, m in enumerate(hits):
        end = hits[k + 1].start() if k + 1 < len(hits) else m.start() + REF_ZH_WINDOW
        if CJK.search(text[m.start():end]):
            n += 1
    return n


def count_zh_ay_blocks(text):
    """统计"被汉化的作者-年份制文献条目"条数。

    [自研补丁 2026-09-19] 作者-年份制文献表（见 REF_AY）没有行首 [n]，条目
    被整条改写后编号/年份也不复存在 —— 行首 [n] 判据恒为 0，断言4 在此类
    页面上形同虚设（Wang 2026 第15页注入前条目被整条汉化仍判 PASS）。
    改以**译文中幸存的姓名锚**切块：相邻两锚之间的跨度即两条目之间的内容，
    含汉字则前一条被汉化。首锚之前 / 末锚之后不计 —— 那是正文或附录，不是
    条目内容（Wang 第15页文献区之后就是中文的附录证明）。
    """
    hits = list(AY_AUTHOR.finditer(text))
    n = 0
    for a, b in zip(hits, hits[1:]):
        span = text[a.end():b.start()]
        if len(span) > REF_AY_SPAN_CAP:
            continue
        if CJK.search(span):
            n += 1
    return n


def page_residues(text):
    """该页出现的"标签残渣"形态（归一化后），供第 5 断言做差分比对。

    [自研补丁 2026-09-19] 归一化只去空白与控制符：判据关心的是"哪种标签
    形态出现在这里"，属性里的空白/控制符差异（渲染可能改写）不该让同一种
    形态被当成两种。
    """
    return [RESIDUE_BLANK.sub("", m.group(0))
            for m in RESIDUE_TAG.finditer(text)]


def text_features(text, page_no):
    """由一段"页文本"算质检特征。

    [自研补丁 2026-09-19] 从 page_features 里抽出来, 为的是让
    tools/pre_render_check.py 能对**回锚后的译文**(out/<name>.imported.json,
    此时还没有 PDF) 用同一套口径算特征。文献区禁汉化这道闸门前移到 render
    之前, 靠的就是"同一份计数代码 + 两种文本来源" —— 否则两边口径迟早漂移,
    又变成翻前翻后各说各话。
    """
    feat = {"page": page_no, "chars": 0, "cjk": 0, "alnum": 0, "cites": 0,
            "ref_entries": 0, "ref_density": 0.0,
            "ref_ay_entries": 0, "ref_ay_density": 0.0,
            "ref_zh_blocks": 0, "ref_zh_blocks_ay": 0,
            "placeholders": 0, "residues": [], "error": None}
    feat["chars"] = len(text.strip())
    feat["cjk"] = len(CJK.findall(text))
    feat["alnum"] = len(re.findall(r"[A-Za-z0-9]", text))
    feat["cites"] = len(CITE_ANY.findall(text))
    feat["ref_entries"] = len(REF_ENTRY.findall(text))
    feat["ref_ay_entries"] = len(REF_AY.findall(text))
    if feat["chars"]:
        feat["ref_density"] = feat["ref_entries"] * 1000.0 / feat["chars"]
        feat["ref_ay_density"] = (feat["ref_ay_entries"] * 1000.0
                                  / feat["chars"])
    feat["ref_zh_blocks"] = count_zh_ref_blocks(text)
    feat["ref_zh_blocks_ay"] = count_zh_ay_blocks(text)
    feat["placeholders"] = len(PLACEHOLDER.findall(text))
    feat["residues"] = page_residues(text)
    return feat


def page_features(reader, idx):
    """提取第 idx 页（0 基）的质检特征，失败返回 error"""
    try:
        text = reader.pages[idx].extract_text() or ""
    except Exception as exc:
        feat = text_features("", idx + 1)
        feat["error"] = str(exc)[:120]
        return feat
    return text_features(text, idx + 1)


def cjk_ratio(feat):
    total = feat["cjk"] + feat["alnum"]
    return feat["cjk"] / total if total else 0.0


def is_ref_page(feat):
    """该页（原文特征）是否为文献页：编号制或作者-年份制条目密集即算。

    [自研补丁 2026-09-19] 两种体例共用 REF_DENSITY。判据只认原文页 ——
    译文页的条目被汉化/换标点后, 行首 [n] 与年份都会被 pypdf 拆散。
    """
    return max(feat["ref_density"], feat["ref_ay_density"]) >= REF_DENSITY


def is_ref_heading(text):
    """这段文字是否为「文献区标题」（见 REF_HEADING）。

    [自研补丁 2026-09-24] 文献区判据的**区域版**: is_ref_page 回答"整页是不是
    文献页"（靠条目密度）, 本函数回答"这一行/这一段是不是文献区的**开头**"。
    半页文献块（正文末页 + 文献标题 + 条目）只有后者能定位 —— 那是 Johnson
    第3页的情形, 页级密度必然低于阈值。

    与 is_ref_page 判据文本同源、粒度不同: 同属"文献区"这一件事, 不各写一套。
    消费者: tools/seg_check.py（标题之后的段落整条保留英文属预期, 豁免漏译判定）。
    """
    return bool(REF_HEADING.match(text or ""))


# ---------------------------------------------------------------- 断言
def check_ref_zh(orig_feats, trans_feats):
    """第 4 断言「参考文献区不得汉化」→ 返回 findings 列表。

    [自研补丁 2026-09-19] 从 run_checks 里抽出来, 让**渲染前预检**
    (tools/pre_render_check.py) 与**渲染后门禁**跑的是同一段代码。前移这道闸门
    的意义: 这 5 篇历史 FAIL 当初全是"PDF 出来了才发现文献区被汉化", 而重出
    PDF 要占服务端一轮 —— 在 render 之前就判死, 那一轮直接省掉。

    只在原文侧判文献页(见 is_ref_page), 译文侧只负责数"被汉化的条目"。
    """
    zh_ref_pages, zh_ref_why, n_ref_pages = [], [], 0
    for o, t in zip(orig_feats, trans_feats):
        if o["error"] or t["error"] or not is_ref_page(o):
            continue
        n_ref_pages += 1
        zh_n = t["ref_zh_blocks"]
        if o["ref_ay_density"] >= REF_DENSITY:
            zh_n += t["ref_zh_blocks_ay"]
        if zh_n >= REF_ZH_MIN:
            zh_ref_pages.append(t["page"])
            zh_ref_why.append(f"第{t['page']}页({zh_n}条)")
    if zh_ref_pages:
        return [(
            "高", f"第{','.join(map(str, zh_ref_pages))}页",
            "文献区汉化断言失败：原文的参考文献条目在译文中被翻译成中文"
            f"（{('、'.join(zh_ref_why))}）。文献条目必须整条原样保留——"
            "人名/标题/期刊名被汉化后文献表不可用，且无法据此回溯原文")]
    if n_ref_pages:
        return [("通过", "全文",
                 f"文献区禁汉化检查通过：{n_ref_pages} 个文献页的条目均保持原样")]
    return []


def check_residue(orig_feats, trans_feats):
    """第 5 断言「渲染残渣」→ 返回 findings 列表。

    [自研补丁 2026-09-19] 缘起：官方 BabelDOC 2.9.0 隔离评估时，产物第 2 页
    真印出了 `</style\\x01id='25'>`，而当时那四项断言**全都抓不到** ——
    汉化率只看"汉字够不够"，引用/占位符/文献区都不看这类标记。

    判据是**差分**而非绝对计数：先收集原文整篇出现过的标签形态，再报"译文有
    而原文没有"的那些。原因是标签形态本身可能就是正文内容 —— NLP 论文里的
    `<EOS>`/`<pad>`（Vaswani 原文 30 处）、讲标记语言的论文里的 `<div class=…>`
    都会合法出现，绝对计数会把它们判成事故。
    实测分离度：Vaswani 原文 30 处 / 我方译文 2 处（差分负）→ 不触发；
    官方该篇原文 0 处 / 官方译文 1 处（差分正）→ 触发。
    """
    known = set()
    for f in orig_feats:
        if not f["error"]:
            known.update(f["residues"])
    pages, samples, total = [], [], 0
    for t in trans_feats:
        if t["error"]:
            continue
        novel = [r for r in t["residues"] if r not in known]
        if novel:
            total += len(novel)
            pages.append(t["page"])
            samples.extend(novel)
    if pages:
        uniq = sorted(set(samples))
        shown = "、".join(f"`{s}`" for s in uniq[:5])
        more = f"等 {len(uniq)} 种" if len(uniq) > 5 else ""
        return [(
            "高", f"第{','.join(map(str, pages))}页",
            f"渲染残渣断言失败：译文出现 {total} 处原文中不存在的标签/内部标记"
            f"残渣（{shown}{more}）—— 渲染层把内部标记当成页面文字画了出来，"
            "读者会直接在版面上看到这些字符")]
    return [("通过", "全文", "渲染残渣检查通过：译文未出现原文之外的标签残渣")]


def run_checks(orig_feats, trans_feats, skip_last=0):
    """
    五项断言。返回 (findings, verdict)
      findings: [(级别, 页码或全局, 描述), ...]  级别: 高/中/提示/通过
      verdict:  "PASS" / "FAIL"
      skip_last: 任务实际配置的 skipLastPages(由服务端传入); 仅末尾连续
                 恰好 skip_last 页且确为原文保留(无汉字)才豁免, 无参数不豁免。
    """
    findings = []
    n = min(len(orig_feats), len(trans_feats))

    # ---------- 1. 汉化率断言 ----------
    # [自研补丁 2026-09-03] 豁免只认任务实际跳页数(报告 🔴5): 旧逻辑仅凭
    # 译文特征(行首 [n] 密集 + 无汉字)豁免, LLM 对文献行原样回显英文、或
    # 跳页数小于实际文献区页数时, 失败页会被静默放行。现在: 末尾
    # skip_last 页且无汉字 → 预期豁免; 豁免区外的无汉字页一律判失败。
    # [自研补丁 2026-09-19] 追加文献页豁免: 文献条目按规定整条保留英文,
    # 该页译文"汉字为零"是预期结果, 不判翻译缺失 —— 反过来由第 4 断言
    # (文献区禁汉化)把关。EgoPhys 第10/11/12页曾因此误报; 作者-年份制
    # 文献页(Wang 第15页, 无行首 [n])同理, 见 is_ref_page。
    # [自研补丁 2026-09-20] 判定上移"全篇", 逐页降级为提示。
    # 事故/依据: Mandujano 2026 第3,9,10,11,12,13,14页是 Table 10.1/10.2
    # (+continued) 整页大表格, 引擎按版面分类不翻表格文本 → 译文 7 页零汉字,
    # 逐页口径判 FAIL。对照试点(2026-09-20)查明: ① 上游 --translate-table-text
    # 在 BabelDOC 0.6.2 已退役(translation_config 收 table_model 即置 None,
    # RapidOCRModel 变 no-op), 透传无效; ② 1.x 同样不翻表格(pdf2zh/high_level.py
    # 的 vcls 含 "table"), 2026-09-13 的质检报告已逐页相同 → **不是换引擎的回退**;
    # ③ PDF 文本层无法区分"按规定不翻"与"卡死漏译"(两者都表现为与原文逐字相同),
    # 故原逐页判据的假阳性无法靠加豁免修掉。建模定阈见 WHOLE_DOC_CJK_FAIL。
    zero_pages, kept_pages, ref_pages = [], [], []
    w_cjk = w_alnum = used = 0
    for i in range(n):
        o, t = orig_feats[i], trans_feats[i]
        if o["error"] or t["error"]:
            continue
        if o["chars"] < MIN_SUBSTANTIAL:
            continue  # 原文页无实质内容（封面/图表页），不参与断言
        cjk_low = cjk_ratio(t) < CJK_FAIL
        if cjk_low and is_ref_page(o):
            ref_pages.append(t["page"])    # 文献页, 英文原样保留属预期
            continue
        if skip_last > 0 and i >= n - skip_last and cjk_low:
            kept_pages.append(t["page"])   # 任务明确跳过的末尾页, 原文保留
            continue
        used += 1
        w_cjk += t["cjk"]
        w_alnum += t["alnum"]
        if cjk_low:
            zero_pages.append(t["page"])
    whole = w_cjk / float(w_cjk + w_alnum or 1)
    if used and whole < WHOLE_DOC_CJK_FAIL:
        findings.append((
            "高", "全文",
            f"汉化率断言失败（全篇口径）：{used} 个参与页合计汉字占比 "
            f"{whole:.1%} < {WHOLE_DOC_CJK_FAIL:.0%}，整篇翻译缺失或大段失败"
            + (f"（其中第{','.join(map(str, zero_pages))}页汉字占比"
               f"<{CJK_FAIL:.0%}）" if zero_pages else "")))
    elif used:
        findings.append((
            "通过", "全文",
            f"汉化率对账通过（全篇口径）：{used} 个参与页合计汉字占比 "
            f"{whole:.1%}（阈值 {WHOLE_DOC_CJK_FAIL:.0%}）"))
        if zero_pages:
            findings.append((
                "提示", f"第{','.join(map(str, zero_pages))}页",
                f"译文汉字占比<{CJK_FAIL:.0%} 的页（{len(zero_pages)} 页）："
                "可能是引擎按规定不翻的整页表格/公式页，也可能是漏译页 —— "
                "PDF 文本层分不清，请对照原文人工确认"))
    else:
        findings.append((
            "提示", "全局",
            "无参与汉化率断言的页（原文无实质内容页，或全为文献/跳页），跳过"))
    if kept_pages:
        findings.append((
            "通过", f"第{','.join(map(str, kept_pages))}页",
            f"原文保留页（任务 skipLastPages={skip_last} 豁免的末尾页），预期行为"))
    if ref_pages:
        findings.append((
            "通过", f"第{','.join(map(str, ref_pages))}页",
            "文献页条目原样保留（译文汉字占比低属预期，条目是否被汉化由第 4 断言把关）"))
    if skip_last <= 0:
        findings.append((
            "提示", "全局",
            "未传入 --skip-last：不豁免任何'英文保留页'，若该任务确有跳页"
            "请由服务端传入实际跳页数（手工运行可加 --skip-last N）"))

    # ---------- 2. 引用完整性对账 ----------
    o_total = sum(f["cites"] for f in orig_feats if not f["error"])
    t_total = sum(f["cites"] for f in trans_feats if not f["error"])
    if o_total > 0:
        diff = abs(o_total - t_total)
        if diff > max(CITE_TOL_ABS, o_total * CITE_TOL_REL):
            # 定位偏差最大的页，方便人工定位
            worst = sorted(
                ((i, abs(orig_feats[i]["cites"] - trans_feats[i]["cites"]))
                 for i in range(n)
                 if not orig_feats[i]["error"] and not trans_feats[i]["error"]),
                key=lambda x: -x[1])[:3]
            worst_str = "、".join(
                f"第{orig_feats[i]['page']}页(差{d})" for i, d in worst if d > 0)
            findings.append((
                "高", "全文",
                f"引用完整性断言失败：原文引用标号 {o_total} 个，译文仅 {t_total} 个"
                f"（差 {diff}）。这是版面错乱/文献混排的典型特征"
                + (f"，偏差最大：{worst_str}" if worst_str else "")))
        else:
            findings.append((
                "通过", "全文",
                f"引用完整性对账通过：原文 {o_total} 个 / 译文 {t_total} 个"
                f"（容差内）"))

    # ---------- 3. 占位符残留 ----------
    ph_total = sum(f["placeholders"] for f in trans_feats if not f["error"])
    ph_pages = [f["page"] for f in trans_feats
                if not f["error"] and f["placeholders"] > 0]
    if ph_total > 0:
        findings.append((
            "高", f"第{','.join(map(str, ph_pages))}页",
            f"占位符残留断言失败：译文残留 {ph_total} 个 {{vN}} 公式占位符，"
            f"公式回填失败，该页公式位置显示的是占位符而非公式"))
    else:
        findings.append(("通过", "全文", "占位符残留检查通过：无 {vN} 残留"))

    # ---------- 4. 参考文献区不得汉化 ----------
    # [自研补丁 2026-09-19] 事故背景: EgoPhys(2026) 首轮 LLM 直译把文献表整条
    # 中文化(K. Zhang→"K. 张"、K. Hauser→"K. 豪泽"、期刊名加书名号), 174 处,
    # 而当时三项断言全 PASS —— 汉化率只查"汉字太少", 不查"英文该留的没留"。
    # 口径: 文献页判据取原文侧密度(见 REF_DENSITY/is_ref_page), 命中后再按
    # 条目块数汉字 —— 只有条目本身被译成中文才算事故; 同页正文的中译不计入
    # (条目块已按 REF_ZH_WINDOW 截断)。
    # [自研补丁 2026-09-19] 两种体例各用一条计数臂: 编号制锚行首 [n](编号在
    # 译文里必然幸存); 作者-年份制无 [n], 锚译文中幸存的姓名(见
    # count_zh_ay_blocks)。仅当该页确为作者-年份制文献页时才启用后者, 否则
    # 正文里的拉丁人名会让中文正文被误算成"被汉化的条目"(实测 CLAP 第15-17页
    # 编号制文献页 + 中文正文: 误报 27/53/17 条)。
    # [自研补丁 2026-09-19] 实现搬进 check_ref_zh(), 与渲染前预检共用同一段
    # 代码 —— 前移这道闸门见 tools/pre_render_check.py。
    findings.extend(check_ref_zh(orig_feats, trans_feats))

    # ---------- 5. 渲染残渣 ----------
    # [自研补丁 2026-09-19] 判据与事故背景见 check_residue / RESIDUE_TAG。
    # 差分口径（只报原文整篇没有的形态），故不会误伤正文里合法的 <EOS> 等。
    findings.extend(check_residue(orig_feats, trans_feats))

    # ---------- 页数一致性 ----------
    if len(orig_feats) != len(trans_feats):
        findings.append((
            "中", "全局",
            f"页数不一致：原文 {len(orig_feats)} 页 vs 译文 {len(trans_feats)} 页，"
            f"仅按前 {n} 页对比，请人工核对尾部"))

    verdict = "FAIL" if any(f[0] == "高" for f in findings) else "PASS"
    return findings, verdict


# ---------------------------------------------------------------- 报告
def build_report(mono_path, orig_path, n_pages, findings, verdict, skip_last=0):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    skip_line = (f"- 跳页豁免: 末尾 {skip_last} 页（任务 skipLastPages）"
                 if skip_last > 0 else
                 "- 跳页豁免: 无（未传 --skip-last，英文保留页不豁免）")
    lines = [
        "# 翻译后质检报告（门禁）",
        "",
        f"- 译文: `{os.path.basename(mono_path)}`",
        f"- 原文: `{os.path.basename(orig_path)}`",
        f"- 质检时间: {now}",
        f"- 对比页数: {n_pages}",
        skip_line,
        f"- **门禁判定: {verdict}**" + ("（可交付）" if verdict == "PASS" else "（存在高危问题，需处理后交付）"),
        "",
        "## 断言结果",
        "",
    ]
    icon = {"高": "🔴", "中": "🟡", "提示": "🔵", "通过": "✅"}
    for level, loc, desc in findings:
        lines.append(f"- {icon.get(level, '⚪')} **[{level}]** {loc}：{desc}")
    lines += ["", "## 断言项说明", "",
              f"1. **汉化率（全篇口径）**：参与页（原文有实质内容、排除跳页与"
              f"文献保留页）的译文**合计**汉字占比 < {WHOLE_DOC_CJK_FAIL:.0%}"
              f"即失败（整篇翻译缺失或大段失败）；逐页汉字占比 < {CJK_FAIL:.0%}"
              "的页只作**提示**列出 —— PDF 文本层无法区分'引擎按规定不翻的整页"
              "表格/公式页'与'卡死漏译页'，须对照原文人工确认",
              "2. **引用完整性**：原文/译文 [n] 标号逐页对账，超容差=版面错乱",
              "3. **占位符残留**：{vN} 公式占位符未回填即失败",
              "4. **文献区禁汉化**：原文文献页（编号制行首 [n] 密度，或作者-年份"
              f"制条目密度 ≥ {REF_DENSITY} 条/千字）的条目在译文中被翻成中文即失败"
              "（人名/标题/期刊名必须原样保留）；反之文献页译文无汉字属预期，"
              "不计入汉化率断言",
              "5. **渲染残渣**：译文出现原文整篇都没有的标签残渣（渲染层"
              "内部标记如 `</style…>` 被当成页面文字画了出来）即失败；"
              "与原文同形的标签（如 NLP 论文正文里的 `<EOS>`）不算",
              "", "---", "",
              "> 由 tools/post_check.py 自动生成，阈值可在文件头部常量区调整。"]
    return "\n".join(lines)


def save_report(content, mono_path):
    os.makedirs(review_dir(), exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = re.sub(r"[^\w\-]+", "_",
                  os.path.splitext(os.path.basename(mono_path))[0])[:40]
    path = os.path.join(review_dir(), f"翻译后质检_{stamp}_{base}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------- 配对
def derive_original(mono_path):
    """mono 译文 → 原始 PDF 路径（去掉 -mono 后缀）"""
    d = os.path.dirname(mono_path)
    base = re.sub(r"-mono\.pdf$", ".pdf", os.path.basename(mono_path))
    cand = os.path.join(d, base)
    return cand if os.path.isfile(cand) else None


def find_latest_mono():
    d = os.path.join(project_root(), "server", "translated")
    monos = glob.glob(os.path.join(d, "*-mono.pdf"))
    if not monos:
        return None
    return max(monos, key=os.path.getmtime)


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="翻译后质检门禁（零 API 成本）")
    ap.add_argument("mono", nargs="?", default=None,
                    help="mono 译文 PDF 路径，缺省取最新")
    ap.add_argument("--original", default=None, help="原始 PDF 路径（默认自动推导）")
    ap.add_argument("--skip-last", type=int, default=0, metavar="N",
                    help="任务实际配置的 skipLastPages: 仅豁免末尾连续 N 个"
                         "原文保留页（由服务端自动传入；手工运行按任务设置填写）")
    args = ap.parse_args()
    if args.skip_last < 0:
        print("❌ --skip-last 不能为负数", file=sys.stderr)
        sys.exit(2)

    mono_path = args.mono or find_latest_mono()
    if not mono_path or not os.path.isfile(mono_path):
        print("❌ 未找到 mono 译文 PDF", file=sys.stderr)
        sys.exit(2)
    orig_path = args.original or derive_original(mono_path)
    if not orig_path or not os.path.isfile(orig_path):
        print("❌ 未找到对应的原始 PDF，请用 --original 指定", file=sys.stderr)
        sys.exit(2)

    try:
        r_orig = PdfReader(orig_path)
        r_trans = PdfReader(mono_path)
        # [自研补丁 2026-09-03] 页数计算移入 try: 旧写法在加密/损坏 PDF 上
        # 裸 traceback 退出码 1, 会被自动化误判为"质量 FAIL"(应为基础设施 2)
        n = min(len(r_orig.pages), len(r_trans.pages))
    except Exception as exc:
        print(f"❌ PDF 无法解析: {exc}", file=sys.stderr)
        sys.exit(2)

    orig_feats = [page_features(r_orig, i) for i in range(n)]
    trans_feats = [page_features(r_trans, i) for i in range(n)]

    findings, verdict = run_checks(orig_feats, trans_feats, skip_last=args.skip_last)
    content = build_report(mono_path, orig_path, n, findings, verdict,
                           skip_last=args.skip_last)
    report_path = save_report(content, mono_path)

    print(f"📄 译文: {os.path.basename(mono_path)} | 对比 {n} 页")
    for level, loc, desc in findings:
        mark = {"高": "🔴", "中": "🟡", "提示": "🔵", "通过": "✅"}.get(level, "⚪")
        print(f"{mark} [{level}] {loc}: {desc}")
    print(f"{'🟢 门禁判定: PASS（可交付）' if verdict == 'PASS' else '🔴 门禁判定: FAIL（需处理）'}")
    print(f"📝 报告: {report_path}")
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
