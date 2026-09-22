# -*- coding: utf-8 -*-
"""[v28.67] 每任务环境隔离单元测试 (P1: 并发提交两篇互相污染)

背景(见 PIPELINE_STORY 第十二章 P1):
    `PAUSE_TRANSLATE` 与 `P2Z_DOC_PDF` 曾经是**在父进程 os.environ 上设**的, 隐含
    假设"同时只翻一篇"。而服务端是 Flask threaded=True、去重只看文件名, 并发提交
    **两篇不同 PDF** 时后一篇会读到前一篇留下的开关:
      · 提字趟(PAUSE_TRANSLATE=1)把另一篇也逼成"只提字"档 -> 那篇产出全英文 PDF,
        而日志全绿(这是唯一能"静默毁掉另一篇产物"的缺陷);
      · P2Z_DOC_PDF 把另一篇的侧车归档到**别人的文档散列**下。
    两道"看着能兜住"的闸都不兜: `_SUBMIT_LOCK` 只护**同名**登记(文件名不同即放行),
    `_sole_task` 只服务失败日志的根因归因(`_failure_brief`), 不阻止任务启动。

本套件锁的是**修法**: 两个键改为"本次调用专属", 经 execute_with_progress 的
extra_env 注入到**那一个子进程**, 父进程 os.environ 全程只读。

用例:
  [A] extra_env 真的到达子进程 (真起子进程读**自己的**环境, 不是读桩)
  [B] 不传 extra_env 时子进程看不到残留 (旧写法此处会看到上一条留下的 1)
  [C] 并发两篇: A 带提字档 / B 不带 -> 各自子进程看到的开关不同 (P1 的回归锁)
  [D] 父进程全局全程干净 (A/B/C 跑完都不许出现这两个键)
  [E] 静态守卫: 源码里不得再出现把这两键写进 os.environ 的语句, 且 translate_pdf
      必须保留 extra_env 形参并把它交给 execute_with_progress

诚实说明"先红后绿": 旧代码的 `execute_with_progress` **没有** extra_env 形参,
本套件在旧代码上第一条就 TypeError —— 它锁的是一个当时**不存在**的契约, 而不是
逐条断言变红。旧写法的行为级红证据是并发场景本身(见 改动记录 v28.67)。

不触网: 子进程只是一句 python -c, 不跑翻译。
运行: venv python test_env_isolation.py, 退出码 0=全过
"""
import ast
import json
import os
import sys
import tempfile
import threading
import time
import types
import shutil

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_SERVER = os.path.join(_REPO, "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import utils.execute as execute  # noqa: E402
from utils.task_manager import task_manager  # noqa: E402

ARGS = types.SimpleNamespace(enable_venv=False)
WATCH = ("PAUSE_TRANSLATE", "P2Z_DOC_PDF")
TID = "T-ENV-ISOLATION"


def child_writes_env(out_path, delay=0.0):
    """一句 python -c: 先睡 delay 秒(制造并发重叠), 再把**自己**的环境写成 JSON。

    不用桩读 Popen 的 env 参数 —— 那只能证明"我们传了这个 dict"; 让真正的子进程
    把它自己看到的环境写出来, 才能证明这个 dict 真的成了它的环境。
    """
    code = (
        "import json,os,time;"
        "time.sleep(%r);"
        "open(%r,'w',encoding='utf-8').write("
        "json.dumps({k: os.environ.get(k) for k in %r}))"
        % (delay, out_path, list(WATCH))
    )
    return [sys.executable, "-c", code]


def read_env(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def env_writes_in_source(path):
    """AST 找"把这两个键写进 os.environ"的语句: 下标赋值 / pop / setdefault。

    比正则稳: 注释与 docstring 里提到键名(本文件到处都是)不会被误判成写。
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        else:
            targets = []
        for t in targets:
            if (isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Attribute)
                    and t.value.attr == "environ"
                    and isinstance(t.slice, ast.Constant)
                    and t.slice.value in WATCH):
                hits.append((node.lineno, "enviro[%s] =" % t.slice.value))
            # os.environ[...] 的键也可能是 setdefault 之类, 不在此列
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("pop", "setdefault")
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "environ"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in WATCH):
            hits.append((node.lineno, "os.environ.%s(%s)" % (node.func.attr, node.args[0].value)))
    return hits


def translate_pdf_contract(path):
    """translate_pdf 必须: 有 extra_env 形参 + 把它交给 execute_with_progress。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "translate_pdf":
            params = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            if "extra_env" not in params:
                return False, "translate_pdf 缺 extra_env 形参"
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == "execute_with_progress":
                    if any(k.arg == "extra_env" for k in sub.keywords):
                        return True, ""
                    return False, "execute_with_progress 调用未传 extra_env"
            return False, "translate_pdf 内找不到 execute_with_progress 调用"
    return False, "找不到 translate_pdf"


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

    real_task = task_manager.active_tasks.get(TID) is None
    if real_task:
        task_manager.active_tasks[TID] = {
            "fileName": "env_isolation.pdf", "active": True, "progress": 0,
            "status": "running", "message": "",
        }
    for k in WATCH:
        os.environ.pop(k, None)

    tmpdir = tempfile.mkdtemp(prefix="pdf2zh_env_")
    try:
        print("[A] extra_env 真的到达子进程 (真起子进程读自己的环境)")
        out_a = os.path.join(tmpdir, "a.json")
        execute.execute_with_progress(
            child_writes_env(out_a), TID, ARGS, None,
            extra_env={"PAUSE_TRANSLATE": "1", "P2Z_DOC_PDF": r"D:\sandbox\A.pdf"})
        got_a = read_env(out_a)
        check("A1 子进程看到 PAUSE_TRANSLATE=1", got_a["PAUSE_TRANSLATE"] == "1", got_a)
        check("A2 子进程看到 P2Z_DOC_PDF",
              got_a["P2Z_DOC_PDF"] == r"D:\sandbox\A.pdf", got_a)

        print("[B] 不传 extra_env 的子进程看不到残留 (旧写法此处会看到上一条的 1)")
        out_b = os.path.join(tmpdir, "b.json")
        execute.execute_with_progress(child_writes_env(out_b), TID, ARGS, None)
        got_b = read_env(out_b)
        check("B1 PAUSE_TRANSLATE 未泄漏给下一个子进程",
              got_b["PAUSE_TRANSLATE"] is None, got_b)
        check("B2 P2Z_DOC_PDF 未泄漏给下一个子进程",
              got_b["P2Z_DOC_PDF"] is None, got_b)

        print("[C] 并发两篇: A 带提字档 / B 不带 -> 各自子进程看到的开关不同")
        out_ca = os.path.join(tmpdir, "ca.json")
        out_cb = os.path.join(tmpdir, "cb.json")
        errs = []

        def run(out, extra, delay):
            try:
                execute.execute_with_progress(
                    child_writes_env(out, delay), TID, ARGS, None, extra_env=extra)
            except Exception as e:  # noqa: BLE001 - 并发里的异常要带回主线程断言
                errs.append(repr(e))

        th_a = threading.Thread(target=run, args=(
            out_ca, {"PAUSE_TRANSLATE": "1", "P2Z_DOC_PDF": r"D:\sandbox\A.pdf"}, 1.0))
        th_b = threading.Thread(target=run, args=(
            out_cb, {"P2Z_DOC_PDF": r"D:\sandbox\B.pdf"}, 0.6))
        th_a.start()
        time.sleep(0.15)          # 让 A 的子进程先跑起来, 两篇真的重叠
        th_b.start()
        th_a.join(120)
        th_b.join(120)
        check("C0 两篇都跑完且未抛错", not errs, errs)
        got_ca, got_cb = read_env(out_ca), read_env(out_cb)
        check("C1 A(提字趟) 子进程看到开关", got_ca["PAUSE_TRANSLATE"] == "1", got_ca)
        check("C2 B(另一篇) 子进程**没被** A 的开关污染",
              got_cb["PAUSE_TRANSLATE"] is None, got_cb)
        check("C3 侧车文档路径各归各",
              (got_ca["P2Z_DOC_PDF"], got_cb["P2Z_DOC_PDF"])
              == (r"D:\sandbox\A.pdf", r"D:\sandbox\B.pdf"), (got_ca, got_cb))

        print("[D] 父进程全局全程干净 (旧写法跑完会在 os.environ 里留下这两个键)")
        for k in WATCH:
            check("D 全局无 %s" % k, k not in os.environ, os.environ.get(k))

        print("[E] 静态守卫: 源码不得再把这两键写进 os.environ")
        server_py = os.path.join(_SERVER, "server.py")
        exec_py = os.path.join(_SERVER, "utils", "execute.py")
        for label, p in (("server.py", server_py), ("execute.py", exec_py)):
            hits = env_writes_in_source(p)
            check("E 无环境写入: %s" % label, not hits, hits)
        ok, why = translate_pdf_contract(server_py)
        check("E3 translate_pdf 保留 extra_env 并交给 execute_with_progress", ok, why)
    finally:
        if real_task:
            task_manager.active_tasks.pop(TID, None)
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n结果: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
