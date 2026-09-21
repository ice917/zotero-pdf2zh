# -*- coding: utf-8 -*-
"""heal_render.py — 渲染残渣治伤: 用原版同区域盖回 (只治"文字没翻错、版面坏了"的两类)

为什么需要:
  译文由 pdf2zh 重排产生。有两类伤, 译文的**文字是对的**, 只是版面坏了 —— 门禁能发现
  (post_check 第五断言), 但修不了, 因为正确形态本来就只存在于原版里, 只能盖回去:

  R) 旋转文本退化: 原版里旋转 90° 的图内标注与页边水印(实测: arXiv 编号、"CLS Token"、
     物体名 avocado/hammer/wrench/cylinder), 在译文里丢了旋转, 被拆成一列单字。
     原版同一坐标处是一整行 —— 删除残字只会让标注**消失**, 必须盖回原版。
  B) 文献区错位: 参考文献页的 [n] 编号在译文里被丢到行内或页顶(实测 p7: [7] 落在正文
     行中间、[23] 落到 [1] 之上), 条目文字本身是英文(禁汉化), 正确形态就是原版那一页。

判据(每条都要求"原版这段该原样保留", 且**译文与原版不同**才动手):
  R: 原版该处有 dir 非水平的行; 译文同区域文本与原版不等 -> redact + 盖回
  B: 译文页有 [1] 行、原版同页也有, 且原版 [1] 以下有 >=5 个 [n] 编号 -> 整区 redact + 盖回
  译文中该区域若出现成句中文(>2 个汉字)则**跳过并记账** —— 那是被翻译过的正文, 不是残渣。
  不变量: **擦的矩形 == 盖的矩形**(R 段因退化单字探出框而外扩 1.5pt, 补盖必须同样外扩,
  否则被擦白的那一圈没人补 —— 实测 p5 右侧 1pt 带残留 3.23 就是这么来的)。外扩会吃到
  译文正文(环内有汉字)或越出页面时, 退回原框不外扩并记账。

**页眉不在治伤范围**: 实测译文页眉与原版逐像素相同(差 0.00)。曾经"看起来像"字形坏,
是本工具立判据时凭空猜的, 不成立 —— 无病的部位一律不许动, 页眉若真有伤请另立判据。

链接保护(必须): `apply_redactions` 会清掉**整页**链接(实测 p7 一次吞掉 60 条)。故每页先
快照链接, redact 之后原样回插, 再交给 relink_pages 搬热区。本工具因此**必须跑在
relink_pages 之前** —— 链接触动之后再盖区域, 热区坐标就会失真。

用法:
  python tools/heal_render.py --target <成品.pdf> --original <原版.pdf> \
      [--dual] [--report <报告.txt>] [--dry-run]
  --dual: 成品是左右页(每 2 页对应原版 1 页); 缺省 mono 逐页对齐
  --dry-run: 只报告不落盘, 兼任**排版伤体检**: 有伤未治 -> 退出码 1, 无伤 -> 0。
      治完再跑一次应报 "体检 PASS"（幂等: 已盖回原版的区域 keeps_rotation 为真, 不再重复动）。

不变量: 只治"文字没翻错、版面坏了"的伤 —— 判断不了的一律跳过并记账, 不猜不硬改。
"""
import argparse
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
LABEL = re.compile(r"^\[(\d{1,3})\]\s*")


def norm(s):
    return re.sub(r"\s+", "", s or "")


def link_key(l):
    """链接身份: 类型 + 矩形。redact 前后用它配对, 判断哪条真的没了。"""
    r = l["from"]
    return (l["kind"], round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))


