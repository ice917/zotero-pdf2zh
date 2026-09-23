# -*- coding: utf-8 -*-
"""
tools/pre_check.py —— PDF 翻译前体检器（零 API 成本，零侵入）

模块职责：
  在提交翻译之前，对 PDF 做一次快速"体检"，提前暴露会触发 pdf2zh
  版面解析失败的高危因素，并给出可操作的处置建议：
    - 参考文献页检测   → 自动推荐 skipLastPages 值（跳过末尾 N 页）
    - 扫描件检测       → 无文本层的页提示先走 OCR（pdf2zh_next + OCR）
    - 公式密集页预警   → 数学符号密度高的页列入"重点核对"名单
    - 字体/结构风险    → Type3 字体、复合字体缺 ToUnicode、生产者指纹(dvips/iText)
    - 加密/损坏预检    → 提前发现无法解析的文件
    - 不可见字符记账   → 零宽/bidi/tag 类逐页记账（只报不拦；剥离由 tools/text_clean.py
                          在载荷侧与回锚侧**走同一个函数**做掉，见该模块头）
  检查结论写入 server/translated/review/翻译前体检报告_*.md，
  与审校报告/存疑清单共用同一目录，形成"翻前体检 → 翻后质检"闭环。

与其他工具的分工（**别搞反**）：
  本工具 = 便宜筛子。字体那一段用 PyMuPDF 读"风险特征"，而 MuPDF 是另一个
  解析器：它能读出结构 **不代表 pdfminer 读得下来**。结论只当线索。
  tools/parse_smoke.py = 真判据：走产线同一条代码路径（pdf2zh.pdfinterp +
  pdfminer 字体构造）逐页试解析，过了才是真的过。被本工具标红 → 必须跑沙盘。

设计原则：
1. **只读**：不修改 PDF 与任何翻译产物，仅输出报告文件。
2. **零依赖增量**：只用 venv 里已有的 pypdf + PyMuPDF，不引入新依赖，零 API 成本。
3. **保守推荐**：skipLastPages 只在"参考文献页构成文档后缀"时推荐；
   参考文献混在正文中间时仅提示，不自动给跳页建议。
4. **优雅降级**：单页解析失败只跳过该页，不中断整体体检。

判定规则（v1，经验阈值）：
  - 参考文献页: 满足任一即候选 ——
      a) 编号制: 行首条目式 [n] >= REF_ENTRY_MIN(3) 且行首占比 >= REF_ENTRY_RATIO(0.5)
      b) 编号制: 条目密度 >= REF_DENSITY(2.5)（每千字）
      c) 作者-年份制条目密度 >= REF_AY_DENSITY(2.5)（每千字）
    体例 b/c 的常量与翻后质检 tools/post_check.py 原样共享 —— 不变量是
    **门禁判 FAIL 的文献页，体检一定也认**（体检可以更敏感，不可更宽）。
  - 扫描页:   页面可提取文本 < SCAN_TEXT_MIN(50) 字符视为无文本层
  - 公式密集: 页面数学符号数 >= MATH_DENSE(40) 视为公式密集页
  - 已是中文: 页面汉字占比 > CJK_MOSTLY(0.5) 标记为"疑似已翻译"

用法：
  python tools/pre_check.py <PDF路径>            # 体检并输出报告
  python tools/pre_check.py <PDF路径> --quiet    # 只写报告，不打印详情
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

import text_clean as _TC   # [v28.80] 不可见字符的**单一剥离函数**(与导出/回锚两侧同源)

# 老 PDF 常见字体表(CMap)损坏会刷屏大量解析警告，对体检结论无贡献，静音处理
logging.getLogger("pypdf").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

# ---------------------------------------------------------------- 常量
REVIEW_DIRNAME = "review"

REF_ENTRY_MIN = 3       # 页内行首条目式 [n] 数达到该值 → 参考文献页候选
# [自研补丁 2026-09-19] 编号制文献页的两道闸门（缺一不可）。
# 缺口一: 只数"行首 [n] 条数 >= 3" 会把正文页当成文献页 —— 实机取证
#   Vaswani p10 = "Table 4 + 正文结尾"（行首 4 条），Zhang/CLAP p2 = teaser
#   页（行首 3 条），两页整页没有一条真文献条目。
# 缺口二: post_check 只按**密度**（>= REF_DENSITY）判，长条目文献页密度会掉到
#   阈值以下 → 整页漏判。实测 Jiang p7（15 条/6891 字=2.18）、Kim p9（6 条/
#   3392 字=1.77）、Melhani p42（5 条/3491 字=1.43）三页全部漏掉。
# 判据用「行首 [n] 占全部 [n] 的比例」补上缺口一 —— 实测分离度极大:
#   文献页 0.83~1.00（Vaswani p11=1.00, Melhani p42=0.83, Brody p14=0.89），
#   正文页 0.00~0.25（Vaswani p10=0.24, Zhang p2=0.19, Jiang p6=0.20）。
REF_ENTRY_RATIO = 0.5
REF_DENSITY = 2.5       # 与 post_check.REF_DENSITY 同值：每千字的条目数。
                        # 这一条必须原样共享，保证"翻后门禁判 FAIL 的文献页，
                        # 翻前体检一定也认"（子集关系，见 is_ref_page）。
# [自研补丁 2026-09-19] 作者-年份制文献页（整表无行首 [n]）的判据。
# 缺口: 本文件此前只认行首 [n]，而 "R. Olfati-Saber and R. M. Murray (2004). ..."
# 这类体例的行首 [n] 密度恒为 0 → 既不推荐 skipLastPages（文献区照常被翻译，
# 翻译后质检的第四断言必然 FAIL），也正是 Wang 2026 那类论文踩的坑。
# 口径与 post_check 同一套常量和正则 —— 翻前体检与翻后门禁对"文献页"必须
# 是同一个定义，否则一个放行、一个判 FAIL。
REF_AY_DENSITY = 2.5    # 原文每千字的作者-年份制条目数达到该值 → 文献页候选
REF_AY = re.compile(r"(?m)^\s*(?:[A-Z]\.\s*){1,4}[A-Z][A-Za-z\-'\u4e00-\u9fff]+"
                    r"[\s\S]{0,80}?\((?:19|20)\d{2}[a-z]?\)")
SCAN_TEXT_MIN = 50      # 页面可提取文本低于该字符数 → 疑似扫描页
MATH_DENSE = 40         # 页面数学符号数达到该值 → 公式密集页
CJK_MOSTLY = 0.5        # 汉字字符占字母数字比例超过该值 → 疑似已是中文

MATH_CHARS = re.compile(
    r"[∑∫∮∂√×÷±≈≠≤≥∞∈∀∃∇∆∏∪∩⊂⊃°µ≡→←↔⇒⟨⟩⟪⟫∂]"
    r"|[\u0391-\u03c9]")  # 数学符号 + 希腊字母（不含普通字母数字）

# --- 闸门 1：字体/结构风险（便宜筛子，PyMuPDF 侧）---
# 生产者/生成器指纹。只收"有确证来源"的两条，其余一律只记录不判定，
# 避免把"格式谱系"当"风险"制造噪声：
#   dvips → Type3 位图字体鼻祖。1986 年为 170K 内存的 Apple LaserWriter 所写，
#           只做 Level 1 Type3 最低要求，生成了语法合法但语义无意义的 /Encoding
#           向量（/A0–/H3 base36），TUGboat tb125rokicki 原文称其"至今仍在，
#           颠覆一切搜索复制"。与本项目 v26.19 的 Type3 崩溃同一科。
#   iText → 官方技术说明：Type3 字形到字符的映射本就不可靠（常用来画符号或
#           防文本提取），即使解析成功也未必有可用文本。
PRODUCER_RISK = (
    ("dvips", "高",
     "生产者是 dvips：Type3 位图字体高发源（/Encoding 语义无意义，1986 年至今未改）。"
     "这类文件的 Type3 既可能让 pdfminer 字体构造抛错、整篇退出，"
     "解析成功也只能得到不可搜索的文本"),
    ("itext", "中",
     "生产者是 iText：Type3 常被用来画符号或防文本提取，"
     "即使解析成功也未必有可用的字符映射"),
)


def project_root():
    """项目根目录（tools/ 的上级）"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def review_dir():
    """审校报告目录：server/translated/review"""
    return os.path.join(project_root(), "server", "translated", REVIEW_DIRNAME)


