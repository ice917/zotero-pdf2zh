# -*- coding: utf-8 -*-
"""user_links.py — 用户自定义概念链接: 把"选中的那段字 -> 你自己给的网址"装进成品

为什么需要:
  原版超链接(引文锚 -> 文献表, 拉丁学名 -> 附录)是**继承来的** —— 读者只能点原书给的
  那几个。而"读到这里我需要一点背景知识"这类需求, 原书不会替你想到; 位置该由读者定。
  本工具把链接从"继承物"变成**可定稿资产**: 锚文本与 URL 都由用户给, 机械地装回原版面。
  与译文同一层级 —— 引文锚保留、学名锚保留、**新增**概念锚、**覆盖**原版链接(同矩形时
  用户优先)。

顺序(不可换): relink_pages -> resolve_links -> **本工具** -> style_links -> verify_links
  必须在 **style_links 之前**: 新锚要跟着一起变蓝, 否则读者看不出能点, 功能等于没做;
  必须在 **resolve_links 之后**: 新链接是 URI, 不参与 NAMED->GOTO 那一步。
  双语版**不必**另跑一遍: dual_links 的译文侧会把 mono 上的 URI 原样搬过去。

两层规格(user_links.json):
  global —— 跨篇通用的阅读偏好(如"基因漂变 -> 某百科")。`occurrence` 是**全篇第几次出现**
            (默认语义即"让概念首次出现处有个入口"), **不许带 page**。
  tasks  —— 单篇的(如"图 3 里那个概念")。`page` 必填(1 基), `occurrence` 是**该页第几次出现**。
  撞锚以**本篇为准**: tasks 里出现同一个锚, 该锚的 global 条目被跳过并记账。

"第几次出现"按**阅读顺序**(先上后下、先左后右)数, 且碎片命中先并成"一次出现"的整框 ——
直接复用 relink_pages.merge_fragments, 不并框会把 `[5]` 数成三个字, "第 3 次"跟人看到的对不上。

门禁(全部在**落盘之前**判完; 有一条不过 -> 一个字都不写):
  锚空 / URL 非 http(s) / global 带 page / tasks 缺 page / 该页(或全篇)0 命中 /
  命中 >1 却没给 occurrence / occurrence 越界 / 两条新锚抢同一块矩形
  —— 一律 FAIL 并点名, 不挑"最像的那一处"。
压住**原有**引文锚不算错(用户手写的意图优先于继承来的), 但删了哪一条要记账、报告点名。

用法:
  python tools/user_links.py --target <成品.pdf> [--spec <user_links.json>] [--task <任务名>]
                             [--dual] [--report <报告>]
  python tools/user_links.py --target <成品.pdf> --check [...]          只预检, 不落盘(面板提示的来源)
  python tools/user_links.py --target <成品.pdf> --page 7 --list-lines  列本页文字(面板选字)
"""
import argparse, io, json, os, re, sys

# 用 reconfigure 而不是 `sys.stdout = io.TextIOWrapper(...)`: 后者每执行一次就多包一层,
# 上一层包装器被回收时会**关掉同一个底层 buffer**, 于是"本工具 + 它 import 的
# relink_pages"这种链式 import 会让 stdout 变成 closed file(实测: 打印第二行就崩)。
# reconfigure 就地改编码, 重复执行是幂等的 —— table_pipe/* 一直用这个写法。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relink_pages import merge_fragments      # 同一把尺子: "一次出现"= 并框之后的一个框

norm = lambda s: re.sub(r"\s+", "", s or "")
URL_OK = re.compile(r"^https?://\S+$")


def find_hits(page, anchor):
    """本页按锚找框: 先**照原样**找, 找不到再退到"去掉所有空白"的形态。

    为什么不直接照 relink_pages 那样去空白: 锚是用户从**本页文字**里点来的
    (见 --list-lines), 页面上明明写着 "GelSight Mini" —— 一去空白就成了页面上
    不存在的字符串。实测(2026-09-22): 『GelSight Mini』去空白后 0 命中, 面板
    把用户自己点的那一行判成"全篇找不到", 只能改成只选单个词。
    relink_pages 那边非去不可, 是因为它的锚从**另一份文件**的文本层抽出来,
    两边的空格排版本就不同; 这里锚与页面同源, 没有那个问题。
    保留去空白这一档是为了**手工敲**进来的、跨行断开的锚(面板只能按行点选)。
    """
    hits = page.search_for(anchor)
    if not hits and norm(anchor) != anchor:
        hits = page.search_for(norm(anchor))
    return hits


