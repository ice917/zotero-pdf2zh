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
  PY = <anaconda>/envs/zotero-pdf2zh-venv/python.exe
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
    """[(页码, 归一化文本, 提取是否成功)]。

    [门禁 fail-closed] 提取抛异常时**不能**把该页文本当空串: 空串会让 --forbid 串
    "必然找不到"从而假通过(UNKNOWN≠PASS), 也会让 --expect 的 0 命中失去解释力。故
    单独记 ok=False, 由 main 判 FAIL 并要求人工看这一页。
    """
    reader = PdfReader(pdf)
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
            ok = True
        except Exception:
            t, ok = "", False
        pages.append((i + 1, norm(t), ok))
    return pages


def hits(pages, needle: str):
    """归一化子串命中的 [(页码, 次数)]。"""
    key = norm(needle)
    if not key:
        return []
    out = []
    for pno, text, _ok in pages:
        c = text.count(key)
        if c:
            out.append((pno, c))
    return out


def _fmt(hl) -> str:
    return "0 命中" if not hl else " ".join(f"p{p}×{c}" for p, c in hl)


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染层验收: 新串落页 / 旧串清零")
    ap.add_argument("--pdf", default="", help="默认取 server/translated 最新 *-mono.pdf")
    # action="extend": 纯 nargs="+" 时重复出现的选项是"后者覆盖前者", `--expect A
    # --expect B` 会只剩 B(静默漏验)。extend 让 `--expect A B` / `--expect A
    # --expect B` 两种写法都累加 —— 见 test_adopt ㉓。
    ap.add_argument("--expect", nargs="+", action="extend", default=[],
                    help="期望出现的串 (0 命中 -> FAIL); 可重复给或一次给多个")
    ap.add_argument("--forbid", nargs="+", action="extend", default=[],
                    help="期望消失的串 (>0 命中 -> FAIL); 同样支持重复累加")
    args = ap.parse_args()

    pdf = args.pdf or latest_mono()
    if not pdf or not os.path.exists(pdf):
        print("找不到 PDF: " + (pdf or "(server/translated 下没有 *-mono.pdf)"))
        return 1
    if not args.expect and not args.forbid:
        print("至少要给 --expect 或 --forbid 之一。")
        return 1

    pages = load_pages(pdf)
    empty = sum(1 for _, t, _ in pages if not t)
    bad = [p for p, _, ok in pages if not ok]
    out = [f"PDF: {pdf}", f"页数 {len(pages)}" + (f" / 提取为空 {empty} 页" if empty else ""), ""]
    if bad:
        out.append("[FAIL] 文本提取失败 第%s页 —— 无法证明这些页的串有无, 按 fail-closed"
                   " 判 FAIL(不把'读不出'当'没有'), 请人工核对这些页"
                   % ",".join(map(str, bad)))
        out.append("")

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
    if bad:
        out.append("提示: 有页文本提取失败 —— 该页结论不可信, 已整体判 FAIL, "
                   "请人工核对该页后再判定。")
    if failed:
        out.append("提示: 强制重渲染要用 force:true 且 config 写全(service/targetLang/"
                   "threadNum/mono/dual/skipSubsetFonts); 仍不生效则 kill 全部 python 后重启。")
    print("\n".join(out))
    return 1 if (failed or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
