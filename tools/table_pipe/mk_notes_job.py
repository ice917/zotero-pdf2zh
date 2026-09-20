# -*- coding: utf-8 -*-
"""表注装配器: 把切格时归为"噪声"的表标题/表注/表下脚注装配成第二个翻译任务

mk_grid.py 的 split_noise 把表注判为噪声不进 TSV; 但表注定义了全部代码含义
(SC/SI/GSI/SHFT/...), 附录没有它读者解不了码。本脚本与 mk_grid 同源分拣
(同一 PROSE_W/TAIL_DY/CHAR_FIX), 产出:

  job_notes_doubao.txt   编号<TAB>英文, 整段粘给豆包
  notes_manifest.json    对账契约(id -> 表/角色/原文/占位符/保护代码)

check 模式: python mk_notes_job.py check [回包文件, 缺省 job_notes_response.tsv]
  门禁 G1 编号对应 / G2 占位符序列 / G3 裸数字 / G5 非空 / G6 代码缩写逐一在译文出现
  全过 -> 占位符还原 -> notes_zh.json(供 mk_appendix.py 排版)

表下脚注(如 T1 的 aUndetermined)从 grid_audit.txt 解析 —— 审计是唯一事实源, 不二次猜测。
"""
import io
import json
import os
import re
import sys

import pymupdf

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SD = os.path.dirname(os.path.abspath(__file__))          # 脚本目录(本件所在)
D = os.environ.get("P2Z_TABLE_DIR") or SD                # 工作目录(数据所在), 缺省=脚本目录
EXC = os.path.join(D, "excerpt_table_pages.pdf")
AUD = os.path.join(D, "grid_audit.txt")

PROSE_W, TAIL_DY = 0.45, 3.0          # 与 mk_grid.py 同值
CHAR_FIX = {chr(1): "±"}
NUMTOK_RE = re.compile(r"\d+(?:[.,]\d+)*")
CODE_RE = re.compile(r"\b[A-Z][A-Z0-9]{0,4}\b")   # 全大写缩写(SC/GSI/SHFT/C3/R/Z...)

TABLES = [("table_10_1", [0]), ("table_10_2", list(range(1, 7)))]

PREAMBLE = """【任务】下面每行是学术表格的标题或表注, 格式: 编号<TAB>英文。请逐行译成简体中文。
【输出】与输入同格式: 编号<TAB>中文。除编号行外不要输出任何别的内容。
【铁律】
1. {S###} 是数据占位符(表号、数字): 一个都不能丢、不能改写、不能调换顺序。
2. 占位符之外不得出现任何数字。
3. ± – % 等符号原样保留。
4. 拉丁学名、人名保留原文。
5. 分类代码缩写(如 R、Z、B、SC、SI、GSI、SHFT、CE)原样保留——它们正是表注要定义的对象。
【待译】"""


def norm(t):
    for bad, good in CHAR_FIX.items():
        t = t.replace(bad, good)
    return " ".join(t.split())


def noise_paragraphs(idx):
    """与 mk_grid.split_noise 同口径: 表注(整幅宽散文)+表注末行 -> 按页合并成段;
    (continued)/表格标记丢弃。返回 [(页码, 段文本), ...] 按页序。"""
    doc = pymupdf.open(EXC)
    out = []
    for i in idx:
        page = doc[i]
        rows = []
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                t = norm("".join(s["text"] for s in ln["spans"]))
                if not t:
                    continue
                bb, d = ln["bbox"], tuple(round(v, 2) for v in ln["dir"])
                if d == (1.0, 0.0):
                    rows.append(dict(y0=bb[1], y1=bb[3], x0=bb[0], x1=bb[2], t=t))
        rows.sort(key=lambda r: (r["y1"], r["x0"]))
        wide = [r for r in rows if (r["x1"] - r["x0"]) > PROSE_W * page.rect.width]
        wids = set(id(r) for r in wide)
        bottoms = [r["y1"] for r in wide]
        para = []
        for r in rows:
            if id(r) in wids:
                para.append(r["t"])
            elif any(0 <= r["y0"] - b < TAIL_DY for b in bottoms):
                para.append(r["t"])           # 表注末行
            elif r["t"].lower().startswith("table 10.") or r["t"].strip() == "(continued)":
                pass                           # 续表标记, 丢弃(审计里 mk_grid 已逐条报)
        if para:
            out.append((i + 1, " ".join(para)))
    doc.close()
    return out


def foots_from_audit(label):
    """从 grid_audit.txt 解析表下脚注(单格行)。审计是唯一事实源。"""
    secs = io.open(AUD, encoding="utf-8").read().split("=" * 78)
    for s in secs:
        if s.strip().startswith(label):
            m = re.search(r"表下脚注\(单格行, 未进 TSV\) \d+ 条:\s*\n((?:  .*\n?)*)", s)
            if not m:
                return []
            return [ln.strip().split(None, 2)[2] for ln in m.group(1).splitlines() if ln.strip()]
    return []