PROJ = os.environ.get("P2Z_PROJ") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------------- 规格装载
def load_spec(path, task):
    """规格 -> (entries, notices)。entry 带 scope; 撞锚时本篇覆盖全局。

    `--task` 缺席时只装 global —— 但若规格里确实有 tasks 段, 必须**说出来**,
    否则用户会以为"怎么没生效"却查不出原因(静默失效是这套工具最怕的形态)。
    """
    with io.open(path, encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, dict):
        raise ValueError("规格根必须是对象: {\"global\": [...], \"tasks\": {...}}")

    ent, notices = [], []
    for e in (spec.get("global") or []):
        ent.append(dict(e, scope="全局"))
    tasks = spec.get("tasks") or {}
    own = []
    if task:
        own = [dict(e, scope="本篇") for e in (tasks.get(task) or [])]
    elif tasks:
        notices.append("NOTICE 规格里有 %d 个任务的条目, 但没给 --task, 这些条目本次未生效: %s"
                       % (len(tasks), ", ".join(sorted(tasks))))
    own_anchors = {norm(e.get("anchor") or "") for e in own}
    kept = []
    for e in ent:
        if norm(e.get("anchor") or "") in own_anchors:
            notices.append("本篇覆盖全局: 『%s』" % e["anchor"])
            continue
        kept.append(e)
    return kept + own, notices


# ------------------------------------------------------------ 页映射(dual)
def page_index(p_mono, dual):
    """规格里的页码一律是 **mono 坐标(1 基)**; dual 时同一页的译文侧是 2p-1(0 基奇数)。

    与 style_links --dual 同一换算口径 —— 不换算就会去绑原版页(英文侧), 锚自然找不到。
    """
    return 2 * p_mono - 1 if dual else p_mono - 1


def doc_pages(n, dual):
    """按 mono 页序给出的**目标页 0 基索引**序列(dual 只取译文侧)。"""
    return list(range(1, n, 2)) if dual else list(range(n))


# ------------------------------------------------------------------- 定位
def occ_list(doc, pages, anchor):
    """全篇按阅读顺序的 [(页0基, 框)] —— "第 N 次出现"的判据就是这份列表。"""
    out = []
    for pno in pages:
        hits = merge_fragments(find_hits(doc[pno], anchor))
        for b in sorted(hits, key=lambda r: (round(r.y0, 1), round(r.x0, 1))):
            out.append((pno, b))
    return out


def plan_entry(doc, pages, e, dual):
    """一条规格 -> 计划(含 FAIL 的判据)。返回 dict(status, detail, pno, rect, ...)。"""
    d = dict(e)
    d["status"], d["detail"], d["pno"], d["rect"] = "fail", "", None, None
    # raw 用来**搜**, anchor_n 用来**认**(撞锚/报告里的键) —— 两者不能混: 一开头就把
    # raw 归一化再拿去搜, find_hits 里的"原样"那一档就永远走不到(实测: 传进来的
    # 已经是去空白的 "GelSightMini", 页面上没这个串, 于是照旧判"全篇找不到")。
    raw = (e.get("anchor") or "").strip()
    anchor = norm(raw)
    d["anchor_n"] = anchor
    if not anchor:
        d["detail"] = "锚文本为空"
        return d
    url = (e.get("url") or "").strip()
    if not URL_OK.match(url):
        d["detail"] = "URL 必须以 http:// 或 https:// 开头: %r" % url
        return d

    scope = e["scope"]
    if scope == "全局":
        if e.get("page") not in (None, ""):
            d["detail"] = "全局条目不许带 page(它要跨篇用) —— 要在某一页生效请改成本篇条目"
            return d
        cand = occ_list(doc, pages, raw)
        where = "全篇"
    else:
        pg = e.get("page")
        if pg in (None, ""):
            d["detail"] = "本篇条目必须给 page(1 基)"
            return d
        try:
            pg = int(pg)
        except (TypeError, ValueError):
            d["detail"] = "page 不是整数: %r" % (e.get("page"),)
            return d
        n_mono = len(pages) if not dual else len(doc) // 2
        if not (1 <= pg <= n_mono):
            d["detail"] = "page %d 越界(本篇 mono 共 %d 页)" % (pg, n_mono)
            return d
        pno = page_index(pg, dual)
        cand = [(pno, b) for b in sorted(merge_fragments(find_hits(doc[pno], raw)),
                                        key=lambda r: (round(r.y0, 1), round(r.x0, 1)))]
        where = "第 %d 页" % pg

    if not cand:
        # 报**用户原样写的**锚, 不报去空白后的 anchor_n —— 报后者会让人对着
        # "GelSightMini" 发懵(他自己写的是 "GelSight Mini")。
        d["detail"] = "%s找不到『%s』—— 锚要连续出现在同一行里, 空格会被忽略" % (
            where, (e.get("anchor") or "").strip())
        return d
    occ = e.get("occurrence")
    if occ in (None, ""):
        if len(cand) > 1:
            d["detail"] = "%s命中 %d 处, 请给 occurrence 指定第几处" % (where, len(cand))
            return d
        occ = 1
    try:
        occ = int(occ)
    except (TypeError, ValueError):
        d["detail"] = "occurrence 不是正整数: %r" % (e.get("occurrence"),)
        return d
    if not (1 <= occ <= len(cand)):
        d["detail"] = "%s只命中 %d 处, occurrence=%d 越界" % (where, len(cand), occ)
        return d

    pno, rect = cand[occ - 1]
    d.update(status="ok", pno=pno, rect=pymupdf.Rect(rect), occurrence=occ,
             hits=len(cand), where=where)
    d["detail"] = "%s命中 %d 处, 取第 %d 处" % (where, len(cand), occ)
    return d