# ---------------------------------------------------------------- 单页分析
def count_citation_markers(text):
    """统计 [n] 形态的引用标号总数（含行内引用）"""
    return len(re.findall(r"\[\d{1,3}\]", text))


def count_reference_entries(text):
    """
    统计"条目式"引用标号：位于行首的 [n]。
    参考文献列表的每条都以 [n] 开头；正文行内引用不在行首。
    这是区分"参考文献页"与"相关工作者节"的关键特征。
    """
    return len(re.findall(r"(?m)^\s*\[\d{1,3}\]", text))


def count_reference_ay_entries(text):
    """统计"作者-年份制"文献条目：行首姓名 + 其后 80 字符内出现 (年份)。

    与 post_check.REF_AY 同口径。正文的行内引用 "(Olfati-Saber and Murray, 2004)"
    不以行首姓名开头，故不在此列 —— 实测正文页密度 <=0.7，文献页 2.93/3.58。
    """
    return len(REF_AY.findall(text))


def ref_ay_density(text):
    """作者-年份制条目密度（每千字）。按**原文页**算，与 post_check 一致。"""
    n = len(text.strip())
    return (count_reference_ay_entries(text) * 1000.0 / n) if n else 0.0


def ref_density(text):
    """编号制条目密度（每千字）。与 post_check.REF_DENSITY 同口径、同阈值。"""
    n = len(text.strip())
    return (count_reference_entries(text) * 1000.0 / n) if n else 0.0


