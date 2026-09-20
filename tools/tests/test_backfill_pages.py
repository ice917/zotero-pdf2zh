# -*- coding: utf-8 -*-
"""backfill_pages 零可译段页判定单元测试 (v28.15, 2026-09-19)

被锁死的缺陷 (与 v28.10 seg_export 同源的**页码口径漂移**, 但更重):
  `zero_pages_from_sidecar` 把侧车的 `o["page"]` 当成输出页码。而 `page` 是
  receive_layout 的**回调计数** —— 页面回调 + 该页的图形回调各占一号, 同一页
  会占多行 (实测 Zhang 15 页占 26 行, 漂 11)。后果不是"点错一页", 而是**全错**:
  图形回调那行没有可译段 -> 被判成"零可译段页" -> 该页(常常是正文页)被原版
  英文页顶掉。实测该篇会输出 [1,11,13,15,17,18,23,24] 八个假零页。

修法 (本次):
  ① 真实页码取 `pageid + 1` (老侧车无 pageid 时回落 `page`, 与旧行为一致);
  ② 判据按**页聚合**: 该页所有记录都不含可译段才算零可译段页 —— 逐条记录判定
     会把同页的文字回调误判成零页 (模块 docstring 原本就这么写, 实现没照做)。

本测试锁五段语义, 全部走 CLI (沙箱 + 合成侧车 + 合成 PDF):
  ① 同页图形记录不得判零 (漂移 + 误判双杀), 真零页按**真实页码**报出
  ② 老侧车 (无 pageid) 回落 `page` 口径, 不误伤归档件
  ③ 无零页 -> 直接返回 0, 不产出文件
  ④ `--pages` 仍是 1 基真实页码 (与显式用法口径一致)
  ⑤ 页数不一致的 1:1 校验原地不动 (回填的前置门禁)

判定手法: 造 mono/orig 两版 PDF, **每页 mediabox 宽度不同** (mono 2xx / orig 5xx),
回填后逐页读宽度即可知道该页取自哪一侧 —— 不依赖肉眼/不依赖 PDF 内容。

运行: venv python test_backfill_pages.py, 退出码 0=全过
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOLS, "backfill_pages.py")

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

from pypdf import PdfReader, PdfWriter  # noqa: E402


def make_pdf(path, n_pages, base_width):
    """n 页空白 PDF, 第 i 页宽度 base_width+i —— 宽度即"这一页来自哪一侧"的指纹。"""
    w = PdfWriter()
    for i in range(n_pages):
        w.add_blank_page(width=base_width + i, height=700)
    with open(path, "wb") as f:
        w.write(f)
    return path


def make_sidecar(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for o in records:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return path


def widths(path):
    return [int(float(p.mediabox.width)) for p in PdfReader(path).pages]


def reported_pages(stdout):
    """从 "回填 N 页 [2, 5] -> ..." 里取回回填页列表; 没这行则 None。"""
    m = re.search(r"回填 \d+ 页 \[([\d,\s]*)\]", stdout)
    if not m:
        return None
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


def run(root, *args):
    proc = subprocess.run([sys.executable, SCRIPT] + list(args),
                          cwd=root, capture_output=True)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def main():
    tmp = tempfile.mkdtemp(prefix="pdf2zh_test_")
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    # 4 页: mono 宽 200..203, orig 宽 500..503
    mono = make_pdf(os.path.join(tmp, "mono.pdf"), 4, 200)
    orig = make_pdf(os.path.join(tmp, "orig.pdf"), 4, 500)

    # ---- ① 漂移 + 同页误判: 真零页只有真实第 2 页 ----
    sc = make_sidecar(os.path.join(tmp, "a.jsonl"), [
        {"page": 1, "pageid": 0, "segs": [{"raw": "{v0}"}]},           # 图形回调 p1
        {"page": 2, "pageid": 0, "segs": [{"raw": "Title of paper"}]},  # 页面回调 p1
        {"page": 3, "pageid": 1, "segs": [{"raw": "{v0}"}]},           # 图形回调 p2
        {"page": 4, "pageid": 1, "segs": [{"raw": "{v1}{v2}"}]},       # 页面回调 p2 (整页表格)
        {"page": 5, "pageid": 2, "segs": [{"raw": "Body text 1"}]},    # 页面回调 p3
    ])
    out = os.path.join(tmp, "a_out.pdf")
    rc, so, se = run(tmp, "--mono", mono, "--original", orig, "--out", out, "--sidecar", sc)
    check("① 退出码 0", rc == 0, "%d %s" % (rc, se[-200:]))
    check("① 只报真实第 2 页 (旧口径的假零页 1/3/4/5 一个都没报)",
          reported_pages(so) == [2], so.strip())
    got = widths(out) if os.path.exists(out) else []
    check("① 仅真实第 2 页取自原版", got == [200, 501, 202, 203], repr(got))

    # ---- ② 同页多记录全部纯字形 -> 只报一次 (去重) ----
    sc2 = make_sidecar(os.path.join(tmp, "b.jsonl"), [
        {"page": 1, "pageid": 0, "segs": [{"raw": "Intro text"}]},
        {"page": 2, "pageid": 1, "segs": [{"raw": "{v0}"}]},
        {"page": 3, "pageid": 1, "segs": [{"raw": "{v1}{v2}"}]},
    ])
    out2 = os.path.join(tmp, "b_out.pdf")
    rc, so, se = run(tmp, "--mono", mono, "--original", orig, "--out", out2, "--sidecar", sc2)
    check("② 同页两条纯字形记录只报一页", rc == 0 and reported_pages(so) == [2], so.strip())

    # ---- ③ 老侧车 (无 pageid) 回落 page 口径 ----
    sc3 = make_sidecar(os.path.join(tmp, "c.jsonl"), [
        {"page": 1, "segs": [{"raw": "Intro text"}]},
        {"page": 2, "segs": [{"raw": "{v0}"}]},
    ])
    out3 = os.path.join(tmp, "c_out.pdf")
    rc, so, se = run(tmp, "--mono", mono, "--original", orig, "--out", out3, "--sidecar", sc3)
    check("③ 无 pageid -> 回落 page 口径", rc == 0 and reported_pages(so) == [2], so.strip())

    # ---- ④ 无零页 -> 早退, 不产文件 ----
    sc4 = make_sidecar(os.path.join(tmp, "d.jsonl"), [
        {"page": 1, "pageid": 0, "segs": [{"raw": "A"}]},
        {"page": 2, "pageid": 1, "segs": [{"raw": "B"}]},
    ])
    out4 = os.path.join(tmp, "d_out.pdf")
    rc, so, se = run(tmp, "--mono", mono, "--original", orig, "--out", out4, "--sidecar", sc4)
    check("④ 无零页 -> 退出码 0 且提示", rc == 0 and "没有需要回填的页" in so, so.strip())
    check("④ 无零页 -> 不产出文件", not os.path.exists(out4), out4)

    # ---- ⑤ --pages 仍按 1 基真实页码; 页数不一致仍拒绝 ----
    out5 = os.path.join(tmp, "e_out.pdf")
    rc, so, se = run(tmp, "--mono", mono, "--original", orig, "--out", out5, "--pages", "2-3")
    got5 = widths(out5) if os.path.exists(out5) else []
    check("⑤ --pages 2-3 -> 第 2,3 页取自原版", rc == 0 and got5 == [200, 501, 502, 203], repr(got5))

    orig3 = make_pdf(os.path.join(tmp, "orig3.pdf"), 3, 500)
    out6 = os.path.join(tmp, "f_out.pdf")
    rc, so, se = run(tmp, "--mono", mono, "--original", orig3, "--out", out6, "--pages", "2")
    check("⑤ 页数不一致 -> 拒绝执行", rc == 1 and "FAIL 页数不一致" in so, so.strip())

    print("\nbackfill_pages 零可译段页判定单元测试: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
