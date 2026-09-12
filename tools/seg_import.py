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
    核心命中后向两侧吸收与字形值一致的标点(全角兼容), 避免渲染时双重标点。"""
    fails, drops = [], []
    pieces = []
    cursor = 0
    for m in V_TOKEN.finditer(raw):
        vn = m.group(1)
        val = vars_.get(vn)
        if val is None:
            continue
        pre, core, post = split_value(val)
        if not core:
            drops.append((vn, val))
            continue
        idx = seg_zh.find(core, cursor)
        if idx < 0:
            fails.append((vn, val))
            continue
        s, e = idx, idx + len(core)
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
        pieces.append((s, e, "{v%s}" % vn))
        cursor = e
    pieces.sort()
    res, last = [], 0
    for s, e, rep in pieces:
        res.append(seg_zh[last:s])
        res.append(rep)
        last = e
    res.append(seg_zh[last:])
    return "".join(res), fails, drops


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
            fixed, fails, drops = reanchor(seg_zh, raw, vv)
            imported["%d#%d" % (pg, seg)] = fixed
            if fails:
                ok = False
                report.append("FAIL p%d#%d 高价值字形未回锚: %s" % (
                    pg, seg, ["{v%s}=%r" % f for f in fails[:8]]))
            if drops:
                report.append("提示 p%d#%d 纯标点字形丢弃 %d 个: %s" % (
                    pg, seg, len(drops), [d[1] for d in drops[:6]]))

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
