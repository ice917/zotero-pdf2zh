# -*- coding: utf-8 -*-
"""v24-A 文档摘要前置单元测试: 生成/落盘复用/prompt注入/fp入键 (不触网)

隔离关键: 在导入 pdf2zh 前重定向 USERPROFILE 到临时目录, 使 expanduser("~")
指向测试沙盒 —— 绝不触碰真实 ~/.cache/pdf2zh/docsummary 里的文档摘要
(曾发生回归清空真实摘要 → 同文档键漂移整篇重译的事故)。
"""
import sys, os, tempfile

_SANDBOX = tempfile.mkdtemp(prefix="pdf2zh_test_")
os.environ["USERPROFILE"] = _SANDBOX

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    r"D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages",
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

import threading
from pdf2zh.translator import OpenAITranslator


class FakeMsg:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMsg(content)


class FakeResp:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class _Completions:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kw):
        self.outer.calls.append(kw.get("messages"))
        return FakeResp(self.outer.reply)


class _Chat:
    def __init__(self, outer):
        self.completions = _Completions(outer)


class FakeClient:
    """捕获消息并返回固定摘要; 计数防重复调用"""
    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.chat = _Chat(self)


class FakeCache:
    """快照桩: 记录 prev 形态快照是否被调用"""
    def __init__(self):
        self.prev_params_json = None
        self.snapshot_called = False

    def snapshot_prev_key(self):
        self.snapshot_called = True


def make_min(summary_reply="这是一篇关于仙人掌科植物繁殖生物学的综述文章，涵盖交配系统、自交不亲和与传粉生态。"):
    tr = object.__new__(OpenAITranslator)
    tr._tls = threading.local()
    tr.lang_in = "en"
    tr.lang_out = "zh"
    tr.model = "deepseek-ai/DeepSeek-V3.2"
    tr.options = {"temperature": 0}
    tr.think_filter_regex = __import__("re").compile(r"^<think>.+?\n*(</think>|\n)*(</think>)\n*")
    tr.params = {}
    tr.cache = FakeCache()
    tr.client = FakeClient(summary_reply)
    tr.add_cache_impact_parameters = lambda k, v: tr.params.__setitem__(k, v)
    tr._polish_enabled = True
    tr._polish_glossary_path = "x"
    tr._polish_glossary = {}
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
    text = "Reproductive Biology of Cactaceae. " * 40   # >600 触发摘要
    # (隔离沙盒内冷启动, 无需清理真实文件)

    # ① 首次调用: 生成摘要 + 落盘 + fp 入键
    s = tr.summarize_document(text)
    check("① 摘要生成", len(s) > 5, repr(s[:50]))
    check("① LLM 恰被调用 1 次", len(tr.client.calls) == 1, len(tr.client.calls))
    check("① fp 入键", "doc_summary_fp" in tr.params, tr.params.get("doc_summary_fp"))
    check("① _doc_summary 就位", tr._doc_context() == s)

    # ② 同文本再调: 读文件不触网
    calls_before = len(tr.client.calls)
    s2 = tr.summarize_document(text)
    check("② 落盘复用不触网", len(tr.client.calls) == calls_before and s2 == s)

    # ③ prompt 注入文档画像
    tr._tls.look_ahead = ""
    msgs = tr.prompt("some segment text")
    body = msgs[0]["content"]
    check("③ prompt 含文档画像", "[Document context]" in body and "仙人掌科" in body)
    check("③ 画像在术语表之前", body.find("[Document context]") < body.find("Glossary") if "Glossary" in body else True)

    # ④ 短文本不触发 (<400)
    tr2 = make_min()
    tr2.summarize_document("too short")
    check("④ 短文本不触发", len(tr2.client.calls) == 0 and tr2._doc_context() == "")

    # ⑤ 空摘要失败安全 (fake 返回空; 用独立文本避免撞①的落盘文件)
    tr3 = make_min()
    tr3.client = FakeClient("")
    out = tr3.summarize_document("Unique empty reply test. " * 40)
    check("⑤ 空摘要失败安全", out == "" and tr3._doc_context() == "")

    print(f"\n文档摘要单元测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
