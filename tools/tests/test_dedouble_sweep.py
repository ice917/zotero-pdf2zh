# -*- coding: utf-8 -*-
"""[v28.67] dedouble_sweep 去叠清扫: import 无副作用 + 路径参数化 (project_map P3)

背景:
    旧版把"备份 + 连库 + UPDATE"全写在**模块顶层**, 且 sidecar / 缓存库 / 文档指纹
    三个路径写死在源码里。后果: 任何 `import dedouble_sweep`(扫描器、IDE 索引、
    测试收集)都会当场改**真实**缓存库并落一份 .bak; 换一篇论文还得改源码。

本套件锁的是:
  [A] import 无副作用 —— 独立子进程里 import, 不许新建文件、不许碰库; 并有 AST 守卫
      禁止顶层出现 shutil.copy2 / sqlite3.connect
  [B] --dry-run 命中但**不落库**、不落备份
  [C] 实跑: 先落备份(内容=改前), 再把该处冗余标点删掉
  [D] 三个路径全部走参数(换篇只换命令行, 不动源码)

全部在临时目录的**合成库**上做, 绝不触碰真实 cache.v1.db。
运行: venv python test_dedouble_sweep.py, 退出码 0=全过
"""
import ast
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_TOOLS = os.path.dirname(_HERE)
_MOD = os.path.join(_TOOLS, "dedouble_sweep.py")


