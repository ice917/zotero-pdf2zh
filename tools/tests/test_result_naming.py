# -*- coding: utf-8 -*-
"""交件命名契约 单元/一致性测试 (v28.68, 2026-09-22)

背景: 采纳回路里"交件"这一环本来就与厂商无关(粘贴通道谁都能用, 桥的工具名也不带厂商),
但从 v26.x 起交件文件名把"豆包"焊了进去 —— 用别家网页 AI 交的稿子也叫 xxx.doubao.txt,
名不副实。v28.68 把后缀族扩成 `doubao|webai`(新件缺省 webai, 旧件继续认), 并把
"后缀长什么样"从**四处实现**收成一份契约(tools/result_naming.py)。

本测试锁四件事, 每一条都对应一个"漏了就会静默出错"的点:

  ① 两份契约逐字节一致 —— server 侧(等交件的轮询)与 tools 侧跨进程不能共享 import,
     只能各自持一份; 而两份一旦漂了, 症状是"等到超时"(看起来像交件没交, 实际是
     名字没对上), 极难归因。故直接比对字节。
  ② 规范化边界: 新件补 .webai / 旧件 .doubao* 原样保留(改名**不许**把在跑的论文断链)
     / 已带族不叠两层 / 无扩展名与多段扩展名的行为与历史逐条一致。
  ③ 两族同权: globs / matches / belongs 都覆盖两族 —— 只扫一族就漏掉另一半,
     而"候选少算一份"正是 deliver 判"不唯一"的反面(会拿错旧件当新一轮)。
  ④ 接线守卫: 四处调用点(bridge / adopt / seg_merge / server)不得再各自写死后缀族,
     且 adopt 的 --clip 落盘名确实走契约(该篇已有唯一交件则沿用其名)。

真盘部分只碰本测试的临时目录, 不动项目 out/。
运行: venv python test_result_naming.py, 退出码 0=全过
"""
import glob
import hashlib
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
ROOT = tempfile.mkdtemp(prefix="p2z_naming_")

# 环境必须在 import 之前落定: adopt / doubao_bridge 在**模块层**读这些量
# (D 目录、inbox、review), 之后改 env 是没用的。
os.environ["P2Z_PROJ"] = ROOT
os.environ["P2Z_INBOX"] = os.path.join(ROOT, "inbox")
os.environ["P2Z_REVIEW"] = os.path.join(ROOT, "review")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import result_naming as RN   # noqa: E402
import doubao_bridge as BR   # noqa: E402
import adopt as AD           # noqa: E402

