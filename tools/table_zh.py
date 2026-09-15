# -*- coding: utf-8 -*-
"""table_zh.py — 表格页定点中文化 (路线 B, v2)
v2: 按 rawdict 字符级 bbox 定位 + 行方向向量决定插入旋转 (landscape 表 rotate=90),
短语/全词两级匹配, 物种名/作者缩写/亚科代码/数字/问号不在词表 => 自动保护。
幂等: 重跑前先由 backfill_pages.py 恢复干净版。
"""
import io, re, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

FONT = r'D:\zotero-pdf2zh\server\fonts\NotoSerifCJKsc-Regular.otf'
PDF = r'D:\zotero-pdf2zh\out\成品\Cactaceae2009_中文版.pdf'
PAGES = [2, 8, 9, 10, 11, 12, 13, 14]  # 0 基: p3, p9-15

PHRASES = {
    'Table 10.1': '表 10.1',
    'Table 10.2': '表 10.2',
    '(continued)': '（续）',
    'No data': '无数据',
    'Two pollinators': '两种传粉者',
    'long-lasting': '长存的',
    'traits and estimation methods': '性状与估算方法',
    'Fruit set': '坐果',
    'self-compatibility': '自交亲和',
    'self-incompatibility': '自交不亲和',
    'crossing experiments': '杂交实验',
    'molecular marker': '分子标记',
    'Floral cup': '花杯',
    'Central America': '中美洲',
    'South America': '南美洲',
}

WORDS = {
    'subfamily': '亚科', 'tribe': '族', 'genera': '属', 'species': '物种',
    'life': '生活', 'form': '型',
    'symmetry': '对称性', 'metabolism': '代谢', 'total': '合计',
    'nectar': '花蜜', 'compatibility': '亲和性', 'reference': '引文',
    'shape': '形状',
    'ﬂoral': '花部', 'floral': '花部', 'longevity': '寿命',
    'inbreeding': '近交', 'depression': '衰退', 'outcrossing': '异交',
    'estimation': '估算', 'method': '方法', 'methods': '方法',
    'pollination': '传粉', 'syndrome': '综合征', 'mating': '交配', 'system': '系统',
    'morphological': '形态-', 'morphological-': '形态-', 'functional': '功能',
    'rate': '率',
    'geographic': '地理', 'distribution': '分布',
    'corolla': '花冠', 'aperture': '开口', 'color': '颜色',
    'perianth': '花被', 'segments': '裂片', 'inflorescence': '花序',
    'inﬂorescence': '花序',
    'treelike': '乔木状', 'shrub-like': '灌木状', 'shrub': '灌木',
    'cushions': '垫状', 'cylindrical': '圆柱状', 'expansive': '扩展状',
    'melittophily': '蜂媒', 'chiropterophily': '蝙蝠媒', 'ornithophily': '鸟媒',
    'phalaenophily': '蛾媒', 'mirmecophily': '蚁媒',
    'solitary': '单生', 'solitaries': '簇生', 'clusters': '簇生',
    'germination': '萌发', 'fruits': '果实', 'fruit': '果',
    'seeds': '种子', 'seed': '种子',
    'red': '红', 'pink': '粉', 'white': '白', 'yellow': '黄',
    'magenta-': '品红-', 'blue': '蓝', 'purple': '紫', 'orange': '橙',
    'green': '绿', 'off-white': '灰白',
    'radial': '辐射对称', 'zygomorphic': '两侧对称', 'bisexual': '两性花',
    'flowers': '花', 'ﬂowers': '花', 'flower': '花', 'ﬂower': '花',
    'traits': '性状', 'of': '', 'to': '至', 'with': '具', 'leaves': '叶',
    'long-': '长-', 'lasting': '存',
    'undetermined': '未确定', 'aundetermined': 'a未确定',
    'gametophytic': '配子体', 'crossing': '杂交', 'experiments': '实验',
    'molecular': '分子', 'marker': '标记', 'herkogamy': '雌雄异位',
    'some': '部分', 'days': '天', 'no': '无',
    'mexico': '墨西哥', 'chile': '智利', 'argentina': '阿根廷',
    'caribbean': '加勒比', 'canada': '加拿大', 'america': '美洲',
    'andes': '安第斯山', 'patagonia': '巴塔哥尼亚',
    'southern': '南', 'lowland': '低地', 'neotropics': '新热带区',
    'from': '自', 'throughout': '贯穿', 'northern': '北',
    'region': '地区', 'short': '矮',
}

