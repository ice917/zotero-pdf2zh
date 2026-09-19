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
    """页级 {vN} -> 段内 0 基 {vk}; raw_pagelevel 提供 token 顺序。

    [自研补丁 2026-09-19] 侧车 trans 是 LLM 产物, 实测会写出 raw 里不存在的
    token 编号(EgoPhys p12#11 的 {v71}、p15#19 的 {v255})。旧写法 mapping[...]
    直接取 → KeyError, 整个注入流程被打断(2 段污染整篇)。
    映射不到就**原样保留**该 token, 交给调用方既有的"译文占位符多重集 ⊆ raw
    多重集"断言去判 —— 那条断言本就是为 LLM 乱写编号设计的。
    """
    mapping = {}
    for k, m in enumerate(V_TOKEN.finditer(raw_pagelevel)):
        mapping.setdefault(m.group(1), k)
    return V_TOKEN.sub(
        lambda m: "{v%d}" % mapping[m.group(1)] if m.group(1) in mapping
        else m.group(0),
        text_pagelevel)


def detect_fp(cur, pages, man):
    """自动探测文档指纹: 取最长的几个段原文(重编号成段内编号)精确查缓存,
    统计各行 translate_engine_params 里的 docsummary 指纹, 取覆盖行数最多者;
    **平票时取最后写入的那一个世代**(见下)。

    [v28.21+] 为什么平票必须取最新: 同一段落在库里会躺着**多代** doc_summary_fp
    (配置改动让摘要键漂移, 每代各存一行, 见 cache.py `_DOC_SUMMARY_PARAM`)。
    旧/新世代覆盖同样的段 -> 票数相同 -> 旧写法 `max(votes, key=votes.get)` 由
    字典插入序决定, 返回先查到的**旧世代**; 随后 UPDATE 只落在被遮蔽的旧行上,
    本脚本照常打印"已更新 N 行"/PASS, 而渲染器读的是新一代那一行 -> PDF 毫无变化。

    判据选"id 最大"(= 最后写入)而不是别的: cache.set() 恒为 INSERT(从不原地
    UPDATE), 故 id 单调 = 写入时序, 渲染器回查时 ③④ 档索引也正是"按 id 升序写
    覆盖" —— 探测的口径就是回查的口径。
    """
    votes = {}  # fp -> (票数, 该 fp 命中行的最大 id)
    segs = []
    for it in man["items"]:
        for p in it["parts"]:
            raw = pages[p["page"]]["segs"][p["seg"]]["raw"]
            if len(raw) >= 80:
                segs.append(renumber(raw, raw))
    for cache_raw in sorted(segs, key=len, reverse=True)[:5]:
        for rowid, params in cur.execute(
                "SELECT id, translate_engine_params FROM _translationcache WHERE original_text=?",
                (cache_raw,)):
            for fp in FP_RE.findall(params or ""):
                n, mx = votes.get(fp, (0, 0))
                votes[fp] = (n + 1, max(mx, rowid))
    if not votes:
        return None
    # 元组按字典序比较: 先比票数, 平票再比"最后写入的行号"。
    return max(votes, key=lambda fp: votes[fp])


def newest_row(cur, cache_raw):
    """该段"最后写入"的那一行 (id 最大) —— 渲染器实际读到的就是它。

    cache.set() 恒为 INSERT, 故 id 单调 = 写入时序; 返回 (id, translation,
    translate_engine_params), 该段没有任何行时返回 None。注入前后都用它复查:
    "写进去了"不等于"渲染器会读到"。
    """
    return cur.execute(
        "SELECT id, translation, translate_engine_params FROM _translationcache"
        " WHERE original_text=? ORDER BY id DESC LIMIT 1",
        (cache_raw,)).fetchone()


