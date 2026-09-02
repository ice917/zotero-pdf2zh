# -*- coding: utf-8 -*-
"""
tools/term_verify.py —— 存疑术语联网查证器（离线批处理，零侵入）

模块职责：
  读取 zotero-pdf2zh 产出的「存疑清单.md」，提取其中的存疑术语，
  直连 OpenAlex 学术 API 检索该术语的真实用法上下文，输出一份
  「术语 + 证据」报告，辅助人工（或 LLM）裁决译法。

关键函数：
- parse_doubt_md(path)              解析存疑清单，提取 (术语, 段落号, 冲突描述)
- openalex_search(term, n, timeout) 检索术语的学术上下文证据
- build_report(items)               组装 Markdown 报告
- main()                            命令行入口

设计原则（针对 AI Butler 联动场景）：
1. **直连 OpenAlex，不经过 local_mcp_server(8788)**。
   OpenAlex 是零 Key 公开 API（日配额 10 万次），无需走本地 MCP。
   这样可以完全规避三条耦合风险：
     - 共享缓存污染（8788 的 _caches 是进程级全局，会被按 LRU 挤占）
     - 熔断级联（8788 变慢 → qwen_proxy 超时重试 → CircuitBreaker 开闸）
     - 外部 API 限流竞争
   对 AI Butler 零影响。
2. **优雅降级**：任一术语查询失败只跳过该项，不影响整体；
   网络不可用时不产出报告，退出码非 0。
3. **礼貌限流**：请求间隔可配（默认 1.0s），遵守 OpenAlex 礼貌池约定。
4. **只读**：不修改任何翻译产物，不写入术语表，仅输出报告文件。

用法：
  python tools/term_verify.py                         # 处理最新的存疑清单
  python tools/term_verify.py <存疑清单.md>           # 指定文件
  python tools/term_verify.py <文件> --limit 10       # 只查前 10 个术语
  python tools/term_verify.py <文件> --delay 2.0      # 放慢请求间隔
"""

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

OPENALEX_API = "https://api.openalex.org/works"
# 礼貌池：带 mailto 进优先通道（可选，留空也能用）
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "").strip()
USER_AGENT = "zotero-pdf2zh-term-verify/1.0 (mailto:%s)" % (OPENALEX_EMAIL or "anonymous")

REVIEW_DIRNAME = "review"


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------
def parse_doubt_md(path):
    """
    解析存疑清单，返回 [{'term': 术语, 'para': 段落号, 'note': 冲突描述}, ...]

    清单结构（由 pdf2zh 润色管线产出）：
        ## 存疑段落 3
        **原文**
        ...
        **初译(保持未动)**
        ...
        **存疑点**
        1. 术语"topology optimization"
           - 冲突点：...
        ---
    """
    text = open(path, encoding="utf-8").read()

    # 按 "## 存疑段落 N" 切块
    chunks = re.split(r"^##\s*存疑段落\s*(\d+)\s*$", text, flags=re.M)
    # chunks = [前缀, 段号, 内容, 段号, 内容, ...]
    items = []
    seen = set()

    for i in range(1, len(chunks) - 1, 2):
        para_no = chunks[i]
        body = chunks[i + 1]

        # 只取 "**存疑点**" 之后的部分
        m = re.search(r"\*\*存疑点\*\*(.*?)(?=\n---|\Z)", body, flags=re.S)
        if not m:
            continue
        doubt_block = m.group(1)

        # 提取术语：支持 术语"xxx" / 术语“xxx” / 专有名词"xxx"
        for tm in re.finditer(
            r'(?:术语|专有名词|短语)\s*["“"]([^"“”]{1,80})["”"]', doubt_block
        ):
            term = tm.group(1).strip()
            if not term or term in seen:
                continue
            seen.add(term)

            # 取该术语后面紧邻的一行作为冲突描述
            tail = doubt_block[tm.end():tm.end() + 300]
            note = tail.split("\n")[0].strip(" ：:-")
            if not note:
                # 若同行没内容, 取下一行
                lines = [x.strip() for x in tail.split("\n") if x.strip()]
                note = lines[1].strip(" ：:-") if len(lines) > 1 else ""
            items.append({
                "term": term,
                "para": para_no,
                "note": note[:160],
            })
    return items


