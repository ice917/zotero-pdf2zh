# -*- coding: utf-8 -*-
"""v23.4 前瞻上下文单元测试: TLS 读取 / prompt 注记 / 缓存键随前瞻变化

运行: venv python test_lookahead.py, 退出码 0=全过
不触网: 用 object.__new__ 绕过 OpenAITranslator.__init__, 手工装配最小属性。
"""
import sys, os

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    r"D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages",
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

import threading
from pdf2zh.translator import OpenAITranslator


def make_min():
    """绕过 __init__ 装配最小可用实例"""
    tr = object.__new__(OpenAITranslator)
    tr._tls = threading.local()
    tr.lang_in = "en"
    tr.lang_out = "zh"
    tr._polish_enabled = True
    tr._polish_glossary_path = "x"          # 非空即可走术语分支
    tr._polish_glossary = {"in-silico": "计算机模拟"}
    return tr


def main():
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS {name}")
        else:
            failed += 1
            print(f"  FAIL {name} {detail}")

    tr = make_min()
    text = "a selfer has{v27} on average{v28} three successful"

    # ① 无前瞻: suffix 为术语态, prompt 无注记
    s0 = tr._cache_key_suffix(text)
    msgs = tr.prompt(text)
    body0 = msgs[0]["content"]
    check("① 无前瞻 suffix 稳定", s0 == tr._cache_key_suffix(text))
    check("① 无前瞻 prompt 无注记", "[Context note]" not in body0)

    # ② 有前瞻: suffix 变化 + prompt 注记出现且含前瞻文本
    tr._tls.look_ahead = "gametes, two as an ovule and pollen parent"
    s1 = tr._cache_key_suffix(text)
    msgs = tr.prompt(text)
    body1 = msgs[0]["content"]
    check("② 前瞻改变缓存键", s1 != s0, f"{s0} vs {s1}")
    check("② prompt 含注记", "[Context note]" in body1)
    check("② 注记含前瞻文本", "gametes, two as an ovule" in body1)
    check("② 注记含只译本段指令", "do NOT translate" in body1)
    check("② suffix 含 la 段", "|la:" in s1)

    # ③ 前瞻清空: 恢复无前瞻键 (防线程复用泄漏)
    tr._tls.look_ahead = ""
    s2 = tr._cache_key_suffix(text)
    check("③ 清空前瞻恢复原键", s2 == s0, f"{s0} vs {s2}")

    # ④ 前瞻不同 → 键不同
    tr._tls.look_ahead = "gametes, three as an ovule parent"
    s3 = tr._cache_key_suffix(text)
    check("④ 不同前瞻键不同", s3 != s1)

    # ⑤ 空段落文本 + 前瞻: 键仍由前瞻驱动
    tr._tls.look_ahead = "continuation words here"
    s4 = tr._cache_key_suffix("")
    tr._tls.look_ahead = "different continuation"
    s5 = tr._cache_key_suffix("")
    check("⑤ 空文本前瞻仍生效", s4 != s5 and "|la:" in s4)

    print(f"\n前瞻上下文单元测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