# ---------------------------------------------------------------- 判定用谓词
def ref_entry_ratio(feat):
    """行首 [n] 占全部 [n] 的比例。

    文献页的条目一律顶格起排（行首 [n]），正文页的 [n] 绝大多数夹在句中。
    实测: 文献页 0.83~1.00，正文页 0.00~0.25（见常量区 REF_ENTRY_RATIO）。
    """
    n = feat["ref_markers"]
    return (feat["ref_entries"] / n) if n else 0.0


def is_ref_page(feat):
    """页特征 → 是否参考文献页。

    判据与 tools/post_check.py 的 is_ref_page 同源，**刻意同名同位以便对账**。
    不变量: **翻后门禁判 FAIL 的文献页，翻前体检一定也认** —— 所以共享的
    密度判据（REF_DENSITY / REF_AY_DENSITY）必须原样保留；本文件只能在此之上
    更敏感（多认几页 → 多建议跳几页），绝不能反过来漏认。
      作者-年份制: 条目密度 >= REF_AY_DENSITY
      编号制      : 密度 >= REF_DENSITY（共享）
                   或 行首条数 >= REF_ENTRY_MIN 且 行首占比 >= REF_ENTRY_RATIO
                   （补 post_check 的漏：长条目文献页密度会掉到阈值以下）
    """
    if feat.get("error") is not None:
        return False
    if feat["ref_ay_density"] >= REF_AY_DENSITY:
        return True
    if feat["ref_density"] >= REF_DENSITY:
        return True
    return (feat["ref_entries"] >= REF_ENTRY_MIN
            and ref_entry_ratio(feat) >= REF_ENTRY_RATIO)


def count_math_symbols(text):
    """统计数学符号出现次数"""
    return len(MATH_CHARS.findall(text))


