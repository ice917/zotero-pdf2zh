# -*- coding: utf-8 -*-
"""mk_grid v5 跨域加固 + mk_job 表头占位符 回归测试 (v42)

缘起(2026-10-10 跨论文类型实测, 5 篇: 生物分类学基准/流行病学/医学基础/仪器/纯数学负例):
管线在基准篇之外暴露四个硬缺陷, 本套件锁住修复:

  B1  mk_job 表头数字不装占位符 -> 临床表头(n = 1,836,006 / 95% CI)忠实照抄的译文
      100% 触发 G3(占位符外不得有数字), 好译文过不了门禁。
      修复: 表头与数据格同款 NUMTOK_RE 占位符化(实测一处改动解锁临床/医学表)。
  L3  mk_grid v4 行首"只认最左簇" -> 临床表缩进子行(8pt)与物种名续行(7pt)几何同信号,
      一刀切全并 -> 子行串格。v5 伴随判据: 缩进第 0 列行, 基线带内有其他列的伴,
      且伴填的是**行首没有的列** -> 新行; 伴填行首已有内容的列 -> 格内续行并回
      (只"有伴就拆"不够: 续行旁 Reference 列的年份同样断行作伴, 靠列占位分)。
  R1  mk_grid 零落格/零列裸崩(IndexError) -> 按证据指路: 侧排未烘正/图片型表格/裁剪框。
  R2  mk_job 空 TSV 裸崩(rows[0] IndexError) -> 指路 grid_audit。

设计约定: 全部在 tempfile 临时目录, 独立进程, 不碰工作区; 合成 PDF 用 pymupdf 现画
(7 页同构网格, 让 mk_grid 原生双表全程跑通, 不打补丁)。
"""
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.join(os.path.dirname(HERE), "table_pipe")
PY = sys.executable

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def run(script, wd, args=()):
    r = subprocess.run([PY, os.path.join(PIPE, script)] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace",
                       env=dict(os.environ, P2Z_TABLE_DIR=wd), cwd=wd, timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def synth_grid_pdf(path, pages=7):
    """合成 7 页同构网格: 3 列; 含 续行(缩进无伴, 要并) 与 子行(缩进+伴填新列, 要拆)。

    行布局(x, y=基线, 文本):
      50,100 "Age, yr"      | 150,100.3 "0.012"                    <- 硬锚点行
      57,118 "(Engelm.) Schum."                                     <- 续行: 缩进7pt, 同带无伴 -> 并回
      50,140 "Sex, male"    | 150,140.3 "0.031"                    <- 硬锚点行(分组头, 占列0/1)
      58,158 "20-29"        | 150,158.3 "317,019" | 250,158.1 "158,450"   <- 子行: 伴填列1/2, 列2 是行首没有的 -> 拆
      58,176 "30-39"        | 150,176.3 "426,863" | 250,176.1 "213,461"
      58,194 "40-49"        | 150,194.3 "205,087" | 250,194.1 "102,538"
    预期 TSV 5 行; 列2 需 >=MIN_SUPPORT(3) 个格才不被并掉, 故子行给满 3 行。
    """
    import pymupdf
    rows = [
        (50, 100, "Age, yr"), (150, 100.3, "0.012"),
        (57, 118, "(Engelm.) Schum."),
        (50, 140, "Sex, male"), (150, 140.3, "0.031"),
        (58, 158, "20-29"), (150, 158.3, "317,019"), (250, 158.1, "158,450"),
        (58, 176, "30-39"), (150, 176.3, "426,863"), (250, 176.1, "213,461"),
        (58, 194, "40-49"), (150, 194.3, "205,087"), (250, 194.1, "102,538"),
    ]
    doc = pymupdf.open()
    for _ in range(pages):
        pg = doc.new_page(width=595, height=842)
        for x, y, t in rows:
            pg.insert_text((x, y), t, fontsize=10)
    doc.save(path)
    doc.close()


def main():
    R = []

    def check(name, ok):
        R.append(ok)
        print(("  ✓ " if ok else "  ✗ ") + name)

    tmp = tempfile.mkdtemp(prefix="grid_v5_")
    try:
        # ---------- L3: 伴随判据(合成 7 页网格, mk_grid 原生跑通双表) ----------
        wd = os.path.join(tmp, "l3")
        os.makedirs(wd)
        synth_grid_pdf(os.path.join(wd, "excerpt_table_pages.pdf"))
        rc, out = run("mk_grid.py", wd)
        check("L3 mk_grid 合成网格跑通 (rc=0)", rc == 0)
        with io.open(os.path.join(wd, "table_10_1.tsv"), encoding="utf-8") as f:
            tsv = [r for r in csv.reader(f, delimiter="\t")]
        check("L3 拆行后共 5 行(父2行+子3行)", len(tsv) == 5)
        if len(tsv) == 5:
            check("L3 续行并回父行: 'Age, yr (Engelm.) Schum.'",
                  tsv[0][0] == "Age, yr (Engelm.) Schum." and tsv[0][1] == "0.012")
            check("L3 分组头行: 'Sex, male' 独立", tsv[1][0] == "Sex, male" and tsv[1][1] == "0.031")
            check("L3 子行独立成行且数值落位: '20-29 | 317,019 | 158,450'",
                  tsv[2] == ["20-29", "317,019", "158,450"])
            check("L3 子行: '40-49 | 205,087 | 102,538'",
                  tsv[4] == ["40-49", "205,087", "102,538"])
        with io.open(os.path.join(wd, "grid_audit.txt"), encoding="utf-8") as f:
            audit = f.read()
        check("L3 审计: 无'格数偏少'行(5 行全部落格)",
              "格数偏少的行: 0 / 4" in audit)

        # ---------- R1: 零落格 / 零列 不裸崩, 按证据指路 ----------
        import pymupdf
        wd = os.path.join(tmp, "r1a")
        os.makedirs(wd)
        d = pymupdf.open()
        d.new_page(width=595, height=842)          # 整页空白: 无文本无图片
        d.save(os.path.join(wd, "excerpt_table_pages.pdf"))
        d.close()
        rc, out = run("mk_grid.py", wd)
        check("R1 零落格: 退出码 1(不裸崩)", rc == 1 and "Traceback" not in out)
        check("R1 零落格: 文案指路'没有任何可落格'+裁剪框",
              "没有任何可落格" in out and "裁剪框" in out)

        wd = os.path.join(tmp, "r1b")
        os.makedirs(wd)
        d = pymupdf.open()
        pg = d.new_page(width=595, height=842)     # 只有 2 个孤立格: 每簇 1 格 < MIN_SUPPORT
        pg.insert_text((50, 100), "a", fontsize=10)
        pg.insert_text((300, 130), "b", fontsize=10)
        d.save(os.path.join(wd, "excerpt_table_pages.pdf"))
        d.close()
        rc, out = run("mk_grid.py", wd)
        check("R1 零列: 退出码 1(不裸崩)", rc == 1 and "Traceback" not in out)
        check("R1 零列: 文案指路'构不成任何一列'",
              "构不成任何一列" in out)

        # ---------- B1 + R2: mk_job 表头占位符 / 空 TSV ----------
        wd = os.path.join(tmp, "b1")
        os.makedirs(wd)
        with io.open(os.path.join(wd, "table_10_1.tsv"), "w", encoding="utf-8") as f:
            f.write("Characteristic\tTotal Cohort (n = 1,836,006)\tSMD\n"
                    "Age, yr\t215,436\t,0.001\n"
                    "Sex, male\t974,275\t0.031\n")
        with io.open(os.path.join(wd, "table_10_2.tsv"), "w", encoding="utf-8") as f:
            f.write("Groups\tp values\nCTRL\t0.034\n")
        rc, out = run("mk_job.py", wd)
        check("B1 mk_job 装配跑通 (rc=0)", rc == 0)
        man = json.load(io.open(os.path.join(wd, "job_manifest.json"), encoding="utf-8"))
        hdr_u = next((u for u in man["units"] if u["r"] == 0
                      and u["orig"].startswith("Total Cohort")), None)
        check("B1 含数字表头已占位符化: 'Total Cohort (n = {S001})'",
              hdr_u is not None and hdr_u["orig"] == "Total Cohort (n = {S001})")
        check("B1 占位符原值入 ph: S001 -> '1,836,006'",
              hdr_u is not None and hdr_u["ph"].get("S001") == "1,836,006")
        smd_u = next((u for u in man["units"] if u["r"] == 0 and u["orig"] == "SMD"), None)
        check("B1 无数字表头不受影响: 'SMD' 原样、无占位符",
              smd_u is not None and not smd_u["ph"])

        # 忠实回包(逐字照抄, 占位符原样) —— 修复前此包 100% 被 G3 拦
        with io.open(os.path.join(wd, "job_response.tsv"), "w", encoding="utf-8") as f:
            for u in man["units"]:
                f.write("%s\t%s\n" % (u["id"], u["orig"]))
        rc, out = run("check_job.py", wd, ["job_response.tsv"])
        check("B1 忠实回包过门禁(修复前必 FAIL G3)", rc == 0 and "结论: PASS" in out)
        with io.open(os.path.join(wd, "table_10_1.zh.tsv"), encoding="utf-8") as f:
            zh = [r for r in csv.reader(f, delimiter="\t")]
        check("B1 回填还原表头数字: 'Total Cohort (n = 1,836,006)'",
              zh and zh[0][1] == "Total Cohort (n = 1,836,006)")

        wd = os.path.join(tmp, "r2")
        os.makedirs(wd)
        io.open(os.path.join(wd, "table_10_1.tsv"), "w").close()   # 空 TSV
        rc, out = run("mk_job.py", wd)
        check("R2 空TSV: 退出码 1(不裸崩)且指路 grid_audit",
              rc == 1 and "Traceback" not in out and "是空的" in out and "grid_audit" in out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = R.count(False)
    print("%s: %d 例, 失败 %d" % (os.path.basename(__file__), len(R), bad))
    sys.exit(1 if bad else 0)


main()
