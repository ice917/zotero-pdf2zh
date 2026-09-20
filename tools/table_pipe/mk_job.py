# -*- coding: utf-8 -*-
"""待译文装配器  v1

读 mk_grid.py 产出的 table_10_*.tsv, 只把"该翻的内容"编号后发给翻译方(豆包):

  1) 列策略: 逐列统计画像 -> 禁翻(NO) / 可翻(TR)。判据全部统计式/结构式,
     不含本书专名, 换一本书应可复用; 每列命中哪条规则、证据比率全部写进审计, 不许静默判:
       R1 数值列   >=90% 格为纯数字/区间/符号
       R5 引文列   >=80% 格含年份或 et al.
       R3' 专名列  >=90% 格呈"大写词+小写词"且 >=60% 格含括号命名人(如 (Engelm.))
       R2 代码列   全部格的字母 token 都 <=4 字符
       R4 拉丁孤词 全部格为单 token、首字母大写、distinct==n、中位长 >=8(亚科名等)
       否则 TR。已知局限: 命名人全裸写(无括号)的物种列可能漏过 R3', 审计里给两个比率供人核。
     表头逐格判: 无字母、或字母 token 全 <=2 字符 -> 不送译。

  2) 自举禁翻面: 禁翻列整体不送译; 可翻格里的数字一律换成 {S###} 占位符(翻完由
     check_job.py 还原); 折行连字空格(前段实测 15 处全部是本身带的 - 或 /)就地还原,
     逐条记入审计。

  3) 产物: job_doubao.txt(编号<TAB>文本, 整段粘给豆包)
           job_manifest.json(表结构 + 编号 -> 行/列/原文/占位符原值, 对账契约)
           job_audit.txt(列画像/判定证据/连字还原/跳过格/规模)。

对账见 check_job.py; 演练回包见 mock_doubao.py。
"""
import csv
import io
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SD = os.path.dirname(os.path.abspath(__file__))          # 脚本目录(本件所在)
D = os.environ.get("P2Z_TABLE_DIR") or SD                # 工作目录(数据所在), 缺省=脚本目录
TABLES = ["table_10_1", "table_10_2"]

NUM_RE   = re.compile(r"^[\d.,:;\s()\-–—±?*%/]*$")            # 纯数值/区间/符号格
CITE_RE  = re.compile(r"\b(?:18|19|20)\d{2}\b|et\s+al\.?")    # 年份 / et al.
BINOM_RE = re.compile(r"^[A-ZÀ-Þ][^\s,;]*\s+[a-zà-ÿﬁﬂ´˜¨ı\-]")  # 大写词 + 小写词(二名法形)
AUTH_RE  = re.compile(r"\([A-Z][^()]*\)")                     # 括号命名人(如 (Engelm.))
TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿﬁﬂ´˜¨ı]+")                  # 字母 token
NUMTOK_RE = re.compile(r"\d+(?:[.,]\d+)*")                    # 可翻格内待锁的数字

HYPH_RE  = re.compile(r"([A-Za-zÀ-ÿﬁﬂ´˜¨ı])-\s+([a-zà-ÿﬁﬂ´˜¨ı])")
SLASH_RE = re.compile(r"([A-Za-zÀ-ÿﬁﬂ´˜¨ı])/\s+(?=[A-Za-zÀ-ÿﬁﬂ´˜¨ı])")


def dehyph(t, log, where):
    """折行连字空格还原: 词尾- + 下词小写开头 -> 拼接; 词尾/ + 下词 -> 拼接。逐条记 log。"""
    t = SLASH_RE.sub(lambda m: (log.append("%s  %r -> %s/" % (where, m.group(0), m.group(1)))
                                or m.group(1) + "/"), t)
    t = HYPH_RE.sub(lambda m: (log.append("%s  %r -> %s-%s" % (where, m.group(0),
                                                               m.group(1), m.group(2)))
                               or m.group(1) + "-" + m.group(2)), t)
    return t


