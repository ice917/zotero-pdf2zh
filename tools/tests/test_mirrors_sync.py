# -*- coding: utf-8 -*-
"""镜像同步门禁: venv 补丁文件 ↔ patches/ 镜像 MD5 一致性核对

背景: v21 曾发生 server.py 改动漏同步镜像的事故(改动记录 2026-09-06 13:55),
后改为人工 Copy-Item + MD5 核对。本测试把核对自动化, 任何一侧改动未同步
即 FAIL, 在回归阶段拦截而不是在事故后补救。

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
    r"D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages",
)

# 镜像名 → (运行侧文件, 相对 REPO 或 VENV_SITE)
MIRRORS = {
    "pdf2zh_cache.py": os.path.join(VENV_SITE, "pdf2zh", "cache.py"),
    "pdf2zh_converter.py": os.path.join(VENV_SITE, "pdf2zh", "converter.py"),
    "pdf2zh_translator.py": os.path.join(VENV_SITE, "pdf2zh", "translator.py"),
    "pdfminer_encodingdb.py": os.path.join(VENV_SITE, "pdfminer", "encodingdb.py"),
    "pdfminer_pdffont.py": os.path.join(VENV_SITE, "pdfminer", "pdffont.py"),
    "server_server.py": os.path.join(REPO, "server", "server.py"),
    "server_utils_config.py": os.path.join(REPO, "server", "utils", "config.py"),
    "server_utils_environment_lifecycle.py": os.path.join(
        REPO, "server", "utils", "environment_lifecycle.py"),
}


def md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest().upper()[:8]


def main():
    passed = failed = 0
    print(f"REPO={REPO}")
    print(f"VENV={VENV_SITE}\n")
    for mirror, runtime in MIRRORS.items():
        m_path = os.path.join(REPO, "patches", mirror)
        if not os.path.exists(m_path):
            print(f"  FAIL {mirror}: 镜像文件缺失")
            failed += 1
            continue
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
    print(f"\n镜像同步: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
