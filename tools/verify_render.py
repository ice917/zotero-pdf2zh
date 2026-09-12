# -*- coding: utf-8 -*-
"""渲染层验收: 缓存手术后"新串是否已落到页上 / 旧串是否彻底消失"。

为什么需要它:
  缓存手术改的是 sqlite 里的译文, 但"改没改到渲染结果"必须看 PDF 才算数 ——
  同名任务命中去重返回旧 taskId、漏了 force:true、旧实例仍在 8890 上服务,
  任一都会让库里已修好的译文渲染不出来。此前每次都临时写 pdfminer/pypdf 脚本,
  本脚本把它固化成一条命令, 与 seams_report.py 组成"发现 -> 手术 -> 验收"闭环。

判据 (零 API 成本, 只读):
  1. 关键词先做"去空白"归一化再匹配 —— PDF 提取常把一行切成多行、词间塞空格,
     原样子串匹配会假阴性 (实测 "雄花具有长花柱，柱头退化" 跨行);
  2. 报告命中页号与次数: 期望串 0 命中 -> FAIL; 禁止串 >0 命中 -> FAIL;
  3. 退出码: 全通过 0 / 有 FAIL 1, 供自动化联动。

用法 (解释器同 tools/tests/run_all.py; 仓库无 venv 目录, 用绝对路径):
  PY = D:/Users/97638/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  默认取 server/translated 里最新的 *-mono.pdf:
    & $PY tools/verify_render.py --expect "雄花具有长花柱" --forbid "雌花花柱长" "雄花花柱亦长"
  指定 PDF (dual 也吃, 只是命中会翻倍):
    & $PY tools/verify_render.py --pdf "...-mono.pdf" --expect "间接估计值" --forbid "采用间接估算法"
  只统计不判定 (看某串出现在哪几页):
    & $PY tools/verify_render.py --expect "仙人掌科"
"""
import argparse
import glob
import logging
import os
import re
import sys
import warnings

from pypdf import PdfReader

# 老 PDF 字体表(CMap)损坏的解析警告对验收无贡献, 静音
logging.getLogger("pypdf").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

BLANK_RE = re.compile(r"\s+")


def project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def translated_dir():
    return os.path.join(project_root(), "server", "translated")


def latest_mono():
    """server/translated 里最新的 *-mono.pdf (排除 dual 与原文)。"""
    cands = glob.glob(os.path.join(translated_dir(), "*-mono.pdf"))
    return max(cands, key=os.path.getmtime) if cands else ""


def norm(s: str) -> str:
    """去空白归一化: 匹配前统一调用, 抵消 PDF 提取的断行与词间空格。"""
    return BLANK_RE.sub("", s or "")


def load_pages(pdf: str):
    """[(页码, 归一化文本)], 提取失败的页文本为空。"""
    reader = PdfReader(pdf)
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        pages.append((i + 1, norm(t)))
    return pages


def hits(pages, needle: str):
    """归一化子串命中的 [(页码, 次数)]。"""
    key = norm(needle)
    if not key:
        return []
    out = []
    for pno, text in pages:
        c = text.count(key)
        if c:
            out.append((pno, c))
    return out


def _fmt(hl) -> str:
    return "0 命中" if not hl else " ".join(f"p{p}×{c}" for p, c in hl)


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染层验收: 新串落页 / 旧串清零")
    ap.add_argument("--pdf", default="", help="默认取 server/translated 最新 *-mono.pdf")
    ap.add_argument("--expect", nargs="+", default=[], help="期望出现的串 (0 命中 -> FAIL)")
    ap.add_argument("--forbid", nargs="+", default=[], help="期望消失的串 (>0 命中 -> FAIL)")
    args = ap.parse_args()

    pdf = args.pdf or latest_mono()
    if not pdf or not os.path.exists(pdf):
        print("找不到 PDF: " + (pdf or "(server/translated 下没有 *-mono.pdf)"))
        return 1
    if not args.expect and not args.forbid:
        print("至少要给 --expect 或 --forbid 之一。")
        return 1

    pages = load_pages(pdf)
    empty = sum(1 for _, t in pages if not t)
    out = [f"PDF: {pdf}", f"页数 {len(pages)}" + (f" / 提取为空 {empty} 页" if empty else ""), ""]

    failed = 0
    for label, items, want in (("期望", args.expect, True), ("禁止", args.forbid, False)):
        for s in items:
            hl = hits(pages, s)
            ok = bool(hl) if want else not hl
            if not ok:
                failed += 1
            out.append(f"[{'OK' if ok else 'FAIL'}] {label}串 {s!r} -> {_fmt(hl)}")

    out.append("")
    out.append(f"通过 {len(args.expect) + len(args.forbid) - failed}"
               f" / 共 {len(args.expect) + len(args.forbid)}; FAIL {failed}")
    if failed:
        out.append("提示: 强制重渲染要用 force:true 且 config 写全(service/targetLang/"
                   "threadNum/mono/dual/skipSubsetFonts); 仍不生效则 kill 全部 python 后重启。")
    print("\n".join(out))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
