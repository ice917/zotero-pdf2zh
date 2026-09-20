# -*- coding: utf-8 -*-
"""v24-A 文档摘要前置单元测试: 生成/落盘复用/prompt注入/fp入键/门闩顺序 (不触网)

隔离关键: 在导入 pdf2zh 前重定向 USERPROFILE 到临时目录, 使 expanduser("~")
指向测试沙盒 —— 绝不触碰真实 ~/.cache/pdf2zh/docsummary 里的文档摘要
(曾发生回归清空真实摘要 → 同文档键漂移整篇重译的事故)。
"""
import sys, os, tempfile

_SANDBOX = tempfile.mkdtemp(prefix="pdf2zh_test_")
os.environ["USERPROFILE"] = _SANDBOX

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

import threading
from pdfminer.layout import LTPage, LTFigure
from pdf2zh.converter import TranslateConverter
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

    # ⑥ [v24-A] converter 门闩"先验后关": 图形对象/文字不足不得关死门闩
    # (Wang 篇事故: 首页 3 个纯图形 LTFigure 先触发 receive_layout, 旧代码
    #  抢先关门 → 整页文字到达时摘要永不生成, 且无落盘/无打印/无异常)
    class FakeTr:
        def __init__(self):
            self.calls = []
            self._doc_summary_done = False

        def summarize_document(self, text):
            self.calls.append(text)
            return "领域：测试。论文类型：单元测试。"

    host = object.__new__(TranslateConverter)
    ftr = FakeTr()
    host.translator = ftr
    host._maybe_summarize_document(LTFigure("f1", (0, 0, 10, 10), (1, 0, 0, 1, 0, 0)), [])
    check("⑥ 图形对象不关门闩", not ftr._doc_summary_done and len(ftr.calls) == 0)

    host._maybe_summarize_document(LTPage(1, (0, 0, 612, 792)), ["短文本"])
    check("⑥ 文字不足不关门闩", not ftr._doc_summary_done and len(ftr.calls) == 0)

    _page = LTPage(1, (0, 0, 612, 792))
    _sstk = ["A sufficiently long paragraph for summary generation. "] * 20
    host._maybe_summarize_document(_page, _sstk)
    check("⑥ 整页文本触发摘要", len(ftr.calls) == 1 and ftr._doc_summary_done)

    host._maybe_summarize_document(_page, _sstk)
    check("⑥ 门闩生效仅一次", len(ftr.calls) == 1)

    # ⑦ [v28.21] 摘要文本键与 {vN} 编号无关 (配置改动不再换键)
    k_a = OpenAITranslator._summary_text_key("levels {v3} taken {v5} together")
    k_b = OpenAITranslator._summary_text_key("levels {v0} taken {v9} together")
    check("⑦ 摘要键对 {vN} 编号不敏感", k_a == k_b, f"{k_a} vs {k_b}")
    check("⑦ 摘要键剔净占位符",
          OpenAITranslator._summary_text_key("a{v0}b")
          == OpenAITranslator._summary_text_key("ab"))

    # ⑧ [v28.21] converter 侧端到端: {vN} 先还原成真实字形, 故两套不同编号的
    # 占位符形态产出**同一段摘要文本** → 同一落盘键 → 第二次不触网。
    # (这正是 v28.21 要治的病: 改 config 让占位符集合变 → 摘要键漂 → 整篇重译)
    class _Ch:
        def __init__(self, t):
            self._t = t

        def get_text(self):
            return self._t

    tr8 = make_min()
    host8 = object.__new__(TranslateConverter)
    host8.translator = tr8
    _page8 = LTPage(1, (0, 0, 612, 792))
    _body8 = ("Retargeting strategies transfer manipulation skills from human hands to "
              "robotic systems and remain a central open problem in this line of work. " * 8)
    _varA = [[_Ch("-")], [_Ch(",")]] + [[] for _ in range(8)]   # {v0}->'-', {v1}->','
    _varB = [[] for _ in range(6)] + [[_Ch("-")], [], [_Ch(",")]]  # {v6}->'-', {v8}->','

    host8._maybe_summarize_document(_page8, [_body8 + "head {v0} mid {v1} tail"], _varA)
    _sent_a = tr8.client.calls[-1][-1]["content"].rsplit("\n\n", 1)[-1]
    check("⑧ {vN} 还原成真实字形", "head - mid , tail" in _sent_a, repr(_sent_a[-30:]))
    check("⑧ 首次生成走 LLM", len(tr8.client.calls) == 1, len(tr8.client.calls))

    tr8._doc_summary_done = False          # 模拟第二次渲染(门闩重置)
    host8._maybe_summarize_document(_page8, [_body8 + "head {v6} mid {v8} tail"], _varB)
    check("⑧ 编号不同的同一段文本 → 同一摘要键(不换键)",
          len(tr8.client.calls) == 1, len(tr8.client.calls))

    # ⑨ [v28.21] var 越界/缺省时占位符原样保留, 由摘要键侧的剔除兜底 ——
    # 残留字面 {vN} 的编号随 config 变, 不能让摘要键跟着漂。
    tr9 = make_min()
    host9 = object.__new__(TranslateConverter)
    host9.translator = tr9
    _body9 = ("Attention mechanisms and contrastive objectives dominate the current "
              "literature on representation learning for robot manipulation tasks. " * 8)
    host9._maybe_summarize_document(_page8, [_body9 + "alpha {v0} beta"], None)
    check("⑨ var 缺省仍生成摘要", len(tr9.client.calls) == 1, len(tr9.client.calls))

    tr9._doc_summary_done = False
    host9._maybe_summarize_document(_page8, [_body9 + "alpha {v7} beta"], None)
    check("⑨ var 缺省: 编号不同仍同一摘要键(不换键)",
          len(tr9.client.calls) == 1, len(tr9.client.calls))

    print(f"\n文档摘要单元测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