PUNCT_RUN = re.compile(r'^(\W*)(.*?)(\W*)$', re.UNICODE)


def insert_zh(page, rect, zh, d, font):
    if not zh:
        return
    if d == (1, 0):  # 横排
        fs = max(4.5, min(rect.height * 0.78, 9.5))
        tl = font.text_length(zh, fontsize=fs)
        if tl > rect.width and tl > 0:
            fs = max(3.8, fs * rect.width / tl)
        pt = (rect.x0, rect.y1 - rect.height * 0.20)
        page.insert_text(pt, zh, fontsize=fs, fontfile=FONT, fontname='ZH', color=(0, 0, 0))
    elif d == (0, -1):  # 底 -> 顶 (landscape 表标准方向), 字面朝左
        fs = max(4.5, min(rect.width * 0.78, 9.5))
        tl = font.text_length(zh, fontsize=fs)
        if tl > rect.height and tl > 0:
            fs = max(3.8, fs * rect.height / tl)
        pt = (rect.x1 - fs * 0.22, rect.y1 - fs * 0.10)
        page.insert_text(pt, zh, fontsize=fs, rotate=90, fontfile=FONT,
                         fontname='ZH', color=(0, 0, 0))
    elif d == (0, 1):  # 顶 -> 底
        fs = max(4.5, min(rect.width * 0.78, 9.5))
        tl = font.text_length(zh, fontsize=fs)
        if tl > rect.height and tl > 0:
            fs = max(3.8, fs * rect.height / tl)
        pt = (rect.x0 + fs * 0.22, rect.y0 + fs * 0.10)
        page.insert_text(pt, zh, fontsize=fs, rotate=270, fontfile=FONT,
                         fontname='ZH', color=(0, 0, 0))
    else:
        pass  # 反向横排极少见, V2 不处理


def main():
    font = pymupdf.Font(fontfile=FONT)
    doc = pymupdf.open(PDF)
    n_ins = 0
    for pno in PAGES:
        page = doc[pno]
        raw = page.get_text('rawdict')
        jobs = []  # (rect, zh, dir)
        for block in raw['blocks']:
            if block.get('type') != 0:
                continue
            for line in block['lines']:
                chars = [c for sp in line['spans'] for c in sp.get('chars', [])]
                if len(chars) < 2:
                    continue
                text = ''.join(c['c'] for c in chars)
                d = tuple(round(x, 2) for x in line['dir'])
                if d not in ((1, 0), (0, -1), (0, 1)):
                    continue
                occupied = [False] * len(text)
                # 短语优先
                for ph, zh in PHRASES.items():
                    start = 0
                    while True:
                        i = text.find(ph, start)
                        if i < 0:
                            break
                        if not any(occupied[i:i + len(ph)]):
                            for k in range(i, i + len(ph)):
                                occupied[k] = True
                            cc = chars[i:i + len(ph)]
                            rect = pymupdf.Rect(cc[0]['bbox'])
                            for c in cc[1:]:
                                rect |= pymupdf.Rect(c['bbox'])
                            jobs.append((rect, zh, d))
                        start = i + max(len(ph), 1)
                # 单词
                for m in re.finditer(r'\S+', text):
                    if any(occupied[m.start():m.end()]):
                        continue
                    tok = m.group(0)
                    pm = PUNCT_RUN.match(tok)
                    core = pm.group(2)
                    if not core:
                        continue
                    zh = WORDS.get(core.lower())
                    if zh is None:
                        continue
                    for k in range(m.start(), m.end()):
                        occupied[k] = True
                    cc = chars[m.start():m.end()]
                    rect = pymupdf.Rect(cc[0]['bbox'])
                    for c in cc[1:]:
                        rect |= pymupdf.Rect(c['bbox'])
                    jobs.append((rect, (pm.group(1) + zh + pm.group(3)) if zh else '', d))
        for r, _, _ in jobs:
            page.add_redact_annot(r, fill=(1, 1, 1))
        try:
            page.apply_redactions(graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        except (AttributeError, TypeError):
            page.apply_redactions()
        cnt = 0
        for r, zh, d in jobs:
            if zh:
                insert_zh(page, r, zh, d, font)
                cnt += 1
        n_ins += cnt
        print('p%d: %d 处' % (pno + 1, cnt))
    tmp = PDF + '.tmp'
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, PDF)
    print('插入中文: %d 处 -> %s' % (n_ins, PDF))


if __name__ == '__main__':
    main()