def snap_links(page):
    """快照本页链接: redact 只清掉**与 redact 矩形相交**的链接(p3/p5 边缘区没链接,
    原链接照样活着), 故不能整批回插 —— 那会把活着的翻倍。回插时按 link_key 配对比对,
    只补回真的消失的那些。只搬可重建的字段(Named 要 nameddest, Goto 要 page/to,
    URI 要 uri, Launch 要 file); 缺关键字段的丢给报告, 不硬造。"""
    out, skipped = [], 0
    for l in page.get_links():
        d = {"kind": l["kind"], "from": pymupdf.Rect(l["from"])}
        k = l["kind"]
        if k == pymupdf.LINK_GOTO:
            if "page" not in l or "to" not in l:
                skipped += 1
                continue
            d["page"], d["to"] = l["page"], pymupdf.Point(l["to"])
        elif k == pymupdf.LINK_URI:
            if not l.get("uri"):
                skipped += 1
                continue
            d["uri"] = l["uri"]
        elif k == pymupdf.LINK_NAMED:
            if not l.get("nameddest"):
                skipped += 1
                continue
            d["nameddest"] = l["nameddest"]
        elif k == pymupdf.LINK_LAUNCH:
            if not l.get("file"):
                skipped += 1
                continue
            d["file"] = l["file"]
        else:
            skipped += 1
            continue
        out.append(d)
    return out, skipped


def dead_links(links, cut):
    """哪些链接会被 redact 吞掉: 与任一切割矩形相交的那些。

    为什么不 redact 之后再问页面: `page.get_links()` 在 `apply_redactions()` 之后
    会抛 PyMuPDF 内部错(链接表指向已消失的 xref, 实测 IndexError), 问不得。
    而"相交即删"与实测吻合(p7 一次吞 60 条, p3/p5 边距区一条不少)。"""
    return [d for d in links if any(d["from"].intersects(c) for c in cut)]


def insert_links(page, links):
    ok = 0
    for d in links:
        try:
            page.insert_link(d)
            ok += 1
        except Exception:
            pass
    return ok


def keeps_rotation(page, R, t_orig):
    """译文该区域是否**仍是旋转的一整行** —— R 的正确性判据。

    不能用"文字相等"判: 退化后的竖列 `a\\nrX\\niv\\n:2\\n6…` 去掉空白后与原版
    `arXiv:2606.15909v1 [cs.RO] …` **完全相同**(同一串字符, 只是换了换行), 实测
    这个假相等放过了 p1 全部水印与 p5 三条图注。要看的是**布局**: 该区域还有没有
    dir 非水平的行, 且其文字覆盖原版那行。"""
    try:
        d = page.get_text("dict", clip=R)
    except Exception:
        return False
    want = norm(t_orig)
    for b in d["blocks"]:
        for l in b.get("lines", []):
            if abs(l.get("dir", (1, 0))[0]) < 0.9:
                s = norm("".join(sp["text"] for sp in l["spans"]))
                if s and (s in want or want in s):
                    return True
    return False


def rot_lines(page):
    """页面里 dir 非水平的行: [(rect, text)] —— 旋转/竖排文本"""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            if abs(l.get("dir", (1, 0))[0]) < 0.9:
                t = "".join(s["text"] for s in l["spans"]).strip()
                if t:
                    out.append((pymupdf.Rect(l["bbox"]), t))
    return out


def rot_clusters(page, gap=25, dx=15):
    """把同一列里的旋转行并成一个区域: [(rect, text)]

    为什么要并: 一列图注常是好几条(实测 p5 的 avocado/hammer/wrench/cylinder 各占一行),
    逐行盖回时**行与行之间的空隙盖不到**, 而译文退化的单字恰好散落在空隙里, 残留下来
    (实测逐行盖回后该区仍有 2.18 的像素差)。并成一整列后空隙一并覆盖。"""
    lines = sorted(rot_lines(page), key=lambda x: x[0].y0)
    clusters = []
    for r, t in lines:
        placed = False
        for c in clusters:
            if abs(c["rect"].x0 - r.x0) <= dx and r.y0 - c["rect"].y1 <= gap:
                c["rect"] |= r
                c["text"].append(t)
                placed = True
                break
        if not placed:
            clusters.append({"rect": pymupdf.Rect(r), "text": [t]})
    return [(c["rect"], " ".join(c["text"])) for c in clusters]


def label_lines(page):
    """[n] 开头的行: [(rect, text)]"""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if LABEL.match(t):
                out.append((pymupdf.Rect(l["bbox"]), t))
    return out


def count_labels(page, rect):
    """rect 内以 [n] 开头的行数 —— 文献区布局判据(译文里编号被丢到行中, 这个数会掉)"""
    n = 0
    for b in page.get_text("dict", clip=rect)["blocks"]:
        for l in b.get("lines", []):
            if LABEL.match("".join(s["text"] for s in l["spans"]).strip()):
                n += 1
    return n


