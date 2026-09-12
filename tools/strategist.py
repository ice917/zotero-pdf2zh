# -*- coding: utf-8 -*-
"""军师层: 通读校对 + 接缝/指代/术语修正写回缓存 (v24b)

角色: 文档级协调者("军师")。队员(分段worker)并行草译后, 军师串读全文,
只处理局部视角看不见的问题:
  1. 跨段指代 ("它/该方法/上述" 指代错误或缺失)
  2. 接缝断裂 (跨页/跨段切分处的句子不连贯)
  3. 术语漂移 (同一概念前后译法不一致)
  4. 文风突变 (口语化/翻译腔突兀)

用法:
  干跑(只看修正建议):  venv python tools/strategist.py
  应用修正:            venv python tools/strategist.py --apply
  指定 sidecar:        --sidecar <path>
  人工裁剪后精准应用:  venv python tools/strategist.py --from-report <curated.json> --apply --allow-drop

安全机制:
  - 锚点校验: 修正译文的 {vN} 占位符多重集必须与原文完全一致, 否则丢弃
    (--allow-drop 放宽为子集: 允许删除垃圾占位符, 仍禁止新增/改号)
  - 默认干跑: 不带 --apply 不写缓存
  - 修正记录: ~/.cache/pdf2zh/segflow/strategist_report.json
  - [v24b.1] 写库前 canon 重排: 库内 translation 以段内出现序 0-based 存储,
    sidecar 是渲染时 current 序号(可乱序/可非 0 起), 直接写回会让占位符
    整体错位(渲染时公式/英文窜位) —— 必须经 raw 的映射重排
  - [v24b.1] --from-report: LLM 建议必须经人工对照 raw 审计后再应用
    (军师只看译文, 看不到占位符背后的英文字形, 会提出"补配子/补3:2"类
    与续段重复、与字形内容重复的建议 —— Cactaceae 实测 22 条仅 16 条可用)
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    r"D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages",
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

SRV_CFG = r"D:\zotero-pdf2zh\server\config\config.json"
SIDEcar_DEFAULT = os.path.join(
    os.path.expanduser("~"), ".cache", "pdf2zh", "segflow", "latest.jsonl")
TOKEN_RE = re.compile(r"\{v\d+\}")
CHUNK_CHAR_BUDGET = 42000        # 单次通读的字符预算 (DeepSeek 128K 上下文内)


# ---------------------------------------------------------------- 工具函数
def token_multiset(s: str):
    return Counter(TOKEN_RE.findall(s or ""))


def tokens_ok(raw: str, revised: str, allow_drop: bool = False) -> bool:
    """锚点校验。默认: 修正译文占位符多重集与原文完全一致。
    allow_drop=True: 放宽为"子集"—— 允许删除占位符(卡在词中间的 cid 垃圾),
    仍禁止新增或改号。"""
    need, got = token_multiset(raw), token_multiset(revised)
    if allow_drop:
        return all(got[k] <= need[k] for k in got)
    return need == got


def canon_remap(text: str, seq: dict) -> str:
    """[v24b.1] 按原文 raw 的 canon 映射把 current 序号译文重排为 canon 序号。

    库内 translation 与 canon 后的 original_text 配对存储(cache.set 用同一
    映射), 而 sidecar/修正建议里的序号是渲染时 current 序号(可非 0 起、
    可乱序, 如 S6 sidecar 以 {v1} 开头而库内以 {v0} 开头)。写库前必须重排,
    否则 cache.get 的回映射会把占位符整体错位一格。"""
    return TOKEN_RE.sub(lambda m: seq.get(m.group(0), m.group(0)), text)


def extract_json_array(text: str):
    """从 LLM 输出中稳健提取 JSON 数组 (容忍 ```json 围栏/前后闲话)。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M)
    lo, hi = text.find("["), text.rfind("]")
    if lo < 0 or hi <= lo:
        return None
    try:
        return json.loads(text[lo:hi + 1])
    except Exception:
        return None


def load_segments(sidecar):
    segs = []
    with open(sidecar, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            for s in rec.get("segs", []):
                segs.append({"seq": len(segs), "raw": s["raw"], "trans": s["trans"]})
    return segs


def llm_client():
    """与渲染路径同源的 LLM 客户端 (silicon/DeepSeek-V3.2, server config 注入)。"""
    import json as _json
    from openai import OpenAI
    cfg = _json.load(open(SRV_CFG, encoding="utf-8"))
    envs = {}
    for t in cfg.get("translators", []):
        if t.get("name") == "silicon":
            envs = t.get("envs", {})
            break
    model = envs.get("SILICON_MODEL") or "deepseek-ai/DeepSeek-V3.2"
    key = envs.get("SILICON_API_KEY")
    if not key:
        raise RuntimeError("server config 未提供 SILICON_API_KEY")
    return OpenAI(api_key=key, base_url="https://api.siliconflow.cn/v1"), model


def llm_review(client, model, chunk_text):
    """军师通读: 返回最小编辑建议 JSON 数组 (可能为 None)。"""
    sysmsg = (
        "你是一位资深学术翻译审校（军师）。下面是一篇论文的中文译文，按段落顺序编号 "
        "[S<序号>]。分段翻译导致少数段落存在局部视角的问题。请通读全文，只报告四类问题：\n"
        "1) 跨段指代：某段开头的指代词(它/该方法/上述等)指代错误或缺指代对象\n"
        "2) 接缝断裂：某段开头/结尾因跨页切分导致句子不连贯\n"
        "3) 术语漂移：同一概念前后译法不一致\n"
        "4) 文风突变：某段口语化/翻译腔突兀\n\n"
        "输出格式（最小编辑，严禁整段重写）：JSON 数组，每项:\n"
        '{"idx": <段序号>, "issue": "<问题简述>", '
        '"find": "<需修正的片段，必须逐字复制自该段译文>", '
        '"replace": "<修正后的片段>"}\n'
        "硬性要求:\n"
        "- find 必须是该段译文中的连续原文片段(逐字一致，含标点)\n"
        "- find 与 replace 中的 {vN} 占位符必须原样保留，不得增删改\n"
        "- 只报告确有必要的问题；没有问题则输出 []"
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.1,
        messages=[{"role": "system", "content": sysmsg},
                  {"role": "user", "content": chunk_text}],
    )
    return extract_json_array(resp.choices[0].message.content)


def load_curated(path: str, segs, allow_drop: bool = False):
    """从人工裁剪清单载入修正: 重新校验 find 唯一性 + 锚点, 就地重建 revised。

    清单格式: {"corrections": [{"idx", "issue", "find", "replace"}, ...]}
    all-or-nothing: 任一项校验失败即返回 None, 不应用任何修正。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("corrections", []) if isinstance(data, dict) else data
    out = []
    for it in items:
        if not isinstance(it, dict):
            print(f"  ✗ 非dict项: {str(it)[:60]}")
            return None
        idx = it.get("idx")
        find = str(it.get("find", ""))
        replace = str(it.get("replace", ""))
        seg = next((s for s in segs if s["seq"] == idx), None)
        if seg is None:
            print(f"  ✗ S{idx} 段不存在 (合法范围 0-{len(segs) - 1})")
            return None
        n = seg["trans"].count(find)
        if n != 1:
            print(f"  ✗ S{idx} find 非唯一命中({n}次): {find[:44]!r}")
            return None
        revised = seg["trans"].replace(find, replace, 1)
        if not tokens_ok(seg["raw"], revised, allow_drop=allow_drop):
            print(f"  ✗ S{idx} 锚点失败 (占位符新增/改号): {find[:40]!r}")
            return None
        out.append({"idx": idx, "issue": str(it.get("issue", "")),
                    "find": find, "replace": replace, "revised": revised})
    return out


# ---------------------------------------------------------------- LLM 通读流程
def llm_review_flow(segs, allow_drop: bool = False):
    """LLM 通读主流程: 分块送审 + 逐条锚点校验。返回采纳的修正列表。"""
    client, model = llm_client()

    # 分块通读 (字符预算内按段切, 相邻块重叠 1 段保持接缝视野)
    chunks, cur, cur_len = [], [], 0
    for s in segs:
        L = len(s["trans"]) + 30
        if cur and cur_len + L > CHUNK_CHAR_BUDGET:
            chunks.append(cur)
            cur = [cur[-1]]          # 重叠 1 段
            cur_len = len(cur[0]["trans"]) + 30
        cur.append(s)
        cur_len += L
    if cur:
        chunks.append(cur)
    print(f"通读分块: {len(chunks)} 块 (预算 {CHUNK_CHAR_BUDGET} 字符/块)")

    corrections, seen = [], set()
    for ci, chunk in enumerate(chunks):
        parts = []
        for s in chunk:
            parts.append(f"[S{s['seq']}] {s['trans']}")
        try:
            items = llm_review(client, model, "\n\n".join(parts))
        except Exception as e:
            print(f"  块{ci}: LLM 调用失败 {e}")
            continue
        items = items or []
        if ci == 0 and items:
            print(f"  [诊断] 首条建议结构: {json.dumps(items[0], ensure_ascii=False)[:200]}")
        kept = 0
        for it in items:
            if not isinstance(it, dict):
                print(f"  ✗ 非dict项: {str(it)[:60]}")
                continue
            raw_idx = it.get("idx", it.get("段落", it.get("segment", "")))
            m = re.search(r"\d+", str(raw_idx))
            if not m:
                print(f"  ✗ idx无法解析: {str(raw_idx)[:40]}")
                continue
            idx = int(m.group())
            find = str(it.get("find", "")).strip()
            replace = str(it.get("replace", "")).strip()
            if not find or not replace:
                print(f"  ✗ S{idx} find/replace 缺失: {str(it)[:80]}")
                continue
            if idx in seen:
                continue
            seg = next((s for s in segs if s["seq"] == idx), None)
            if seg is None:
                print(f"  ✗ S{idx} 段不存在 (合法范围 0-{len(segs)-1})")
                continue
            trans = seg["trans"]
            if trans.count(find) != 1:
                print(f"  ✗ S{idx} find 非唯一命中({trans.count(find)}次): {find[:50]!r}")
                continue
            revised = trans.replace(find, replace, 1)
            if not tokens_ok(seg["raw"], revised, allow_drop=allow_drop):
                print(f"  ✗ S{idx} 锚点失败 (replace 改动了占位符)")
                continue
            corrections.append({"idx": idx, "issue": str(it.get("issue", it.get("问题", ""))),
                                "find": find, "replace": replace,
                                "revised": revised})
            seen.add(idx)
            kept += 1
        print(f"  块{ci}: 建议 {len(items)} 条, 采纳 {kept} 条")
    return corrections


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="军师层: 通读校对 + 修正写回缓存")
    ap.add_argument("--sidecar", default=SIDEcar_DEFAULT)
    ap.add_argument("--apply", action="store_true", help="实际写回缓存 (默认干跑)")
    ap.add_argument("--allow-drop", action="store_true",
                    help="允许删除占位符的修正 (仍禁止新增/改号), "
                         "用于清理卡在词中间的 cid 垃圾占位符")
    ap.add_argument("--db", default=os.path.expanduser(
        os.path.join("~", ".cache", "pdf2zh", "cache.v1.db")))
    ap.add_argument("--from-report", metavar="PATH",
                    help="跳过 LLM 通读, 从人工裁剪清单 JSON 载入修正 "
                         "(载入时重新逐条校验), 用于人工审计后的精准应用")
    args = ap.parse_args()

    segs = load_segments(args.sidecar)
    segs = [s for s in segs if len(s["raw"].strip()) > 1]
    total = sum(len(s["trans"]) for s in segs)
    print(f"军师: 载入 {len(segs)} 段 / {total} 字符 (sidecar={args.sidecar})")
    if not segs:
        return 1

    if args.from_report:
        corrections = load_curated(args.from_report, segs,
                                   allow_drop=args.allow_drop)
        if corrections is None:
            print("裁剪清单校验失败 —— all-or-nothing, 未应用任何修正")
            return 1
        print(f"裁剪清单: {len(corrections)} 条全部重校验通过")
    else:
        corrections = llm_review_flow(segs, allow_drop=args.allow_drop)

    print(f"\n军师修正总数: {len(corrections)}")
    for c in corrections:
        print(f"  S{c['idx']} [{c['issue'][:30]}] {c['revised'][:50]}…")

    # 写回缓存
    applied = 0
    if args.apply and corrections:
        import sqlite3
        from pdf2zh.cache import TranslationCache
        con = sqlite3.connect(args.db)
        cur = con.cursor()
        for c in corrections:
            seg = next(s for s in segs if s["seq"] == c["idx"])
            canon_text, seq = TranslationCache._canon_seq(seg["raw"])
            # [v24b.1] revised 是 current 序号, 库内 translation 是 canon 序号
            cur.execute("UPDATE _translationcache SET translation=? "
                        "WHERE original_text=?",
                        (canon_remap(c["revised"], seq), canon_text))
            applied += max(cur.rowcount, 0)
        con.commit()
        con.close()
        print(f"已写回缓存: {applied} 行 (force 重渲染后生效)")
    elif corrections:
        print("(干跑模式: 加 --apply 写回缓存)")

    # 审计记录 (--from-report 模式不覆盖 LLM 原始报告, 裁剪清单本身即记录)
    if not args.from_report:
        rep_dir = os.path.dirname(args.sidecar)
        with open(os.path.join(rep_dir, "strategist_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"corrections": corrections,
                       "applied": bool(args.apply and corrections)},
                      f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