# ----------------------------------------------------------------------
# 检索
# ----------------------------------------------------------------------
def _is_relevant(title, term, strict=True):
    """
    相关性过滤。OpenAlex 的 search 是全文检索，通用短词会召回大量无关文献：
      - "void states"  实测召回 Ferroptosis(细胞死亡)、Ruling The Void(政治学书籍)
      - "CM"           实测召回 DC-DC 变换器、电动车充电、跨膜蛋白预测（全无关）

    strict=True  : 标题需包含术语的**全部**实词（精确优先）
    strict=False : 标题包含术语的**任一**实词（用于补足，避免 0 结果）

    对缩写/短词（如 "CM"）特殊处理：用**词边界**匹配，避免 "cm" 作为子串
    命中无关词。2026-09-02 修复：此前无实词时直接 return True 导致完全不过滤。
    """
    title_l = title.lower()
    words = [w for w in re.findall(r"[a-z]{3,}", term.lower())]

    if not words:
        # 缩写或无英文实词: 用词边界匹配整串, 而非 return True
        t = re.escape(term.lower().strip())
        if not t:
            return False
        return bool(re.search(r"\b%s\b" % t, title_l))

    if strict:
        return all(w in title_l for w in words)
    return any(w in title_l for w in words)


def openalex_search(term, max_results=5, timeout=8, context="", max_retries=4):
    """
    检索术语的学术上下文证据。
    返回 {'ok': bool, 'papers': [{'title','year','cited'}...], 'error': str}

    context: 领域上下文词（如 "topology optimization"），会拼接到检索词后，
             显著提升通用短词的召回精度（实测必加，否则噪音很大）。

    max_retries: 429 限流的指数退避重试次数。
             2026-09-02 实测 18 个术语有 5 个因 HTTP 429 失败(28%)，
             故加入退避重试：等待 2s / 4s / 8s 后重试。
    """
    query = (term + " " + context).strip() if context else term
    params = {
        "search": query,
        "per-page": max(1, min(max_results * 3, 50)),  # 多取一些, 供相关性过滤
        "select": "title,publication_year,cited_by_count",
    }
    if OPENALEX_EMAIL:
        params["mailto"] = OPENALEX_EMAIL
    url = OPENALEX_API + "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break  # 成功, 跳出重试循环
        except urllib.error.HTTPError as e:
            last_err = "HTTP %s: %s" % (e.code, e.reason)
            # 仅对 429(限流) / 5xx(服务端错误) 重试, 4xx 客户端错误不重试
            if e.code == 429 or 500 <= e.code < 600:
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)      # 2s, 4s, 8s
                    time.sleep(wait)
                    continue
            return {"ok": False, "papers": [], "error": last_err}
        except Exception as e:
            return {"ok": False, "papers": [], "error": "%s: %s" % (type(e).__name__, e)}
    else:
        return {"ok": False, "papers": [], "error": last_err or "未知错误"}

    # 分级过滤: 先严格(匹配全部实词), 不足再用宽松(匹配任一实词)补足。
    # 目的: 优先保证精确性, 同时避免严格过滤导致 0 结果(如 "void states"
    #       在论文标题里常写作 "void regions"/"void elements")。
    raw = []
    for w in data.get("results", []):
        title = (w.get("title") or "").strip()
        if not title:
            continue
        raw.append({
            "title": title,
            "year": w.get("publication_year"),
            "cited": w.get("cited_by_count", 0),
        })

    strict = [p for p in raw if _is_relevant(p["title"], term, strict=True)]
    papers = strict[:max_results]
    for p in papers:
        p["relevance"] = "strong"        # 标题含全部实词, 强证据
    if len(papers) < max_results:
        seen = {p["title"] for p in papers}
        loose = [p for p in raw
                 if p["title"] not in seen
                 and _is_relevant(p["title"], term, strict=False)]
        for p in loose:
            p["relevance"] = "weak"      # 仅含部分实词, 可能领域偏移
        papers += loose[:max_results - len(papers)]

    return {"ok": True, "papers": papers, "error": None}


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------
def build_report(src_path, results, elapsed):
    lines = []
    lines.append("# 存疑术语查证报告")
    lines.append("")
    lines.append("- 源清单：`%s`" % os.path.basename(src_path))
    lines.append("- 查证数据源：OpenAlex（零 Key 公开 API，直连，不经本地 MCP）")
    lines.append("- 术语总数：%d ｜ 查询成功：%d ｜ 失败：%d ｜ 耗时：%.1fs"
                 % (len(results),
                    sum(1 for r in results if r["res"]["ok"]),
                    sum(1 for r in results if not r["res"]["ok"]),
                    elapsed))
    lines.append("")
    lines.append("> 本报告只提供**术语的真实用法证据**，不自动改译文。")
    lines.append("> 请据此人工裁决，确认后再写入术语表。")
    lines.append("")

    for idx, r in enumerate(results, 1):
        term, para, note, res = r["term"], r["para"], r["note"], r["res"]
        lines.append("## %d. %s" % (idx, term))
        lines.append("")
        lines.append("- 存疑段落：%s" % para)
        if note:
            lines.append("- 冲突描述：%s" % note)
        if not res["ok"]:
            lines.append("- **查证失败**：%s" % res["error"])
            lines.append("")
            continue
        if not res["papers"]:
            lines.append("- **未找到高相关文献**：OpenAlex 未检索到标题含该术语的文献")
            lines.append("  （可能术语过于具体、拼写特殊，或需补充 `--context` 领域上下文）")
            lines.append("")
            continue
        lines.append("- 文献语境证据：")
        n_weak = sum(1 for p in res["papers"] if p.get("relevance") == "weak")
        if n_weak:
            lines.append("  - ⚠️ 其中 **%d 条为弱证据**（标题仅含部分词，可能领域偏移），"
                         "请优先参考未标记的强证据" % n_weak)
        for p in res["papers"]:
            y = p["year"] if p["year"] else "n.d."
            tag = "  [弱]" if p.get("relevance") == "weak" else ""
            lines.append("  - (%s, 被引 %d)%s %s" % (y, p["cited"], tag, p["title"]))
        lines.append("")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def find_latest_doubt_md(base_dir):
    """在 server/translated/review 下找最新的存疑清单"""
    pattern = os.path.join(base_dir, "server", "translated", REVIEW_DIRNAME, "存疑清单*.md")
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)  # tools/ 的上级

    ap = argparse.ArgumentParser(description="存疑术语联网查证器")
    ap.add_argument("src", nargs="?", help="存疑清单 .md 路径（默认取最新一份）")
    ap.add_argument("--limit", type=int, default=0, help="只查前 N 个术语（0=全部）")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="请求间隔秒数（礼貌限流）。默认 2.0s；"
                         "2026-09-02 实测 1.0s 时 18 个术语有 5 个遭 HTTP 429")
    ap.add_argument("--per-term", type=int, default=5, help="每术语取多少篇文献")
    ap.add_argument("--context", default=os.getenv("TERM_CONTEXT", ""),
                    help="领域上下文词，会拼到检索词后以提升通用短词的召回精度 "
                         "(例: --context 'topology optimization')；"
                         "也可用环境变量 TERM_CONTEXT 设置")
    ap.add_argument("--out", help="输出报告路径（默认与源清单同目录）")
    args = ap.parse_args()

    src = args.src or find_latest_doubt_md(project_root)
    if not src or not os.path.isfile(src):
        print("[ERROR] 未找到存疑清单，请显式指定路径", file=sys.stderr)
        return 2

    print("[INFO] 源清单: %s" % src)
    items = parse_doubt_md(src)
    print("[INFO] 提取到 %d 个存疑术语" % len(items))
    if not items:
        print("[WARN] 未提取到术语，检查清单格式是否变化", file=sys.stderr)
        return 1

    if args.limit > 0:
        items = items[:args.limit]
        print("[INFO] 按 --limit 仅处理前 %d 个" % len(items))

    if args.context:
        print("[INFO] 领域上下文: %s" % args.context)

    results = []
    t0 = time.time()
    for i, it in enumerate(items, 1):
        print("[%d/%d] 查证: %s" % (i, len(items), it["term"]))
        res = openalex_search(it["term"], max_results=args.per_term,
                              context=args.context)
        results.append({
            "term": it["term"], "para": it["para"],
            "note": it["note"], "res": res,
        })
        if i < len(items):
            time.sleep(args.delay)
    elapsed = time.time() - t0

    report = build_report(src, results, elapsed)
    out = args.out or os.path.join(
        os.path.dirname(src),
        "术语查证报告_%s.md" % time.strftime("%Y%m%d_%H%M%S"),
    )
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)

    ok = sum(1 for r in results if r["res"]["ok"])
    print("[DONE] 报告已生成: %s" % out)
    print("[DONE] 成功 %d / 共 %d ｜ 耗时 %.1fs" % (ok, len(results), elapsed))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