def text_lines(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t:
                out.append((pymupdf.Rect(l["bbox"]), t))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--dual", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    doc = pymupdf.open(args.target)
    orig = pymupdf.open(args.original)
    log = []

    def src_idx(pno):
        return pno // 2 if args.dual else pno

    n_rot = n_refs = n_link = n_skip = n_grow_off = 0
    for pno in range(len(doc)):
        page = doc[pno]
        si = src_idx(pno)
        if si >= len(orig):
            log.append("p%d SKIP 原版无对应页(%d)" % (pno + 1, si + 1))
            continue
        op = orig[si]
        W = page.rect.width
        overlays = []
        cut = []                               # 本次切割矩形(用于判定哪些链接会被吞)
        # ---- R: 旋转文本退化 ----
        for R, t_orig in rot_clusters(op):
            if R.is_empty or R.width < 1 or R.height < 1:
                continue
            if keeps_rotation(page, R, t_orig):
                continue                       # 该处仍是旋转的一整行(回填页等), 不动
            t_tgt = page.get_text(clip=R).strip()
            if len(CJK.findall(t_tgt)) > 2:
                n_skip += 1
                log.append("p%d SKIP R 该处译文是中文(%r) 不敢盖" % (pno + 1, t_tgt[:20]))
                continue
            # 擦的范围比原版那行的外框大 1.5pt: 退化后的单字常有一两个字形探出框外
            # (实测不外扩 p3/p5 仍留 0.85/1.84 的残差)。**但盖的范围必须与擦的完全相同** ——
            # 擦掉一圈却不补回来, 那一圈就成了抹白的空环(实测 p5 右侧 1pt 带与原版差 3.23;
            # 而同尺寸的补盖把它压到 0.02)。外扩若会吃到译文正文(有汉字)或越出页面,
            # 则退回原框不扩。
            rp = R + (-1.5, -1.5, 1.5, 1.5)
            if len(CJK.findall(page.get_text(clip=rp).strip())) > 2:
                n_grow_off += 1
                log.append("p%d R 外扩环内有中文, 退回原框不外扩 %r" % (pno + 1, t_tgt[:20]))
                rp = pymupdf.Rect(R)
            elif not (page.rect.contains(rp) and op.rect.contains(rp)):
                rp = pymupdf.Rect(R)
            page.add_redact_annot(rp, fill=None)
            cut.append(rp)
            overlays.append((rp, "R", t_orig, t_tgt))
            n_rot += 1

        # ---- B: 文献区错位 ----
        yA = next((r.y0 for r, t in label_lines(page) if LABEL.match(t).group(1) == "1"), None)
        yB = next((r.y0 for r, t in label_lines(op) if LABEL.match(t).group(1) == "1"), None)
        if yA is not None and yB is not None:
            nlab = sum(1 for r, t in label_lines(op) if r.y0 > yB - 1)
            if nlab >= 5:
                top = yA - 3
                box_t = pymupdf.Rect(0, top, W, page.rect.height)
                box_o = pymupdf.Rect(0, yB - 3, W, op.rect.height)
                # 布局判据: 原版该区每行都以 [n] 开头; 译文若也如此就是好的(不能只看文字,
                # 退化竖列那种"字符一样只换换行"的假相等会把坏的判成好的)
                n_t, n_o = count_labels(page, box_t), count_labels(op, box_o)
                t_tgt = page.get_text(clip=box_t).strip()
                if n_t >= n_o and n_o:
                    log.append("p%d 文献区布局已正常([n]行 %d/%d), 跳过" % (pno + 1, n_t, n_o))
                elif len(CJK.findall(t_tgt)) > 2:
                    n_skip += 1
                    log.append("p%d SKIP B 文献区含中文(%d 字) 不敢盖"
                               % (pno + 1, len(CJK.findall(t_tgt))))
                else:
                    # 只擦 [1] 行及其下方(含 [1] 自己), 免得削到上方正文末行的底部
                    for r, t in text_lines(page):
                        if r.y0 >= yA:
                            rc = pymupdf.Rect(0, r.y0 - 0.5, W, r.y1 + 0.5)
                            page.add_redact_annot(rc, fill=None)
                            cut.append(rc)
                    # [1] 之上的散落编号(实测 [23] 落在 [1] 上方)单独清掉, 不盖回
                    for r, t in label_lines(page):
                        if r.y0 < yA:
                            page.add_redact_annot(r, fill=None)
                            cut.append(r)
                            log.append("p%d 清掉上方散落编号 %s" % (pno + 1, t[:6]))
                    # 落点: **原样贴回原版的位置**(同坐标恒等拷贝), 这样这一区与原版逐像素相同。
                    # 曾经按"原版 [1] -> 译文 [1]"对齐(yB -> yA), 看着更聪明, 其实把整区相对
                    # 原版平移了 (yB-yA)pt —— 文本层照样一致, 像素差却始终 30+, 白改一场。
                    # 只有原版位置会压到上方译文(yB < yA)时才退而求其次贴到 yA, 并记账。
                    dst_top = yB if yB >= yA else yA
                    if dst_top != yB:
                        log.append("p%d 文献区按译文位置贴(yB=%.0f<yA=%.0f, 防压上方正文)"
                                   % (pno + 1, yB, yA))
                    h = min(page.rect.height - dst_top, op.rect.height - yB)
                    overlays.append((pymupdf.Rect(0, dst_top, W, dst_top + h), "B",
                                     "文献区 %d 条" % nlab, "%d 行" % len(text_lines(page))))
                    n_refs += 1

        if not overlays:
            continue
        links, lskip = snap_links(page)
        dead = dead_links(links, cut)
        page.apply_redactions()
        for R, kind, t_orig, t_tgt in overlays:
            if kind == "R":
                clip = R
            else:
                clip = pymupdf.Rect(R.x0, yB, R.x1, yB + R.height)
            try:
                page.show_pdf_page(R, orig, si, clip=clip)
            except Exception as e:
                log.append("p%d 盖回失败 %s: %s" % (pno + 1, kind, e))
                continue
            log.append("p%d %s 盖回 %r (译文原为 %r)" % (pno + 1, kind, t_orig[:26], t_tgt[:26]))
        back = insert_links(page, dead)
        n_link += back
        if back != len(dead) or lskip:
            log.append("p%d 链接 快照%d 判定被吞%d 补回%d 丢弃%d"
                       % (pno + 1, len(links), len(dead), back, lskip))

    print("R 旋转文本 %d 处 | B 文献区 %d 页 | 补回链接 %d 条 | 跳过 %d 处 | 不外扩 %d 处"
          % (n_rot, n_refs, n_link, n_skip, n_grow_off))
    if args.report:
        with io.open(args.report, "w", encoding="utf-8") as f:
            f.write("\n".join(log) + "\n")
        print("报告:", args.report)
    else:
        for line in log:
            print(" ", line)
    if args.dry_run:
        # dry-run 兼任**排版伤体检**: 治过的成品跑出来必须是 0/0, 有伤就非零退出。
        # 门禁与治伤共用同一段判据(keeps_rotation / count_labels), 不会两套口径漂移 ——
        # 这也正是它比在 post_check 里另立判据更可靠的地方(post_check 是 pypdf 文本层,
        # 看不见"旋转退化"与"[n] 错位"这类几何伤)。
        n_skip_txt = ("  (其中 %d 处因该处译文是中文而不敢动, 需人工看)" % n_skip) if n_skip else ""
        if n_rot or n_refs:
            print("体检 FAIL: 还有排版伤未治 —— R %d 处 / B %d 页%s。去掉 --dry-run 落盘治伤。"
                  % (n_rot, n_refs, n_skip_txt))
            return 1
        # 跳过处不算 FAIL(与 post_check 的"提示级"同口径): 那是"该处译文是中文, 不敢盖",
        # 需要人眼判, 但退出码只留 0/1 两态, 免得 `!= 0` 的调用方把提示当事故。
        print("体检 PASS: 无待治排版伤(旋转文本退化 / 文献区 [n] 错位)%s" % n_skip_txt)
        return 0
    if n_rot or n_refs:
        doc.saveIncr()
    # 落盘后重开核对: redact 前后链接数必须一致(get_links 在 redact 之后不可信, 只能重开问)
    if n_rot or n_refs:
        doc.close()
        chk = pymupdf.open(args.target)
        per = [len(chk[i].get_links()) for i in range(len(chk))]
        print("链接核对: %d 条 %s" % (sum(per), per))
        chk.close()
    else:
        doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
