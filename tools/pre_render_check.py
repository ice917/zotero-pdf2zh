# -*- coding: utf-8 -*-
"""
tools/pre_render_check.py —— 渲染前内容预检（文献区禁汉化）

模块职责：
  在 force_rerender **之前**，对"回锚后的译文"（out/<name>.imported.json，
  此时还没有 PDF）跑一遍「参考文献区不得汉化」判据。判 FAIL 就不渲染。

为什么要前移：
  这道判据原本只存在于 tools/post_check.py，而 post_check 要拿渲染出来的
  mono PDF 才跑得动 —— 于是"文献区被汉化"只能在出 PDF 之后才发现，得删掉
  重出。重出 PDF 本身只要 45 秒（缓存是热的），但要占服务端一轮、还得人来
  判断。前移到 render 之前，这一轮直接省掉。

判据不是新写的：
  直接调用 tools/post_check.py 的 check_ref_zh() —— 渲染前后用的是**同一段
  代码**，只是文本来源不同：
    渲染后  pypdf 从 mono PDF 逐页抽文本        （post_check）
    渲染前  imported.json 的 "页码#段序" 键按页拼回文本（本工具）
  口径共享是硬要求：否则两边迟早漂移，又变成"翻前放行、翻后判死"。

能查 / 不能查（**别指望这一道就够**）：
  能   —— 文献区禁汉化（纯内容判据，与版面无关）
  不能 —— 汉化率：按"页"聚合，而 imported.json 的页码是**原文页码**，
          跳页/合页后页码会错位，误报风险高于收益，仍留给 post_check
  不能 —— 占位符残留：{vN} 是**渲染期**才被替换的
  不能 —— 引用完整性：它查的正是"渲染后 [n] 有没有被版面吃掉/拆散"
  这三条只能在 post_check 上跑，所以 gate 阶段一步都不能省。

已知不保真的地方（本工具判 PASS ≠ post_check 一定 PASS）：
  imported.json 是一段一段的译文，本工具用 "\n" 拼回页文本。行首 [n] 判据
  （post_check.REF_ENTRY）是按行锚定的，所以"一个段里塞进多个文献条目"时，
  段内第二条起可能匹配不到 —— 本工具会比 post_check 略宽。方向是安全的：
  它不会把好页判死，只可能漏掉少数被汉化的条目，漏掉的仍由 gate 阶段的
  post_check 兜住。

用法：
  python tools/pre_render_check.py --original <原文.pdf> --imported <out/x.imported.json>
  python tools/pre_render_check.py ... --quiet      # 只打印结论
退出码：0 = PASS（可以渲染）；1 = FAIL（不要渲染）
"""

import argparse
import json
import os
import re
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import post_check as PC      # 复用判据, 见模块 docstring "判据不是新写的"
from pypdf import PdfReader

# imported.json 的键: "<页码>#<段序>", 页码 1 基、段序 0 基(实测 wang2026)
KEY = re.compile(r"^(\d+)#(\d+)$")


def load_trans_pages(path):
    """imported.json → {页码: 该页译文（按段序拼回）}。

    缺失的页一律当空串：被跳页（skipLastPages）的文献页本来就没有译文，
    那页保持的是原文英文，不构成"汉化"，与 post_check 的口径一致。
    """
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    by_page = {}
    for k, v in data.items():
        m = KEY.match(str(k))
        if not m:
            continue
        by_page.setdefault(int(m.group(1)), []).append((int(m.group(2)), str(v)))
    return {pg: "\n".join(t for _, t in sorted(items))
            for pg, items in by_page.items()}


def trans_features(trans_pages, n_pages):
    """{页码: 译文} + 页数 → 译文侧特征列表（与原文侧同长）。

    缺页补空文本：被跳页的文献页本就没有译文，那页保持原文英文。空文本的
    chars=0、ref_zh_blocks=0，在 check_ref_zh 里既不构成汉化也不影响结论。

    单独抽出来是为了**不依赖 PDF** 就能单测整条判据路径（与 test_post_check
    的"合成 feats"约定一致）。
    """
    return [PC.text_features(trans_pages.get(i + 1, ""), i + 1)
            for i in range(n_pages)]


def build_features(orig_pdf, trans_pages):
    """原文特征（逐页抽文本）+ 译文特征（按页拼文本），两列同长。

    行数取"原文页数"与"译文出现的最大页码"的较大者，防止译文页码超出原文
    页数时被悄悄截掉（截掉的那几页恰恰最可能是文献页）。
    """
    r = PdfReader(orig_pdf)
    n = max(len(r.pages), max(trans_pages) if trans_pages else 0)
    orig = [PC.page_features(r, i) for i in range(n)]
    return orig, trans_features(trans_pages, n)


def check_pages(orig_feats, trans_pages):
    """渲染前预检的完整判据: 原文特征 + 译文 → (findings, 高危项)。

    与 post_check 的差别只有文本来源（一个从 PDF 抽、一个从 imported.json 拼），
    判据本身是同一个 check_ref_zh —— 这是本工具存在的全部理由。
    """
    trans = trans_features(trans_pages, len(orig_feats))
    findings = PC.check_ref_zh(orig_feats, trans)
    return findings, [f for f in findings if f[0] in ("高", "中")]


def main():
    ap = argparse.ArgumentParser(description="渲染前内容预检：文献区禁汉化")
    ap.add_argument("--original", required=True, help="原文 PDF")
    ap.add_argument("--imported", required=True,
                    help="回锚后的译文 out/<name>.imported.json")
    ap.add_argument("--quiet", action="store_true", help="只打印结论")
    args = ap.parse_args()

    for p in (args.original, args.imported):
        if not os.path.exists(p):
            print("[pre-render] 找不到: %s" % p)
            return 1

    trans_pages = load_trans_pages(args.imported)
    orig, _ = build_features(args.original, trans_pages)
    findings, bad = check_pages(orig, trans_pages)

    if not args.quiet:
        print("[pre-render] 原文 %d 页 / 译文覆盖 %d 页 / 判据 %d 条"
              % (len(orig), len(trans_pages), len(findings)))
        for lv, loc, desc in findings:
            print("  [%s] %s: %s" % (lv, loc, desc))

    if bad:
        print("🔴 渲染前预检 FAIL —— 已拦下, 不要渲染:")
        for _, loc, desc in bad:
            print("   %s: %s" % (loc, desc.split("：", 1)[0]))
        print("   处置: 把被汉化的文献条目还原成原文后重交, "
              "或在 seg_export 时确保规则第 6 条生效")
        return 1
    print("✅ 渲染前预检 PASS —— 文献区未汉化, 可以进入 render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