def rollback(fp, raws=(), dry=False):
    """[v28.23] 撤销骨架行: 删掉"译文 == 原文"的库行。

    为什么需要: 暂停档(PAUSE_TRANSLATE=1)为了让 seg_inject 有行可改, 会按最终
    键形态落一批 raw→raw 骨架行。正常回路里它们随即被豆包稿改写; 但回路**半途
    失败**(导出失败/交件拒收/注入 dry FAIL/等不到交稿)时若不撤掉, 第二趟就会
    命中它们 -> 整篇出英文 PDF —— 正是本档最怕的那个后果。

    两道判据, 优先用段原文(更精确, 且不依赖指纹能否探到):
      a) raws 非空: 只删 original_text 落在**本篇载荷段原文**集合里的行;
      b) 否则用 fp:  只删文档作用域内的行。
    两种都叠加 `translation = original_text` —— 骨架行的定义就是这个, 它不可能
    误删任何真译文(真译文与原文逐字相同, 等于没翻, 删掉也无害)。两者都没有则
    拒绝执行(宁可漏撤, 不可误删全库)。
    """
    raws = sorted({r for r in raws if r})
    if raws:
        cond_head = "translation = original_text AND original_text IN "
        print("作用域: 本篇载荷 %d 段原文" % len(raws))
    elif fp:
        cond_head = None
        print("作用域: 文档指纹 %s" % fp)
    else:
        print("FAIL rollback: 既无段原文也无文档指纹 -> 拒绝删 (怕误删全库)")
        return 1

    con = sqlite3.connect(CACHE)
    cur = con.cursor()
    if cond_head:
        # 分块: SQLite 变量上限, 长论文段数可能上千
        found = []
        for i in range(0, len(raws), 400):
            chunk = raws[i:i + 400]
            found += cur.execute(
                "SELECT id, substr(original_text, 1, 50) FROM _translationcache WHERE "
                + cond_head + "(" + ",".join("?" * len(chunk)) + ")", chunk).fetchall()
    else:
        found = cur.execute(
            "SELECT id, substr(original_text, 1, 50) FROM _translationcache"
            " WHERE translate_engine_params LIKE ? AND translation = original_text",
            ("%" + fp + "%",)).fetchall()
    print("骨架行: %d" % len(found))
    for rid, head in found[:8]:
        print("  id=%s %r" % (rid, head))
    if dry or not found:
        con.close()
        print("结论: PASS%s" % (" (dry, 未删)" if dry else " (无可删)"))
        return 0
    bak = CACHE + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(CACHE, bak)
    print("备份:", bak)
    n = 0
    if cond_head:
        ids = [r[0] for r in found]
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            cur.execute("DELETE FROM _translationcache WHERE id IN ("
                        + ",".join("?" * len(chunk)) + ")", chunk)
            n += cur.rowcount
    else:
        cur.execute("DELETE FROM _translationcache WHERE translate_engine_params LIKE ?"
                    " AND translation = original_text", ("%" + fp + "%",))
        n = cur.rowcount
    con.commit()
    con.close()
    print("结论: PASS (已删 %d 行骨架)" % n)
    return 0


def raws_of_run(manifest_path, sidecar_path):
    """本篇载荷涉及的"缓存侧原文" (与注入时用的口径逐字一致)。

    manifest_path 为空时退化为"这份侧车里的全部段" —— 用在导出阶段就失败、台账
    里没留下 manifest 的场合: 此时侧车是本次第一趟刚写的, 它已经覆盖整篇, 范围
    只会比载荷大(多删不到任何东西: 判据还叠着 translation == original_text)。
    """
    pages = load_pages(sidecar_path)
    out = []
    if not manifest_path:
        for rec in pages.values():
            for s in rec.get("segs", []):
                raw = s["raw"]
                out.append(renumber(raw, raw))
        return out
    with open(manifest_path, encoding="utf-8") as f:
        man = json.load(f)
    for it in man["items"]:
        for p in it["parts"]:
            raw = pages[p["page"]]["segs"][p["seg"]]["raw"]
            out.append(renumber(raw, raw))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--imported", default="")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--shift", default="", help='如 "S4:1,S9:2"')
    ap.add_argument("--sidecar", default=SIDECAR, help="侧车路径; 新论文先归档 latest.jsonl 再指向它")
    ap.add_argument("--fp", default="",
                    help="文档指纹; 缺省自动探测(平票取最后写入的世代; 探测失败回落 Cactaceae 默认)")
    ap.add_argument("--dry", action="store_true", help="只演算不写库")
    ap.add_argument("--rollback", action="store_true",
                    help="[v28.23] 撤销模式: 删本篇(或本文档指纹作用域下)的 raw→raw 骨架行, 不做注入")
    args = ap.parse_args()

    if args.rollback:
        raws = []
        if os.path.exists(args.sidecar):
            try:
                raws = raws_of_run(args.manifest if (args.manifest and os.path.exists(args.manifest)) else "",
                                   args.sidecar)
            except Exception as exc:
                print("⚠️ 段原文口径不可用(%s), 改用文档指纹作用域" % exc)
        return rollback(args.fp, raws, dry=args.dry)
    if not args.imported or not args.manifest:
        print("FAIL: 非 --rollback 模式必须给 --imported 与 --manifest")
        return 1

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
            if args.dry:
                # [v28.21+] 自检(预演): 该段"最新行"是否落在本次作用域里。
                # 不在 -> 实写只会落在被遮蔽的旧世代行上, 渲染器不会读到。
                newest = newest_row(cur, cache_raw)
                if newest and fp not in (newest[2] or ""):
                    ok = False
                    print("  [FAIL 自检] 该段最新行(id %d)不在作用域 %s 内"
                          " —— 实写落在被遮蔽世代" % (newest[0], fp))
            else:
                cur.execute(
                    "UPDATE _translationcache SET translation=? WHERE original_text=? AND " + scope,
                    (new_trans, cache_raw, scope_arg))
                print("  已更新 %d 行" % cur.rowcount)
                # [v28.21+] 自检(实写): 复查最新行是否真的变成新译文。
                newest = newest_row(cur, cache_raw)
                if newest and newest[1] != new_trans:
                    ok = False
                    print("  [FAIL 自检] 最新行(id %d)译文未被更新 —— 注入落在被遮蔽世代"
                          % newest[0])

    if not args.dry:
        con.commit()
    con.close()
    print("结论: %s%s" % ("PASS" if ok else "FAIL", " (dry-run 未写库)" if args.dry else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
