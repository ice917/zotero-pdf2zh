# -*- coding: utf-8 -*-
"""全书去叠打磨: 对 M1/M2a/M2b/B4/B5 注入过的全部缓存行, 用 B5 固化的去叠规则清扫
规则(保守):
  R1 字形值含 ')' 且译文 token 后紧跟 ')'/'）' -> 删; 若 run 尾 '.' 再删 '。'; 尾 ',' 再删 '、''，'
  R2 run 形如 ').'(独立, 无 '）' 可删) 且后跟 '。' -> 删 '。'
  R3 run 形如 '),' 且后跟 '、'/'，' -> 删
不触碰: 缺 token 段(如 18#1 v107 未锚), 无 token 位置, 缩写点(run 不含 ')')

[v28.67] 修两个坑(见 改动记录 与 project_map P3):
  ① 旧版把"备份 + 连库 + UPDATE"全写在**模块顶层** —— 只要有人 `import
     dedouble_sweep`(扫描器、IDE 索引、将来的测试收集)就会当场改**真实**缓存库
     并落一份备份; 换一篇论文还得改源码里的路径与指纹。现在全部收进 main(),
     只有直接运行才动库, 且 sidecar / 库路径 / 文档指纹 / manifest 目录一律走参数。
  ② --dry-run 可先看"会改多少处"再决定是否落库; 库的备份始终在写之前落。

用法:
  python tools/dedouble_sweep.py \\
      --sidecar     <segflow/<篇名>.jsonl> \\
      --db          <%USERPROFILE%\\.cache\\pdf2zh\\cache.v1.db> \\
      --fingerprint <docsummary:<pdf_md5_16>:<载入指纹>> \\
      --manifests   <放 *.manifest.json 的目录, 如 inbox> \\
      [--dry-run]
退出码: 0=正常(含 dry-run), 2=参数/文件缺失
"""
import argparse
import datetime
import glob
import io
import json
import os
import re
import shutil
import sqlite3
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

V = re.compile(r"\{v(\d+)\}")
PUNCT = set(".,;:!?()[]{}<>\"'“”‘’（）《》【】、，。；：？！…—·-–‐‑‒―")


def load_pages(sidecar):
    """侧车逐行 = 段表快照; 按 page 建索引(与本脚本历史行为一致)。"""
    pages = {}
    with open(sidecar, encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            pages[o["page"]] = o
    return pages


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


def sweep(pages, manifests_dir, con, fingerprint, dry_run=False):
    """对每个 manifest 的每个 part 清扫缓存行; 返回 (改了几个 manifest, 共修几处)。"""
    cur = con.cursor()
    total, touched = 0, 0
    for man_path in sorted(glob.glob(os.path.join(manifests_dir, "*.manifest.json"))):
        with open(man_path, encoding="utf-8") as f:
            man = json.load(f)
        name = os.path.basename(man_path).replace(".manifest.json", "")
        rows_changed = 0
        for it in man["items"]:
            for p in it["parts"]:
                pg, seg = p["page"], p["seg"]
                o = pages.get(pg)
                if o is None:
                    continue
                vv = o.get("vars") or {}
                raw = o["segs"][seg].get("raw") or ""
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
                    (cache_raw, '%' + fingerprint + '%')).fetchall()
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
                        if not dry_run:
                            cur.execute('update _translationcache set translation=? where rowid=?',
                                        (new, rowid))
                        rows_changed += n
        if rows_changed:
            print('%-18s 修 %d 处' % (name, rows_changed))
            touched += 1
        total += rows_changed
    return touched, total


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="全书去叠打磨(会改**真实**缓存库; 写库前先落一份备份)")
    ap.add_argument("--sidecar", required=True, help="segflow/<篇名>.jsonl")
    ap.add_argument("--db", required=True, help="pdf2zh 翻译缓存库 cache.v1.db")
    ap.add_argument("--fingerprint", required=True,
                    help="文档作用域的 docsummary 指纹(只清扫这一篇的行)")
    ap.add_argument("--manifests", required=True, help="放 *.manifest.json 的目录(如 inbox)")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args(argv)

    for label, p in (("sidecar", args.sidecar), ("db", args.db)):
        if not os.path.exists(p):
            print("找不到 %s: %s" % (label, p))
            return 2
    if not os.path.isdir(args.manifests):
        print("找不到 manifest 目录: %s" % args.manifests)
        return 2

    pages = load_pages(args.sidecar)
    if not pages:
        print("侧车是空的, 无可清扫内容: %s" % args.sidecar)
        return 0

    bak = None
    if not args.dry_run:
        bak = args.db + '.bak-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        shutil.copy2(args.db, bak)
        print('备份:', bak)

    con = sqlite3.connect(args.db)
    try:
        touched, total = sweep(pages, args.manifests, con, args.fingerprint, args.dry_run)
        if args.dry_run:
            print('dry-run: 命中 %d 个 manifest, 合计可修 %d 处(未落库)' % (touched, total))
        else:
            con.commit()
            print('合计修 %d 处' % total)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
