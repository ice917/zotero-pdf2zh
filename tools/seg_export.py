# -*- coding: utf-8 -*-
"""seg_export.py — 从侧车导出"编号段落包"给豆包翻译 (M1 工具)

做什么:
  1. 读 C:\\Users\\<user>\\.cache\\pdf2zh\\segflow\\latest.jsonl
     (每行 = 一次 receive_layout 回调: page/pageid/segs/vars。**不是**"每行一页":
      图形对象也各占一行, 那行的 pageid 就是它所在的真实页)
  2. 过滤纯字形段 (页眉/页码/整页表格 = {vN} 组成, 无可译文字)
  3. 还原字形: {vN} -> vars[str(N)], 让豆包看到真实数字/拉丁名
  4. 检测跨页续接 (上段尾无句末标点 + 下段首小写) -> 合并为一条, 原断点插 ⋮
  5. 输出 payload 文件 (#S 编号行) + manifest.json (编号 -> 页/段映射;
     每条 part 同时记 page=侧车内部坐标 与 true_page=真实 PDF 页码)

用法 (--pages 是**真实 PDF 页码**, 与质检/体检报告同一口径; v28.10 起):
  python tools/seg_export.py --pages 2-4 --name payload_p2_p4 \
      --doc "Reproductive Biology of Cactaceae (Desert Plants, 2009)"
产出:
  D:\\zotero-pdf2zh\\inbox\\payload_p2_p4.txt
  D:\\zotero-pdf2zh\\inbox\\payload_p2_p4.manifest.json

文档抬头与术语表都可参数化 (v28.1):
  --doc   抬头文本。缺省只写页码 —— 不再内置某篇论文的抬头, 否则导出别的论文
          时会给豆包一个错误的文档先验 (旧版硬编码 Cactaceae, 导出任何一篇都带它)。
  --terms 术语表 csv (english,chinese, 无表头)。缺省 server/glossary/terms.csv;
          传空串则退化为"按学科惯例统一译名", 不注入任何具体术语。
"""
import argparse
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SIDECAR = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "segflow", "latest.jsonl")
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
INBOX = os.environ.get("P2Z_INBOX", os.path.join(PROJ, "inbox"))
TERMS_CSV = os.path.join(PROJ, "server", "glossary", "terms.csv")

V_TOKEN = re.compile(r"\{v(\d+)\}")
PURE_GLYPH = re.compile(r"^(?:\{v\d+\})+$")
TERMINAL = tuple(".!?:;)】」”']")  # 句末/收尾标点 (含引括号收口)

RULES = """[文档] {doc}第{pages}页
[任务] 把下列每个 #S 段落译成简体中文（学术书排版用），只输出译文，不要任何解释。
[规则]
1. 每段独立翻译；译文前先写一行原样的 #S编号；不许合并、拆分、增删段落。
2. 数字、拉丁学名、人名、单位、[n] 引用标号、化学式：原样保留，不译不改不移动位置。
   数字与字母连写的记号（如 3D、2D、4×4、P1、COVID-19）是一整块，整体照抄，
   不得改写成「三维」「二维」「四乘四」这类中文说法 —— 原文里的每个数字字符
   都必须在译文中原样出现，一个都不能少。
3. ⋮ 是原文分页断点：译文在语义对应的断点处保留一个 ⋮；除此之外不得出现该符号。
{terms}5. 中文通顺为学术散文，不要翻译腔；段内语序可按中文习惯调整，但事实与数字不得增减。
6. 参考文献区不得汉化：行首 [n] 的编号制条目，或行首"姓名 (年份)"的作者-年份制条目，
   整条原样保留 —— 人名/标题/期刊名/年份/卷期页/DOI/URL 一律不译、不改写、不重排、不换标点；
   正文中的行内引用照常处理。若某段整段都是文献条目，原样输出该段即可。
7. 公式片段（变量、符号、运算符、上下标及其紧邻的标点或编号连成的一串）是一整块：
   整块逐字符照抄，块内不得插入中文、不得增删或改动任何一个字符；要调整语序就把中文放在块外。
   宁可读起来别扭、甚至略有冗余，也不要拆开这块去"理顺"。
"""

TERMS_FALLBACK = "4. 术语统一：按学科惯例统一译名，同一术语全文译法一致。\n"


def load_terms(path):
    """读术语表 csv (无表头, 'english,chinese') -> [(en, zh), ...]; 缺文件即空表"""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",", 1)
            if len(parts) != 2:
                continue
            en, zh = parts[0].strip(), parts[1].strip()
            if en and zh:
                out.append((en, zh))
    return out


def terms_line(path):
    """术语表 -> RULES 第 4 条的整行; 空表/缺文件退化为通用要求(不撒谎不误导向)"""
    terms = load_terms(path)
    if not terms:
        return TERMS_FALLBACK
    return "4. 术语统一：%s。\n" % "；".join("%s=%s" % (en, zh) for en, zh in terms)


