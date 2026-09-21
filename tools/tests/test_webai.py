# -*- coding: utf-8 -*-
"""换翻译方(网页 AI 不绑豆包)单元/集成测试 (v28.62, 2026-09-21)

用户观察: "我们的网页 AI 剪切板翻译, 可以交给不同种类的网页 AI" —— 对, 而且**机制上本来
就是厂商无关的**: 真契约只有两条(编号守恒 + 占位符原位), 载荷自带抬头所以换家不用改提示词。
本套件锁死"换家"需要的三件配套, 以及它们**不该**动的东西:

  ① 回包"包装"清洗(watch_clip.parse_units) —— 别家爱套 ``` 围栏、爱加首尾客套、爱把编号
     列成 Markdown 清单。清洗只动**包装**: 围栏行丢, 尾部客套丢(且必须与译文隔空行), 编号
     行容忍行首包装。**误吃译文是最坏的失败**, 故专门有一组"不许动"的断言。
  ② 同源守卫(reviewer.family_of / conflicts / panel.rv_conflict) —— 被翻译方不得自校
     (relay_spec §4.5)。翻译方选 DeepSeek 网页版而审核者也是 DeepSeek 时必须拒审, 且要
     拦在**联网之前**(不花那笔钱)。
  ③ 溯源(panel.ledger + ⑤ 文案 + 审核报告抬头) —— 换了家要能归因/比对, 不能只靠印象。

不打任何真接口、不花一分钱: 清洗与守卫全是纯函数; 台账落在临时工作目录。
运行: venv python test_webai.py, 退出码 0=全过
"""
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
TABLE_PIPE = os.path.join(TOOLS, "table_pipe")

# 环境必须在 import 之前落定: panel/watch_clip 在**模块层**读这些量(D 目录、inbox 路径),
# 之后改 env 是没用的。KEY 一并清掉, 免得本机真密钥被这里意外用上(--selftest 会走网络)。
TMP = tempfile.mkdtemp(prefix="p2z_webai_")
os.environ["P2Z_PROJ"] = TMP
os.environ["P2Z_TABLE_DIR"] = os.path.join(TMP, "tbl")
os.environ["P2Z_INBOX"] = os.path.join(TMP, "inbox")
os.environ["P2Z_REVIEW_DIR"] = os.path.join(TMP, "review")
os.environ.pop("SILICON_API_KEY", None)
os.makedirs(os.environ["P2Z_TABLE_DIR"], exist_ok=True)
os.makedirs(os.environ["P2Z_INBOX"], exist_ok=True)

if TABLE_PIPE not in sys.path:
    sys.path.insert(0, TABLE_PIPE)

import watch_clip as WC     # noqa: E402
import reviewer as RV       # noqa: E402
import panel as PN          # noqa: E402

