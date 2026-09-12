# -*- coding: utf-8 -*-
"""接缝体检: 列出侧车里所有"跨页接续"的段对, 供人工快速扫出断句与截断误译。

为什么需要它:
  pdf2zh 1.x 逐页流式解析 —— 一句话被页边界切开时, 前段渲染的那一刻后段还没
  解析出来, 所以页内前瞻 (v23.4) 覆盖不到; 缓存层又是"段本地字符串改写", 没有
  跨页重排的修复出口。这类问题只能人工发现后做缓存手术。本脚本把"发现"这一步
  压缩成一条命令, 不必等军师跑 25 分钟。

判据 (零成本, 数据全在侧车里):
  1. 只取"正文段"(剥掉 {vN} 后仍有实义字符), 表格/页脚/纯占位符段跳过;
     相邻两段正文若跨页, 即为一条接缝 —— 中间隔多少表格段都会跨越 (如 S8 与
     S12 之间隔着页脚 + 整页 Table 10.1);
  2. 标记: 前段译文尾无句末标点 -> "疑断"; 后段原文首为小写字母且前段原文尾
     无句末标点 -> "疑续"; 两条同时成立标 [!] (强疑断句), 单条成立标 [~];
  3. --with-report 时交叉军师报告, 显示该段是否已被点名 (报告须与侧车同批)。

用法 (解释器同 tools/tests/run_all.py; 仓库无 venv 目录, 用绝对路径):
  PY = D:/Users/97638/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  列出全部接缝:   & $PY tools/seams_report.py
  交叉军师报告:   & $PY tools/seams_report.py --with-report
  写入文件:       & $PY tools/seams_report.py --out seams.txt
  指定侧车/截断长度: --sidecar <path> --chars 120

拿到清单之后怎么处理 (详见 改动记录.md):
  - 前段译文语义被扭曲 (如被截断成 "three successful" 后猜成"三倍于") -> 截断误译, 必修;
  - 只是物理切开、两页译文拼起来读得通 -> 默认不修 (补字必然与邻段开头重复);
  - 原文本身就这么排版 (断在参考文献作者名 / 跨多页表格) -> 不动。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategist import load_segments, TOKEN_RE   # 复用侧车解析与占位符正则

SIDECAR_DEFAULT = os.path.join(
    os.path.expanduser("~"), ".cache", "pdf2zh", "segflow", "latest.jsonl")
REPORT_DEFAULT = os.path.join(os.path.dirname(SIDECAR_DEFAULT), "strategist_report.json")

_SENT_END_RE = re.compile(r"[。！？…；]\s*$")        # 译文句末标点
_RAW_END_RE = re.compile(r"[.?!;:]\s*$")             # 原文句末标点
_LOWER_RE = re.compile(r"^[a-z]")


def body_text(raw: str) -> str:
    """剥掉 {vN} 后的实义文本: 用来识别表格/页脚/纯占位符段。"""
    return TOKEN_RE.sub("", raw or "").strip()


def load_report_idx(path: str):
    """{idx: '报告'|'修正'} —— 报告缺失或格式不符时返回空表 (交叉信息是可选的)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    out = {}
    for key, tag in (("reports", "报告"), ("corrections", "修正")):
        for it in d.get(key) or []:
            if isinstance(it, dict) and "idx" in it:
                try:
                    out[int(it["idx"])] = tag
                except (TypeError, ValueError):
                    pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="跨页接缝体检 (侧车 -> 人工清单)")
    ap.add_argument("--sidecar", default=SIDECAR_DEFAULT)
    ap.add_argument("--report", default=REPORT_DEFAULT)
    ap.add_argument("--out", default="")
    ap.add_argument("--with-report", action="store_true")
    ap.add_argument("--chars", type=int, default=100, help="接缝两侧显示字符数")
    ap.add_argument("--min-chars", type=int, default=2, help="正文段最少实义字符数")
    args = ap.parse_args()

    if not os.path.exists(args.sidecar):
        print("侧车不存在: " + args.sidecar)
        return 1
    allsegs = load_segments(args.sidecar)
    if not allsegs:
        print("侧车为空: " + args.sidecar)
        return 1
    pages = [s["page"] for s in allsegs if s["page"] is not None]
    if not pages:
        print("该侧车没有 page 字段 (v26-L1 之前导出的), 无法做接缝体检。")
        print("先重启服务并 force 重渲染一次, 让 converter 重新导出带 page 的侧车。")
        return 1

    body = [s for s in allsegs if len(body_text(s["raw"])) >= args.min_chars]
    rep = load_report_idx(args.report) if args.with_report else {}

    out = []
    out.append("侧车: " + args.sidecar)
    out.append(f"总段 {len(allsegs)} / 正文段 {len(body)} / 跳过 {len(allsegs) - len(body)}"
               f" (表格·页脚·纯占位符) / 页 {min(pages)}..{max(pages)}")
    if args.with_report:
        tag = f"corrections {sum(1 for v in rep.values() if v == '修正')} / " \
              f"reports {sum(1 for v in rep.values() if v == '报告')}"
        out.append(f"军师报告: {args.report} ({tag})")

    rows = []
    for i in range(1, len(body)):
        a, b = body[i - 1], body[i]
        if a["page"] == b["page"]:
            continue
        ta, ra, rb = a["trans"].rstrip(), a["raw"].rstrip(), b["raw"].lstrip()
        cut = not _SENT_END_RE.search(ta)
        cont = bool(_LOWER_RE.match(rb)) and not _RAW_END_RE.search(ra)
        flag = "[!]" if (cut and cont) else ("[~]" if (cut or cont) else "[ ]")
        rows.append((flag, a, b, b["seq"] - a["seq"] - 1))

    strong = sum(1 for r in rows if r[0] == "[!]")
    weak = sum(1 for r in rows if r[0] == "[~]")
    out.append(f"跨页接缝 {len(rows)} 处 (强疑断句 {strong} / 弱 {weak})")
    out.append("")
    n = args.chars
    for flag, a, b, gap in rows:
        head = f"{flag} S{a['seq']}(p{a['page']}) -> S{b['seq']}(p{b['page']})"
        if gap:
            head += f"   隔{gap}段"
        if rep:
            marks = [rep.get(a["seq"]), rep.get(b["seq"])]
            hit = [f"{'上' if k == 0 else '下'}段已{rep[a['seq']] if k == 0 else rep[b['seq']]}"
                   for k, v in enumerate(marks) if v]
            if hit:
                head += "   [军师: " + "/".join(hit) + "]"
        out.append(head)
        out.append("      A尾 …" + a["trans"].rstrip()[-n:])
        out.append("      B头 " + b["trans"].lstrip()[:n])
    out.append("")
    out.append("[!] = 前段译文无句末标点 且 后段原文以小写续接, 最可能是被切开的同一句话")
    out.append("逐条判定: 译文语义被扭曲 -> 截断误译必修; 两页拼读通顺 -> 默认不修;")
    out.append("          原文本身如此排版(参考文献作者名 / 跨多页表格) -> 不动。")

    txt = "\n".join(out)
    print(txt)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
        print("\n已写入 " + args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
