# -*- coding: utf-8 -*-
"""seg_inject.py — 把豆包译文(imported.json)写回缓存库 (M1 最后一环)

两条路线(由引擎画像决定, 见 engine.py):
  pdf2zh 1.x  侧车路线 —— 三道安全设计:
    1. 重编号: 侧车 raw 用页级 {vN}, 缓存用段内 0 基 {vk} —— 按段内 token 顺序映射
    2. 文档作用域: UPDATE 限定 translate_engine_params LIKE '%<doc_summary_fp>%'
       (doc_summary_fp 是文档摘要的 md5, 跨文档唯一; 防止 'Introduction' 这类
        短原文误伤其他文档的缓存行)
    3. 前置断言: 更新前校验"缓存现译文 == 侧车 trans 重编号" (证明行集找对了);
       占位符多重集 ⊆ 缓存 raw 的多重集 (渲染契约)

  next / BabelDOC  tracking 路线 (v28.41, 见 inject_next):
    没有侧车、没有页级 {vN}、没有文档指纹, 三道安全设计换成:
    1. 段表与载荷**逐段对账**: 按 manifest 的定位键(src)落到段表那一条, 再用正文指纹
       (fp)确认还是那一份 —— 不"重推段表再比坐标"(那要重放导出时的 --pdf 锚定, 参数
       一变就整列错位); working 下的段表会被下次渲染覆盖, 真换了就拒收
    2. 定位 = 对 original_text 精确匹配该段的**整条 prompt**(段表 llm_translate_trackers
       [].input 里就有原文), 不需要作用域
    3. 前置断言: 该行数组里 id==multi_paragraph_index 那一条的 output 必须等于段表
       记下的 output(证明行找对了 + id 映射也对上了); 改完再复查该行

用法:
  python tools/seg_inject.py --imported out\\payload_p2_p4.imported.json \\
      --manifest inbox\\payload_p2_p4.manifest.json [--shift "S4:1"] [--tracking <段表>]
  --shift: 合并段断点移位 "S4:1" = 把 part2 首字移到 part1 尾 (修豆包把
           '可育' 拆到 ⋮ 两侧的情况) —— **仅 1.x**; next 载荷按 #S 编号, 没有该坐标
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
# [v28.39] 引擎画像: 缓存库与侧车路径随 P2Z_ENGINE 走(adopt 会把该变量传给子进程)。
# 缺省画像 = pdf2zh 1.x, 于是这两条路径与引入本层之前逐字节一致。**必须在这里取画像
# 而不是写死** —— CACHE 没有 CLI 覆盖口, 写死等于"父进程按 next 算、子进程往 1.x 库写",
# 那是最难发现的一类静默错(更新报成功, 渲染器读的却是另一个库)。
import engine as _ENG                                     # noqa: E402
import text_clean as _TC                                  # noqa: E402  不可见字符剥离(两侧同源)
_PROF = _ENG.active()
SIDECAR = _PROF.sidecar or ""
CACHE = _PROF.cache_db
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


def align_next(args):
    """next 路线: 载荷 ↔ 段表**逐段对账** (inject 与 rollback **必须共用同一份口径**)。

    撤销若用了不一样的定位口径, 就可能撤到别的段上去 —— 而两边都会"看起来对上了"。
    返回 (items, mits, tk, err): err 非空即拒收(调用方 return 1)。
    """
    with open(args.manifest, encoding="utf-8") as f:
        man = json.load(f)
    if man.get("engine") != "next":
        print("FAIL: 这份 manifest 的 engine=%r 不是 next —— 它是另一个引擎导出的载荷, "
              "写进本画像的库只会静默无效" % man.get("engine"))
        return None, None, "", "engine"
    src = man.get("source") or {}
    tk = args.tracking or (src.get("path") if src.get("kind") == "tracking_json" else "") or ""
    if not tk or not os.path.exists(tk):
        print("FAIL: 段表不存在: %s\n      用 --tracking 指回**导出这份载荷时**的那一份" % tk)
        return None, None, tk, "tracking"

    mits = man.get("items") or []
    # 对账 = manifest 的定位键(src)逐条落回段表, 再用正文指纹(fp)确认还是那一份。
    # **不重推段表再比坐标**: 载荷里的页号是导出时的锚定结果(给 --pdf 才锚, 不然按序
    # 猜), 而 #S 编号的排序依赖页号 —— 重推时参数一变就整列错位, 会把一份好载荷判成
    # "不是从当前段表导出的"(真样本 129 段里 #S11 起全错)。
    try:
        d = _ENG.load_tracking(tk)
    except (IOError, ValueError) as e:
        print("FAIL: 段表读不了: %s" % e)
        return None, mits, tk, "load"
    recs = []
    for i, m in enumerate(mits, 1):
        try:
            rec, txt = _ENG.tracking_segment(d, m.get("src"))
        except (TypeError, ValueError, KeyError, IndexError) as e:
            print("FAIL: #S%d 在段表里定位不到(%s) —— working 下的 translate_tracking.json 会"
                  "被**下一次渲染覆盖**, 这份载荷不是从当前段表导出的。用 --tracking 指回"
                  "导出时那一份" % (i, e))
            return None, mits, tk, "locate"
        if _ENG.text_fp(txt) != (m.get("fp") or ""):
            print("FAIL: #S%d 的正文与段表对不上 —— 段表里该位置现在是 %r(载荷导出时是另一"
                  "份)。用 --tracking 指回导出这份载荷时的那一份" % (i, txt[:60]))
            return None, mits, tk, "fp"
        recs.append(rec)
    items = [{"rec": r, "pool": m.get("pool") or "page"}
             for r, m in zip(recs, mits)]
    return items, mits, tk, ""


def rollback_next(args):
    """next 路线: 撤销 inject —— 把**我们写进去的那一条**改回引擎当时的译文。

    为什么不直接拿 inject 前那份 `.bak` 整库还原: **缓存库是全机共用的**。`.bak` 是
    "某一时刻的整库快照", 还原它会把快照之后**别的论文**合法落下的译文一并退掉 ——
    一次撤销伤到无关的篇, 而且不报错。所以这里做**行级撤销**, 范围 = 本篇载荷那几段。

    判据(与 1.x 的 `translation == original_text` 骨架行判据同精神): 只改"现行值 ==
    imported.json 里我们注入的那一版"的那一条 —— 它证明这条改动确实出自我们这次注入。
    值已经变了(别人又改过 / 引擎重译过) -> **不碰, 只报告**: 少了这条判据, 撤销就成了
    "把引擎当时的译文强塞回去", 会覆盖别人的合法改动。

    三个输入与 inject 完全一致(manifest + imported + 段表), 缺一不可 —— 它们分别是
    "撤哪几段"(对账)、"撤的是不是我们那一版"(provenance)、"改回什么值 / 该批次的下标"。
    """
    if not args.imported or not os.path.exists(args.imported):
        print("FAIL: 找不到 imported.json: %s" % (args.imported or "(未给)"))
        return 1
    with open(args.imported, encoding="utf-8") as f:
        imported = json.load(f)
    items, mits, tk, err = align_next(args)
    if err:
        return 1

    con = sqlite3.connect(CACHE)
    cur = con.cursor()
    plan, gone = [], 0
    for i, (it, m) in enumerate(zip(items, mits), 1):
        key = "S%d" % i
        if key not in imported:
            continue
        new = imported[key].strip()                 # 我们注入的那一版
        old = it["rec"].get("output") or ""         # 引擎当时的译文 = 要还原成的值
        # 页号只用于**报错时指路**, 取自 manifest(段表侧的锚定参数可能已不同)。
        pg = (m.get("parts") or [{}])[0].get("page")
        loc = "#S%d（第%s页%s）" % (
            i, "?" if pg is None else pg,
            "·跨页" if it["pool"] == "cross_page" else "")
        prompts, mpi = _ENG.seg_cache_targets(it)
        if not prompts or mpi is None or not old:
            print("FAIL %s: 段表里缺 prompt / multi_paragraph_index / 当时的 output —— 还原"
                  "不了(段表被换过或被截断), 绝不猜" % loc)
            con.close()
            return 1
        for pr in prompts:
            rows = cur.execute("SELECT id, translation FROM %s WHERE original_text=?"
                               % _ENG.CACHE_TABLE, (pr,)).fetchall()
            if not rows:
                gone += 1
                print("%s: 缓存里已无该批次的 prompt 行 -> 本次注入没有残留" % loc)
                continue
            for rid, raw in rows:
                try:
                    _lst, byid = _ENG.batch_entries(raw)
                except Exception as exc:
                    print("FAIL %s: 行 id=%s 的现行译文不是 JSON(%s) -> 拒收(不猜该改哪一条)"
                          % (loc, rid, exc))
                    con.close()
                    return 1
                now = byid.get(int(mpi))
                if now == new:
                    plan.append((loc, rid, int(mpi), old))
                elif now == old:
                    print("%s: 行 id=%s 已是引擎当时的译文(未注入或已还原), 不动" % (loc, rid))
                else:
                    print("%s: 行 id=%s 的 id=%d 现行值既不是我们注入的那版、也不是段表那版"
                          " -> 已被别的改动覆盖, **不动**(怕抹掉别人的合法改动)"
                          % (loc, rid, int(mpi)))
    if not plan:
        con.close()
        print("还原行数: 0%s" % ("(prompt 行已不在, 无残留)" if gone else ""))
        print("结论: PASS (无可撤)")
        return 0

    if not args.dry:
        bak = CACHE + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(CACHE, bak)
        print("备份:", bak)
    n = 0
    for loc, rid, mpi, old in plan:
        if args.dry:
            print("%s: 行 id=%s 的 id=%d 将还原 (%d 字)" % (loc, rid, mpi, len(old)))
            n += 1
            continue
        # 逐行**重读**再改: 同一行可能承载同批次的多段(plan 里就有多条落同一行)。
        raw = cur.execute("SELECT translation FROM %s WHERE id=?" % _ENG.CACHE_TABLE,
                          (rid,)).fetchone()
        try:
            lst, _byid = _ENG.batch_entries(raw[0] if raw else "")
        except Exception:
            print("  [FAIL 自检] 行 id=%s 在还原前读不回来了" % rid)
            con.close()
            return 1
        for e in lst:
            if int(e.get("id", -1)) == mpi:
                e["output"] = old
        cur.execute("UPDATE %s SET translation=? WHERE id=?" % _ENG.CACHE_TABLE,
                    (json.dumps(lst, ensure_ascii=False, indent=4), rid))
        print("  已还原 %d 行" % cur.rowcount)
        n += 1
    if not args.dry:
        con.commit()
    con.close()
    print("还原行数: %d" % n)
    print("结论: PASS%s" % (" (dry-run 未写库)" if args.dry else ""))
    return 0


def inject_next(args):
    """next 路线: 把 imported.json({"S5": 译文}) 写回缓存库。

    定位口径(与 1.x 的**根本差别**, 已实测: babeldoc/translator/translator.py:141-165):
      next 的 LLM 通道键 = 引擎实际发出的**整条 prompt**(`llm_translate(final_input)`
      -> `cache.get/set(final_input)`), 而这条 prompt 的原文就记在段表该段的
      `llm_translate_trackers[].input` 里(上游 set_input 与 cache.set 用的是同一个字符串)。
      于是:
        * 定位 = 对 original_text 做**精确匹配**, 不需要 1.x 的 doc_summary_fp 作用域
          —— 整条 prompt 自带文档上下文, 天然只属于本篇;
        * 不需要 {vN} 重编号 —— 载荷正文就是原生形态;
        * 要改的位置 = 该行 translation(上游原始回复, 一个 JSON 数组)里
          `id == multi_paragraph_index` 的那一条(上游把批次各段按序塞进数组, id = 下标)。

    前置断言(与 1.x 同精神, 专防"改错行/改错段"): 改之前, 该行数组里那一条的 output
    必须**等于段表里记下的该段 output**(引擎当时写下的译文)。不等即拒收 —— 它同时
    证明了两件事: 行找对了, id 映射也对上了。写完再复查一次该行是否真的变了。
    """
    with open(args.imported, encoding="utf-8") as f:
        imported = json.load(f)
    items, mits, _tk, err = align_next(args)
    if err:
        return 1

    if not args.dry:
        bak = CACHE + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(CACHE, bak)
        print("备份:", bak)

    con = sqlite3.connect(CACHE)
    cur = con.cursor()
    ok, n_rows, n_segs = True, 0, 0
    for i, (it, m) in enumerate(zip(items, mits), 1):
        key = "S%d" % i
        if key not in imported:
            continue
        # [v28.80] 不可见字符体检: 落盘前最后一道 —— 写进库的东西会被渲染器**原样排到
        # 纸上**(零宽字符在 PDF 里是看不见的乱码/CID 0)。剥离属归一化, 不参与判定:
        # 本路线不做字符串定位, 没有"两侧"问题。
        new = _TC.strip(imported[key].strip())
        # 页号只用于**报错时指路**, 取自 manifest(段表侧的锚定参数可能已不同, 见上)。
        pg = (m.get("parts") or [{}])[0].get("page")
        loc = "#S%d（第%s页%s）" % (
            i, "?" if pg is None else pg,
            "·跨页" if it["pool"] == "cross_page" else "")
        if not new:
            ok = False
            print("FAIL %s: imported.json 里是空译文" % loc)
            continue
        prompts, mpi = _ENG.seg_cache_targets(it)
        cur_out = it["rec"].get("output") or ""
        if not prompts:
            ok = False
            print("FAIL %s: 段表里这段没有 llm_translate_trackers —— 这篇不是走 LLM 通道"
                  "跑的(或跑的时候没开缓存), 没有行可写" % loc)
            continue
        if mpi is None:
            ok = False
            print("FAIL %s: 段表里这段没有 multi_paragraph_index, 定不了它在批次 JSON 里的 "
                  "id —— 绝不猜(猜错就是改到同批另一段的译文上)" % loc)
            continue
        if not cur_out:
            ok = False
            print("FAIL %s: 段表里没记下这段当时的译文, 无从做前置断言 -> 拒收" % loc)
            continue
        n_segs += 1
        for pr in prompts:
            rows = cur.execute("SELECT id, translation FROM %s WHERE original_text=?"
                               % _ENG.CACHE_TABLE, (pr,)).fetchall()
            if not rows:
                ok = False
                print("FAIL %s: 缓存里找不到该批次的 prompt 行 —— 这篇不是用 LLM 通道跑的, "
                      "或缓存被清过(同一份 prompt 是精确键, 不存在近似命中)" % loc)
                continue
            for rid, raw in rows:
                try:
                    lst, byid = _ENG.batch_entries(raw)
                except Exception as exc:
                    ok = False
                    print("FAIL %s: 行 id=%s 的现行译文不是 JSON(%s) -> 拒收" % (loc, rid, exc))
                    continue
                if byid.get(int(mpi)) != cur_out:
                    ok = False
                    print("FAIL %s: 前置断言不成立 —— 行 id=%s 里 id=%d 的现行译文与段表对不上;"
                          "\n      段表: %r\n      缓存: %r\n      不做任何写入"
                          % (loc, rid, int(mpi), cur_out[:80], str(byid.get(int(mpi)))[:80]))
                    continue
                for e in lst:
                    if int(e.get("id", -1)) == int(mpi):
                        e["output"] = new
                txt = json.dumps(lst, ensure_ascii=False, indent=4)
                if args.dry:
                    print("%s: 行 id=%s 的 id=%d 将被改写 (%d 字 -> %d 字)"
                          % (loc, rid, int(mpi), len(cur_out), len(new)))
                    n_rows += 1
                    continue
                cur.execute("UPDATE %s SET translation=? WHERE id=?" % _ENG.CACHE_TABLE,
                            (txt, rid))
                print("  已更新 %d 行" % cur.rowcount)
                n_rows += 1
                # 自检: 复查该行现在真带着新译文(防写进了被遮蔽的世代)
                now = cur.execute("SELECT translation FROM %s WHERE id=?" % _ENG.CACHE_TABLE,
                                  (rid,)).fetchone()
                try:
                    _l2, b2 = _ENG.batch_entries(now[0] if now else "")
                except Exception:
                    b2 = {}
                if b2.get(int(mpi)) != new:
                    ok = False
                    print("  [FAIL 自检] 行 id=%s 写后复查没读到新译文" % rid)
    if not args.dry:
        con.commit()
    con.close()
    print("涉及段数: %d / 命中行数: %d" % (n_segs, n_rows))
    print("结论: %s%s" % ("PASS" if ok else "FAIL", " (dry-run 未写库)" if args.dry else ""))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--imported", default="")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--shift", default="", help='如 "S4:1,S9:2"')
    ap.add_argument("--sidecar", default=SIDECAR, help="侧车路径; 新论文先归档 latest.jsonl 再指向它")
    ap.add_argument("--tracking", default="",
                    help="next: translate_tracking.json 路径; 缺省用 manifest.source.path "
                         "(working 下那份会被下次渲染覆盖, 换篇后要用 --tracking 指回导出时那一份)")
    ap.add_argument("--fp", default="",
                    help="文档指纹; 缺省自动探测(平票取最后写入的世代; 探测失败回落 Cactaceae 默认)")
    ap.add_argument("--dry", action="store_true", help="只演算不写库")
    ap.add_argument("--rollback", action="store_true",
                    help="[v28.23] 撤销模式: 删本篇(或本文档指纹作用域下)的 raw→raw 骨架行, 不做注入")
    args = ap.parse_args()

    # 引擎接线门禁: 单跑本工具时也要拦 —— adopt 那边拦的是它自己派发的调用, 拦不住
    # 手工 `python seg_inject.py`(画像说 next 而缓存手术口径只对 1.x 验过)。
    _ok, _why = _PROF.stage_ok("inject" if not args.rollback else "rollback")
    if not _ok:
        print("FAIL: 引擎 %s 上未接线: %s" % (_PROF.key, _why))
        return 2

    if args.rollback:
        # [v28.43] next 的撤销必须**在**读侧车之前分流 —— next 没有侧车, 下面那段
        # (按 页码#段号 从侧车取原文当作用域)在 next 上会当场 KeyError。
        if _PROF.seg_source == "tracking_json":
            if args.fp:
                print("FAIL: --fp 是 1.x 的文档作用域指纹; next 的撤销按段表逐段定位, "
                      "不需要它")
                return 1
            if not args.imported or not args.manifest:
                print("FAIL: next 的 --rollback 需要 --imported 与 --manifest —— 它们分别是"
                      "\"撤的是我们注入的那一版\"与\"撤哪几段\";\n"
                      "      两个都由 import 阶段产出/登记, 台账里能查到路径")
                return 1
            return rollback_next(args)
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

    # [v28.41] next 路线在此分流: 定位口径是"整条 prompt 精确匹配 + 改批次 JSON 里
    # id==multi_paragraph_index 那一条", 与 1.x 的侧车/页级重编号/fp 作用域没有对应物。
    # 必须**在**下面读侧车之前分流 —— next 没有侧车, 那段代码在 next 载荷上会当场炸
    # KeyError(它按 页码#段号 取址, 而 next 载荷的坐标是 #S 阅读序)。
    if _PROF.seg_source == "tracking_json":
        if args.shift:
            print("FAIL: --shift 是 1.x 合并段(⋮ 两侧)的断点移位口径; next 载荷按 #S 编号, "
                  "没有对应的段坐标可移")
            return 1
        return inject_next(args)

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
            # cache_raw 是**缓存主键**, 必须与引擎写下的逐字节相同 —— 两侧都不动它。
            cache_raw = renumber(raw_page, raw_page)
            # [v28.80] 不可见字符体检(注入落盘前): 只剥**要写进去的译文**, 且剥在
            # renumber **之前** —— 夹在 `{v` 与数字之间的零宽字符会让重编号看不见这个
            # 占位符。前置断言用的 cur_trans 同样不动(它比的是库里既有的行)。
            new_trans = renumber(_TC.strip(imported[key]), raw_page)

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