def true_page(o):
    """侧车记录的**真实 PDF 页码**(1 基); 记录里没有 pageid 时回落 None。

    侧车的 `page` 是 receive_layout 的**回调计数**, 不是页码: 图形对象也会各占
    一号 (end_figure -> receive_layout(fig)), 所以图多的论文整体漂移。真实页码
    只有 `pageid` 说得准 (LTPage.pageid, 0 基; 图形继承所在页的 pageid)。
    """
    pid = o.get("pageid")
    return None if pid is None else int(pid) + 1


def load_pages(pages_want, sidecar):
    """按**真实 PDF 页码**选页 -> (记录表, 缺页列表)。

    为什么按真实页码: 质检/体检报告说的都是真实页码, 用户在报告里读到
    "第43,44页"再照抄到 `--pages` 上是最自然的用法。而 v28.10 之前这里按回调
    计数匹配, 对图多的论文会整体漂移 —— 实测 Melhani 真实第43,44页对应侧车
    第55,56条(漂 12), `--pages 43-44` 取回的是第31,32页正文, 且库内照样命中
    12 行、段号照样连续, 一路"看起来正常"(静默错)。

    返回的记录表仍以侧车原 `page` 为键 —— 那是**载荷内部坐标**
    (manifest / imported.json / seg_inject 沿用它), 不随本函数改变;
    `pageid` 缺失的老侧车退化为按 `page` 匹配(与旧行为一致, 不误伤归档件)。
    """
    got, seen = {}, set()
    with open(sidecar, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            tp = true_page(o)
            if tp is None:                      # 老侧车没有 pageid
                if o["page"] in pages_want:
                    got[o["page"]] = o
                    seen.add(o["page"])
                continue
            if tp in pages_want:
                got[o["page"]] = o
                seen.add(tp)
    return got, [p for p in sorted(pages_want) if p not in seen]


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
    ap.add_argument("--pages", required=True,
                    help="真实 PDF 页码(与质检/体检报告同一口径), 如 2-4 或 2,3,4")
    ap.add_argument("--name", required=True, help="payload 文件名(不含扩展名)")
    ap.add_argument("--sidecar", default=SIDECAR, help="侧车路径; 新论文请先归档 latest.jsonl 再用")
    ap.add_argument("--doc", default="", help='文档抬头, 如 "标题 (期刊, 年份)"; 缺省只写页码')
    ap.add_argument("--terms", default=TERMS_CSV,
                    help="术语表 csv (english,chinese 无表头); 缺省 %s; 传空串则不注入具体术语" % TERMS_CSV)
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
    pages, missing = load_pages(pages_want, args.sidecar)
    if missing:
        print("侧车缺页: %s" % missing)
        return 1

    # ---- 收集可译段 (顺序: 页升序, 段升序) ----
    items = []   # {"parts": [(侧车坐标页, 段序, 文本, 真实页码)], "merged": bool}
    warnings = []
    for pg in sorted(pages):
        o = pages[pg]
        tp = true_page(o)
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
            items.append({"parts": [(pg, i, text, tp if tp is not None else pg)],
                          "merged": False})

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
        # 相邻性按**真实页码**判: 侧车坐标页是回调计数, 用它判会把"同页的图形记录"
        # 当成隔页, 也会把"隔着一页"当成相邻。
        pa, pb = a["parts"][-1][3], b["parts"][0][3]
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
    doc = args.doc.strip()
    if args.terms == TERMS_CSV and not os.path.exists(TERMS_CSV):
        # 沙箱/换机时 P2Z_PROJ 一改, 默认术语表就跟着落空; 静默退化会让人以为术语生效了
        warnings.append("默认术语表不存在: %s —— 本次只写通用术语要求, 用 --terms 指定" % TERMS_CSV)
    lines = [RULES.format(doc=(doc + " ") if doc else "",
                          pages=args.pages,
                          terms=terms_line(args.terms))]
    for it in items:
        lines.append("#%s" % it["key"])
        body = ""
        for j, (_pg, _idx, text, _tp) in enumerate(it["parts"]):
            if j:
                body += "⋮"
            body += text
        lines.append(body)
    manifest = {"name": args.name, "pages": args.pages, "items": []}
    for it in items:
        manifest["items"].append({
            "key": it["key"],
            "merged": it["merged"],
            # page 是**侧车内部坐标**(回调计数), imported.json / seg_inject 沿用它;
            # true_page 是**真实 PDF 页码**(给人看、给报告用)。两者都留着, 是为了让
            # 下游(seg_import 的报错与返工单)不必再自己重算一遍 pageid 口径。
            "parts": [{"page": pg, "seg": idx, "true_page": tp}
                      for pg, idx, _t, tp in it["parts"]],
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
        loc = "+".join("p%d#%d" % (tp, idx) for _pg, idx, _t, tp in it["parts"])
        t = it["parts"][-1][2] if not it["merged"] else it["parts"][-1][2][:25] + "…"
        print("  %-5s %-12s %5dch  %s" % (
            "#" + it["key"], loc,
            sum(len(p[2]) for p in it["parts"]),
            ("[合并] 头: " + it["parts"][0][2][:20] + "… 尾: " + t) if it["merged"]
            else "尾: …" + t[-25:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
