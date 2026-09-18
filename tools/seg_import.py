# -*- coding: utf-8 -*-
"""seg_import.py — 收豆包译文: 解析/对账/回锚 (M1 工具, 与 seg_export.py 配对)

做什么:
  1. 取译文: --clip 读剪贴板, 或 --text 指定文件 (豆包回复的全文)
  2. 解析: 按 #S编号 行切块 (容忍 markdown 加粗/代码围栏)
  3. 对账 manifest: 编号集合一致; 合并段 ⋮ 恰好 1 个且两侧非空; 普通段禁止 ⋮
  4. 回锚: 对每段按侧车 raw 的 {vN} 原序, 在译文中定位字形值 -> 还原为 {vN}
     高价值字形 (含字母/数字) 找不到 -> FAIL; 纯标点找不到 -> 丢弃并记提示
  5. 产出 out/<name>.imported.json: {(page,seg): 带{vN}的译文} + 校验报告

用法:
  python tools/seg_import.py --manifest inbox\\payload_p2_p4.manifest.json --clip
退出码: 全过 0 / 有 FAIL 1
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SIDECAR = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "segflow", "latest.jsonl")
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
OUTDIR = os.path.join(PROJ, "out")

KEY_LINE = re.compile(r"^\s*#?\**\s*S(\d+)\s*\**\s*$")
V_TOKEN = re.compile(r"\{v(\d+)\}")


def load_sidecar_pages(sidecar):
    pages = {}
    with open(sidecar, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            pages[o["page"]] = o
    return pages


def get_clipboard():
    ps = ("powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw")
    return subprocess.run(ps, capture_output=True, check=True).stdout.decode("utf-8", "replace")


def parse_blocks(text):
    """返回 {int_key: 译文正文}, 忽略编号行之前的引导文字。"""
    blocks, cur = {}, None
    buf = []
    for line in text.splitlines():
        m = KEY_LINE.match(line)
        if m:
            if cur is not None:
                blocks[cur] = "\n".join(buf).strip()
            cur, buf = int(m.group(1)), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        blocks[cur] = "\n".join(buf).strip()
    return blocks


PUNCT_CHARS = set(".,;:!?()[]{}<>\"'“”‘’（）《》【】、，。；：？！…—·-–‐‑‒―")
_FW = str.maketrans("（）：；，", "():;,")


def _fw(ch):
    return ch.translate(_FW)


# [自研补丁 2026-09-19] 标点等价类。
# 合格的中文译者会按中文排版规范把 core 内部的半角标点写成全角
# (2.1,(20) -> 2.1，(20)), 或把并列逗号写成顿号 ((26),(27) -> (26)、(27))。
# 旧实现用 str.find 做字面匹配 -> 这类段落全被判"高价值字形未回锚"。
# Wang 篇实测: 99 处 FAIL 中约 20 处纯由此产生。
PUNCT_EQUIV = {
    ",": ",，、",
    ".": ".。",
    "(": "(（",
    ")": ")）",
    ";": ";；",
    ":": ":：",
    "?": "?？",
    "!": "!！",
    "[": "[【",
    "]": "]】",
}


def _core_pattern(core):
    """core -> 正则。标点位允许半/全角等价; 数字间的逗号额外允许整块消失
    (豆包把 2,000 写成 2000 时仍能命中, 渲染时仍用字形原值)。"""
    parts = []
    for k, ch in enumerate(core):
        if ch in PUNCT_EQUIV:
            cls = "[%s]" % re.escape(PUNCT_EQUIV[ch])
            if (ch == "," and 0 < k < len(core) - 1
                    and core[k - 1].isdigit() and core[k + 1].isdigit()):
                cls += "?"      # 千分位弹性
            parts.append(cls)
        else:
            parts.append(re.escape(ch))
    return re.compile("".join(parts))


def _spans(pat, text):
    return [(m.start(), m.end()) for m in pat.finditer(text)]


def _overlaps(s, e, taken):
    return any(s < te and ts < e for ts, te in taken)


def _free_spans(pat, seg_zh, taken):
    """pat 在译文里全部未被占用的落点(按出现序)。"""
    return [sp for sp in _spans(pat, seg_zh) if not _overlaps(sp[0], sp[1], taken)]


def split_value(val):
    """'(1793),' -> ('(', '1793', '),'); '-' -> ('', '', '')"""
    i = 0
    while i < len(val) and val[i] in PUNCT_CHARS:
        i += 1
    j = len(val)
    while j > i and val[j - 1] in PUNCT_CHARS:
        j -= 1
    return val[:i], val[i:j], val[j:]


def reanchor(seg_zh, raw, vars_):
    """只回锚高价值字形(核心=字母/数字), 纯标点字形按设计丢弃。
    核心命中后向两侧吸收与字形值一致的标点(全角兼容), 避免渲染时双重标点。

    两阶段定位 (2026-09-19 Wang 篇实测: 单遍单调游标 99 处 FAIL; 两遍后 21,
    其中 7 处是短字形抢位造成的假 FAIL):
      ① 独特性 -- 长 core 优先, 只占"全局唯一落点"的 token。长公式更长更独特,
         必须先行; 否则短字形(k→∞Ekxi(k)− / ak,s / s)会先抢走长公式唯一的
         落点, 反过来把长公式判 FAIL。
      ② 语序   -- 剩下的按原文顺序推进单调游标。译文语序与原文一致时最准;
         游标之后没有候选(中文把符号提前了)才退到"离游标最近"。

    返回 (译文, fails, drops, notes):
      fails -- 高价值字形在译文里定位不到 -> 调用方按 FAIL 处理
      drops -- 纯标点字形, 按设计丢弃
      notes -- (vn, val, 候选数) 歧义锚定台账, 供人工复查
    """
    fails, drops, notes = [], [], []
    toks = []
    for m in V_TOKEN.finditer(raw):
        vn = m.group(1)
        val = vars_.get(vn)
        if val is None:
            continue
        pre, core, post = split_value(val)
        if not core or not re.search(r"[A-Za-z0-9]", core):
            # 纯标点/纯符号字形(/ 等)按设计丢弃, 不参与搜索
            drops.append((vn, val))
            continue
        toks.append((vn, val, pre, core, post))

    taken, placed, rest = [], {}, []
    for t in sorted(toks, key=lambda x: -len(x[3])):    # ① 长 core 优先
        free = _free_spans(_core_pattern(t[3]), seg_zh, taken)
        if len(free) == 1:
            placed[t[0]] = free[0]
            taken.append(free[0])
        else:
            rest.append(t)

    cursor = 0
    for t in rest:                                      # ② 原文顺序 + 单调游标
        vn, val, core = t[0], t[1], t[3]
        free = _free_spans(_core_pattern(core), seg_zh, taken)
        if not free:
            fails.append((vn, val))
            continue
        ahead = [sp for sp in free if sp[0] >= cursor]
        hit = ahead[0] if ahead else min(free, key=lambda sp: abs(sp[0] - cursor))
        if len(free) > 1:
            notes.append((vn, val, len(free)))
        placed[vn] = hit
        taken.append(hit)
        cursor = hit[1]

    pieces = []
    for vn, _val, pre, _core, post in toks:
        if vn not in placed:
            continue
        s, e = placed[vn]
        os_, oe_ = s, e
        if pre:
            k = s
            while k > 0 and seg_zh[k - 1] in PUNCT_CHARS and _fw(seg_zh[k - 1]) in pre:
                k -= 1
            if k < s and _fw(seg_zh[k:s]) == _fw(pre):
                s = k
        if post:
            k = e
            while k < len(seg_zh) and seg_zh[k] in PUNCT_CHARS and _fw(seg_zh[k]) in post:
                k += 1
            if k > e and _fw(seg_zh[e:k]) == _fw(post):
                e = k
        if _overlaps(s, e, [x for x in taken if x != (os_, oe_)]):
            s, e = os_, oe_     # 吸收越界(语序重排后) -> 退回裸匹配, 不侵占用区间
        pieces.append((s, e, "{v%s}" % vn))
    pieces.sort()
    res, last = [], 0
    for s, e, rep in pieces:
        res.append(seg_zh[last:s])
        res.append(rep)
        last = e
    res.append(seg_zh[last:])
    return "".join(res), fails, drops, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--clip", action="store_true", help="从剪贴板取译文")
    ap.add_argument("--text", default="", help="从文件取译文")
    ap.add_argument("--sidecar", default=SIDECAR, help="侧车路径; 与导出时一致")
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        man = json.load(f)

    if args.clip:
        text = get_clipboard()
        src = "剪贴板"
    else:
        with open(args.text, encoding="utf-8-sig") as f:
            text = f.read()
        src = args.text

    # 去掉 markdown 代码围栏
    text = re.sub(r"^```[a-z]*\s*$", "", text, flags=re.M).strip()
    blocks = parse_blocks(text)
    print("来源: %s (%d 字符) -> 解析出 %d 段" % (src, len(text), len(blocks)))

    want = {it["key"]: it for it in man["items"]}
    got = {int(k): v for k, v in blocks.items()}
    report, ok = [], True

    # ---- 编号对账 (统一为整数) ----
    want_ids = {int(str(k).lstrip("S")) for k in want}
    miss = sorted(want_ids - set(got))
    extra = sorted(set(got) - want_ids)
    if miss:
        ok = False
        report.append("FAIL 缺段: %s" % miss)
    if extra:
        ok = False
        report.append("FAIL 多段: %s" % extra)

    pages = load_sidecar_pages(args.sidecar)
    imported = {}
    for key_s, it in sorted(want.items()):
        key = int(str(key_s).lstrip("S"))
        zh = got.get(key, "")
        n_parts = len(it["parts"])
        parts_zh = zh.split("⋮")
        # ⋮ 对账
        if it["merged"]:
            if len(parts_zh) != n_parts:
                ok = False
                report.append("FAIL #S%d ⋮ 数量 %d != 预期 %d" % (key, len(parts_zh) - 1, n_parts - 1))
                continue
        else:
            if "⋮" in zh:
                ok = False
                report.append("FAIL #S%d 普通段出现了 ⋮" % key)
            parts_zh = [zh.replace("⋮", "")]
        if not zh.strip():
            ok = False
            report.append("FAIL #S%d 译文为空" % key)
            continue
        # 回锚
        for (pg, seg), seg_zh in zip([(p["page"], p["seg"]) for p in it["parts"]], parts_zh):
            o = pages[pg]
            vv = o.get("vars") or {}
            raw = o["segs"][seg].get("raw") or ""
            if not V_TOKEN.search(raw):
                imported["%d#%d" % (pg, seg)] = seg_zh
                continue
            fixed, fails, drops, notes = reanchor(seg_zh, raw, vv)
            imported["%d#%d" % (pg, seg)] = fixed
            if fails:
                ok = False
                report.append("FAIL p%d#%d 高价值字形未回锚: %s" % (
                    pg, seg, ["{v%s}=%r" % f for f in fails[:8]]))
            if drops:
                report.append("提示 p%d#%d 纯标点字形丢弃 %d 个: %s" % (
                    pg, seg, len(drops), [d[1] for d in drops[:6]]))
            if notes:
                report.append("提示 p%d#%d 歧义锚定 %d 个(取最近, 需复查): %s" % (
                    pg, seg, len(notes),
                    ["{v%s}=%r x%d" % n for n in notes[:6]]))

    os.makedirs(OUTDIR, exist_ok=True)
    out_json = os.path.join(OUTDIR, os.path.basename(args.manifest).replace(".manifest.json", "") + ".imported.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(imported, f, ensure_ascii=False, indent=1)

    print("--- 校验报告 ---")
    for r in report:
        print(r)
    print("回锚段数: %d" % len(imported))
    print("产出: %s" % out_json)
    print("结论: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
