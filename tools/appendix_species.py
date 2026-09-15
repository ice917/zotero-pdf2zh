# -*- coding: utf-8 -*-
"""appendix_species.py — 物种中文名对照: 书末附录页 + 正文学名弹窗注释
1) 若缺附录页则追加(第 35 页, 不插页不动页码): 学名-中文名对照表, 置信度分级。
2) 全书学名位置挂弹窗注释(矩形钉成 4pt 小点, 无可见图标): 点击弹中文名, 原地
   不跳转、不遮挡正文。
幂等: 重跑先清旧注释/链接(仅物种类), 附录页存在则跳过绘制。
数据: species_zh.json (中文名来自 iPlant/PPBC/多肉联萌/中文维基, 置信度高/中/低;
查无通行名如实标注 null —— 红线: 绝不编造)。

用法:
  python tools/appendix_species.py [--pdf <成品.pdf>] [--data <species_zh.json>] \\
      [--font <CJK字体.otf>]
  默认取本仓库 out/成品/Cactaceae2009_中文版.pdf; 换文献请显式传 --pdf。
  本工具**就地修改** --pdf, 建议先备份。
"""
import io, json, re, sys, os, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_PDF = os.path.join(ROOT, 'out', '成品', 'Cactaceae2009_中文版.pdf')
DEF_DATA = os.path.join(ROOT, 'out', 'species_zh.json')
DEF_FONT = os.path.join(ROOT, 'server', 'fonts', 'NotoSerifCJKsc-Regular.otf')
APP_PAGE = 34          # 0 基: 附录追加为第 35 页
APP_Y0 = 144.0         # 附录条目起始 y(与绘制一致)
APP_ROW_H = 19.5
APP_COL_X = [72, 322]
APP_PER_COL = 34
LIG = [('fi', 'ﬁ'), ('fl', 'ﬂ'), ('ff', 'ﬀ'), ('ffi', 'ﬃ')]
MARK = {'高': '●', '中': '◐', '低': '○'}


def variants(name):
    out = {name}
    for a, b in LIG:
        for x in list(out):
            if a in x:
                out.add(x.replace(a, b))
    return out


def draw_appendix(page, data, font):
    """绘制附录页, 返回条目起始 y (供链接定位, 与绘制永远一致)"""
    page.insert_text((72, 72), '附录　物种中文名对照', fontsize=14,
                     fontfile=font, fontname='ZH', color=(0, 0, 0))
    intro = ('本页收录正文与表 10.2 出现的 68 个物种。中文名为园艺/文献通行名, '
             '置信度以 ●(高) ◐(中) ○(低) 标注; — 表示暂未查到通行中文名, 拉丁学名原样保留。'
             '数据来源: iPlant/PPBC、多肉联萌对照表、中文维基百科等 (2026-09 查证)。'
             '原书个别拼写疑误处原样收录并加注。正文中点按学名可弹出本条目中文名。')
    y = 88
    for seg in re.findall(r'.{1,46}', intro):
        page.insert_text((72, y), seg, fontsize=7.2, fontfile=font,
                         fontname='ZH', color=(0.25, 0.25, 0.25))
        y += 10
    y += 6
    for idx, (latin, d) in enumerate(sorted(data.items())):
        col = idx // APP_PER_COL
        row = idx % APP_PER_COL
        x = APP_COL_X[col]
        yy = y + row * APP_ROW_H
        page.insert_text((x, yy), latin, fontsize=6.8, fontname='helv', color=(0, 0, 0))
        zh = d.get('zh')
        conf = d.get('conf')
        ztxt = ((zh if zh else '—') + (' ' + MARK.get(conf, '') if zh else ''))
        page.insert_text((x, yy + 9), ztxt, fontsize=7.6, fontfile=font,
                         fontname='ZH', color=(0, 0, 0))
    return y


def main():
    ap = argparse.ArgumentParser(description='物种中文名附录页 + 正文学名弹窗注释')
    ap.add_argument('--pdf', default=DEF_PDF, help='成品 PDF(就地修改, 建议先备份)')
    ap.add_argument('--data', default=DEF_DATA, help='物种中文名数据 JSON')
    ap.add_argument('--font', default=DEF_FONT, help='中日韩字体文件(.otf/.ttf)')
    ap.add_argument('--redraw', action='store_true',
                    help='附录页已存在时也删掉重绘(改导语/条目后使用)')
    args = ap.parse_args()
    for p in (args.pdf, args.data, args.font):
        if not os.path.exists(p):
            print('文件不存在: %s' % p)
            return 1

    data = json.load(open(args.data, encoding='utf-8'))
    doc = pymupdf.open(args.pdf)

    # ---------- 0) 幂等清理: 物种链接与旧注释 ----------
    removed = 0
    for pno in range(min(len(doc), APP_PAGE)):
        page = doc[pno]
        for a in list(page.annots(types=[pymupdf.PDF_ANNOT_TEXT])):
            page.delete_annot(a)
            removed += 1
        for l in list(page.get_links()):
            if l['kind'] == pymupdf.LINK_GOTO and l.get('page') == APP_PAGE:
                page.delete_link(l)
    print('清理: 旧注释/物种链接 done')

    # ---------- 1) 附录页(缺则绘制; --redraw 则先删掉旧页) ----------
    y_entries = APP_Y0
    if len(doc) > APP_PAGE and args.redraw:
        doc.delete_page(APP_PAGE)
        print('附录页: --redraw 已删除旧页')
    if len(doc) <= APP_PAGE:
        page = doc.new_page(width=612, height=792)
        y_entries = draw_appendix(page, data, args.font)
        print('附录页: 已绘制(第 %d 页), 条目起始 y=%.1f' % (APP_PAGE + 1, y_entries))
    else:
        print('附录页: 已存在, 跳过绘制')

    # ---------- 2) 学名 -> 弹窗注释(矩形钉成小点: 图标不可见级别, 点击弹中文名) ----------
    n_annot = 0
    for idx, (latin, d) in enumerate(sorted(data.items())):
        zh = d.get('zh')
        conf = d.get('conf') or ''
        tip = ('中文名：%s\n拉丁学名：%s %s\n置信度：%s' %
               (zh, latin, d.get('auth', ''), conf or '暂未查到通行中文名'))
        if d.get('note'):
            tip += '\n注：' + d['note']
        for pno in range(min(len(doc), APP_PAGE)):
            page = doc[pno]
            for v in variants(latin):
                for r in page.search_for(v):
                    pt = pymupdf.Point(r.x1 + 1, r.y0 + 1)
                    a = page.add_text_annot(pt, tip, icon='Note')
                    a.set_rect(pymupdf.Rect(pt.x, pt.y, pt.x + 4, pt.y + 4))
                    a.update()
                    n_annot += 1
    print('学名弹窗注释: %d 处' % n_annot)

    tmp = args.pdf + '.tmp'
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, args.pdf)
    print('成品 %d 页 -> %s' % (len(pymupdf.open(args.pdf)), args.pdf))


if __name__ == '__main__':
    main()