def check_overlaps(plans):
    """两条新锚抢同一块矩形 -> 都点名。用户自己写重的两条, 不该由机器挑一条执行。"""
    ok = [p for p in plans if p["status"] == "ok"]
    bad = []
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            a, b = ok[i], ok[j]
            if a["pno"] == b["pno"] and a["rect"].intersects(b["rect"]):
                bad.append((a, b))
                b["status"] = "fail"
                b["detail"] = "与另一条新锚抢同一块矩形: 『%s』(%s) vs 『%s』(%s)" % (
                    a["anchor_n"], a["where"], b["anchor_n"], b["where"])
                a["status"] = "fail"
                a["detail"] = "与另一条新锚抢同一块矩形: 『%s』(%s) vs 『%s』(%s)" % (
                    a["anchor_n"], a["where"], b["anchor_n"], b["where"])
    return bad


# --------------------------------------------------------------------- 主
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--spec", default=os.path.join(PROJ, "user_links.json"))
    ap.add_argument("--task", default="", help="本篇任务名(规格 tasks 段的键)")
    ap.add_argument("--dual", action="store_true",
                    help="成品是双语版(规格页码按 mono 坐标, 落到译文侧)")
    ap.add_argument("--check", action="store_true", help="只预检, 不落盘")
    ap.add_argument("--page", type=int, default=0, help="配合 --list-lines: 列第几页(mono 坐标)")
    ap.add_argument("--list-lines", action="store_true", help="列本页文字(面板选字用)")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    doc = pymupdf.open(args.target)

    # ---- 面板选字: 列本页文字(不改文件) ----
    if args.list_lines:
        if not args.page:
            print("FAIL --list-lines 需要 --page")
            return 1
        pno = page_index(args.page, args.dual)
        if not (0 <= pno < len(doc)):
            print("FAIL 第 %d 页越界(共 %d 页)" % (args.page, len(doc)))
            return 1
        lines, seen = [], set()
        for ln in doc[pno].get_text("text").splitlines():
            t = ln.strip()
            if len(t) < 2 or t in seen:
                continue
            seen.add(t)
            lines.append(t[:120])
        # pages 一并回带: 面板要它来**限住页码输入框**, 否则用户填到第 99 页才被告知越界。
        n_mono = len(doc) // 2 if args.dual else len(doc)
        print(json.dumps({"page": args.page, "pages": n_mono, "total": len(doc),
                          "lines": lines[:400]}, ensure_ascii=False))
        return 0

    if args.dual and len(doc) % 2:
        print("FAIL --dual 但页数是奇数(%d), 这不是双语版成品" % len(doc))
        return 1
    if not os.path.exists(args.spec):
        print("FAIL 规格文件不存在: %s" % args.spec)
        return 1

    try:
        entries, notices = load_spec(args.spec, args.task)
    except Exception as e:
        print("FAIL 规格无法解析(%s): %s" % (args.spec, e))
        return 1

    pages = doc_pages(len(doc), args.dual)
    plans = [plan_entry(doc, pages, e, args.dual) for e in entries]
    check_overlaps(plans)

    fails = [p for p in plans if p["status"] != "ok"]

    # ---- 预检: 到此为止, 一个字都不写 ----
    if args.check:
        for p in plans:
            mark = "✓" if p["status"] == "ok" else "✗"
            scope = "%s%s" % (p["scope"], ("/p%d" % p["page"]) if p["scope"] == "本篇" else "")
            print("  %s [%s] 『%s』 -> %s" % (mark, scope, p["anchor_n"], p.get("url", "")))
            print("      %s" % p["detail"])
        for n in notices:
            print("  " + n)
        print("预检: 可装 %d 条 | 不通过 %d 条%s"
              % (len(plans) - len(fails), len(fails), "" if not fails else " —— 修好再跑"))
        return 1 if fails else 0

    if fails:
        print("FAIL %d 条不通过, 成品未改动:" % len(fails))
        for p in fails:
            print("  『%s』(%s) %s" % (p["anchor_n"], p["scope"], p["detail"]))
        for n in notices:
            print("  " + n)
        return 1

    # ---- 应用: 用户手写的意图优先于继承来的 -> 压住原有链接就删掉它, 但要记账 ----
    stat, log = [], []
    for p in plans:
        page = doc[p["pno"]]
        for l in page.get_links():
            if pymupdf.Rect(l["from"]).intersects(p["rect"]):
                kind = {pymupdf.LINK_GOTO: "引文锚(GOTO)", pymupdf.LINK_URI: "原有超链接(URI)",
                        pymupdf.LINK_NAMED: "命名链接(NAMED)"}.get(l["kind"], "链接%d" % l["kind"])
                page.delete_link(l)
                stat.append(("覆盖", p["pno"] + 1, p["anchor_n"], l["kind"]))
                log.append("p%d 覆盖原有 %s -> %s" % (p["pno"] + 1, kind, p["anchor_n"]))
        page.insert_link({"kind": pymupdf.LINK_URI, "from": p["rect"],
                          "uri": p["url"]})
        stat.append(("新装", p["pno"] + 1, p["anchor_n"], None))

    tmp = args.target + ".tmp"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, args.target)

    # ---- 落盘后重开核对: 计划里的每一条都必须在成品里找得到(不信内存) ----
    chk = pymupdf.open(args.target)
    placed = 0
    for p in plans:
        hit = any(l["kind"] == pymupdf.LINK_URI and (l.get("uri") or "") == p["url"]
                  and pymupdf.Rect(l["from"]).intersects(p["rect"])
                  for l in chk[p["pno"]].get_links())
        if hit:
            placed += 1
        else:
            log.append("落盘后核对失败: p%d 『%s』没找到对应 URI 链接" % (p["pno"] + 1, p["anchor_n"]))
    chk.close()

    n_cov = sum(1 for s in stat if s[0] == "覆盖")
    n_glob = sum(1 for p in plans if p["scope"] == "全局")
    print("概念链接: 新装 %d 条(全局 %d / 本篇 %d) | 覆盖原有链接 %d 条 | 落盘后核对 %d/%d"
          % (len(plans), n_glob, len(plans) - n_glob, n_cov, placed, len(plans)))
    for n in notices:
        print("  " + n)
    print("  顺序提醒: 本工具必须跑在 style_links 之前 —— 接着跑 "
          "`python tools/style_links.py --target <成品>%s`, 新锚才会变蓝。"
          % (" --dual" if args.dual else ""))
    if args.report:
        with io.open(args.report, "w", encoding="utf-8") as f:
            f.write("动作\t页\t锚\t原链接kind\n")
            for s in stat:
                f.write("%s\t%d\t%s\t%s\n" % (s[0], s[1], s[2], s[3] if s[3] is not None else ""))
            for ln in log:
                f.write("\t\t%s\t\n" % ln)
        print("  报告:", args.report)
    # 覆盖原有链接是**正常动作**(用户手写的意图优先于继承来的), 只报不拦 ——
    # 拦的只有"落盘后核对不上": 那是文件真出问题了。两者混在一个 log 里看过一次,
    # 结果是"每跑一次正常覆盖就退 1", 面板会把它当失败。
    for ln in log:
        print("  " + ln)
    return 1 if placed != len(plans) else 0


if __name__ == "__main__":
    sys.exit(main())
