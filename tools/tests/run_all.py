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
   21. test_engine.py            引擎接缝: 画像/接线/段表契约/同引擎不重绑/子工具单跑门禁
                                 + v28.40 next 载荷出口(段表 -> #S 载荷)
                                 + v28.41 next 回写通道(载荷 -> 缓存: import 守恒/inject 定位)
                                 + 定位键对账(不重推段表比坐标) (89 例)
   22. test_reviewer.py         v28.59 ⑥ 语义审核(硅基流动第三方只审不改): 密钥回落/
                                 切块保序/回包宽容解析/多块并发聚合/报告落盘/面板接线
   23. test_panel_idle.py       v28.60 面板兜底退出可暂停: 倒计时由服务器给/暂停只停
                                 闲置腿(告别腿照走)/继续时时钟归零/HTTP 契约
   24. test_webai.py            v28.62 翻译方可换(剪贴板这条路不绑豆包): 回包包装清洗
                                 (围栏/尾部客套/编号行清单化; 误吃译文的反例也在)/同源
                                 拒审(拦在联网之前)/溯源台账与 ⑤ 归因/前端接线
   25. test_heal_render.py      v28.63 渲染残渣治伤: 干跑即体检(有伤退出码 1/治后幂等 0)/
                                 擦哪儿盖哪儿(逐像素 0.00)/redact 吞链接必须补回/dual 页映射
  26. test_dual_links.py       v28.64 dual 双侧链接: 落点映射 2d+1 不跨侧/原版侧 NAMED->GOTO
                                 且 y 不镜像/译文页原有 URI 活下来/同源与页数两道门禁
  27. test_relink_pages.py     v28.65 热区重定位: 碎片命中必须先并成"一次出现"的整框
                                 (否则热区只剩一个括号宽, 点数字点不到)/并框判据/端到端
  28. test_env_isolation.py    v28.67 每任务环境隔离(P1): 提字档只注入本次子进程/不传即无
                                 残留/并发两篇各看各的/父进程全局全程干净 + 源码静态守卫
  29. test_dedouble_sweep.py   v28.67 去叠清扫 import 无副作用(P3): 合成库上验 import 不落
                                 文件/--dry-run 不落库/实跑先备份改写前/路径全参数化
  30. test_polish_paths.py     v28.67 润色路径(P4): example 不带本机盘符(相对仓库根)/
                                 回填锚定 + 缺文件响亮告警/目录类缺失不算错/接线守卫
  31. test_result_naming.py    v28.68 交件命名契约: 两份契约逐字节一致(tools↔server)/
                                 新件缺省 webai 而旧件 .doubao* 原样保留(改名不断链)/
                                 两族同权(glob/matches/belongs) + 四处调用点接线守卫
  32. test_pre_render_ledger.py v28.69 渲染前保底底片 + 本篇待办: import 无副作用/
                                 底片内容与只读契约/前端接线
                                 v28.70 底片失败即中止出稿(工序一步没走 + 台账「未出稿」)
                                 v28.71 判据换成「这一篇该做的都做了吗」: 产物判据 done/stale/
                                 todo 不落内存 + 篇名归属(foreign/unlabeled) + 缺了只提醒不拦
                                 (台账「已出稿(缺: X)」), 逃生门那条线整条删除

设计约定:
    - 全部 stdlib + venv 内 pdf2zh, 不需要 pytest
    - 每个测试独立进程运行, 互不污染; 汇总退出码 (0=全过)
    - venv 路径可用环境变量 PDF2ZH_VENV_SITE 覆盖
    - 回归**不改工作区**: 教训台账(lessons.py)指到临时文件, 见下面 P11 注释
    - 灵敏度测试(需真实 LLM 消耗)不属于本回归套件, 其脚本与结论见 改动记录.md 2026-09-11 条目
"""
import os
import subprocess
import sys
import tempfile

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
    "test_reviewer.py",           # v28.59 ⑥ 语义审核(硅基流动第三方只审不改)
    "test_panel_idle.py",         # v28.60 面板兜底退出可暂停(倒计时/暂停/时钟归零)
    "test_webai.py",              # v28.62 翻译方可换: 回包包装清洗/同源拒审/溯源台账
    "test_heal_render.py",        # v28.63 渲染残渣治伤: 干跑体检/擦盖同框/链接补回/dual 映射
    "test_dual_links.py",         # v28.64 dual 双侧链接: 落点映射/两侧门禁/URI 活下来
    "test_relink_pages.py",       # v28.65 碎片热区: 并框判据 + 端到端覆盖整锚
    "test_env_isolation.py",      # v28.67 每任务环境隔离(P1): 提字档只进本次子进程
    "test_dedouble_sweep.py",     # v28.67 去叠清扫(P3): import 无副作用 + 路径参数化
    "test_polish_paths.py",       # v28.67 润色路径(P4): example 不带本机盘符 + 缺路径响亮报错
    "test_result_naming.py",    # v28.68 交件命名契约: 两侧一致/新旧互认/两族同权/接线守卫
    "test_pre_render_ledger.py",  # v28.69 渲染前保底底片 / v28.70 底片失败即停 / v28.71 本篇待办只提醒
]

# 套件 → 它的"被测对象"(相对项目根)。**只在对象不随包分发时才需要登记** ——
# 其余套件的被测对象都是 tools/ 下已入库的脚本, 天然在场。
# strategist.py 属"含本机路径、本地保留暂不开源"(见 .gitignore 开源红线二期),
# 公开 clone 里没有它 → test_strategist.py 必须 SKIP 而不是 FAIL。
# (v28.67 起 `dedouble_sweep.py` 已随 P3 重写脱敏入库, 故不再登记 —— 公开侧也会真跑。)
OPTIONAL_SUBJECTS = {
    "test_strategist.py": "tools/strategist.py",
}


def main():
    venv_python = os.environ.get("PDF2ZH_PYTHON", sys.executable)
    py = venv_python if os.path.exists(venv_python) else sys.executable
    root = os.path.dirname(os.path.dirname(HERE))
    print(f"回归运行器 | python={py}\n{'=' * 62}")
    # [v28.67] P11: 回归不该动工作区。test_seg_rework 会走 seg_import 写返工单 ->
    # 顺带 _LES.record() 记教训, 而 lessons 的默认 PATH 是**入库文件** tools/lessons.tsv
    # (去重时还会把日期刷成今天) —— 跑一次回归就脏一次 git status。把台账指到临时文件,
    # 被测代码路径一行不改。
    env = os.environ.copy()
    env["PDF2ZH_LESSONS_TSV"] = os.path.join(
        tempfile.mkdtemp(prefix="pdf2zh_regr_lessons_"), "lessons.tsv")
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
        proc = subprocess.run([py, path], cwd=HERE, env=env)
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