IDS = ["S1", "S2"]
PRE = "S"


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

    P = lambda text, pre=PRE, ids=IDS: WC.parse_units(text, ids, pre)   # noqa: E731

    # ---- ① 基准: 裸文本(豆包形态)一个字都不能动 ----
    bare = "#S1\n根系的分布受土壤水分影响。\n#S2\n第二段的译文。"
    got = P(bare)
    check("① 裸文本原样读出", got == {"S1": "根系的分布受土壤水分影响。", "S2": "第二段的译文。"},
          repr(got))

    # ---- ① 围栏: 整篇被 ``` 包住 -> 只丢围栏, 内容一字不动 ----
    fenced = "```markdown\n#S1\n根系的分布受土壤水分影响。\n#S2\n第二段的译文。\n```"
    check("① 围栏行不混进译文", P(fenced) == got, repr(P(fenced)))
    check("① 围栏(无语言标签)同样处理",
          P("```\n#S1\n甲\n#S2\n乙\n```") == {"S1": "甲", "S2": "乙"})

    # ---- ① 尾部客套: 与译文**隔空行**才洗(宁可漏洗不可误吃) ----
    tail = "#S1\n甲\n#S2\n乙\n\n以上是全文译文，如需调整请告诉我。"
    check("① 尾部客套(隔空行)被丢掉", P(tail) == {"S1": "甲", "S2": "乙"}, repr(P(tail)))
    tight = "#S1\n甲\n#S2\n乙\n以上是全文译文，如需调整请告诉我。"
    check("① 客套紧贴译文(无空行)不动手", P(tight)["S2"].endswith("请告诉我。"), repr(P(tight)))
    real = "#S1\n甲\n#S2\n乙\n\n以上是结果分析，说明该模型更优。"
    check("① 正文里的「以上是…」不许当客套吃掉", P(real)["S2"] == "乙\n\n以上是结果分析，说明该模型更优。",
          repr(P(real)))
    long_line = "希望这些译文对你有帮助。" + "另外原文里两处缩写我保留未译，" * 2   # 40 字以上
    long_tail = "#S1\n甲\n#S2\n乙\n\n" + long_line
    check("① 超长客套行(%d 字 > 上限)不洗 —— 长到不像客套就当正文留着" % len(long_line),
          long_line in P(long_tail)["S2"], repr(P(long_tail)))

    # ---- ① 编号行包装: 有的 AI 把编号回成清单/粗体 ----
    for tag, txt in (("清单符 -", "- #S1\n甲\n- #S2\n乙"),
                     ("有序 1.", "1. #S1\n甲\n2. #S2\n乙"),
                     ("有序 (1)", "(1) #S1\n甲\n(2) #S2\n乙"),
                     ("粗体", "**#S1**\n甲\n**#S2**\n乙"),
                     ("引用符 >", "> #S1\n甲\n> #S2\n乙")):
        check("① 编号行容忍包装: %s" % tag, P(txt) == {"S1": "甲", "S2": "乙"}, repr(P(txt)))

    # ---- ① 内容行里的列表符号**不许**剥(原文本身可能就是列表) ----
    bullet = "#S1\n- 根系分布受水分影响。\n- 叶片面积随之减小。\n#S2\n乙"
    check("① 译文内的项目符号原样保留", P(bullet)["S1"].startswith("- 根系分布"),
          repr(P(bullet)))

    # ---- ① 首部客套: 编号行之前落不到任何段上, 天然无害(只断言这个事实) ----
    head = "好的，我按你的要求逐段翻译如下：\n\n#S1\n甲\n#S2\n乙"
    check("① 首部客套不影响识别", P(head) == {"S1": "甲", "S2": "乙"}, repr(P(head)))

    # ---- ① 表格 TSV 形态: 围栏 + 粗体编号 ----
    tsv = "```\n**k001**\t译文一\nk002 译文二\n```"
    check("① TSV 形态兼容围栏与粗体编号",
          WC.parse_units(tsv, ["k001", "k002"], "k") == {"k001": "译文一", "k002": "译文二"})

    # ---- ② 认族: 按名字认, 认不出算"不同源" ----
    check("② 豆包 -> doubao", RV.family_of("豆包") == "doubao")
    check("② DeepSeek 网页版 -> deepseek", RV.family_of("DeepSeek 网页版") == "deepseek")
    check("② 硅基流动 API(DeepSeek) -> deepseek",
          RV.family_of("硅基流动 API(DeepSeek)") == "deepseek")
    check("② ChatGPT -> chatgpt", RV.family_of("ChatGPT") == "chatgpt")
    check("② 其他网页 AI 认不出 -> 空", RV.family_of("其他网页 AI") == "")
    check("② 认不出不算冲突", RV.conflicts("", {"model": "deepseek-ai/DeepSeek-V3.2"}) == "")

    # ---- ② 同源判定: 拿假 cfg 注入, 不依赖本机 config.json ----
    cfg_ds = {"model": "deepseek-ai/DeepSeek-V3.2"}
    check("② 翻译方 deepseek 撞审核者 deepseek",
          RV.conflicts("deepseek", cfg_ds) == "deepseek")
    check("② 翻译方 doubao 与 deepseek 审核者不冲突",
          RV.conflicts("doubao", cfg_ds) == "")

    # ---- ② 面板侧: 同源清单由服务器算(前端只做成员判断) ----
    check("② 面板: 豆包不同源", PN.rv_conflict("豆包") == "")
    check("② 面板: DeepSeek 网页版同源", PN.rv_conflict("DeepSeek 网页版") == "deepseek")
    check("② 面板: 硅基流动 API 也同源", PN.rv_conflict("硅基流动 API(DeepSeek)") == "deepseek")
    check("② 面板: 默认翻译方是豆包(不受同源限制)", PN._load_vendor() == "豆包")

    # ---- ② 守卫拦在联网之前: 同源时 review() 直接返回错误, 不发请求 ----
    check("② 同源时空文本先报空(顺序: 空 -> 守卫)",
          "空" in PN.review("")["error"], repr(PN.review("")))
    PN._save_vendor("DeepSeek 网页版")
    r = PN.review("#S1\n甲\n#S2\n乙")
    check("② 同源时拒审(不联网)", "error" in r and "同源" in r["error"], repr(r))
    check("② 拒审理由写明双方, 且点明红线",
          "DeepSeek" in r["error"] and "被翻译方不得自校" in r["error"], repr(r))
    PN._save_vendor("豆包")                       # 复位
    r = PN.review("#S1\n甲\n#S2\n乙")
    check("② 换回豆包后不再是同源之争(转为缺编号/缺原文类错误)",
          "同源" not in r.get("error", ""), repr(r))

    # ---- ③ 台账: 追加式, 抬头只写一次, 制表符不入列 ----
    led = PN.VENDOR_LEDGER
    if os.path.exists(led):
        os.remove(led)
    PN.ledger("检查", "表格正文", 115, 2, "过", "")
    PN.ledger("出稿", "正文 Lee2026", 137, 0, "已出稿", "D:\\out\\a.docx")
    PN.ledger("检查", "?", 0, 0, "编号不齐", "读到 0 条\t带制表符的备注")
    with io.open(led, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]
    check("③ 台账抬头只有一行", lines[0].startswith("时间\t阶段\t任务\t翻译方\t单元\t待看"),
          lines[0])
    check("③ 台账落了三行", len(lines) == 4, str(len(lines)))
    check("③ 台账记了翻译方", lines[1].split("\t")[3] == "豆包", lines[1])
    check("③ 台账每行 8 列(制表符被替换)", all(len(ln.split("\t")) == 8 for ln in lines[1:]),
          repr(lines))
    check("③ 编号不齐也入账(换家后的证据)", "编号不齐" in lines[3], lines[3])

    # ---- ③ 溯源进审核报告抬头 ----
    rep = RV.report_md({"model": "deepseek-ai/DeepSeek-V3.2", "n_units": 3, "n_chunks": 1,
                        "n_failed": 0, "secs": 1, "items": [], "vendor": "ChatGPT"},
                       doc="[文档] 某论文", stem="表格正文")
    check("③ 审核报告写明翻译方与审核者",
          "翻译方: ChatGPT" in rep and "DeepSeek-V3.2" in rep, rep[:200])
    rep2 = RV.report_md({"model": "m", "n_units": 1, "n_chunks": 1, "n_failed": 0,
                         "secs": 0, "items": []}, stem="x")
    check("③ 无翻译方时写(未标注), 不留空档", "(未标注)" in rep2, rep2[:200])

    # ---- ④ 责任归属: 只报角色, 具体是谁由面板补 ----
    check("④ 译者侧 FAIL -> 翻译方",
          PN.blame_of(["FAIL p6#4 高价值字形未回锚"], False) == "翻译方")
    check("④ 工具标记 -> 工具",
          PN.blame_of(["FAIL 未接线: 引擎缺 config"], False) == "工具")
    check("④ 没失败 -> 空", PN.blame_of([], True) == "")
    check("④ 混合日志里工具词不夺权(实测误判过)",
          PN.blame_of(["PASS 读 manifest 完成", "FAIL p3#2 段数不符"], False) == "翻译方")

    # ---- ④ 前端接线: 下拉/路由/文案都在, 且不再写死"豆包的问题" ----
    page = PN.PAGE
    for frag in ('id="vendorSel"', "/api/vendor", "复制给翻译方", "applyVendorGuard",
                 "rv_conflicts", "翻译方已切到"):
        check("④ 面板含 %s" % frag, frag in page)
    check("④ 前端不再写死「豆包的问题」", "'豆包的问题'" not in page)
    check("④ 翻译方选项里有别家 AI", "ChatGPT" in PN.VENDORS and "Kimi" in PN.VENDORS)
    check("④ 前端不自己认族(规则只在服务器)", "vendorFamily" not in page)

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s: %d passed, %d failed" % (os.path.basename(__file__), passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