def cjk_ratio(text):
    """汉字字符相对字母数字的比例（判断是否已是中文译本）"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    alnum = len(re.findall(r"[A-Za-z0-9]", text))
    if cjk + alnum == 0:
        return 0.0
    return cjk / (cjk + alnum)


def analyze_page(reader, idx):
    """
    分析第 idx 页（0 基），返回特征字典。
    单页解析失败时返回 {'error': ...}，不中断整体。
    """
    feat = {"page": idx + 1, "chars": 0, "ref_markers": 0, "ref_entries": 0,
            "ref_density": 0.0, "ref_ay": 0, "ref_ay_density": 0.0,
            "math": 0, "cjk_r": 0.0, "invis": "", "error": None}
    try:
        page = reader.pages[idx]
        text = page.extract_text() or ""
        feat["chars"] = len(text.strip())
        feat["ref_markers"] = count_citation_markers(text)
        feat["ref_entries"] = count_reference_entries(text)
        feat["ref_density"] = ref_density(text)
        feat["ref_ay"] = count_reference_ay_entries(text)
        feat["ref_ay_density"] = ref_ay_density(text)
        feat["math"] = count_math_symbols(text)
        feat["cjk_r"] = cjk_ratio(text)
        # [v28.80] 不可见字符记账(只报不拦) —— 详见 invis_section 的说明
        feat["invis"] = _TC.phrase(_TC.scan(text))
    except Exception as exc:  # 单页损坏/字体异常
        feat["error"] = str(exc)[:120]
    return feat


# ---------------------------------------------------------------- 字体/结构画像
def _composite_type(ftype):
    """复合字体（字符→字形要查 CMap）：这类字体缺 ToUnicode 最易产生乱码/伪汉字"""
    return ("Type0" in ftype) or ("CIDFont" in ftype) or ("TrueType" in ftype)


def _missing_tounicode(doc, xref, ftype):
    """
    字体字典无 /ToUnicode → True。
    只对复合字体问这个问题：Type1/base14 无 ToUnicode 是常态（走 /Encoding），
    对它们报"缺映射"纯属噪声。
    """
    if not _composite_type(ftype):
        return False
    try:
        kind, _val = doc.xref_get_key(xref, "ToUnicode")
        return kind == "null"
    except Exception:
        return False


def scan_fonts(pdf_path):
    """
    用 PyMuPDF 逐页读字体画像（**便宜筛子**：MuPDF 能读 != pdfminer 能读）。

    返回 {'error','producer','creator','pages':[{'page','fonts','type3','noto',
          'unembedded','count'}]}
      type3      : Type3 字体名（v26.19 实测崩因，pdfminer 侧会在字体构造处抛错）
      noto       : 复合字体中缺 /ToUnicode 的（文本极易成乱码/伪汉字）
      unembedded : 无内嵌字体文件（只看不判，渲染问题不属解析层崩溃）
    任何异常都不中断体检：出问题就返回 error / 该页记 error。
    """
    out = {"error": None, "producer": "", "creator": "", "pages": []}
    try:
        import fitz
    except Exception as exc:
        out["error"] = "PyMuPDF 不可用: %s" % exc
        return out
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        out["error"] = "PyMuPDF 打不开: %s" % str(exc)[:120]
        return out

    try:
        meta = doc.metadata or {}
        out["producer"] = (meta.get("producer") or "").strip()
        out["creator"] = (meta.get("creator") or "").strip()
        for i in range(doc.page_count):
            rec = {"page": i + 1, "fonts": [], "type3": [], "noto": [],
                   "unembedded": [], "count": 0, "error": None}
            try:
                seen = set()
                for f in doc[i].get_fonts(full=True):
                    xref, ext, ftype, basefont = f[0], f[1], str(f[2]), str(f[3])
                    if xref in seen:      # 同一字体在一页里可能被多处引用
                        continue
                    seen.add(xref)
                    label = "%s:%s" % (ftype, basefont)
                    rec["fonts"].append(label)
                    if "Type3" in ftype:
                        rec["type3"].append(label)
                    if _missing_tounicode(doc, xref, ftype):
                        rec["noto"].append(label)
                    if not ext:
                        rec["unembedded"].append(label)
                rec["count"] = len(rec["fonts"])
            except Exception as exc:      # 单页读不动不影响其它页
                rec["error"] = str(exc)[:120]
            out["pages"].append(rec)
    finally:
        doc.close()
    return out


# ---------------------------------------------------------------- 判定
def decide(pages, total, docrisk=None):
    """
    汇总所有页特征 → 体检结论。
    docrisk: scan_fonts() 的返回（可为 None = 不做字体侧判定）
    返回 (recommend_skip, findings)
      recommend_skip: 推荐的 skipLastPages 值（0 = 不推荐跳页）
      findings:       [(级别, 页码或全局, 描述), ...]  级别: 高/中/提示
    """
    findings = []
    recommend_skip = 0

    # --- 参考文献页检测 ---
    # 编号制: 行首 [n] 顶格起排（>3 条且占全部 [n] 的一半以上），或密度够高
    # 作者-年份制: 行首姓名 + 年份，按**密度**判（正文页实测 <=0.7，文献页 >=2.9）
    # 两种体例都要认 —— 只认第一种时，作者-年份制论文的文献区拿不到跳页建议，
    # 会被照常翻译，翻后第四断言必 FAIL（见文件头常量区说明）。
    # 判据收在模块级 is_ref_page()，与 post_check 同名同位，便于对账。
    ref_pages = [p["page"] for p in pages if is_ref_page(p)]
    ref_kinds = {p["page"]: ("编号制"
                            if (p["ref_density"] >= REF_DENSITY
                                or p["ref_entries"] >= REF_ENTRY_MIN)
                            else "作者-年份制")
                 for p in pages if is_ref_page(p)}
    if ref_pages:
        first = ref_pages[0]
        contiguous = ref_pages == list(range(first, total + 1))
        kind_str = "/".join(sorted(set(ref_kinds.values())))
        if contiguous and first > 1:
            recommend_skip = total - first + 1
            findings.append((
                "高", f"第{first}-{total}页",
                f"检测到参考文献区（体例: {kind_str}），这些页构成文档后缀。"
                f"建议插件设置「最后几页跳过翻译」= {recommend_skip}，"
                f"参考文献保持英文原版可避开双栏小字号版面解析失败的高发区"))
        else:
            pages_str = ",".join(str(p) for p in ref_pages)
            findings.append((
                "中", f"第{pages_str}页",
                f"检测到参考文献特征页（体例: {kind_str}），但未构成文档后缀"
                "（可能混有正文）。不建议自动跳页；如该区域乱码，"
                "考虑用 --pages 分页区间单独处理"))

    # --- 扫描页检测 ---
    scan_pages = [p["page"] for p in pages
                  if p["error"] is None and p["chars"] < SCAN_TEXT_MIN]
    if scan_pages:
        pages_str = ",".join(str(p) for p in scan_pages)
        findings.append((
            "高", f"第{pages_str}页",
            f"页面几乎无可提取文本（<{SCAN_TEXT_MIN}字符），疑似扫描图。"
            f"pdf2zh 1.x 对扫描页效果差，建议改用 pdf2zh_next 引擎并开启 OCR"))

    # --- 公式密集页预警 ---
    math_pages = [p["page"] for p in pages
                  if p["error"] is None and p["math"] >= MATH_DENSE]
    if math_pages:
        pages_str = ",".join(str(p) for p in math_pages)
        findings.append((
            "中", f"第{pages_str}页",
            f"数学符号密度高（>={MATH_DENSE}个），公式保护（-f/-c 占位符）"
            f"负担重，翻译后请重点核对该页公式还原效果"))

    # --- 已是中文 ---
    cjk_pages = [p["page"] for p in pages
                 if p["error"] is None and p["cjk_r"] > CJK_MOSTLY]
    if cjk_pages:
        pages_str = ",".join(str(p) for p in cjk_pages)
        findings.append((
            "提示", f"第{pages_str}页",
            "页面以中文为主：可能是已是译本/中文文献，也可能是老 PDF 字体映射"
            "(ToUnicode)损坏产生的伪字符（老文献高发）。建议人工抽查一页确认，"
            "若为伪字符则该页翻译质量不可信"))

    # --- 单页解析失败 ---
    err_pages = [p["page"] for p in pages if p["error"] is not None]
    if err_pages:
        pages_str = ",".join(str(p) for p in err_pages)
        findings.append((
            "高", f"第{pages_str}页",
            "页面解析抛错（加密/字体表损坏），pdf2zh 大概率同样失败，"
            "建议先修复 PDF 或跳过这些页"))

    # --- 不可见字符（零宽/格式/bidi/tag 类）---
    # [v28.80] 只报不拦。它们在正文里**看不见**, 但会让回锚的字形定位(str.find)落空
    # —— 报出来的 FAIL 人眼复核时无从解释。剥除由 tools/text_clean.py 在**两侧同时**
    # 做掉(载荷侧 restore / 回锚侧 seg_zh 与字形值), 故不影响本次翻译的正确性;
    # 这里记账是为了事后能按码点追查它的来源(网页粘贴 / OCR 残留 / 引擎写入)。
    invis_pages = [p["page"] for p in pages if p.get("invis")]
    if invis_pages:
        pages_str = ",".join(str(x) for x in invis_pages)
        findings.append((
            "提示", f"第{pages_str}页",
            "正文里检出不可见字符（零宽 / 格式 / bidi / tag 类）：它人眼看不见，却会让"
            "回锚的字形定位落空。导出与回锚两侧已用**同一个** tools/text_clean.py 同时"
            "剥离，不影响本次翻译；码点与位置见报告「不可见字符」节，可据此追查来源"))

    # --- 字体/结构风险（闸门 1 → 是否必须跑闸门 2）---
    findings += _font_findings(docrisk)

    return recommend_skip, findings


def _font_findings(docrisk):
    """
    字体侧风险结论（便宜筛子）。只产"线索"，真正的判据是 tools/parse_smoke.py。
    """
    out = []
    if not docrisk:
        return out
    if docrisk["error"]:
        out.append(("提示", "全局",
                    "字体画像不可用（%s）：本次只做文本/结构侧体检，"
                    "没有字体侧线索" % docrisk["error"]))
        return out

    # --- Type3：本项目已实锤的"单个字体炸整篇"元凶 ---
    t3 = [p for p in docrisk["pages"] if p["type3"]]
    if t3:
        pages_str = ",".join(str(p["page"]) for p in t3)
        names = ", ".join(sorted({n for p in t3 for n in p["type3"]}))[:160]
        out.append((
            "高", f"第{pages_str}页",
            f"检出 Type3 字体（{names}）。Type3 是用户自定义字体，字形由内嵌绘图"
            "指令描述，pdfminer 侧处理路径与常规字体不同：实测（v26.19）单个 Type3 "
            "就能让字体构造抛 AttributeError、pdf2zh 整篇退出码 1 且产物一个都不写。"
            "**提交前必须跑：python tools/parse_smoke.py <该PDF>** —— 体检只报线索，"
            "沙盘走产线同一条代码路径，过了才敢提交"))

    # --- 复合字体缺 ToUnicode ---
    noto = [p for p in docrisk["pages"] if p["noto"]]
    if noto:
        pages_str = ",".join(str(p["page"]) for p in noto)
        out.append((
            "中", f"第{pages_str}页",
            "复合字体（Type0/CIDFont/TrueType）缺 /ToUnicode 字符映射表。"
            "解析通常不会崩，但**字符到汉字的映射不可靠**：表现为乱码、伪汉字、"
            "或「整页已是中文」的假象（老文献高发）。翻译前请人工看一眼这几页的提取"
            "文本是否可读；不可读说明该页译文不可信"))

    # --- 生产者/生成器指纹 ---
    hay = ("%s %s" % (docrisk["producer"], docrisk["creator"])).lower()
    for key, level, desc in PRODUCER_RISK:
        if key in hay:
            out.append((level, "全局", desc))

    return out


# ---------------------------------------------------------------- 报告
def build_report(pdf_path, total, pages, recommend_skip, findings, encrypted,
                 docrisk=None):
    """组装 Markdown 体检报告"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 翻译前体检报告",
        "",
        f"- 文件: `{os.path.basename(pdf_path)}`",
        f"- 体检时间: {now}",
        f"- 总页数: {total}" + ("（⚠ 文件已加密）" if encrypted else ""),
        f"- 推荐 skipLastPages: **{recommend_skip}**",
        "",
        "## 页面特征",
        "",
        "| 页 | 可提取字符 | [n]标号 | 行首条目 | 数学符号 | 汉字占比 | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in pages:
        if p["error"]:
            note = "解析失败: " + p["error"]
        elif is_ref_page(p):
            note = "文献页"
        else:
            note = ""
        if p.get("invis"):
            note = (note + "; " if note else "") + "不可见字符"
        lines.append(
            "| {page} | {chars} | {ref_markers} | {ref_entries} | {math} | {cjk_r:.0%} | {note} |".format(
                note=note, **p))
    lines += font_section(docrisk)
    lines += invis_section(pages)
    lines += ["", "## 结论与建议", ""]
    if not findings:
        lines.append("未发现高危因素，可正常提交翻译。")
    else:
        for level, loc, desc in findings:
            lines.append(f"- **[{level}]** {loc}：{desc}")
    lines += ["", "---", "",
              "> 由 tools/pre_check.py 自动生成，阈值可在文件头部常量区调整。",
              "> 本工具是**便宜筛子**（字体段用 PyMuPDF），只报线索；",
              "> 被判红时请跑 tools/parse_smoke.py —— 那才是走产线同一条路径的真判据。"]
    return "\n".join(lines)