def classify(vals):
    """vals = 该列全部非空数据格 -> (policy, rule, evidence)"""
    n = len(vals)
    if not n:
        return "NO", "空列", "无非空格"
    num  = sum(1 for v in vals if NUM_RE.match(v))
    cite = sum(1 for v in vals if CITE_RE.search(v))
    bino = sum(1 for v in vals if BINOM_RE.match(v))
    auth = sum(1 for v in vals if AUTH_RE.search(v))
    code = sum(1 for v in vals if all(len(t) <= 4 for t in TOKEN_RE.findall(v)))
    if num / n >= 0.9:
        return "NO", "R1数值", "数值格 %d/%d" % (num, n)
    if cite / n >= 0.8:
        return "NO", "R5引文", "含年份/et al. %d/%d" % (cite, n)
    if bino / n >= 0.9 and auth / n >= 0.6:
        return "NO", "R3'专名", "二名法 %d/%d + 括号命名人 %d/%d" % (bino, n, auth, n)
    if code == n:
        return "NO", "R2代码", "全部字母token<=4字符 %d/%d" % (code, n)
    singles = [TOKEN_RE.findall(v) for v in vals]
    if all(len(tk) == 1 for tk in singles) and len(set(vals)) == n \
            and all(v[:1].isupper() for v in vals):
        med = sorted(len(tk[0]) for tk in singles)[len(singles) // 2]
        if med >= 8:
            return "NO", "R4拉丁孤词", "单token大写孤词, 中位长 %d" % med
    return "TR", "R6自由文本", "其余"


def classify_header(cell):
    tk = TOKEN_RE.findall(cell)
    if not tk or all(len(t) <= 2 for t in tk):
        return "NO"
    return "TR"


PREAMBLE = """【任务】下面每行是学术表格里的一个待译片段, 格式: 编号<TAB>英文。请逐行译成简体中文。
【输出】与输入同格式: 编号<TAB>中文。除编号行外不要输出任何别的内容。
【铁律】
1. {S###} 是数据占位符: 一个都不能丢、不能改写(大小写/位数都不能变)、不能调换顺序。
2. 占位符之外不得出现任何数字(所有数字都已在占位符里)。
3. ± – % 等符号原样保留。
4. 若出现拉丁学名或人名片段, 保留原文。
【待译】"""


def main():
    manifest = {"tables": {}, "units": []}
    audit, phmap = [], {}
    units = manifest["units"]

    def new_ph(val):
        pid = "S%03d" % (len(phmap) + 1)
        phmap[pid] = val
        return "{%s}" % pid

    for tb in TABLES:
        with io.open(os.path.join(D, tb + ".tsv"), encoding="utf-8") as f:
            rows = [r for r in csv.reader(f, delimiter="\t")]
        hdr, data = rows[0], rows[1:]
        ncol = len(hdr)

        pol, ev = [], []
        for c in range(ncol):
            vals = [r[c].strip() for r in data if c < len(r) and r[c].strip()]
            p, rule, e = classify(vals)
            pol.append(p)
            ev.append((len(vals), len(set(vals)), rule, e))
        hpol = [classify_header(h) for h in hdr]

        manifest["tables"][tb] = {"header": hdr, "rows": data,
                                  "col_pol": pol, "head_pol": hpol}

        A = ["=" * 78, "%s : %d 列, 数据行 %d" % (tb, ncol, len(data)),
             "-" * 78, "列画像与判定:"]
        for c in range(ncol):
            nv, nd, rule, e = ev[c]
            A.append("  [%2d] %-40s n=%2d d=%2d -> %-2s %s  (%s)"
                     % (c, hdr[c][:40], nv, nd, pol[c], rule, e))
        no_h = " ".join("%d:%s" % (c, hpol[c]) for c in range(ncol) if hpol[c] == "NO")
        A.append("  表头不送译的格: " + (no_h or "无(全部TR)"))

        # 单元装配: 编号全局递增; 占位符从 orig 提取(全局编号, phmap 里存原值)
        nlog, skipped, nunit0 = [], [], len(units)

        def add_unit(r, c, text):
            units.append({"id": "k%03d" % (len(units) + 1), "table": tb,
                          "r": r, "c": c, "orig": text, "ph": {}})

        for c in range(ncol):
            if hpol[c] == "TR":
                add_unit(0, c, dehyph(hdr[c], nlog, "表头[%d]" % c))
        for c in range(ncol):
            if pol[c] != "TR":
                continue
            for ri, row in enumerate(data):
                cell = row[c].strip() if c < len(row) else ""
                if not cell or NUM_RE.match(cell) or not any(ch.isalpha() for ch in cell):
                    if cell:
                        skipped.append((c, ri + 1, cell))
                    continue
                where = "%s r%d c%d" % (tb, ri + 1, c)
                t = dehyph(cell, nlog, where)
                add_unit(ri + 1, c, NUMTOK_RE.sub(lambda m: new_ph(m.group(0)), t))

        for u in units:
            u["ph"] = {pid: phmap[pid] for pid in re.findall(r"\{(S\d{3})\}", u["orig"])}

        A.append("-" * 78)
        A.append("连字/斜杠折行还原 %d 处:" % len(nlog))
        A += ["  " + x for x in nlog] or ["  无"]
        A.append("-" * 78)
        agg = {}
        for c, ri, cell in skipped:
            agg.setdefault((c, cell), []).append(ri)
        A.append("可翻列跳过格(无字母内容, 不送译) %d 格:" % len(skipped))
        for (c, cell), ris in sorted(agg.items()):
            A.append("  c%d %r x%d  行%s" % (c, cell, len(ris),
                                            ",".join(map(str, ris[:12])) + ("..." if len(ris) > 12 else "")))
        nu = len(units) - nunit0
        nc = sum(len(u["orig"]) for u in units[nunit0:])
        A.append("-" * 78)
        A.append("本表单元 %d 个 / %d 字符; NO 列 %d / TR 列 %d"
                 % (nu, nc, sum(1 for p in pol if p == "NO"), sum(1 for p in pol if p == "TR")))
        audit.append("\n".join(A))

    with io.open(os.path.join(D, "job_doubao.txt"), "w", encoding="utf-8") as f:
        f.write(PREAMBLE + "\n")
        for u in units:
            f.write("%s\t%s\n" % (u["id"], u["orig"]))
    with io.open(os.path.join(D, "job_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    with io.open(os.path.join(D, "job_audit.txt"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(audit) + "\n")

    print("单元 %d 个 / %d 字符 -> job_doubao.txt" % (len(units), sum(len(u["orig"]) for u in units)))
    print("占位符 %d 个" % len(phmap))
    print("manifest -> job_manifest.json")
    print("审计     -> job_audit.txt")
    # 列策略速览
    for tb in TABLES:
        tp = manifest["tables"][tb]
        print("%s 列策略: %s" % (tb, "".join("T" if p == "TR" else "." for p in tp["col_pol"])))


main()
