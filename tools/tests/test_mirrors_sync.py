# -*- coding: utf-8 -*-
"""镜像同步门禁: venv 补丁文件 ↔ patches/ 镜像 MD5 一致性核对

背景: v21 曾发生 server.py 改动漏同步镜像的事故(改动记录 2026-09-06 13:55),
后改为人工 Copy-Item + MD5 核对。本测试把核对自动化, 任何一侧改动未同步
即 FAIL, 在回归阶段拦截而不是在事故后补救。

[v28.15] 清单 9 → 11: 补 server/utils/execute.py (PTY 增量解码 + 看门狗 + 独占闸)
与 server/utils/task_manager.py (任务状态机) —— 两者都带 18/2 处 [自研补丁] 标记,
是"只存在于本机运行侧"的关键改动, 此前不在门禁内 (改崩了没有任何提示)。

[v28.45] 清单 11 → 13: pdf2zh_next 的 working_dir 参数面补丁落在 **next venv**
(zotero-pdf2zh-next-venv), 与 1.x venv 是两个 site 根。next 根解析顺序:
PDF2ZH_NEXT_VENV_SITE → PDF2ZH_NEXT_PYTHON 同级 → 同 envs 目录下的
zotero-pdf2zh-next-venv; 都找不到则该两项 SKIP(公开克隆不该满屏红)。

[v29] 清单 13 → 14: 补 pdf2zh/high_level.py —— 扫描件清底需要在版面产物里把
**图/表区**从其余保留区(页眉页脚/公式)中分出来(记 -1 vs 0), 否则清底会把图抹白。
此前该文件是上游原版、不在门禁内, 现在有改动就必须进清单。

运行: 任意 python 均可 (不依赖 pdf2zh), 退出码 0=全部一致
"""
import sys, os, hashlib

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.basename(REPO) != "zotero-pdf2zh":
    # 容错: 允许从 tools/tests 上层运行
    for up in range(4):
        cand = os.path.abspath(os.path.join(os.path.dirname(__file__), *([".."] * (up + 2))))
        if os.path.basename(cand) == "zotero-pdf2zh":
            REPO = cand
            break

VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)

# [v28.45] 第二个 site 根: pdf2zh_next / babeldoc 补丁装在 next venv
NEXT_VENV_SITE = os.environ.get("PDF2ZH_NEXT_VENV_SITE")
if not NEXT_VENV_SITE and os.environ.get("PDF2ZH_NEXT_PYTHON"):
    NEXT_VENV_SITE = os.path.join(
        os.path.dirname(os.environ["PDF2ZH_NEXT_PYTHON"]), "Lib", "site-packages")
if not NEXT_VENV_SITE:
    # 约定: 与本 venv 同目录(<envs>\...): site-packages → Lib → <venv> → <envs>
    envs = os.path.dirname(os.path.dirname(os.path.dirname(VENV_SITE)))
    cand = os.path.join(envs, "zotero-pdf2zh-next-venv", "Lib", "site-packages")
    if os.path.isdir(cand):
        NEXT_VENV_SITE = cand

SITES = {"main": VENV_SITE, "next": NEXT_VENV_SITE, "repo": REPO}

# 镜像名 → (site 根, 运行侧相对路径)
MIRRORS = {
    "pdf2zh_cache.py": ("main", os.path.join("pdf2zh", "cache.py")),
    "pdf2zh_converter.py": ("main", os.path.join("pdf2zh", "converter.py")),
    "pdf2zh_high_level.py": ("main", os.path.join("pdf2zh", "high_level.py")),
    "pdf2zh_translator.py": ("main", os.path.join("pdf2zh", "translator.py")),
    "pdfminer_encodingdb.py": ("main", os.path.join("pdfminer", "encodingdb.py")),
    "pdfminer_pdffont.py": ("main", os.path.join("pdfminer", "pdffont.py")),
    "pdfminer_pdfinterp.py": ("main", os.path.join("pdfminer", "pdfinterp.py")),
    "pdf2zh_next_config_model.py": ("next", os.path.join("pdf2zh_next", "config", "model.py")),
    "pdf2zh_next_high_level.py": ("next", os.path.join("pdf2zh_next", "high_level.py")),
    "server_server.py": ("repo", os.path.join("server", "server.py")),
    "server_utils_config.py": ("repo", os.path.join("server", "utils", "config.py")),
    "server_utils_environment_lifecycle.py": (
        "repo", os.path.join("server", "utils", "environment_lifecycle.py")),
    "server_utils_execute.py": ("repo", os.path.join("server", "utils", "execute.py")),
    "server_utils_task_manager.py": ("repo", os.path.join("server", "utils", "task_manager.py")),
}


def md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest().upper()[:8]


def main():
    passed = failed = skipped = 0
    print(f"REPO={REPO}")
    print(f"VENV={VENV_SITE}")
    print(f"NEXT={NEXT_VENV_SITE or '<未找到>'}\n")
    for mirror, (site, rel) in MIRRORS.items():
        m_path = os.path.join(REPO, "patches", mirror)
        if not os.path.exists(m_path):
            print(f"  FAIL {mirror}: 镜像文件缺失")
            failed += 1
            continue
        root = SITES.get(site)
        if not root:
            print(f"  SKIP {mirror}: 未找到 {site} site 根 "
                  f"(设 PDF2ZH_NEXT_VENV_SITE 或 PDF2ZH_NEXT_PYTHON)")
            skipped += 1
            continue
        runtime = os.path.join(root, rel)
        if not os.path.exists(runtime):
            print(f"  FAIL {mirror}: 运行侧文件缺失 {runtime}")
            failed += 1
            continue
        hm, hr = md5(m_path), md5(runtime)
        if hm == hr:
            print(f"  PASS {mirror} ({hm})")
            passed += 1
        else:
            print(f"  FAIL {mirror}: 镜像={hm} 运行侧={hr} —— 需重新同步!")
            failed += 1
    print(f"\n镜像同步: {passed} PASS / {failed} FAIL / {skipped} SKIP")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