def invis_section(pages):
    """不可见字符记账小节（无命中则返回空列表；**只记账，不拦翻译**）。

    [v28.80] 为什么单列一节: 零宽/bidi/tag 类字符在正文里**看不见**, 但会让回锚的
    字形定位(str.find)落空 —— 由此产生的 FAIL 人眼复核时无从解释。剥除已由
    tools/text_clean.py 在导出/回锚**两侧同时**做掉(归一化, 不改任何判据), 本节只把
    "哪一页、什么码点、在第几个字符"记下来, 便于事后追查来源。

    它测的是**原文页**(pypdf 提取出来的文本), 而不是译文 —— 译文侧没有体检时机
    (译文是豆包给的), 那边靠剥离兜住。
    """
    hits = [p for p in pages if p.get("invis")]
    if not hits:
        return []
    out = ["", "## 不可见字符", "",
           "本节**只记账、不拦翻译** —— 剥离由 tools/text_clean.py 在载荷侧与回锚侧"
           "**走同一个函数**做掉，不改任何判据。",
           "",
           "| 页 | 命中（码点 · 次数 · 首次位置）|",
           "|---|---|"]
    for p in hits:
        out.append("| {page} | {invis} |".format(page=p["page"], invis=p["invis"]))
    out += ["",
            "> 条目**人眼看不见是正常的**（零宽字符没有宽度），按码点核对即可。",
            "> 变体选择符（`∑︀`、`⚠️`）与特殊空格（U+00A0/U+202F/U+3000）**不剥**，",
            "> 它们有宽度或呈现含义 —— 若在这里看到它们，属正常，不是缺陷。"]
    return out


