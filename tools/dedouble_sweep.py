# -*- coding: utf-8 -*-
"""全书去叠打磨: 对 M1/M2a/M2b/B4/B5 注入过的全部缓存行, 用 B5 固化的去叠规则清扫
规则(保守):
  R1 字形值含 ')' 且译文 token 后紧跟 ')'/'）' -> 删; 若 run 尾 '.' 再删 '。'; 尾 ',' 再删 '、''，'
  R2 run 形如 ').'(独立, 无 '）' 可删) 且后跟 '。' -> 删 '。'
  R3 run 形如 '),' 且后跟 '、'/'，' -> 删
不触碰: 缺 token 段(如 18#1 v107 未锚), 无 token 位置, 缩写点(run 不含 ')')"""
import datetime, glob, io, json, re, shutil, sqlite3, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SIDECAR = r'D:\zotero-pdf2zh\segflow\cactaceae2009.jsonl'
DB = r'C:\Users\97638\.cache\pdf2zh\cache.v1.db'
FP = 'docsummary:3768fd6fce999176:bc4a2947'
V = re.compile(r'\{v(\d+)\}')

pages = {}
for line in open(SIDECAR, encoding='utf-8'):
    o = json.loads(line)
    pages[o['page']] = o

def renumber_map(raw):
    m = {}
    for k, mm in enumerate(V.finditer(raw)):
        m.setdefault(mm.group(1), k)
    return m  # 页级 vN -> 段内 vk

def trailing_run(val):
    j = len(val)
    while j > 0 and val[j - 1] in PUNCT:
        j -= 1
    return val[j:]

import argparse
ap = argparse.ArgumentParser()
PUNCT = set(".,;:!?()[]{}<>\"'“”‘’（）《》【】、，。；：？！…—·-–‐‑‒―")

bak = DB + '.bak-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
shutil.copy2(DB, bak)
print('备份:', bak)

con = sqlite3.connect(DB)
cur = con.cursor()
total_rows, total_fix = 0, 0
for man_path in sorted(glob.glob(r'D:\zotero-pdf2zh\inbox\*.manifest.json')):
    man = json.load(open(man_path, encoding='utf-8'))
    name = man_path.split('\\')[-1].replace('.manifest.json', '')
    rows_changed = 0
    for it in man['items']:
        for p in it['parts']:
            pg, seg = p['page'], p['seg']
            o = pages[pg]
            vv = o.get('vars') or {}
            raw = o['segs'][seg].get('raw') or ''
            if not V.search(raw):
                continue
            v2k = renumber_map(raw)
            k2v = {}
            for vn, vk in v2k.items():
                k2v.setdefault(vk, vn)
            cache_raw = re.sub(r'\{v(\d+)\}', lambda m: '{v%d}' % v2k[m.group(1)], raw)
            rows = cur.execute(
                'select rowid, translation from _translationcache '
                'where original_text=? and translate_engine_params like ?',
                (cache_raw, '%' + FP + '%')).fetchall()
            for rowid, tr in rows:
                out, last, n = [], 0, 0
                for mm in V.finditer(tr):
                    out.append(tr[last:mm.end()])
                    last = mm.end()
                    vn = k2v.get(int(mm.group(1)))
                    val = vv.get(vn, '') if vn else ''
                    run = trailing_run(val)
                    j = last
                    if ')' in val and j < len(tr) and tr[j] in (')', '）'):
                        j += 1
                        if run.endswith('.') and j < len(tr) and tr[j] == '。':
                            j += 1
                        elif run.endswith(',') and j < len(tr) and tr[j] in ('、', '，'):
                            j += 1
                    elif re.search(r'\)\.$', run) and j < len(tr) and tr[j] == '。':
                        j += 1
                    elif run.endswith('),') and j < len(tr) and tr[j] in ('、', '，'):
                        j += 1
                    if j > last:
                        n += 1
                        last = j
                out.append(tr[last:])
                new = ''.join(out)
                if n:
                    cur.execute('update _translationcache set translation=? where rowid=?', (new, rowid))
                    rows_changed += n
    if rows_changed:
        print('%-18s 修 %d 处' % (name, rows_changed))
    total_rows += rows_changed
con.commit()
con.close()
print('合计修 %d 处' % total_rows)
