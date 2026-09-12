# -*- coding: utf-8 -*-
"""zotero-pdf2zh 回归测试聚合运行器

用法 (推荐用 venv python):
    D:\\Users\\97638\\anaconda3\\envs\\zotero-pdf2zh-venv\\python.exe tools\\tests\\run_all.py

聚合内容:
    1. test_cache_canonical.py   缓存规范化单元测试 (10 例, 临时库)
    2. test_cache_integration.py v22→v23 迁移集成测试 (合成数据, 临时库)
    3. test_mirrors_sync.py      补丁镜像 MD5 门禁 (venv ↔ patches/)

设计约定:
    - 全部 stdlib + venv 内 pdf2zh, 不需要 pytest
    - 每个测试独立进程运行, 互不污染; 汇总退出码 (0=全过)
    - venv 路径可用环境变量 PDF2ZH_VENV_SITE 覆盖
    - 灵敏度测试(需真实 LLM 消耗)不属于本回归套件, 其脚本与结论见 改动记录.md 2026-09-11 条目
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SUITES = [
    "test_mirrors_sync.py",       # 先跑环境一致性 (不依赖 venv, 失败早暴露)
    "test_cache_canonical.py",    # 单元
    "test_cache_integration.py",  # 集成
    "test_lookahead.py",          # v23.4 前瞻上下文
    "test_docsummary.py",         # v24-A 文档摘要前置
    "test_strategist.py",         # v24b 军师层校验
]


def main():
    venv_python = os.environ.get(
        "PDF2ZH_PYTHON",
        r"D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\python.exe",
    )
    py = venv_python if os.path.exists(venv_python) else sys.executable
    print(f"回归运行器 | python={py}\n{'=' * 62}")
    failures = []
    for suite in SUITES:
        path = os.path.join(HERE, suite)
        print(f"\n--- {suite} ---")
        proc = subprocess.run([py, path], cwd=HERE)
        if proc.returncode != 0:
            failures.append(suite)
    print(f"\n{'=' * 62}")
    if failures:
        print(f"回归结果: FAIL — 未通过: {', '.join(failures)}")
        return 1
    print("回归结果: 全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
