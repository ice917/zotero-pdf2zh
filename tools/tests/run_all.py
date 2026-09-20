# -*- coding: utf-8 -*-
"""zotero-pdf2zh 回归测试聚合运行器

用法 (必须用装了 pdf2zh 的那个 venv python):
    & <venv>/python.exe tools/tests/run_all.py

解释器默认取本进程的 sys.executable —— 也就是说**用哪个 python 跑本文件,
就用哪个 python 跑各套件**。要指向别的环境用环境变量 PDF2ZH_PYTHON。

分发版可能不带个别工具 (strategist.py 等含本机路径的工具本地保留、不入库),
对应套件会自动 **SKIP** 并单列, 不算失败 —— 否则别人 clone 下来一片红,
回归就从"门禁"退化成"噪音"。本机是全量环境, 一个 SKIP 都不该出现。

聚合内容:
    1. test_cache_canonical.py   缓存规范化单元测试 (10 例, 临时库)
    2. test_cache_integration.py v22→v23 迁移集成测试 (合成数据, 临时库)
    3. test_mirrors_sync.py      补丁镜像 MD5 门禁 (venv ↔ patches/)
    4. test_lookahead.py         v23.4 前瞻上下文单元测试 (10 例)
    5. test_docsummary.py        v24-A 文档摘要前置单元测试 (9 例)
    6. test_strategist.py        v24b 军师层校验单元测试 (49 例, 含 v26-L1/L2)
    7. test_progress_log.py      v26.20 无控制台进度监视器 (27 例)
    8. test_failure_brief.py     v26.20 失败根因提取(闸门4) (32 例)
    9. test_post_check.py        v26.21 文献区禁汉化门禁(第四断言) + v28.17 渲染残渣(第五断言) (69 例)
   10. test_seg_reanchor.py      v26.22 回锚匹配器(语序重排/标点全角化/长core优先) (19 例)
   11. test_adopt.py             v28 采纳管线「必经」入口: 顺序门禁/段号守恒/指纹固定 + v28.22 --expect 累加 (46 例)
   12. test_seg_export.py        v28.1 导出抬头/术语表参数化 + v28.10 真实页码选页 + v28.22 规则 2 块内空格/标点 (52 例)
   13. test_pre_check.py         v28.2 翻前体检文献页判据(两体例+两道闸门) (30 例)
   14. test_doubao_bridge.py     v28.3 豆包 MCP 桥契约(路径可移植+交件命名对齐+读报告) (40 例)
   15. test_pre_render_check.py  v28.5 渲染前预检(判据与 post_check 同源) (24 例)
   16. test_segflow_archive.py   v28.9 侧车按文档归档(写入端与认领端同一口径) (12 例)
   17. test_seg_rework.py        v28.13 import 失败自动落「给豆包的返工单」 (32 例)
   18. test_backfill_pages.py    v28.15 零可译段页判定(真实页码+按页聚合) (9 例)
   19. test_seg_inject.py        v28.21+ 文档指纹探测平票取最新世代 + 注入后自检 (9 例)
   20. test_seg_merge.py         v28.30 按段号合稿: 未动段字节不变 + 回读校验哨兵 (18 例)
   21. test_engine.py            引擎接缝: 画像/接线/段表契约/同引擎不重绑/子工具单跑门禁 (42 例)

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
    "test_progress_log.py",       # v26.20 无控制台进度监视器
    "test_failure_brief.py",      # v26.20 失败根因提取(闸门4)
    "test_post_check.py",         # v26.21 文献区禁汉化门禁(第四断言) + v28.17 渲染残渣(第五断言)
    "test_seg_reanchor.py",       # v26.22 回锚匹配器(语序重排/标点全角化)
    "test_adopt.py",              # v28 采纳管线「必经」入口
    "test_seg_export.py",         # v28.1 导出抬头/术语表参数化
    "test_pre_check.py",          # v28.2 翻前体检文献页判据(两体例+两道闸门)
    "test_doubao_bridge.py",      # v28.3 豆包 MCP 桥契约(路径可移植+交件命名对齐)
    "test_pre_render_check.py",   # v28.5 渲染前预检(判据与 post_check 同源)
    "test_segflow_archive.py",    # v28.9 侧车按文档归档(写入端与认领端同一口径)
    "test_seg_rework.py",         # v28.13 import 失败自动落「给豆包的返工单」
    "test_backfill_pages.py",     # v28.15 零可译段页判定(真实页码+按页聚合)
    "test_seg_inject.py",         # v28.21+ 文档指纹探测平票取最新世代 + 注入后自检
    "test_seg_merge.py",          # v28.30 按段号合稿(未动段字节不变 + 回读校验哨兵)
    "test_engine.py",             # 引擎接缝(画像/接线/段表契约)
]

# 套件 → 它的"被测对象"(相对项目根)。**只在对象不随包分发时才需要登记** ——
# 其余套件的被测对象都是 tools/ 下已入库的脚本, 天然在场。
# strategist.py 属"含本机路径、本地保留暂不开源"(见 .gitignore 开源红线二期),
# 公开 clone 里没有它 → test_strategist.py 必须 SKIP 而不是 FAIL。
OPTIONAL_SUBJECTS = {
    "test_strategist.py": "tools/strategist.py",
}


def main():
    venv_python = os.environ.get("PDF2ZH_PYTHON", sys.executable)
    py = venv_python if os.path.exists(venv_python) else sys.executable
    root = os.path.dirname(os.path.dirname(HERE))
    print(f"回归运行器 | python={py}\n{'=' * 62}")
    failures, skipped = [], []
    for suite in SUITES:
        path = os.path.join(HERE, suite)
        print(f"\n--- {suite} ---")
        # 缺套件文件或缺被测对象 → SKIP。分发版不带 strategist.py, 不 SKIP 就一片红。
        need = [path] + ([os.path.join(root, OPTIONAL_SUBJECTS[suite])]
                         if suite in OPTIONAL_SUBJECTS else [])
        absent = [p for p in need if not os.path.exists(p)]
        if absent:
            print("SKIP 本机未部署: %s"
                  % ", ".join(os.path.relpath(p, root).replace("\\", "/") for p in absent))
            skipped.append(suite)
            continue
        proc = subprocess.run([py, path], cwd=HERE)
        if proc.returncode != 0:
            failures.append(suite)
    print(f"\n{'=' * 62}")
    if skipped:
        print(f"跳过 {len(skipped)} 套件(被测对象未部署): {', '.join(skipped)}")
    if failures:
        print(f"回归结果: FAIL — 未通过: {', '.join(failures)}")
        return 1
    print("回归结果: 全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