def build_fixture(root, fingerprint="docsummary:abc123:def456"):
    """造一份"会被修一处"的最小现场: 侧车 + manifest + 合成缓存库。"""
    sidecar = os.path.join(root, "side.jsonl")
    with open(sidecar, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "page": 0,
            "vars": {"0": "(T2)"},              # 字形值含 ')'
            "segs": [{"raw": "alpha {v0} beta"}],
        }, ensure_ascii=False) + "\n")

    inbox = os.path.join(root, "inbox")
    os.makedirs(inbox, exist_ok=True)
    with open(os.path.join(inbox, "paper.manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"items": [{"parts": [{"page": 0, "seg": 0}]}]}, f)

    db = os.path.join(root, "cache.v1.db")
    con = sqlite3.connect(db)
    con.execute("create table _translationcache ("
                "original_text text, translation text, translate_engine_params text)")
    # 译文 token 后紧跟全角 '）' —— R1 的典型冗余(字形值已含 ')')
    con.execute("insert into _translationcache values (?,?,?)",
                ("alpha {v0} beta", "阿尔法{v0}）贝塔", fingerprint))
    con.commit()
    con.close()
    return sidecar, inbox, db, fingerprint


def read_translation(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("select translation from _translationcache").fetchone()[0]
    finally:
        con.close()


def backups(db):
    d = os.path.dirname(db)
    base = os.path.basename(db) + ".bak-"
    return sorted(n for n in os.listdir(d) if n.startswith(base))


def top_level_side_effects():
    """模块顶层(排除 __main__ 分支与函数体)不得出现 copy2 / connect —— import 就该只定义。

    只走**语句本身**, 不下潜函数体: 函数体里的 copy2/connect 是"被调用才发生",
    正是我们要的; 危险的是"import 当场执行"。
    """
    with open(_MOD, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    hits = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If)):
            continue                        # 定义/分支: 不执行, 跳过
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                name = getattr(fn, "attr", "") or getattr(fn, "id", "")
                if name in ("copy2", "copytree", "connect"):
                    hits.append((sub.lineno, name))
    return hits


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

    root = tempfile.mkdtemp(prefix="pdf2zh_dedouble_")
    try:
        sidecar, inbox, db, fp = build_fixture(root)

        print("[A] import 无副作用")
        empty = os.path.join(root, "clean")
        os.makedirs(empty, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import dedouble_sweep" % _TOOLS],
            cwd=empty, capture_output=True, text=True)
        check("A1 import 成功", proc.returncode == 0, proc.stderr[-300:])
        check("A2 import 不新建任何文件", os.listdir(empty) == [], os.listdir(empty))
        check("A3 合成库未被碰过(无备份)", backups(db) == [], backups(db))
        hits = top_level_side_effects()
        check("A4 顶层无 copy2/connect (AST 守卫)", not hits, hits)
        with open(_MOD, encoding="utf-8") as f:
            src = f.read()
        check("A5 有 __main__ 守卫", '__name__ == "__main__"' in src)

        print("[B] --dry-run: 报得出会修几处, 但一个字节都不落库")
        before = read_translation(db)
        proc = subprocess.run(
            [sys.executable, _MOD, "--sidecar", sidecar, "--db", db,
             "--fingerprint", fp, "--manifests", inbox, "--dry-run"],
            capture_output=True, text=True)
        check("B1 dry-run 退出码 0", proc.returncode == 0, proc.stderr[-300:])
        check("B2 报出可修 1 处", "可修 1 处" in proc.stdout, proc.stdout[-300:])
        check("B3 库内容未变", read_translation(db) == before, read_translation(db))
        check("B4 未落备份", backups(db) == [], backups(db))

        print("[C] 实跑: 先备份(内容=改前), 再删掉冗余的 '）'")
        proc = subprocess.run(
            [sys.executable, _MOD, "--sidecar", sidecar, "--db", db,
             "--fingerprint", fp, "--manifests", inbox],
            capture_output=True, text=True)
        check("C1 退出码 0", proc.returncode == 0, proc.stderr[-300:])
        after = read_translation(db)
        check("C2 冗余 '）' 已删", after == "阿尔法{v0}贝塔", after)
        baks = backups(db)
        check("C3 落了 1 份备份", len(baks) == 1, baks)
        if baks:
            bak_path = os.path.join(root, baks[0])
            check("C4 备份里是改前内容", read_translation(bak_path) == before,
                  read_translation(bak_path))

        print("[D] 路径全参数化: 换一篇/换库只换命令行, 不改源码")
        root2 = os.path.join(root, "other")
        os.makedirs(root2, exist_ok=True)
        s2, i2, db2, fp2 = build_fixture(root2, fingerprint="docsummary:zzz:999")
        proc = subprocess.run(
            [sys.executable, _MOD, "--sidecar", s2, "--db", db2,
             "--fingerprint", fp2, "--manifests", i2],
            capture_output=True, text=True)
        check("D1 另一篇同样能跑通", proc.returncode == 0, proc.stderr[-300:])
        check("D2 另一库也被修", read_translation(db2) == "阿尔法{v0}贝塔",
              read_translation(db2))

        root3 = os.path.join(root, "third")
        os.makedirs(root3, exist_ok=True)
        s3, i3, db3, _ = build_fixture(root3)
        keep = read_translation(db3)
        proc = subprocess.run(
            [sys.executable, _MOD, "--sidecar", s3, "--db", db3,
             "--fingerprint", "docsummary:not-this-one:x", "--manifests", i3,
             "--dry-run"],
            capture_output=True, text=True)
        check("D3 指纹不匹配 -> 可修 0 处, 不误伤别人的行",
              proc.returncode == 0 and "可修 0 处" in proc.stdout, proc.stdout[-200:])
        check("D4 指纹不匹配 -> 库未变、无备份",
              read_translation(db3) == keep and backups(db3) == [], backups(db3))

        print("[E] 缺文件/缺目录 -> 退出码 2, 不动库")
        proc = subprocess.run(
            [sys.executable, _MOD, "--sidecar", os.path.join(root, "nope.jsonl"),
             "--db", db2, "--fingerprint", fp2, "--manifests", i2],
            capture_output=True, text=True)
        check("E1 侧车不存在 -> rc=2", proc.returncode == 2, proc.returncode)
        proc = subprocess.run(
            [sys.executable, _MOD, "--sidecar", s2, "--db", db2,
             "--fingerprint", fp2, "--manifests", os.path.join(root, "nodir")],
            capture_output=True, text=True)
        check("E2 manifest 目录不存在 -> rc=2", proc.returncode == 2, proc.returncode)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    print("\n结果: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
