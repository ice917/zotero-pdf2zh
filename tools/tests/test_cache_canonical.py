# -*- coding: utf-8 -*-
"""v23 缓存规范化单元测试 (10 例)

运行: 用 zotero-pdf2zh-venv 的 python 执行本文件, 退出码 0=全过
    D:\\Users\\97638\\anaconda3\\envs\\zotero-pdf2zh-venv\\python.exe test_cache_canonical.py

覆盖: 编号平移命中 / 重映射正确性 / 译文token乱序往返 / 交叉引用 /
LLM自造token保留 / 旧形态回查 / 表变更后不误命中 / 后缀隔离 / 纯文本往返
"""
import sys, os

# 允许从任意 python 启动: 优先注入 venv site-packages
_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    r"D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages",
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

from pdf2zh.cache import TranslationCache, init_test_db, clean_test_db


def main():
    test_db = init_test_db()
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS {name}")
        else:
            failed += 1
            print(f"  FAIL {name} {detail}")

    try:
        # 用例1: 同一段, 全局编号平移 +4, 应命中并正确重映射
        c = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "model": "m1"})
        c.set("levels{v3} taken together{v5} are not sufficient{v7}",
              "层级{v3}合起来{v5}不足{v7}")
        got = c.get("levels{v7} taken together{v9} are not sufficient{v11}")
        check("编号平移命中", got is not None)
        check("重映射正确", got == "层级{v7}合起来{v9}不足{v11}", repr(got))

        # 用例2: 平移往返
        c2 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "model": "m1"})
        c2.set("filter {v10}EKF{v11} designed", "滤波器{v10}（EKF{v11}）")
        got_b = c2.get("filter {v20}EKF{v21} designed")
        check("乱序/平移往返", got_b == "滤波器{v20}（EKF{v21}）", repr(got_b))

        # 用例3: 译文 token 交叉引用(出现顺序与原文不同)
        c2.set("A {v1} B {v2} C", "甲{v2}与乙{v1}")
        got_c = c2.get("A {v8} B {v9} C")
        check("交叉引用重映射", got_c == "甲{v9}与乙{v8}", repr(got_c))

        # 用例4: LLM 自造 token 原样保留
        c2.set("text {v2} more", "文本{v2}自造{v99}")
        got_d = c2.get("text {v5} more")
        check("自造token保留", got_d is not None and "{v99}" in got_d, repr(got_d))

        # 用例5: 旧形态回查 (snapshot_legacy_key + remove_param)
        c3 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "model": "m2", "old_fp": "ABC"})
        c3.set("legacy {v0} text", "旧条目{v0}")
        c3.snapshot_legacy_key()
        c3.remove_param("old_fp")
        got_e = c3.get("legacy {v4} text")
        check("旧形态回查命中", got_e == "旧条目{v4}", repr(got_e))

        # 用例6: 术语表变更后旧形态天然失效 (fp 不同 → 不误命中)
        c4 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "old_fp": "OLD"})
        c4.set("glossary {v0} case", "旧术语{v0}")
        c4.snapshot_legacy_key()
        c4.remove_param("old_fp")
        c5 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "old_fp": "NEW"})
        c5.snapshot_legacy_key()
        c5.remove_param("old_fp")
        got_f = c5.get("glossary {v1} case")
        check("表变更后旧条目不误命中", got_f is None, repr(got_f))

        # 用例7: 后缀隔离
        c6 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh"})
        c6.set("same text {v0}", "后缀A译文{v0}", key_suffix="#g23:aaa")
        got_g = c6.get("same text {v3}", key_suffix="#g23:aaa")
        got_h = c6.get("same text {v3}", key_suffix="#g23:bbb")
        check("后缀A命中", got_g == "后缀A译文{v3}", repr(got_g))
        check("后缀B不串", got_h is None, repr(got_h))

        # 用例8: 纯文本往返
        c6.set("plain paragraph", "纯文本段落")
        check("纯文本命中", c6.get("plain paragraph") == "纯文本段落")
    finally:
        clean_test_db(test_db)

    print(f"\n单元测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
