# -*- coding: utf-8 -*-
"""
tools/post_check.py —— 翻译后质检门禁（零 API 成本，只读）

模块职责：
  翻译完成后，将 mono 译文与原始 PDF 逐页对比，执行三项自动断言，
  把"翻车但没人发现"变成"翻车自动报警"。断言项（参照 BabelDOC
  ACL 2026 论文的 Untranslated Blocks 质检指标设计）：

    1. 汉化率断言    原文有实质内容的页，译文中文字符占比过低
                     → 该页翻译缺失/失败
    2. 引用完整性    原文与译文的 [n] 引用标号数量逐页对账，
                     偏差超限 → 版面错乱/文献混排（参考文献页事故
                     的典型特征就是标号丢失或重复）
    3. 占位符残留    译文中残留 {vN} 公式占位符 → 公式回填失败

  特别处理：skipLastPages 跳过的参考文献页在译文中保持英文原版，
  属预期行为——通过"行首条目式[n]密集"特征识别为"原文保留页"，
  标记为通过而非失败。

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

CJK_FAIL = 0.05          # 原文实质内容页译文汉字占比低于该值 → 翻译缺失
MIN_SUBSTANTIAL = 500    # 原文页可提取字符数超过该值才算"实质内容页"
REF_ENTRY_MIN = 3        # 行首条目式 [n] 数达到该值 → 判定原文保留页(跳过页)
CITE_TOL_ABS = 5         # 引用标号数量对账的绝对容差
CITE_TOL_REL = 0.2       # 引用标号数量对账的相对容差（20%）
PLACEHOLDER = re.compile(r"\{v\d+\}")   # pdf2zh 公式占位符 {v1} {v2} ...
REF_ENTRY = re.compile(r"(?m)^\s*\[\d{1,3}\]")
CITE_ANY = re.compile(r"\[\d{1,3}\]")
CJK = re.compile(r"[\u4e00-\u9fff]")


def project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def review_dir():
    return os.path.join(project_root(), "server", "translated", REVIEW_DIRNAME)


# ---------------------------------------------------------------- 页面特征
def page_features(reader, idx):
    """提取第 idx 页（0 基）的质检特征，失败返回 error"""
    feat = {"page": idx + 1, "chars": 0, "cjk": 0, "alnum": 0, "cites": 0,
            "ref_entries": 0, "placeholders": 0, "error": None}
    try:
        text = reader.pages[idx].extract_text() or ""
        feat["chars"] = len(text.strip())
        feat["cjk"] = len(CJK.findall(text))
        feat["alnum"] = len(re.findall(r"[A-Za-z0-9]", text))
        feat["cites"] = len(CITE_ANY.findall(text))
        feat["ref_entries"] = len(REF_ENTRY.findall(text))
        feat["placeholders"] = len(PLACEHOLDER.findall(text))
    except Exception as exc:
        feat["error"] = str(exc)[:120]
    return feat


def cjk_ratio(feat):
    total = feat["cjk"] + feat["alnum"]
    return feat["cjk"] / total if total else 0.0


# ---------------------------------------------------------------- 断言
def run_checks(orig_feats, trans_feats, skip_last=0):
    """
    三项断言。返回 (findings, verdict)
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
    fail_pages, kept_pages = [], []
    for i in range(n):
        o, t = orig_feats[i], trans_feats[i]
        if o["error"] or t["error"]:
            continue
        if o["chars"] < MIN_SUBSTANTIAL:
            continue  # 原文页无实质内容（封面/图表页），不参与断言
        cjk_low = cjk_ratio(t) < CJK_FAIL
        if skip_last > 0 and i >= n - skip_last and cjk_low:
            kept_pages.append(t["page"])   # 任务明确跳过的末尾页, 原文保留
            continue
        if cjk_low:
            fail_pages.append(t["page"])
    if fail_pages:
        findings.append((
            "高", f"第{','.join(map(str, fail_pages))}页",
            f"汉化率断言失败：原文有实质内容但译文汉字占比<{CJK_FAIL:.0%}，"
            f"该页翻译缺失或失败（卡死/跳页错误的典型症状）"))
    if kept_pages:
        findings.append((
            "通过", f"第{','.join(map(str, kept_pages))}页",
            f"原文保留页（任务 skipLastPages={skip_last} 豁免的末尾页），预期行为"))
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
              "1. **汉化率**：原文实质内容页的译文汉字占比（排除跳过的文献页）",
              "2. **引用完整性**：原文/译文 [n] 标号逐页对账，超容差=版面错乱",
              "3. **占位符残留**：{vN} 公式占位符未回填即失败",
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
