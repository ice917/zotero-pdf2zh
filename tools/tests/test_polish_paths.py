# -*- coding: utf-8 -*-
"""config.json.example 的润色路径: 不带本机盘符 + 缺路径响亮报错 (v28.67, P4)

被锁死的缺陷 —— **托管事实源里焊着本机绝对路径, 换机后无声失效**:
  `config.json.example` 是 POLISH* 键的托管事实源(`server/utils/config.py` 在插件
  推送配置时无条件从它回填), 而它一度写着 `D:/zotero-pdf2zh/server/glossary/terms.csv`
  —— 那正是**本机仓库路径**。别人 clone 下来 / 仓库换盘后, POLISH_GLOSSARY 指向一个
  不存在的文件; 而译端对缺失文件只 `logger.exception` 之后静默降级
  (`patches/pdf2zh_translator.py: _load_polish_glossary` 返回 {}), 钩子**无声失效**。
  钩子的启动自检也只打 basename, 看不出文件在不在。

修法两条(本套件各锁一条):
  ① example 里写**相对仓库根**的路径, 回填时锚定成绝对路径 -> 克隆不带本机盘符;
  ② 回填当场查存在性, 缺文件打 ⚠️ 并点名"会无声失效" -> 不再静默。

运行: venv python test_polish_paths.py, 退出码 0=全过
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
SERVER = os.path.join(REPO, "server")
EXAMPLE = os.path.join(SERVER, "config", "config.json.example")
CONFIG_PY = os.path.join(SERVER, "utils", "config.py")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

from utils import config as C  # noqa: E402

# 一个"绝对路径"的判据: 盘符开头(Windows) 或 / 开头(POSIX)
ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|/)")


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

    # ============ 一、polish_backfill_value: 锚定 + 告警 ============
    got, warn = C.polish_backfill_value("POLISH_GLOSSARY", "server/glossary/terms.csv", REPO)
    check("① 相对路径按**仓库根**锚定成绝对路径",
          got == os.path.normpath(os.path.join(REPO, "server/glossary/terms.csv")),
          got)
    check("① 路径存在 -> 不告警", warn == "", warn)

    got, warn = C.polish_backfill_value("POLISH_GLOSSARY", "server/glossary/nope.csv", REPO)
    check("② 缺文件 -> 响亮告警(⚠️ + 键名 + 会无声失效)",
          warn.startswith("⚠️") and "POLISH_GLOSSARY" in warn and "无声失效" in warn, warn)
    check("② 告警里给出解析后的绝对路径(便于排查)", got in warn, warn)

    got, warn = C.polish_backfill_value("POLISH_GLOSSARY", "D:/somewhere/else/terms.csv", REPO)
    check("③ 绝对值原样保留(不拿仓库根去拼)",
          got == "D:/somewhere/else/terms.csv", got)
    check("③ 绝对值但不存在 -> 也告警", warn != "" and "D:/somewhere/else/terms.csv" in warn, warn)

    got, warn = C.polish_backfill_value("POLISH_REPORT_DIR", "server/no/such/dir", REPO)
    check("④ 目录类缺失**不算错**(落盘时才 makedirs) -> 不告警", warn == "", warn)
    check("④ 目录类仍锚定成绝对路径",
          got == os.path.normpath(os.path.join(REPO, "server/no/such/dir")), got)

    for k in ("POLISH", "POLISH_REFLECT", "POLISH_ANCHOR"):
        got, warn = C.polish_backfill_value(k, "1", REPO)
        check("⑤ 非路径键(%s) 原样透传, 不拼路径不告警" % k,
              got == "1" and warn == "", (got, warn))

    # ============ 二、契约: example 不带本机盘符 + 相对路径真实存在 ============
    with open(EXAMPLE, encoding="utf-8") as f:
        ex = json.load(f)
    envs = {}
    for t in ex.get("translators", []):
        for k, v in (t.get("envs") or {}).items():
            if k.startswith("POLISH"):
                envs[k] = v
    path_keys = [k for k in envs if k in C._POLISH_FILE_KEYS + C._POLISH_DIR_KEYS]
    check("⑥ example 里有润色路径键(不然本套件测了个空)",
          set(path_keys) >= set(C._POLISH_FILE_KEYS), sorted(envs))
    bad = [k for k in path_keys if ABS.match(str(envs[k]))]
    check("⑥ example 的润色路径**一律相对仓库根**, 不带本机盘符", not bad,
          {k: envs[k] for k in bad})
    missing = [k for k in path_keys if k in C._POLISH_FILE_KEYS
               and not os.path.exists(os.path.join(REPO, envs[k]))]
    check("⑥ example 的相对路径在仓库里真实存在(克隆下来直接可用)", not missing,
          {k: envs[k] for k in missing})

    # ============ 三、接线守卫: helper 必须真的被回填循环调用 ============
    src = open(CONFIG_PY, encoding="utf-8").read()
    check("⑦ 回填循环调用 polish_backfill_value(改了 helper 没接上就白改)",
          "polish_backfill_value(_k, _v, _repo_root)" in src)
    check("⑦ 告警真的打出来(不是算完就扔)", "if _warn:" in src and "print(_warn)" in src)
    check("⑦ 仓库根由 config.py 位置推出(不焊死本机路径)",
          "_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(" in src
          and not re.search(r"""["']D:[\\/]""", src)
          and not re.search(r"""["']C:[\\/]""", src))

    # ============ 四、端到端: 拿真 example 的值过一遍回填 ============
    resolved = {}
    warnings = []
    for k in path_keys:
        v, w = C.polish_backfill_value(k, envs[k], REPO)
        resolved[k] = v
        if w:
            warnings.append(w)
    check("⑧ 真 example 全量回填: 零告警(本机路径都在)", not warnings, warnings)
    check("⑧ 回填结果都是绝对路径",
          all(ABS.match(v) for v in resolved.values()), resolved)
    check("⑧ 回填结果与本机原绝对路径等价(换写法不换值)",
          resolved.get("POLISH_GLOSSARY") == os.path.join(
              REPO, "server", "glossary", "terms.csv"), resolved.get("POLISH_GLOSSARY"))

    print("\n润色路径单元测试: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
