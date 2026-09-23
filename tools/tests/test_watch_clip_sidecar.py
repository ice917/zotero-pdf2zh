# -*- coding: utf-8 -*-
"""watch_clip 侧车身份判据回归 (v28.75, 2026-09-23)

锁的是**判错篇 = 整条管线错篇**的那三处修复(此前零覆盖)。三者是链式依赖,
任何一处退回旧口径, 下游(seg_import 回锚 / BODY_PDF 指向)就静默错一篇:

  ① `_payload_samples`: 只采**第一个 #S 之后**的行。它之前是装配器写的前言
     ([文档]/[任务]/[规则] + 编号规则正文, 见 seg_export.RULES) —— 那几行**任何**
     侧车里都不会有, 采到它们 = 身份分恒为 0。
  ② `_sidecar_identity`: 比对前**还原占位符 + 去空白**。侧车 `segs[].raw` 里数字/字母
     块被换成了 `{vN}`(原值在同行的 vars), 不还原则对**自己**的侧车也是 0 命中;
     载荷正文与侧车记录排版本就不同, 不去空白同样比不出。
  ③ `body_sidecar().score()` 第三档 `mtime` **破平局**: 前两档都平局时(这批候车里没有
     本篇侧车, 或本载荷没有正文采样), 旧口径按 `os.listdir` 的任意顺序定胜负 ——
     实测把新篇判给了上一篇的侧车。装配与侧车同一刻落盘, 取最新那份才对得上。

隔离: 环境变量必须在 import 之前落定(watch_clip 在**模块层**读 P2Z_*), 并把
USERPROFILE 重定向进沙盒 —— `body_sidecar` 用 `expanduser("~")` 找 segflow 目录,
不重定向就会读到真实 `~/.cache/pdf2zh/segflow`(候选集不可控, 结论不可复现)。

运行: venv python test_watch_clip_sidecar.py, 退出码 0=全过
"""
import io
import json
import os
import sys
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="p2z_wc_")
os.environ["USERPROFILE"] = _SANDBOX                     # expanduser("~") -> 沙盒
SEGFLOW = os.path.join(_SANDBOX, ".cache", "pdf2zh", "segflow")
INBOX = os.path.join(_SANDBOX, "inbox")
TABLE = os.path.join(_SANDBOX, "table")
for _d in (SEGFLOW, INBOX, TABLE):
    os.makedirs(_d, exist_ok=True)

NAME = "payload_wc"
for _k, _v in (("P2Z_PROJ", _SANDBOX), ("P2Z_TABLE_DIR", TABLE),
               ("P2Z_INBOX", INBOX), ("P2Z_BODY_NAME", NAME),
               ("P2Z_BODY_PDF", os.path.join(_SANDBOX, "demo.pdf"))):
    os.environ[_k] = _v

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_TOOLS, os.path.join(_TOOLS, "table_pipe")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import watch_clip as WC  # noqa: E402

BODY_LINE = "accuracy 0.69 of the batch experiment"       # 37 字符, 过 >25 门槛
SEG_TEXT = "accuracy {v0} of the batch experiment"
LONG_LINE = "x" * 200                                     # 采样要截到 120


def write_payload(text):
    """把载荷写到 body_payload() 指的位置(模块层已按 P2Z_INBOX/P2Z_BODY_NAME 定好)。"""
    with io.open(WC.body_payload(), "w", encoding="utf-8") as f:
        f.write(text)
    return WC.body_payload()


def write_sidecar(name, rows):
    """rows = [(page, raw)]; 每行是一条 receive_layout 回调记录。"""
    p = os.path.join(SEGFLOW, name)
    with io.open(p, "w", encoding="utf-8") as f:
        for pg, raw, vs in rows:
            f.write(json.dumps({"page": pg, "vars": vs, "segs": [{"raw": raw}]},
                               ensure_ascii=False) + "\n")
    return p