def font_section(docrisk):
    """字体/结构画像小节（无数据则返回空列表）"""
    if not docrisk:
        return []
    lines = ["", "## 字体/结构画像", ""]
    if docrisk["error"]:
        lines.append("- ⚠ 未取得字体画像：%s" % docrisk["error"])
        return lines
    lines += ["- 生产者: `%s`" % (docrisk["producer"] or "(空)"),
              "- 生成器: `%s`" % (docrisk["creator"] or "(空)"),
              "",
              "> 便宜筛子口径：MuPDF 能读出结构 **不代表 pdfminer 读得下来**。",
              "",
              "| 页 | 字体数 | Type3 | 缺ToUnicode(复合) | 未嵌入 | 字体清单 |",
              "|---|---|---|---|---|---|"]
    for p in docrisk["pages"]:
        if p["error"]:
            lines.append("| %d | - | - | - | - | 读取失败: %s |"
                         % (p["page"], p["error"].replace("|", "/")))
            continue
        lines.append("| {page} | {count} | {t3} | {noto} | {unemb} | {allf} |".format(
            page=p["page"], count=p["count"],
            t3=", ".join(p["type3"]).replace("|", "/") or "-",
            noto=", ".join(p["noto"]).replace("|", "/") or "-",
            unemb=len(p["unembedded"]) or "-",
            allf=", ".join(p["fonts"]).replace("|", "/")[:200] or "-"))
    return lines


