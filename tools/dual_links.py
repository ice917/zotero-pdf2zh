# -*- coding: utf-8 -*-
"""dual_links.py — 双语版(dual)双侧链接一次修好

为什么需要:
  dual 每对页 = [原版页, 译文页](0 基偶数页是原版侧, 奇数页是译文侧)。实测 GeoTLM:
    原版侧 145 条 **NAMED** + 1 条 URI —— dual 自己的命名目标树是**完好的**
        (78 条, 页码已是 dual 坐标、一律指向偶数页), 但 NAMED 依赖命名树,
        Edge 等简易阅读器点不动;
    译文侧 除 1 条 URI 外**一条都没有** —— 点了完全没反应。
  而 relink_pages / resolve_links / style_links 三件套都假设"成品与原版 1:1"
  (relink 对 dual 直接 `FAIL 页数不一致`), 故 dual 需要本工具。

两条腿:
  A) 译文侧(0 基奇数页) —— **不重新定位, 直接搬 mono**。
     实测 dual 译文页与 mono 对应页是同一份排版: rect 逐页相同; 文本差异 100%
     来自 mono 的 style_links 叠绘(`[1]` vs `[1][1]`, SequenceMatcher 只有一个
     non-equal 段且全是这种重复)。故 mono 已 relink 好的热区坐标在 dual 上原样
     成立 —— 比在译文页重新搜锚文本可靠得多(dual 的锚文本没被叠绘, 搜 `[1]` 会
     命中一片)。只做一件事: GOTO 落点页 d -> 2d+1(译文侧)。
  B) 原版侧(0 基偶数页) —— NAMED -> 显式 GOTO。目标页取 **dual 自己的**命名树
     (已是 dual 坐标、指向原版侧), y 从 PDF 底部原点转 fitz 顶部原点。

口径(用户拍板): 译文页点引用**跳译文侧**(阅读连续, Fig. 3/Sec. 4.4 落到中文
图表标题); 原版页跳原版侧(顺其原语义)。两侧各自成对, **不跨侧**。

前置门禁(不满足则 FAIL, 不猜):
  mono 与 dual 必须同源 —— 逐页比对 translate 侧文本的字符计数, dual 译文页的
  每个字符在 mono 对应页里必须**至少同样多**(mono 因叠绘只会更多)。不满足说明
  mono 不是这一版 dual 的 mono(或 mono 还没 relink 过), 搬过去就是错的。

用法(顺序: heal_render -> 本工具 -> style_links --dual):
  python tools/dual_links.py --dual <成品-dual.pdf> --mono <成品-mono.pdf> [--report <报告>]
  幂等: 译文侧是"清空重建", 再跑一次结果相同; 原版侧已无 NAMED 可转。
"""
import argparse, collections, io, os, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

norm = lambda s: __import__("re").sub(r"\s+", "", s or "")


def link_key(l):
    """链接身份 —— 用于"译文页原有的 URI/LAUNCH 里, mono 没搬来的那些"去重"""
    k = l["kind"]
    if k == pymupdf.LINK_URI:
        return (k, l.get("uri") or "")
    if k == pymupdf.LINK_LAUNCH:
        return (k, l.get("file") or "")
    r = l["from"]
    return (k, round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))