def clear_segflow():
    for f in os.listdir(SEGFLOW):
        os.remove(os.path.join(SEGFLOW, f))


def write_manifest(pages):
    with io.open(WC.body_manifest(), "w", encoding="utf-8") as f:
        json.dump({"items": [{"parts": [{"page": p} for p in pages]}]}, f)


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

    # ---------------------------------------------------------------- ① 采样口径
    PREAMBLE = ("[文档] demo 第1-3页\n"
                "[任务] 把下列每个 #S 段落译成简体中文（学术书排版用），只输出译文，不要任何解释。\n"
                "[规则]\n"
                "1. 每段独立翻译；译文前先写一行原样的 #S编号；不许合并、拆分、增删段落。\n"
                "2. 数字、拉丁学名、人名、单位、[n] 引用标号、化学式：原样保留，不译不改不移动位置。\n")

    p = write_payload(PREAMBLE)
    check("① 只有前言(#S 之前的行都不算) -> 采样为空",
          WC._payload_samples(p) == [], repr(WC._payload_samples(p)))

    p = write_payload(PREAMBLE + "#S1\n" + BODY_LINE + "\n"
                      "the second body sentence of this paragraph\n")
    smp = WC._payload_samples(p)
    check("① 前言之后才采", smp == [BODY_LINE,
                                "the second body sentence of this paragraph"], repr(smp))
    check("① 规则正文没混进来",
          not any(("每段独立翻译" in s) or ("拉丁学名" in s) or ("#S编号" in s) for s in smp),
          repr(smp))

    p = write_payload("#S1\nshort line\n"                      # <=25 字符, 滤掉
                      "#S2\n"                                   # 编号行本身不算采样
                      "[文档] 抬头样长句子abcdefghijklmnopqrstuvwxyz\n"
                      "【待译】装配前缀样长句子abcdefghijklmnopqrstuvwxyz\n"
                      + LONG_LINE + "\n")
    smp = WC._payload_samples(p)
    check("① 短行/编号行/#与[与【 开头的行都不采", len(smp) == 1, repr(smp))
    check("① 超长行截到 120 字符", smp and smp[0] == "x" * 120, repr(smp and smp[0][:20]))

    check("① 文件不存在 -> 空, 不抛", WC._payload_samples(os.path.join(_SANDBOX, "nope.txt")) == [])

    # ---------------------------------------------------------------- ② 身份比对口径
    side = write_sidecar("id.jsonl", [(1, SEG_TEXT, {"0": "0.69"})])
    check("② 还原占位符后命中(不还原则对自己也是 0)",
          WC._sidecar_identity(side, [BODY_LINE]) == 1,
          repr(WC._sidecar_identity(side, [BODY_LINE])))
    check("② 采样的空白差异不影响命中",
          WC._sidecar_identity(side, ["accuracy   0.69   of  the batch experiment"]) == 1)
    check("② 拿占位符原文去比 -> 0(口径是比还原后的具体值)",
          WC._sidecar_identity(side, [SEG_TEXT]) == 0)
    check("② 别篇段落 -> 0",
          WC._sidecar_identity(side, ["a completely different sentence about other things"]) == 0)
    check("② 多行侧车累计命中",
          WC._sidecar_identity(write_sidecar("id2.jsonl", [
              (1, SEG_TEXT, {"0": "0.69"}),
              (2, "and the second recorded paragraph", {})]),
              [BODY_LINE, "and the second recorded paragraph"]) == 2)
    check("② 缺文件 -> 0", WC._sidecar_identity(os.path.join(SEGFLOW, "nope.jsonl"),
                                            [BODY_LINE]) == 0)
    check("② 空采样 / 空路径 -> 0",
          WC._sidecar_identity(side, []) == 0 and WC._sidecar_identity("", [BODY_LINE]) == 0)
    bad = _write_raw(os.path.join(SEGFLOW, "bad.jsonl"), "not json\n")
    check("② 非 JSON 行 -> 保守退 0(不抛)", WC._sidecar_identity(bad, [BODY_LINE]) == 0)

    # ---------------------------------------------------------------- ③ 候选挑选
    clear_segflow()
    write_payload("#S1\n" + BODY_LINE + "\n")                 # 采样 = [BODY_LINE]
    write_manifest([1, 2, 3])                                 # want = {1,2,3}

    # ③-1 身份优先于页覆盖度
    a = write_sidecar("pdf-aaaaaaaaaaaaaaaa.jsonl",
                      [(1, "unrelated recorded text here", {}),
                       (2, "unrelated recorded text here", {}),
                       (3, "unrelated recorded text here", {})])     # 覆盖 3, 身份 0
    b = write_sidecar("pdf-bbbbbbbbbbbbbbbb.jsonl", [(9, SEG_TEXT, {"0": "0.69"})])  # 覆盖 0, 身份 1
    check("③ 身份优先: 命中在别的页码上也赢覆盖度",
          WC.body_sidecar() == b, WC.body_sidecar())

    # ③-2 身份同为 1 时比覆盖度
    clear_segflow()
    a = write_sidecar("pdf-cccccccccccccccc.jsonl", [(1, SEG_TEXT, {"0": "0.69"})])
    b = write_sidecar("pdf-dddddddddddddddd.jsonl", [(1, SEG_TEXT, {"0": "0.69"}),
                                                    (2, SEG_TEXT, {"0": "0.69"}),
                                                    (3, SEG_TEXT, {"0": "0.69"})])
    check("③ 身份打平 -> 页覆盖度多者胜", WC.body_sidecar() == b, WC.body_sidecar())

    # ③-3 前两档全平 -> mtime 破平局(旧口径按 listdir 任意顺序, 会判给上一篇)
    clear_segflow()
    x = write_sidecar("pdf-eeeeeeeeeeeeeeee.jsonl", [(1, SEG_TEXT, {"0": "0.69"})])
    y = write_sidecar("pdf-ffffffffffffffff.jsonl", [(1, SEG_TEXT, {"0": "0.69"})])
    os.utime(x, (1000.0, 1000.0))
    os.utime(y, (2000.0, 2000.0))
    check("③ 全平 -> 取 mtime 更新者", WC.body_sidecar() == y, WC.body_sidecar())
    os.utime(x, (3000.0, 3000.0))                             # 角色对调, 结论必须跟着翻
    check("③ 破平局随 mtime 走(不受 listdir 顺序影响)",
          WC.body_sidecar() == x, WC.body_sidecar())

    # ③-4 没有正文采样时不下结论错误: 一律退到覆盖度(此处全 0) -> 仍取最新
    clear_segflow()
    x = write_sidecar("pdf-1111111111111111.jsonl", [(1, "some recorded text", {})])
    y = write_sidecar("pdf-2222222222222222.jsonl", [(1, "some recorded text", {})])
    os.utime(x, (1000.0, 1000.0))
    os.utime(y, (2000.0, 2000.0))
    write_payload(PREAMBLE)                                   # 没有 #S -> 采样空
    check("③ 无采样+全覆盖度 -> 仍取最新(不许按 listdir 顺序)",
          WC.body_sidecar() == y, WC.body_sidecar())

    # ③-5 manifest 缺失/坏掉 -> want 为空集, 不抛
    clear_segflow()
    z = write_sidecar("pdf-3333333333333333.jsonl", [(1, SEG_TEXT, {"0": "0.69"})])
    os.remove(WC.body_manifest())
    write_payload("#S1\n" + BODY_LINE + "\n")
    try:
        got = WC.body_sidecar()
        ok = got == z
    except Exception as e:                                    # noqa: BLE001
        ok, got = False, "raised %r" % (e,)
    check("③ manifest 缺失 -> 退化为纯身份判定, 不抛", ok, repr(got))

    print("\nwatch_clip 侧车身份判据回归: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


def _write_raw(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


if __name__ == "__main__":
    sys.exit(main())
