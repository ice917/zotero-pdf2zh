# -*- coding: utf-8 -*-
"""species_extract.py — 用斜体字体特征提取全书拉丁学名清单
学名在书中是斜体排版(rawdict span fontname 含 Italic), 连字 ﬁ/ﬂ 需归一。
输出: out/species_inventory.json  [{name, pages:[1基页码]}], 附词组片段日志。
"""
import io, json, re, sys, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymupdf

PDF = r'D:\zotero-pdf2zh\out\成品\Cactaceae2009_中文版.pdf'
OUT = r'D:\zotero-pdf2zh\out\species_inventory.json'

norm = lambda s: (s or '').replace('ﬁ', 'fi').replace('ﬂ', 'fl').replace('ﬀ', 'ff').replace('ﬃ', 'ffi')


def main():
    doc = pymupdf.open(PDF)
    occ = collections.defaultdict(set)   # name -> {页码}
    frag = collections.Counter()
    for pno in range(len(doc)):
        raw = doc[pno].get_text('rawdict')
        for block in raw['blocks']:
            if block.get('type') != 0:
                continue
            for line in block['lines']:
                # 斜体 span 拼接成行内斜体串 (rawdict 无 text, 从 chars 拼)
                ital = [(''.join(c['c'] for c in sp.get('chars', [])), sp['bbox'])
                        for sp in line['spans']
                        if 'italic' in (sp.get('font') or '').lower()
                        or 'oblique' in (sp.get('font') or '').lower()]
                if not ital:
                    continue
                text = ' '.join(t for t, _ in ital)
                # 双名/三名: 属(大写起) + 种加词(+ 变种等), 允许连字符与缩写属
                for m in re.finditer(
                        r"\b([A-Z][a-z]{2,}) ([a-z][a-z\-]{2,})(?: var\. ([a-z\-]+))?",
                        text):
                    name = m.group(0)
                    frag[name] += 1
                    occ[name].add(pno + 1)
                # 缩写属: "O. microdasys"
                for m in re.finditer(r"\b([A-Z])\. ([a-z][a-z\-]{2,})", text):
                    frag[m.group(0)] += 1
    inv = [{'name': k, 'pages': sorted(v)} for k, v in sorted(occ.items(), key=lambda x: -len(x[1]))]
    json.dump(inv, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('斜体片段 top15:', frag.most_common(15))
    print('独立条目:', len(inv))
    for it in inv[:20]:
        print('  %-34s 页%s' % (it['name'], it['pages'][:8]))
    print('产出:', OUT)


if __name__ == '__main__':
    main()