def same_source(mono, dual):
    """mono 与 dual 的译文侧是否同源: dual 每个字符在 mono 里至少同样多。

    mono 的 style_links 叠绘只会让字符**变多**, 故"dual ⊆ mono(按计数)"是同源的
    必要条件。反过来若 mono 是另一版(或没 relink 过), 计数对不上。
    逐页比对, 返回第一处不符的页码(1 基)或 None。
    """
    for k in range(len(mono)):
        ca = collections.Counter(norm(mono[k].get_text()))
        cb = collections.Counter(norm(dual[2 * k + 1].get_text()))
        lacking = {c: n - ca[c] for c, n in cb.items() if ca[c] < n}
        if lacking:
            return k + 1, lacking
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dual", required=True)
    ap.add_argument("--mono", required=True)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    doc = pymupdf.open(args.dual)
    mono = pymupdf.open(args.mono)
    if len(doc) != 2 * len(mono):
        print("FAIL dual 页数 %d ≠ mono %d × 2 —— 不是同一对成品" % (len(doc), len(mono)))
        return 1
    bad_page, lacking = same_source(mono, doc)
    if bad_page:
        print("FAIL 第 %d 对页的译文侧与 mono 不同源(缺 %s) —— "
              "mono 必须是这一版 dual 的 mono, 且已跑过 relink_pages/resolve_links。"
              % (bad_page, dict(list(lacking.items())[:5])))
        return 1

    names = doc.resolve_names()
    stat = collections.Counter()
    log = []

    for pno in range(len(doc)):
        page = doc[pno]
        k = pno // 2
        if pno % 2:
            # ---- A) 译文侧: 清空重建, 从 mono 对应页搬 ----
            old = page.get_links()
            keep = [l for l in old if l["kind"] in (pymupdf.LINK_URI, pymupdf.LINK_LAUNCH)]
            new = []
            for l in mono[k].get_links():
                if l["kind"] == pymupdf.LINK_GOTO:
                    dp = l.get("page", -1)
                    if dp < 0 or dp >= len(mono):
                        stat["src_bad"] += 1
                        log.append("p%d mono 落点越界 %s" % (k + 1, dp))
                        continue
                    d = {"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(l["from"]),
                         "page": 2 * dp + 1, "to": pymupdf.Point(l["to"])}
                elif l["kind"] in (pymupdf.LINK_URI, pymupdf.LINK_LAUNCH):
                    d = dict(l)
                    d["from"] = pymupdf.Rect(l["from"])
                else:                      # mono 上不该再出现 NAMED 等
                    stat["src_skip"] += 1
                    log.append("p%d mono 有 %d 类链接, 不搬" % (k + 1, l["kind"]))
                    continue
                new.append(d)
            have = {link_key(d) for d in new}
            for l in keep:                 # 译文页原有、mono 没搬来的 URI/LAUNCH 保住
                if link_key(l) not in have:
                    d = dict(l)
                    d["from"] = pymupdf.Rect(l["from"])
                    new.append(d)
                    stat["keep_extra"] += 1
            for l in old:
                page.delete_link(l)
            for d in new:
                try:
                    page.insert_link(d)
                    stat["tr"] += 1
                except Exception as e:
                    stat["fail"] += 1
                    log.append("p%d 译文侧插入失败: %s" % (pno + 1, e))
        else:
            # ---- B) 原版侧: NAMED -> 显式 GOTO ----
            for l in page.get_links():
                if l["kind"] != pymupdf.LINK_NAMED:
                    continue
                n = l.get("nameddest") or l.get("name") or ""
                d = names.get(n)
                if not d or d.get("page", -1) < 0 or d["page"] >= len(doc):
                    stat["named_fail"] += 1
                    log.append("p%d 命名目标缺失/越界: %s" % (pno + 1, n))
                    continue
                to = d["to"]
                tx = to[0] if isinstance(to, (tuple, list)) else to.x
                ty = to[1] if isinstance(to, (tuple, list)) else to.y
                tp = d["page"]
                tpage = doc[tp]
                ny = max(0.0, min(tpage.rect.height, tpage.rect.height - ty))
                if tx != 0:                # x=0 是"左对齐"语义, 保持
                    tx = min(max(tx, 0.0), tpage.rect.width)
                new = {kk: vv for kk, vv in l.items() if kk not in ("nameddest", "name")}
                new["kind"] = pymupdf.LINK_GOTO
                new["page"] = tp
                new["to"] = pymupdf.Point(tx, ny)
                page.delete_link(l)
                page.insert_link(new)
                stat["named"] += 1

    tmp = args.dual + ".tmp"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, args.dual)

    chk = pymupdf.open(args.dual)          # 落盘后重开核对: 逐页条数
    per = [len(p.get_links()) for p in chk]
    cross = 0
    for pno, p in enumerate(chk):
        for l in p.get_links():
            if l["kind"] == pymupdf.LINK_GOTO and (l["page"] % 2) != (pno % 2):
                cross += 1
    chk.close()

    print("译文侧补链接 %d 条(落点 -> 奇数页) | 原版侧 NAMED->GOTO %d 条 | "
          "保住译文页原有 URI %d 条 | 跨侧落点 %d" % (stat["tr"], stat["named"], stat["keep_extra"], cross))
    if stat["fail"] or stat["named_fail"] or stat["src_bad"] or stat["src_skip"]:
        print("  异常: 插入失败 %d | 命名缺失 %d | mono 落点越界 %d | 不搬的链接 %d"
              % (stat["fail"], stat["named_fail"], stat["src_bad"], stat["src_skip"]))
    print("链接核对: %d 条 %s" % (sum(per), per))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write("\n".join(log) + "\n")
        print("报告:", args.report)
    return 1 if (stat["fail"] or stat["named_fail"] or cross) else 0


if __name__ == "__main__":
    sys.exit(main())
