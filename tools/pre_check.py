# -*- coding: utf-8 -*-
"""
tools/pre_check.py —— PDF 翻译前体检器（零 API 成本，零侵入）

模块职责：
  在提交翻译之前，用 pypdf 对 PDF 做一次快速"体检"，提前暴露
  会触发 pdf2zh 版面解析失败的高危因素，并给出可操作的处置建议：
    - 参考文献页检测   → 自动推荐 skipLastPages 值（跳过末尾 N 页）
    - 扫描件检测       → 无文本层的页提示先走 OCR（pdf2zh_next + OCR）
    - 公式密集页预警   → 数学符号密度高的页列入"重点核对"名单
    - 加密/损坏预检    → 提前发现无法解析的文件
  检查结论写入 server/translated/review/翻译前体检报告_*.md，
  与审校报告/存疑清单共用同一目录，形成"翻前体检 → 翻后质检"闭环。

设计原则：
1. **只读**：不修改 PDF 与任何翻译产物，仅输出报告文件。
2. **零依赖增量**：只用 venv 里已有的 pypdf，不引入新依赖，零 API 成本。
3. **保守推荐**：skipLastPages 只在"参考文献页构成文档后缀"时推荐；
   参考文献混在正文中间时仅提示，不自动给跳页建议。
4. **优雅降级**：单页解析失败只跳过该页，不中断整体体检。

判定规则（v1，经验阈值）：
  - 参考文献页: 行首条目式 [n] >= REF_ENTRY_MIN(3) 或含 References 标题
    （条目式=行首 [n]，正文行内引用不算——这是区分文献页与相关工作者节的关键）
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

# 老 PDF 常见字体表(CMap)损坏会刷屏大量解析警告，对体检结论无贡献，静音处理
logging.getLogger("pypdf").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

# ---------------------------------------------------------------- 常量
REVIEW_DIRNAME = "review"

REF_ENTRY_MIN = 3       # 页内行首条目式 [n] 数达到该值 → 参考文献页候选
REF_HEADING_WORDS = ("references", "bibliography", "参考文献", "文献")
SCAN_TEXT_MIN = 50      # 页面可提取文本低于该字符数 → 疑似扫描页
MATH_DENSE = 40         # 页面数学符号数达到该值 → 公式密集页
CJK_MOSTLY = 0.5        # 汉字字符占字母数字比例超过该值 → 疑似已是中文

MATH_CHARS = re.compile(
    r"[∑∫∮∂√×÷±≈≠≤≥∞∈∀∃∇∆∏∪∩⊂⊃°µ≡→←↔⇒⟨⟩⟪⟫∂]"
    r"|[\u0391-\u03c9]")  # 数学符号 + 希腊字母（不含普通字母数字）


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
            "math": 0, "cjk_r": 0.0, "heading": False, "error": None}
    try:
        page = reader.pages[idx]
        text = page.extract_text() or ""
        feat["chars"] = len(text.strip())
        feat["ref_markers"] = count_citation_markers(text)
        feat["ref_entries"] = count_reference_entries(text)
        feat["math"] = count_math_symbols(text)
        feat["cjk_r"] = cjk_ratio(text)
        low = text.lower()
        feat["heading"] = any(w in low for w in REF_HEADING_WORDS)
    except Exception as exc:  # 单页损坏/字体异常
        feat["error"] = str(exc)[:120]
    return feat


# ---------------------------------------------------------------- 判定
def decide(pages, total):
    """
    汇总所有页特征 → 体检结论。
    返回 (recommend_skip, findings)
      recommend_skip: 推荐的 skipLastPages 值（0 = 不推荐跳页）
      findings:       [(级别, 页码或全局, 描述), ...]  级别: 高/中/提示
    """
    findings = []
    recommend_skip = 0

    # --- 参考文献页检测：条目式 [n]（行首）密集 或 含 References 标题 ---
    # 条目式标号是参考文献列表的专属特征，正文行内引用不会被误判
    ref_pages = [p["page"] for p in pages
                 if p["error"] is None
                 and (p["ref_entries"] >= REF_ENTRY_MIN or p["heading"])]
    if ref_pages:
        first = ref_pages[0]
        contiguous = ref_pages == list(range(first, total + 1))
        if contiguous and first > 1:
            recommend_skip = total - first + 1
            findings.append((
                "高", f"第{first}-{total}页",
                f"检测到参考文献区（引用标号密集/含标题），这些页构成文档后缀。"
                f"建议插件设置「最后几页跳过翻译」= {recommend_skip}，"
                f"参考文献保持英文原版可避开双栏小字号版面解析失败的高发区"))
        else:
            pages_str = ",".join(str(p) for p in ref_pages)
            findings.append((
                "中", f"第{pages_str}页",
                "检测到参考文献特征页，但未构成文档后缀（可能混有正文）。"
                "不建议自动跳页；如该区域乱码，考虑用 --pages 分页区间单独处理"))

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

    return recommend_skip, findings


# ---------------------------------------------------------------- 报告
def build_report(pdf_path, total, pages, recommend_skip, findings, encrypted):
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
        note = "解析失败: " + p["error"] if p["error"] else \
               ("含References标题" if p["heading"] else "")
        lines.append(
            "| {page} | {chars} | {ref_markers} | {ref_entries} | {math} | {cjk_r:.0%} | {note} |".format(
                note=note, **p))
    lines += ["", "## 结论与建议", ""]
    if not findings:
        lines.append("未发现高危因素，可正常提交翻译。")
    else:
        for level, loc, desc in findings:
            lines.append(f"- **[{level}]** {loc}：{desc}")
    lines += ["", "---", "",
              "> 由 tools/pre_check.py 自动生成，阈值可在文件头部常量区调整。"]
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


def main():
    ap = argparse.ArgumentParser(description="PDF 翻译前体检器（零 API 成本）")
    ap.add_argument("pdf", nargs="?", default=None, help="PDF 路径，缺省取最新上传件")
    ap.add_argument("--quiet", action="store_true", help="只写报告，不打印详情")
    args = ap.parse_args()

    pdf_path = args.pdf or find_latest_pdf()
    if not pdf_path or not os.path.isfile(pdf_path):
        print("❌ 未找到 PDF 文件", file=sys.stderr)
        sys.exit(2)

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
            print("❌ PDF 已加密且需要打开口令, 请先去除密码"
                  "(如 qpdf --decrypt in.pdf out.pdf)后再体检/翻译",
                  file=sys.stderr)
        else:
            print(f"❌ PDF 无法解析: {exc}", file=sys.stderr)
        sys.exit(2)

    pages = [analyze_page(reader, i) for i in range(total)]
    recommend_skip, findings = decide(pages, total)

    content = build_report(pdf_path, total, pages, recommend_skip,
                           findings, encrypted)
    report_path = save_report(content, pdf_path)

    # 控制台摘要
    print(f"📄 {os.path.basename(pdf_path)} | {total} 页"
          + (" | ⚠ 已加密" if encrypted else ""))
    print(f"🎯 推荐 skipLastPages: {recommend_skip}")
    if findings:
        for level, loc, desc in findings:
            mark = {"高": "🔴", "中": "🟡", "提示": "🔵"}.get(level, "⚪")
            print(f"{mark} [{level}] {loc}: {desc}")
    else:
        print("🟢 未发现高危因素，可直接提交翻译")
    print(f"📝 报告: {report_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
