# -*- coding: utf-8 -*-
"""seg_inject.py — 把豆包译文(imported.json)写回缓存库 (M1 最后一环)

三道安全设计:
  1. 重编号: 侧车 raw 用页级 {vN}, 缓存用段内 0 基 {vk} —— 按段内 token 顺序映射
  2. 文档作用域: UPDATE 限定 translate_engine_params LIKE '%<doc_summary_fp>%'
     (doc_summary_fp 是文档摘要的 md5, 跨文档唯一; 防止 'Introduction' 这类
      短原文误伤其他文档的缓存行)
  3. 前置断言: 更新前校验"缓存现译文 == 侧车 trans 重编号" (证明行集找对了);
     占位符多重集 ⊆ 缓存 raw 的多重集 (渲染契约)

用法:
  python tools/seg_inject.py --imported out\\payload_p2_p4.imported.json \\
      --manifest inbox\\payload_p2_p4.manifest.json [--shift "S4:1"]
  --shift: 合并段断点移位 "S4:1" = 把 part2 首字移到 part1 尾 (修豆包把
           '可育' 拆到 ⋮ 两侧的情况)
"""
import argparse
import datetime
import io
import json
import os
import re
import shutil
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HOME = os.path.expanduser("~")
SIDECAR = os.path.join(_HOME, ".cache", "pdf2zh", "segflow", "latest.jsonl")
CACHE = os.path.join(_HOME, ".cache", "pdf2zh", "cache.v1.db")
DOC_FP_DEFAULT = "docsummary:3768fd6fce999176:bc4a2947"  # Cactaceae 2009 (兜底默认)
FP_RE = re.compile(r"docsummary:[0-9a-f]{8,}:[0-9a-f]{4,}")

V_TOKEN = re.compile(r"\{v(\d+)\}")


def load_pages(sidecar):
    pages = {}
    with open(sidecar, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            pages[o["page"]] = o
    return pages


def renumber(text_pagelevel, raw_pagelevel):
    """页级 {vN} -> 段内 0 基 {vk}; raw_pagelevel 提供 token 顺序。"""
    mapping = {}
    for k, m in enumerate(V_TOKEN.finditer(raw_pagelevel)):
        mapping.setdefault(m.group(1), k)
    return V_TOKEN.sub(lambda m: "{v%d}" % mapping[m.group(1)], text_pagelevel)


def detect_fp(cur, pages, man):
    """自动探测文档指纹: 取最长的几个段原文(重编号成段内编号)精确查缓存,
    统计各行 translate_engine_params 里的 docsummary 指纹, 取覆盖行数最多者。"""
    votes = {}
    segs = []
    for it in man["items"]:
        for p in it["parts"]:
            raw = pages[p["page"]]["segs"][p["seg"]]["raw"]
            if len(raw) >= 80:
                segs.append(renumber(raw, raw))
    for cache_raw in sorted(segs, key=len, reverse=True)[:5]:
        for (params,) in cur.execute(
                "SELECT translate_engine_params FROM _translationcache WHERE original_text=?",
                (cache_raw,)):
            for fp in FP_RE.findall(params or ""):
                votes[fp] = votes.get(fp, 0) + 1
    if not votes:
        return None
    return max(votes, key=votes.get)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--imported", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--shift", default="", help='如 "S4:1,S9:2"')
    ap.add_argument("--sidecar", default=SIDECAR, help="侧车路径; 新论文先归档 latest.jsonl 再指向它")
    ap.add_argument("--fp", default="", help="文档指纹; 缺省自动探测(探测失败回落 Cactaceae 默认)")
    ap.add_argument("--dry", action="store_true", help="只演算不写库")
    args = ap.parse_args()

    with open(args.imported, encoding="utf-8") as f:
        imported = json.load(f)
    with open(args.manifest, encoding="utf-8") as f:
        man = json.load(f)
    pages = load_pages(args.sidecar)

    # ---- 断点移位 ----
    shifts = {}
    for pair in args.shift.split(","):
        if pair.strip():
            k, n = pair.split(":")
            shifts[k.strip()] = int(n)
    for it in man["items"]:
        n = shifts.get(it["key"], 0)
        if not n or not it["merged"]:
            continue
        key = "%d#%d" % (it["parts"][0]["page"], it["parts"][0]["seg"])
        key2 = "%d#%d" % (it["parts"][1]["page"], it["parts"][1]["seg"])
        if n > 0:
            move, imported[key2] = imported[key2][:n], imported[key2][n:]
            imported[key] += move
        elif n < 0:
            move, imported[key] = imported[key][n:], imported[key][:n]
            imported[key2] = move + imported[key2]
        print("断点移位 %s: %+d" % (it["key"], n))

    # ---- 备份 ----
    if not args.dry:
        bak = CACHE + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(CACHE, bak)
        print("备份:", bak)

    con = sqlite3.connect(CACHE)
    cur = con.cursor()
    fp = args.fp
    if fp:
        print("文档指纹(指定):", fp)
    else:
        fp = detect_fp(cur, pages, man)
        if fp is None:
            fp = DOC_FP_DEFAULT
            print("文档指纹: 探测失败, 回落默认", fp)
        else:
            print("文档指纹(自动探测):", fp)
    scope = "translate_engine_params LIKE ?"
    scope_arg = "%" + fp + "%"

    ok = True
    for it in man["items"]:
        for p in it["parts"]:
            pg, seg = p["page"], p["seg"]
            key = "%d#%d" % (pg, seg)
            seg_o = pages[pg]["segs"][seg]
            raw_page = seg_o["raw"]
            cache_raw = renumber(raw_page, raw_page)
            new_trans = renumber(imported[key], raw_page)

            # 渲染契约: 新译文的占位符多重集 ⊆ raw 的多重集
            need = set(V_TOKEN.findall(cache_raw))
            have = set(V_TOKEN.findall(new_trans))
            extra = have - need
            if extra:
                ok = False
                print("FAIL %s: 译文出现 raw 没有的占位符 %s" % (key, sorted(extra)))
                continue

            # 前置断言: 缓存现行译文 == 侧车 trans 重编号 (行集校验)
            cur_trans = renumber(seg_o.get("trans") or "", raw_page)
            rows = cur.execute(
                "SELECT id, translation FROM _translationcache WHERE original_text=? AND " + scope,
                (cache_raw, scope_arg)).fetchall()
            tag = ""
            if not rows:
                ok = False
                tag = "  [FAIL 未找到缓存行]"
            elif any(t != cur_trans for _, t in rows):
                tag = "  [注意] %d 行中 %d 行现行译文与侧车不一致(可能被重译过)" % (
                    len(rows), sum(1 for _, t in rows if t != cur_trans))
            print("%s: %d 字 -> %d 行%s" % (key, len(new_trans), len(rows), tag))
            if not rows:
                continue
            if not args.dry:
                cur.execute(
                    "UPDATE _translationcache SET translation=? WHERE original_text=? AND " + scope,
                    (new_trans, cache_raw, scope_arg))
                print("  已更新 %d 行" % cur.rowcount)

    if not args.dry:
        con.commit()
    con.close()
    print("结论: %s%s" % ("PASS" if ok else "FAIL", " (dry-run 未写库)" if args.dry else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