def build_batch_report(rows):
    """
    批量汇总报告：一张总表 + 每篇的完整体检结论（复用 build_report）。
    用途：样本普查 —— 按"格式谱系 × 出版年代"抽一批 Zotero 里的 PDF 跑一遍，
    先看风险分布，再挑红的去跑 tools/parse_smoke.py。
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["# 翻译前体检 · 批量汇总", "",
             "- 体检时间: %s" % now,
             "- 篇数: %d" % len(rows),
             "",
             "| 文件 | 页数 | skip | 生产者 | Type3页 | 缺ToUnicode页 | 结论 |",
             "|---|---|---|---|---|---|---|"]
    counts = {"高": 0, "中": 0, "提示": 0, None: 0}
    for res in rows:
        base = os.path.basename(res["path"])[:44]
        if res["error"]:
            lines.append("| %s | - | - | - | - | - | 🔴 %s |"
                         % (base, res["error"].replace("|", "/")))
            counts["高"] += 1
            continue
        dr = res["docrisk"]
        t3 = ",".join(str(p["page"]) for p in dr["pages"] if p["type3"]) or "-"
        noto = ",".join(str(p["page"]) for p in dr["pages"] if p["noto"]) or "-"
        lv = worst_level(res["findings"])
        counts[lv] += 1
        mark = {"高": "🔴", "中": "🟡", "提示": "🔵", None: "🟢"}[lv]
        lines.append("| %s | %d | %d | %s | %s | %s | %s%s |" % (
            base, res["total"], res["recommend_skip"],
            (dr["producer"] or "-").replace("|", "/")[:28],
            t3, noto, mark, lv or "无风险"))
    lines += ["",
              "**分布: 🔴高 %d 篇 · 🟡中 %d 篇 · 🔵提示 %d 篇 · 🟢无风险 %d 篇**"
              % (counts["高"], counts["中"], counts["提示"], counts[None]),
              "", "---", ""]
    for res in rows:
        if res["error"]:
            continue
        lines.append(build_report(res["path"], res["total"], res["pages"],
                                  res["recommend_skip"], res["findings"],
                                  res["encrypted"], res["docrisk"]))
        lines += ["", "---", ""]
    return "\n".join(lines)


def save_report(content, pdf_path):
    """报告写入 review 目录，文件名带时间戳与 PDF 名"""
    os.makedirs(review_dir(), exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(pdf_path))[0])[:40]
    path = os.path.join(review_dir(), f"翻译前体检_{stamp}_{base}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------- 入口
def find_latest_pdf():
    """不带参数时，取 server/translated 下最新的原始上传 PDF"""
    d = os.path.join(project_root(), "server", "translated")
    cands = [p for p in glob.glob(os.path.join(d, "*.pdf"))
             if not re.search(r"-(mono|dual|compare|mono-cut|dual-cut|crop-compare)", p)]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def check_one(pdf_path):
    """
    单篇体检（不写报告）。成功返回特征字典，失败返回 {'error': ...}。
    加密预检见下方 [自研补丁] 注释。
    """
    # [自研补丁 2026-09-03] 加密 PDF 定向提示: 旧代码先 len(pages) 再查
    # is_encrypted, 带用户口令的 PDF 在 len() 处直接抛 FileNotDecryptedError,
    # "加密预检"分支不可达且报错文案不明确
    reader = None
    try:
        reader = PdfReader(pdf_path)
        encrypted = bool(getattr(reader, "is_encrypted", False))
        if encrypted:
            # 仅所有者口令(限制权限)的 PDF 用空口令即可读
            try:
                reader.decrypt("")
            except Exception:
                pass
        total = len(reader.pages)
    except Exception as exc:
        if reader is not None and getattr(reader, "is_encrypted", False):
            return {"path": pdf_path,
                    "error": "PDF 已加密且需要打开口令, 请先去除密码"
                             "(如 qpdf --decrypt in.pdf out.pdf)后再体检/翻译"}
        return {"path": pdf_path, "error": "PDF 无法解析: %s" % exc}

    pages = [analyze_page(reader, i) for i in range(total)]
    docrisk = scan_fonts(pdf_path)
    recommend_skip, findings = decide(pages, total, docrisk)
    return {"path": pdf_path, "total": total, "encrypted": encrypted,
            "pages": pages, "docrisk": docrisk,
            "recommend_skip": recommend_skip, "findings": findings,
            "error": None}


def worst_level(findings):
    """结论里最高的级别（用于控制台记号与批量排序）"""
    for lv in ("高", "中", "提示"):
        if any(f[0] == lv for f in findings):
            return lv
    return None


def print_summary(res):
    """控制台摘要"""
    name = os.path.basename(res["path"])
    if res["error"]:
        print("🔴 %s | %s" % (name, res["error"]))
        return
    mark = {"高": "🔴", "中": "🟡", "提示": "🔵"}.get(worst_level(res["findings"]), "🟢")
    print("%s %s | %s 页%s | skipLastPages=%d" % (
        mark, name, res["total"],
        " | ⚠ 已加密" if res["encrypted"] else "", res["recommend_skip"]))
    for level, loc, desc in res["findings"]:
        m = {"高": "🔴", "中": "🟡", "提示": "🔵"}.get(level, "⚪")
        print("     %s [%s] %s: %s" % (m, level, loc, desc))


def main():
    ap = argparse.ArgumentParser(description="PDF 翻译前体检器（零 API 成本）")
    ap.add_argument("pdf", nargs="?", default=None, help="PDF 路径，缺省取最新上传件")
    ap.add_argument("--dir", default=None, help="批量：目录（递归找 PDF），出一份汇总报告")
    ap.add_argument("--limit", type=int, default=0, help="批量时最多几篇（0=不限）")
    ap.add_argument("--quiet", action="store_true", help="只写报告，不打印详情")
    args = ap.parse_args()

    if args.dir:
        from parse_smoke import list_pdfs          # 同目录，复用产物过滤规则
        targets = list_pdfs(args.dir)
        if args.limit:
            targets = targets[:args.limit]
        if not targets:
            print("❌ 目录下没找到 PDF: %s" % args.dir, file=sys.stderr)
            sys.exit(2)
        rows = []
        for p in targets:
            res = check_one(p)
            rows.append(res)
            if not args.quiet:
                print_summary(res)
        content = build_batch_report(rows)
        report_path = save_report(content, "batch%d" % len(rows))
        print("📝 报告: %s" % report_path)
        sys.exit(0)

    pdf_path = args.pdf or find_latest_pdf()
    if not pdf_path or not os.path.isfile(pdf_path):
        print("❌ 未找到 PDF 文件", file=sys.stderr)
        sys.exit(2)

    res = check_one(pdf_path)
    if res["error"]:
        print("❌ %s" % res["error"], file=sys.stderr)
        sys.exit(2)

    content = build_report(res["path"], res["total"], res["pages"],
                           res["recommend_skip"], res["findings"],
                           res["encrypted"], res["docrisk"])
    report_path = save_report(content, pdf_path)
    if not args.quiet:
        print_summary(res)
    print("📝 报告: %s" % report_path)
    sys.exit(0)


if __name__ == "__main__":
    main()