TOOLS_RN = os.path.join(TOOLS, "result_naming.py")
SRV_RN = os.path.join(REPO, "server", "utils", "result_naming.py")
# 四处调用点(相对仓库根): 前三处 import 契约, 第四处持副本
CALLERS = ("tools/doubao_bridge.py", "tools/adopt.py", "tools/seg_merge.py",
           "server/server.py")


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _src(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def main():
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    # ---- ① 两份契约逐字节一致 ----
    check("① server 侧契约在场", os.path.exists(SRV_RN), SRV_RN)
    if os.path.exists(SRV_RN):
        a, b = _md5(TOOLS_RN), _md5(SRV_RN)
        check("① 两份契约逐字节一致(漂了就是'等到超时')", a == b, "tools=%s server=%s" % (a, b))

    # ---- ② 规范化边界 ----
    N = RN.normalize
    check("② 默认族 = webai", RN.DEFAULT_FAMILY == "webai", RN.DEFAULT_FAMILY)
    check("② 两族名单", RN.FAMILIES == ("doubao", "webai"), RN.FAMILIES)
    check("② 新件补缺省族", N("egophys2026.txt") == "egophys2026.webai.txt", N("egophys2026.txt"))
    check("② 无扩展名 → .webai.txt", N("roundtrip_test") == "roundtrip_test.webai.txt",
          N("roundtrip_test"))
    check("② 多段扩展名剥最后一节", N("paper.v2.txt") == "paper.v2.webai.txt", N("paper.v2.txt"))
    check("② 非 .txt 也统一成 .txt", N("note.md") == "note.webai.txt", N("note.md"))
    check("② 旧件 .doubao 原样保留(改名不许断链)",
          N("wang2026.doubao.txt") == "wang2026.doubao.txt", N("wang2026.doubao.txt"))
    check("② 旧件 .doubao3 原样保留",
          N("wang2026.doubao3.txt") == "wang2026.doubao3.txt", N("wang2026.doubao3.txt"))
    check("② 新件 .webai 不叠成 .webai.webai",
          N("wang2026.webai.txt") == "wang2026.webai.txt", N("wang2026.webai.txt"))
    check("② .webai2 轮次保留", N("wang2026.webai2.txt") == "wang2026.webai2.txt",
          N("wang2026.webai2.txt"))
    check("② 显式指定旧族仍可用", N("x.txt", "doubao") == "x.doubao.txt", N("x.txt", "doubao"))
    check("② 已带族时 family 参数不再叠(不叠两层)",
          N("x.webai.txt", "doubao") == "x.webai.txt", N("x.webai.txt", "doubao"))

    # ---- ③ 篇名归一 / 认件 / 归属 ----
    check("③ stem_of 认新族", RN.stem_of("payload_x.webai2.txt") == "payload_x",
          RN.stem_of("payload_x.webai2.txt"))
    check("③ stem_of 认旧族", RN.stem_of("payload_x.doubao.txt") == "payload_x",
          RN.stem_of("payload_x.doubao.txt"))
    check("③ stem_of 三条入口同归(不一致就找不到载荷)",
          RN.stem_of("payload_x.webai2.txt") == RN.stem_of("payload_x.txt") == RN.stem_of("payload_x"))
    check("③ matches 只认交件, 中间产物不进",
          RN.matches("a.webai.txt") and RN.matches("a.doubao3.txt")
          and not RN.matches("a.imported.json") and not RN.matches("a.merged.txt")
          and not RN.matches("a.txt"))
    check("③ globs 覆盖两族", sorted(RN.globs("x")) == ["x.doubao*.txt", "x.webai*.txt"],
          RN.globs("x"))
    check("③ belongs 两族都算, 同前缀他篇不算",
          RN.belongs("x.webai2.txt", "x") and RN.belongs("x.doubao.txt", "x")
          and not RN.belongs("xy.webai.txt", "x"))

    # ---- ③b 真盘: 两族一次扫齐, 中间产物一件不进来 ----
    out = os.path.join(ROOT, "out")
    os.makedirs(out, exist_ok=True)
    for fn in ("paper.doubao.txt", "paper.doubao2.txt", "paper.webai.txt",
               "paper.imported.json", "paper.merged.txt", "paperX.webai.txt"):
        with open(os.path.join(out, fn), "w", encoding="utf-8") as f:
            f.write("#S1\n甲\n")
    want = ["paper.doubao.txt", "paper.doubao2.txt", "paper.webai.txt"]
    hits = sorted(os.path.basename(p) for pat in RN.globs("paper")
                  for p in glob.glob(os.path.join(out, pat)))
    check("③ 两族候选一次扫齐(只扫一族就少认件)", hits == want, hits)

    # ---- ④ 四处调用点: 引契约, 不写死后缀族 ----
    check("④ bridge 与契约同源(不是各自一套)", BR.RN is RN)
    check("④ adopt 与契约同源", AD.RN is RN)
    check("④ bridge 落盘走新族 + 旧件原样",
          BR._result_name("paper.txt") == "paper.webai.txt"
          and BR._result_name("paper.doubao.txt") == "paper.doubao.txt",
          BR._result_name("paper.txt"))
    check("④ bridge 篇名两条入口同归",
          BR._stem_of("paper.webai2.txt") == BR._stem_of("paper.txt") == "paper")
    BR.OUTDIR = out
    check("④ bridge 列件认两族",
          sorted(BR._result_names("paper")) == want, BR._result_names("paper"))
    AD.OUTDIR = out
    check("④ adopt 的交件发现走同一份口径",
          [os.path.basename(p) for p in AD.delivery_candidates("paper")] == want,
          AD.delivery_candidates("paper"))

    for rel in CALLERS:
        s = _src(rel)
        check("④ %s 引契约且不写死后缀" % os.path.basename(rel),
              "result_naming" in s and not re.search(r"""["']\.doubao\*\.txt""", s),
              rel)

    # adopt 的 --clip: 落盘名必须走契约(该篇恰有一份交件则沿用其名, 否则缺省族)
    clip = _src("tools/adopt.py").split("def stage_deliver", 1)[1].split("\ndef ", 1)[0]
    check("④ deliver 的 --clip 落盘名走契约", "delivery_candidates" in clip and "RN.normalize" in clip,
          clip[:200])

    shutil.rmtree(ROOT, ignore_errors=True)
    print("\n交件命名契约: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
