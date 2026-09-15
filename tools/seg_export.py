# -*- coding: utf-8 -*-
"""seg_export.py — 从侧车导出"编号段落包"给豆包翻译 (M1 工具)

做什么:
  1. 读 C:\\Users\\<user>\\.cache\\pdf2zh\\segflow\\latest.jsonl (每行一页: segs+vars)
  2. 过滤纯字形段 (页眉/页码/整页表格 = {vN} 组成, 无可译文字)
  3. 还原字形: {vN} -> vars[str(N)], 让豆包看到真实数字/拉丁名
  4. 检测跨页续接 (上段尾无句末标点 + 下段首小写) -> 合并为一条, 原断点插 ⋮
  5. 输出 payload 文件 (#S 编号行) + manifest.json (编号 -> 页/段映射)

用法:
  python tools/seg_export.py --pages 2-4 --name payload_p2_p4
产出:
  D:\\zotero-pdf2zh\\inbox\\payload_p2_p4.txt
  D:\\zotero-pdf2zh\\inbox\\payload_p2_p4.manifest.json
"""
import argparse
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SIDECAR = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "segflow", "latest.jsonl")
INBOX = os.environ.get("P2Z_INBOX", r"D:\zotero-pdf2zh\inbox")

V_TOKEN = re.compile(r"\{v(\d+)\}")
PURE_GLYPH = re.compile(r"^(?:\{v\d+\})+$")
TERMINAL = tuple(".!?:;)】」”']")  # 句末/收尾标点 (含引括号收口)

RULES = """[文档] Reproductive Biology of Cactaceae (Desert Plants, 2009) 第{pages}页
[任务] 把下列每个 #S 段落译成简体中文（学术书排版用），只输出译文，不要任何解释。
[规则]
1. 每段独立翻译；译文前先写一行原样的 #S编号；不许合并、拆分、增删段落。
2. 数字、拉丁学名、人名、单位、[n] 引用标号、化学式：原样保留，不译不改不移动位置。
3. ⋮ 是原文分页断点：译文在语义对应的断点处保留一个 ⋮；除此之外不得出现该符号。
4. 术语统一：Cactaceae=仙人掌科；herkogamy=雌雄异位；breeding system=繁殖系统；
   successful gamete(s)=可育配子；selfing/selfer=自交/自交个体；outcrossing=异交。
5. 中文通顺为学术散文，不要翻译腔；段内语序可按中文习惯调整，但事实与数字不得增减。
"""


def load_pages(pages_want, sidecar):
    got = {}
    with open(sidecar, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if o["page"] in pages_want:
                got[o["page"]] = o
    return got


def restore(raw, vars_):
    def sub(m):
        return vars_.get(m.group(1), m.group(0))
    text = V_TOKEN.sub(sub, raw).strip()
    # PDF 换行断词, 两种情况区分处理:
    #   音节断词 (后随小写): "obser- vation" -> "observation", "Astera- ceae" -> "Asteraceae"
    #   复合名断词 (后随大写): "Lovett- Doust" -> "Lovett-Doust", 保留连字符只删空格
    text = re.sub(r"([A-Za-z])- (?=[a-z])", r"\1", text)
    text = re.sub(r"([A-Za-z])- (?=[A-Z])", r"\1-", text)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", required=True, help="如 2-4 或 2,3,4")
    ap.add_argument("--name", required=True, help="payload 文件名(不含扩展名)")
    ap.add_argument("--sidecar", default=SIDECAR, help="侧车路径; 新论文请先归档 latest.jsonl 再用")
    ap.add_argument("--force", action="store_true", help="续接检测失败也照常导出(逐段独立)")
    args = ap.parse_args()

    # 页码解析: 支持 "2-4" / "2,3,4" / 混合 "1,21-22" (逗号分隔, 每项为单页或区间)
    pages_want = set()
    for tok in args.pages.split(","):
        if "-" in tok:
            a, b = (int(x) for x in tok.split("-"))
            pages_want.update(range(a, b + 1))
        else:
            pages_want.add(int(tok))
    pages = load_pages(pages_want, args.sidecar)
    missing = [p for p in sorted(pages_want) if p not in pages]
    if missing:
        print("侧车缺页: %s" % missing)
        return 1

    # ---- 收集可译段 (顺序: 页升序, 段升序) ----
    items = []   # {"parts": [(page, idx, text)], "merged": bool}
    warnings = []
    for pg in sorted(pages):
        o = pages[pg]
        vv = o.get("vars") or {}
        for i, s in enumerate(o["segs"]):
            raw = (s.get("raw") or "").strip()
            if not raw or PURE_GLYPH.match(raw):
                continue
            text = restore(raw, vv)
            if V_TOKEN.search(text):
                warnings.append("p%d#%d 仍有未还原占位符: %s" % (pg, i, V_TOKEN.findall(text)[:5]))
            if not re.search(r"[A-Za-z0-9]", text):
                continue
            items.append({"parts": [(pg, i, text)], "merged": False})

    # ---- 跨页续接检测与合并 ----
    def tail(t):
        return t[-1]

    def head(t):
        return t[0]

    merged_log = []
    i = 0
    while i < len(items) - 1:
        a, b = items[i], items[i + 1]
        ta = a["parts"][-1][2]
        tb = b["parts"][0][2]
        pa, pb = a["parts"][-1][0], b["parts"][0][0]
        # 页码必须物理相邻才可能跨页续接; 离散页集(如 1,21-22)不得跨空隙合并
        if pb == pa + 1 and tail(ta) not in TERMINAL and (tb[0].islower() or tb[0].isdigit()):
            a["parts"].append(b["parts"][0])
            a["merged"] = True
            items.pop(i + 1)
            merged_log.append("合并: p%d 段尾 + p%d 段头 (断点⋮)" % (pa, pb))
            continue
        i += 1

    # ---- 编号 ----
    for n, it in enumerate(items, 1):
        it["key"] = "S%d" % n

    # ---- 输出 payload ----
    lines = [RULES.replace("{pages}", args.pages)]
    for it in items:
        lines.append("#%s" % it["key"])
        body = ""
        for j, (pg, idx, text) in enumerate(it["parts"]):
            if j:
                body += "⋮"
            body += text
        lines.append(body)
    manifest = {"name": args.name, "pages": args.pages, "items": []}
    for it in items:
        manifest["items"].append({
            "key": it["key"],
            "merged": it["merged"],
            "parts": [{"page": pg, "seg": idx} for pg, idx, _ in it["parts"]],
        })

    os.makedirs(INBOX, exist_ok=True)
    p_txt = os.path.join(INBOX, args.name + ".txt")
    p_man = os.path.join(INBOX, args.name + ".manifest.json")
    with open(p_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(p_man, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # ---- 报告 ----
    total = sum(len(p[2]) for it in items for p in it["parts"])
    print("payload: %s (%d 段 / %d 原文字符)" % (p_txt, len(items), total))
    for m in merged_log:
        print("  " + m)
    for w in warnings:
        print("  [警告] " + w)
    for it in items:
        loc = "+".join("p%d#%d" % (pg, idx) for pg, idx, _ in it["parts"])
        t = it["parts"][-1][2] if not it["merged"] else it["parts"][-1][2][:25] + "…"
        print("  %-5s %-12s %5dch  %s" % (
            "#" + it["key"], loc,
            sum(len(p[2]) for p in it["parts"]),
            ("[合并] 头: " + it["parts"][0][2][:20] + "… 尾: " + t) if it["merged"]
            else "尾: …" + t[-25:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
