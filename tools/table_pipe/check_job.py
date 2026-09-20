# -*- coding: utf-8 -*-
"""对账器  v1

读 job_manifest.json + 豆包回包(每行: 编号<TAB>译文; 无法解析的行忽略并计数)。
门禁任一失败 -> 整体失败, 逐条列出, 不写 zh TSV, 退出码 1:
  G1 编号一一对应: 缺 / 多 / 重复 逐条报
  G2 占位符序列: 每行 {S###} 的个数、编号、出现顺序与 manifest 完全一致
  G3 裸数字: 译文里占位符之外不得出现任何数字(装配时数字已全部装进占位符)
  G4 符号保真: 原文含 +- 的行, 译文里 +- 个数不变
  G5 译文非空
全过 -> 占位符还原, 按 manifest 回填原表结构 -> table_10_*.zh.tsv + check_report.txt。

用法: python check_job.py [回包文件, 缺省 job_response.tsv]
"""
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
PH = re.compile(r"\{S\d{3}\}")
ID_RE = re.compile(r"^(k\d{3})\t(.*)$")


def main():
    resp_file = sys.argv[1] if len(sys.argv) > 1 else "job_response.tsv"
    with io.open(os.path.join(D, "job_manifest.json"), encoding="utf-8") as f:
        man = json.load(f)
    units = {u["id"]: u for u in man["units"]}

    got, dup, ignored = {}, [], 0
    with io.open(os.path.join(D, resp_file), encoding="utf-8-sig") as f:
        for ln in f:
            ln = ln.rstrip("\n").rstrip("\r")
            m = ID_RE.match(ln)
            if not m:
                if ln.strip():
                    ignored += 1
                continue
            if m.group(1) in got:
                dup.append(m.group(1))
            else:
                got[m.group(1)] = m.group(2)

    V = []
    # G1 编号一一对应
    miss = sorted(set(units) - set(got))
    extra = sorted(set(got) - set(units))
    if miss:
        V.append("G1 缺行 %d: %s" % (len(miss), " ".join(miss)))
    if extra:
        V.append("G1 多行 %d: %s" % (len(extra), " ".join(extra)))
    if dup:
        V.append("G1 重复行 %d: %s" % (len(dup), " ".join(sorted(set(dup)))))
    if ignored:
        V.append("G1 无法解析行 %d (已忽略, 请人工确认)" % ignored)

    # G2-G5 逐行
    for kid in sorted(set(units) & set(got)):
        u, t = units[kid], got[kid]
        exp = ["{%s}" % p for p in u["ph"]]
        act = PH.findall(t)
        if exp != act:
            V.append("G2 %s 占位符不符: 期望 %s 实得 %s" % (kid, exp or "无", act or "无"))
        if re.search(r"\d", PH.sub("", t)):
            V.append("G3 %s 占位符外出现裸数字: %r" % (kid, PH.sub("", t)))
        if u["orig"].count("±") != t.count("±"):
            V.append("G4 %s ± 个数变: 原 %d 译 %d" % (kid, u["orig"].count("±"), t.count("±")))
        if not t.strip():
            V.append("G5 %s 译文为空" % kid)

    # 报告
    R = ["对账 %s: 单元 %d, 回包 %d" % (resp_file, len(units), len(got))]
    if V:
        R.append("-" * 78)
    R += V or ["全部门禁通过 (G1-G5)"]
    ok = not V
    if ok:
        # 还原回填
        for tb, td in man["tables"].items():
            rows = [list(td["header"])] + [list(r) for r in td["rows"]]
            for u in man["units"]:
                if u["table"] != tb:
                    continue
                t = got[u["id"]]
                for pid, val in u["ph"].items():
                    t = t.replace("{%s}" % pid, val)
                assert "{S" not in t, "还原后仍残留占位符: " + t
                rows[u["r"]][u["c"]] = t
            out = os.path.join(D, tb + ".zh.tsv")
            with io.open(out, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write("\t".join(r) + "\n")
            R.append("回填 -> %s" % out)
    R.append("结论: " + ("PASS" if ok else "FAIL (退出码 1)"))
    with io.open(os.path.join(D, "check_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(R) + "\n")
    print("\n".join(R))
    sys.exit(0 if ok else 1)


main()
