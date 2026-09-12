# -*- coding: utf-8 -*-
"""接缝专项审查: 把跨页接缝清单送一次 LLM, 判类型并给出可写库的最小编辑。

为什么需要它:
  tools/seams_report.py 零成本捞出了"哪些段对跨页续接", 但"这条是截断误译还是
  纯物理切开"仍需逐条读原文/译文 —— Cactaceae 有 25 条接缝。军师(strategist.py)
  全文通读能判, 但把九成篇幅花在无关段上, 一次 25 分钟。本脚本只把接缝本身
  (前段原文尾+译文尾 / 后段原文头+译文头) 送一次 LLM, 秒级返回类型判定。

判据 (即"手术铁律", 与 改动记录.md 一致):
  1) 截断误译 —— 前段原文被页边界切断, 译文尾是对残句的猜测, 与后段原文头语义
     不接 (实测 "...three successful" 被猜成"三倍于")。可修: 改前段尾;
  2) 头并句   —— 后段译文头把本属前段的半句并了进来(与后段原文头不符)。可修: 改后段头;
  3) 物理切开 —— 两页译文拼读通顺。只报告, 不修 —— 补字必然与邻段开头重复;
  4) 排版如此 —— 原文本身断在参考文献作者名 / 跨多页表格处。只报告, 不动。

[v26.3] 悬空功能词硬信号: 前段原文尾落在 in/of/and/were 这类词上 -> 送审文本里显式标注,
prompt 禁止把带标注的接缝判成"物理切开"。起因: 模型把 S81 (`...fruit set in`) 判成
"物理切开、无错误" —— 而 `in` 悬空恰恰证明该句并未结束。实测 25 条接缝 3 条带此信号
(S81 漏判那条 / S87 已手术那条 / `In general` 良性一条)。信号一直在数据里, 缺的只是提示。
实测效果 (15 条送审): S81 从"物理切开"纠正为"截断误译" ✔; 代价是 S114 / S130 被连带判为
"截断误译"(人工已认定通顺/完整), 即**召回换精确** —— 且它们都因锚点校验失败停留在 reports
层, 不写库, 人工多读两行即可排除。另实测模型会把建议写成 no-op(只复述一遍)或删掉非残渣
占位符, 两类都已被 validate 拦下。

产出: ~/.cache/pdf2zh/segflow/seam_review.json, 与军师报告同构
  {"corrections": [{idx, type, issue, find, replace}],
   "reports":     [{idx, type, issue}]}
  - corrections 经人工审计裁剪后可直接喂:
      & $PY tools/strategist.py --from-report <file> --apply
    (脚本自身**不写缓存**; 军师看不到占位符背后的字形, 建议一律须人工过目)
  - reports 供 & $PY tools/seams_report.py --with-report --report <file> 交叉标注
  - 校验不过的 corrections 自动降级为 reports(附原因), 只损便利不丢信息
  - "改哪一段"由模型用 which(前段/后段) 表达, 段号由脚本推出 —— 不采信模型自报的
    段号 (实测模型把 idx 填成接缝序号 1, 于是这件事从设计上就不该交给模型)

台账 (~/.cache/pdf2zh/segflow/surgery_log.json) 里登记过的接缝默认跳过 —— 已人工
确认并处理过; --redo 可强制重审。

用法 (解释器同 tools/tests/run_all.py; 仓库无 venv 目录, 用绝对路径):
  PY = D:/Users/97638/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  干跑(只打印, 不调用 LLM):   & $PY tools/seam_review.py --dry-run
  实际送审:                   & $PY tools/seam_review.py
  连带审查台账已手术项:       & $PY tools/seam_review.py --redo
  放大对照字符数:             & $PY tools/seam_review.py --chars 240
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategist import (load_segments, llm_client, extract_json_array,  # noqa: E402
                        legend_of, droppable_of, tokens_ok)
from seams_report import (SIDECAR_DEFAULT, LOG_DEFAULT, collect_seams,  # noqa: E402
                          sidecar_fp, load_ledger, kept_visible, dangling_word)

REVIEW_DEFAULT = os.path.join(os.path.dirname(SIDECAR_DEFAULT), "seam_review.json")
KINDS = ("截断误译", "头并句", "物理切开", "排版如此")
FIXABLE = ("截断误译", "头并句")
SEAM_BUDGET = 40000         # 单次送审的字符预算 (接缝清单远小于全文)
DEFAULT_CHARS = 160         # 前后段各取多少字符做对照

SYSMSG = (
    "你是资深学术翻译审校。下面列出若干条\"跨页接缝\"—— 原文的同一句话被 PDF 页边界"
    "切开, 前后两半分属不同页面(中间常隔着表格/图片, 因此两条接缝之间可能相隔很多段)。"
    "每条接缝给出四行对照: 前段原文尾 / 前段译文尾 / 后段原文头 / 后段译文头。\n\n"
    "逐条判定类型, 只输出 JSON 数组, 每条接缝一个对象:\n"
    '{"seam": <接缝序号>, "type": "<类型>", "which": "<前段|后段|无>", '
    '"issue": "<简述>", "find": "<该段译文中的连续片段>", "replace": "<修正后片段>"}\n\n'
    "type 必须是以下四种之一:\n"
    "1) 截断误译 —— 前段原文被切断, 译文尾是对残句的猜测, 与后段原文头语义不接。"
    "可修: which=\"前段\", find=前段译文尾那段错误译文;\n"
    "2) 头并句 —— 后段译文头把本属前段的半句并了进来(与后段原文头不符)。"
    "可修: which=\"后段\", find=后段译文头多出来的片段;\n"
    "3) 物理切开 —— 两页译文拼起来读得通, 没有错。which=\"无\";\n"
    "4) 排版如此 —— 原文本身就断在参考文献作者名 / 跨多页表格处。which=\"无\"。\n\n"
    "若某条带 ⟨前段原文尾是悬空功能词 …⟩ 标注: 前段原文尾落在 in / of / and / were 这类"
    "词上, 该句后面必须还有成分 —— 这是句子被页边界硬切断的证据。带此标注的接缝"
    "**不得判为 3) 物理切开**, 必须归入 1) 或 2)。\n\n"
    "硬性要求:\n"
    "- which 只能是 \"前段\" / \"后段\" / \"无\" 三者之一 —— 要改的是前段还是后段; "
    "绝不要写接缝序号或段序号, 段号由脚本按 which 推出\n"
    "- 只有 1) 2) 才给 find/replace, 且 which 必须指向那段; 3) 4) 只写 type 与 issue\n"
    "- find 必须逐字复制自该段译文(含标点), 且在该段译文中唯一出现\n"
    "- 严禁靠增删段首/段尾内容来\"缝合\": 补进去的字必然与邻段或占位符内容重复。"
    "要修的是被切断后产生的错误译文, 不是接缝本身\n"
    "- find/replace 中的 {vN} 占位符必须原样保留, 不得新增或改号; 唯一例外是 "
    "⟨可删占位符 前段: … | 后段: …⟩ 里属于你正在修改那一段的那些\n"
    "- 严禁把 ⟨占位符内容⟩ 里的字面内容写进 find/replace (会与占位符渲染重复)\n"
    "- find 不得跨越 {vN} 占位符\n"
    "- issue 只写问题描述, 不要回吐格式编号"
)


def _drop_list(seg, limit: int = 12) -> str:
    """该段可删占位符清单 (截断显示)。

    实测一段能积下 44 个排版残渣占位符, 全列会淹没 prompt; 只列前 limit 个,
    模型若删了未列出的项, 锚点校验会拦下并降级为报告 —— 失败安全。"""
    d = sorted(droppable_of(seg), key=lambda t: int(t[2:-1]))
    if not d:
        return ""
    shown = " ".join(d[:limit])
    return shown + (f" …(共{len(d)}个)" if len(d) > limit else "")


def seam_text(items, chars: int) -> str:
    """把接缝清单组装成送审文本: 每条四行对照 + 图例 + 可删占位符。

    items 为 [(全局编号, 接缝行)], 编号写进 [接缝N] 供模型回填、供脚本回定位。"""
    parts = []
    for no, r in items:
        a, b = r["a"], r["b"]
        head = (f"[接缝{no}] 前段 S{a['seq']}(p{a['page']}) / 后段 S{b['seq']}(p{b['page']})")
        if r["gap"]:
            head += f" / 中间相隔 {r['gap']} 段"
        lines = [head,
                 "前段原文尾: …" + a["raw"].rstrip()[-chars:],
                 "前段译文尾: …" + a["trans"].rstrip()[-chars:]]
        dw = r.get("dangling") or dangling_word(a)
        if dw:
            lines.append(f"  ⟨前段原文尾是悬空功能词 '{dw}' —— 原文的句子在此处被硬切断, "
                         f"并未结束; 后段头的内容很可能属于前一句⟩")
        lg = legend_of(a) or legend_of(b)
        if lg:
            lines.append(f"  ⟨占位符内容: {lg}⟩")
        lines += ["后段原文头: " + b["raw"].lstrip()[:chars] + "…",
                  "后段译文头: " + b["trans"].lstrip()[:chars] + "…"]
        drops = [f"{tag}: {s}" for tag, s in
                 (("前段", _drop_list(a)), ("后段", _drop_list(b))) if s]
        if drops:
            lines.append("  ⟨可删占位符 " + " | ".join(drops) + "⟩")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def chunk_numbered(items, chars: int):
    """按字符预算把 [(编号, 接缝行)] 分块 (每条独立, 无需重叠)。"""
    out, cur, n = [], [], 0
    for it in items:
        L = chars * 4 + 200
        if cur and n + L > SEAM_BUDGET:
            out.append(cur)
            cur, n = [], 0
        cur.append(it)
        n += L
    if cur:
        out.append(cur)
    return out


def call_llm(client, model, text: str):
    resp = client.chat.completions.create(
        model=model, temperature=0.1,
        messages=[{"role": "system", "content": SYSMSG},
                  {"role": "user", "content": text}])
    return extract_json_array(resp.choices[0].message.content) or []


def validate(idx, item, segs):
    """把一条修正建议对回原文/译文校验。返回 (ok, reason)。

    复刻 strategist.load_curated 的两道关 (find 唯一 / 锚点), 以及段号合法。
    idx 由脚本按 which 推出(前段/后段), 不采信模型自报的段号 —— 实测模型会把
    idx 填成接缝序号 1, 于是"改哪一段"整件事从设计上就不该由模型表达。"""
    seg = next((s for s in segs if s["seq"] == idx), None)
    if seg is None:
        return False, f"S{idx} 段不存在"
    find = str(item.get("find") or "")
    replace = str(item.get("replace") or "")
    if not find:
        return False, "find 为空"
    if find == replace:
        return False, "find 与 replace 相同(空操作)"   # 实测 S18: 模型只复述了一遍
    n = seg["trans"].count(find)
    if n != 1:
        return False, f"find 非唯一命中({n}次)"
    revised = seg["trans"].replace(find, replace, 1)
    if not tokens_ok(seg["raw"], revised, droppable=droppable_of(seg)):
        return False, "锚点失败(占位符新增/改号/越权删除)"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser(description="跨页接缝专项 LLM 审查")
    ap.add_argument("--sidecar", default=SIDECAR_DEFAULT)
    ap.add_argument("--log", default=LOG_DEFAULT)
    ap.add_argument("--out", default=REVIEW_DEFAULT)
    ap.add_argument("--all", action="store_true", help="连带审查高噪接缝")
    ap.add_argument("--redo", action="store_true", help="连带审查台账已登记(已手术)的接缝")
    ap.add_argument("--chars", type=int, default=DEFAULT_CHARS)
    ap.add_argument("--min-chars", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true", help="只列清单, 不调用 LLM")
    args = ap.parse_args()

    if not os.path.exists(args.sidecar):
        print("侧车不存在: " + args.sidecar)
        return 1
    segs = load_segments(args.sidecar)
    body, rows = collect_seams(segs, args.min_chars)
    if not rows:
        print("该侧车没有跨页接缝 (或缺少 page 字段: v26-L1 之前导出的侧车无法体检)。")
        return 1
    fp = sidecar_fp(segs)
    ledger = load_ledger(args.log, fp)

    todo = rows if args.all else [r for r in rows if kept_visible(r, ledger)]
    skipped = 0
    if not args.redo:
        before = len(todo)
        todo = [r for r in todo if r["a"]["seq"] not in ledger and r["b"]["seq"] not in ledger]
        skipped = before - len(todo)

    print(f"侧车: {args.sidecar}")
    print(f"原文指纹 {fp} / 接缝 {len(rows)} 条 / 本次送审 {len(todo)} 条"
          + (f" / 跳过已手术 {skipped} 条(--redo 可重审)" if skipped else ""))
    if not todo:
        print("没有待审接缝。")
        return 0

    numbered = list(enumerate(todo, 1))
    chunks = chunk_numbered(numbered, args.chars)
    if args.dry_run:
        for k, ch in enumerate(chunks, 1):
            print(f"\n--- 分块 {k}/{len(chunks)} ({len(ch)} 条) ---")
            print(seam_text(ch, args.chars))
        print("\n[dry-run] 未调用 LLM。")
        return 0

    client, model = llm_client()
    raw_items = []
    for k, ch in enumerate(chunks, 1):
        print(f"送审分块 {k}/{len(chunks)} ({len(ch)} 条)…", flush=True)
        got = call_llm(client, model, seam_text(ch, args.chars))
        for it in got:
            if isinstance(it, dict):
                raw_items.append(it)

    by_no = dict(numbered)
    corrections, reports = [], []
    for it in raw_items:
        kind = str(it.get("type") or "").strip()
        issue = str(it.get("issue") or "").strip()
        try:
            r = by_no.get(int(it.get("seam")))
        except (TypeError, ValueError):
            r = None
        if r is None:
            reports.append({"idx": -1, "type": kind or "未定位",
                            "issue": f"(无法定位接缝 seam={it.get('seam')}) " + issue})
            continue
        head = f"S{r['a']['seq']}(p{r['a']['page']}) -> S{r['b']['seq']}(p{r['b']['page']})"
        if kind not in KINDS:
            reports.append({"idx": r["a"]["seq"], "type": kind or "未知",
                            "issue": f"(类型不合法) {issue}", "seam": head})
            continue
        if kind in FIXABLE:
            which = str(it.get("which") or "").strip()
            idx = {"前段": r["a"]["seq"], "后段": r["b"]["seq"]}.get(which)
            if idx is None:
                reports.append({"idx": r["a"]["seq"], "type": kind,
                                "issue": f"(which={which!r} 无法定位到前段/后段) {issue}",
                                "seam": head})
                continue
            ok, why = validate(idx, it, segs)
            if ok:
                corrections.append({"idx": idx, "type": kind, "issue": issue,
                                    "find": str(it["find"]), "replace": str(it.get("replace") or "")})
                continue
            reports.append({"idx": idx, "type": kind,
                            "issue": f"(校验未过: {why}) {issue}", "seam": head})
            continue
        reports.append({"idx": r["a"]["seq"], "type": kind, "issue": issue, "seam": head})

    # ---------------- 打印 ----------------
    print()
    for c in corrections:
        print(f"[修正] S{c['idx']}  {c['type']}  {c['issue']}")
        print(f"       find:    {c['find']}")
        print(f"       replace: {c['replace']}")
    for r in reports:
        loc = r.get("seam", "")
        print(f"[报告] {r['type']}" + (f"  {loc}" if loc else "") + f"  {r['issue']}")
    print(f"\n修正 {len(corrections)} 条 / 报告 {len(reports)} 条 / 送审 {len(todo)} 条")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"corrections": corrections, "reports": reports},
                      f, ensure_ascii=False, indent=1)
        print(f"已写入 {args.out}")
        if corrections:
            print("下一步(人工审计后): & $PY tools/strategist.py "
                  f"--from-report \"{args.out}\" --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