def assemble():
    units, audit = [], []
    phmap = {}

    def new_ph(val):
        pid = "S%03d" % (len(phmap) + 1)
        phmap[pid] = val
        return "{%s}" % pid

    for label, idx in TABLES:
        paras = noise_paragraphs(idx)
        seen = {}
        kept = []
        for pg, text in paras:
            if text in seen:
                audit.append("%s p%d 重复表注(已并): %s..." % (label, pg, text[:50]))
                continue
            seen[text] = pg
            kept.append((pg, text))
        foots = foots_from_audit(label)

        A = ["%s: 表注段 %d (去重后) / 表下脚注 %d" % (label, len(kept), len(foots))]
        for pg, text in kept:
            uid = "n%03d" % (len(units) + 1)
            body = NUMTOK_RE.sub(lambda m: new_ph(m.group(0)), text)
            units.append({"id": uid, "table": label, "role": "note",
                          "orig": body, "ph": {}, "pg": pg})
            A.append("  %s [p%d 注] %s" % (uid, pg, text[:70]))
        for t in foots:
            uid = "n%03d" % (len(units) + 1)
            body = NUMTOK_RE.sub(lambda m: new_ph(m.group(0)), t)
            units.append({"id": uid, "table": label, "role": "foot",
                          "orig": body, "ph": {}, "pg": 0})
            A.append("  %s [脚注] %s" % (uid, t[:70]))
        audit.append("\n".join(A))

    for u in units:
        u["ph"] = {pid: phmap[pid] for pid in re.findall(r"\{(S\d{3})\}", u["orig"])}
        # 保护代码从"占位符还原后"的原文提取, 排除我们自己的占位符名(S001...)
        raw = u["orig"]
        for pid, val in u["ph"].items():
            raw = raw.replace("{%s}" % pid, val)
        u["codes"] = sorted(set(c for c in CODE_RE.findall(raw)
                                if not re.match(r"^S\d+$", c)))

    with io.open(os.path.join(D, "job_notes_doubao.txt"), "w", encoding="utf-8") as f:
        f.write(PREAMBLE + "\n")
        for u in units:
            f.write("%s\t%s\n" % (u["id"], u["orig"]))
    with io.open(os.path.join(D, "notes_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"units": units}, f, ensure_ascii=False, indent=1)
    print("\n".join(audit))
    print("-" * 78)
    print("单元 %d 个 / %d 字符 -> job_notes_doubao.txt"
          % (len(units), sum(len(u["orig"]) for u in units)))
    print("保护代码: %s" % sorted(set(c for u in units for c in u["codes"])))


def check():
    resp = sys.argv[2] if len(sys.argv) > 2 else "job_notes_response.tsv"
    with io.open(os.path.join(D, "notes_manifest.json"), encoding="utf-8") as f:
        units = {u["id"]: u for u in json.load(f)["units"]}
    got, ignored = {}, 0
    with io.open(os.path.join(D, resp), encoding="utf-8-sig") as f:
        for ln in f:
            # 网页对话渲染会把 TAB 转成空格: 分隔符放宽为 TAB 或首个空格
            m = re.match(r"^(n\d{3})[\t ](.*)$", ln.rstrip("\r\n"))
            if m:
                got[m.group(1)] = m.group(2)
            elif ln.strip():
                ignored += 1
    V = []
    miss = sorted(set(units) - set(got))
    extra = sorted(set(got) - set(units))
    if miss:
        V.append("G1 缺行 %d: %s" % (len(miss), " ".join(miss)))
    if extra:
        V.append("G1 多行 %d: %s" % (len(extra), " ".join(extra)))
    if ignored:
        V.append("G1 无法解析行 %d (已忽略)" % ignored)
    PH = re.compile(r"\{S\d{3}\}")
    for uid in sorted(set(units) & set(got)):
        u, t = units[uid], got[uid]
        if ["{%s}" % p for p in u["ph"]] != PH.findall(t):
            V.append("G2 %s 占位符不符" % uid)
        if re.search(r"\d", PH.sub("", t)):
            V.append("G3 %s 占位符外出现裸数字: %r" % (uid, PH.sub("", t)[:50]))
        if not t.strip():
            V.append("G5 %s 译文为空" % uid)
        for c in u["codes"]:
            if c not in t:
                V.append("G6 %s 代码 %r 未在译文出现" % (uid, c))
    R = ["表注对账 %s: 单元 %d, 回包 %d" % (resp, len(units), len(got))]
    R += V or ["全部门禁通过 (G1/G2/G3/G5/G6)"]
    ok = not V
    if ok:
        notes = {}
        for uid in sorted(units):
            u = units[uid]
            t = got[uid]
            for pid, val in u["ph"].items():
                t = t.replace("{%s}" % pid, val)
            notes.setdefault(u["table"], {})[u["role"]] = t
        with io.open(os.path.join(D, "notes_zh.json"), "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=1)
        R.append("回填 -> notes_zh.json")
    R.append("结论: " + ("PASS" if ok else "FAIL (退出码 1)"))
    print("\n".join(R))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    check() if len(sys.argv) > 1 and sys.argv[1] == "check" else assemble()
