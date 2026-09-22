# -*- coding: utf-8 -*-
"""④ 语义审核单元/集成测试 (v28.59, 2026-09-21)

要解决的**用户侧**问题: 门禁只判机器判得了的东西(编号/字形/占位符/⋮), 语义错译
(半句丢失、否定颠倒、数字单位错位、术语漂移)一概够不到; 面板的「待看清单」也只给
读数(长度比/一致性)。用户只能自己通读整篇去发现 —— 本件把审核者补进门禁之外那一层。

三条边界(本测试逐条锁死):
  1. **审核者是第三方**: 翻译方是豆包, 审核走硅基流动 —— 被翻译方不得自校
     (relay_spec 第 4 条红线);
  2. **只报不改**: 审核者绝不产出改写稿(生成式改写不可信, 润色钩子退役的教训);
  3. **不参与放行**: 存疑清单只给人裁决, 放行权仍在本机门禁。

测试全部离线(不花 API 钱): _chat / audit 用替身注入, 只验契约与解析。

运行: venv python test_reviewer.py, 退出码 0=全过
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
TABLE_PIPE = os.path.join(TOOLS, "table_pipe")
for p in (TOOLS, TABLE_PIPE):
    if p not in sys.path:
        sys.path.insert(0, p)

# 环境必须在 import 之前落定: watch_clip / reviewer / panel 都在**模块层**读这些变量。
# 顺手把 SILICON_API_KEY 摘掉 —— 测试绝不能意外打到真接口上。
_TMP = tempfile.mkdtemp(prefix="p2z_reviewer_")
os.environ["P2Z_PROJ"] = _TMP
os.environ["P2Z_TABLE_DIR"] = os.path.join(_TMP, "work")
os.environ["P2Z_INBOX"] = os.path.join(_TMP, "inbox")
os.environ["P2Z_BODY_NAME"] = "payload_demo"
os.environ["P2Z_BODY_PDF"] = os.path.join(_TMP, "demo.pdf")
os.environ["P2Z_REVIEW_DIR"] = os.path.join(_TMP, "review")
os.environ.pop("SILICON_API_KEY", None)
os.environ.pop("SILICON_MODEL", None)
os.environ.pop("SILICON_BASE_URL", None)

import reviewer as RV       # noqa: E402
import watch_clip as WC     # noqa: E402
import panel as PN          # noqa: E402

DUMMY_CFG = {"key": "sk-test", "model": "test-model", "base": "https://example.invalid/v1"}

# 面板侧集成用的合成载荷/清单: 与真实正文任务同一结构(items 只有坐标, 原文在 payload)
PAYLOAD = ("[文档] Demo 2026 第1页\n"
           "[任务] 把下列每个 #S 段落译成简体中文。\n"
           "#S1\n"
           "The model reaches 0.69 accuracy.\n"
           "#S2\n"
           "We use {v0}D convolution for the encoder.\n")
MANIFEST = {"name": "payload_demo", "pdf": os.path.join(_TMP, "demo.pdf"),
            "items": [{"key": "S1", "parts": [{"page": 0, "seg": 0}]},
                      {"key": "S2", "parts": [{"page": 0, "seg": 1}]}]}
REPLY = "#S1\n模型的准确率达到 0.63。\n#S2\n编码器使用三维卷积。\n"


def write_env_files():
    os.makedirs(os.environ["P2Z_INBOX"], exist_ok=True)
    with open(os.path.join(os.environ["P2Z_INBOX"], "payload_demo.txt"),
              "w", encoding="utf-8") as f:
        f.write(PAYLOAD)
    with open(os.path.join(os.environ["P2Z_INBOX"], "payload_demo.manifest.json"),
              "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, ensure_ascii=False)


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

    # ---- ① 密钥解析: 环境变量优先, 否则读 server config 的 translators[silicon] ----
    os.environ["SILICON_API_KEY"] = "sk-env"
    os.environ["SILICON_MODEL"] = "env-model"
    c = RV.config()
    check("① 环境变量优先", c["key"] == "sk-env" and c["model"] == "env-model", c)
    check("① 未给 base 时用硅基流动默认端点",
          c["base"] == "https://api.siliconflow.cn/v1", c)
    del os.environ["SILICON_API_KEY"], os.environ["SILICON_MODEL"]
    with tempfile.TemporaryDirectory(prefix="p2z_rv_cfg_") as td:
        cfgp = os.path.join(td, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"translators": [{"name": "silicon",
                                        "envs": {"SILICON_API_KEY": "sk-cfg",
                                                 "SILICON_MODEL": "cfg-model"}}]}, f)
        old = RV.SRV_CFG
        RV.SRV_CFG = cfgp
        c = RV.config()
        check("① 回落 server config 的 translators[silicon]", c["key"] == "sk-cfg", c)
        check("① 模型取自 config", c["model"] == "cfg-model", c)
        RV.SRV_CFG = os.path.join(td, "nope.json")
        check("① 两处都没有 -> key 为空(调用方据此报错)", RV.config()["key"] == "", "")
        RV.SRV_CFG = old
    check("① 缺密钥时 audit 明确报错不抛异常",
          RV.audit([("S1", "a", "b")], cfg={"key": ""})["ok"] is False)

    # ---- ② 术语表: 注释行不可见(与 seg_export.load_terms 同口径) ----
    with tempfile.TemporaryDirectory(prefix="p2z_rv_t_") as td:
        tp = os.path.join(td, "terms.csv")
        with open(tp, "w", encoding="utf-8") as f:
            f.write("# 注释行, 不参与\n# 来源: 早期累积\nin-silico,计算机模拟\n"
                    "只有一列\nstomata,气孔\n")
        terms = RV.load_terms(tp)
        check("② 注释行与单列行都跳过", terms == [("in-silico", "计算机模拟"),
                                                  ("stomata", "气孔")], terms)
    check("② 表不存在返回空表不抛", RV.load_terms(os.path.join(_TMP, "no.csv")) == [])

    # ---- ③ 切块: 保序、不丢编号、单段超预算的自成一块 ----
    small = [("S%d" % i, "a" * 50, "b" * 50) for i in range(1, 11)]
    ch = RV.split_chunks(small, budget=300)
    check("③ 按预算切块", len(ch) == 4, [len(x) for x in ch])
    check("③ 编号保序不丢",
          [p[0] for c2 in ch for p in c2] == [p[0] for p in small], ch)
    huge = [("S1", "x" * 900, "y" * 900), ("S2", "a", "b")]
    ch2 = RV.split_chunks(huge, budget=300)
    check("③ 超预算的单段自成一块(不切段)",
          [len(x) for x in ch2] == [1, 1] and ch2[0][0][0] == "S1", ch2)

    # ---- ④ 提示词: 排除机械门禁的判据(否则噪音淹掉真问题) + 术语表 + 原文译文成对 ----
    pr = RV._prompt([("S12", "原文甲", "译文甲")], doc="[文档] X 第3页",
                    terms=[("stomata", "气孔")])
    check("④ 带上文档抬头", "[文档] X 第3页" in pr, pr[:200])
    check("④ 注入术语表", "stomata -> 气孔" in pr, pr[:400])
    check("④ 逐段给出原文与译文", "[#S12]\n原文: 原文甲\n译文: 译文甲" in pr, pr[-200:])
    check("④ 明说不报编号/占位符/⋮(那是机械门禁的活)",
          "编号是否齐全" in pr and "⋮" in pr and "机械门禁" in pr, "")
    check("④ 明说只报不改", "不要改写或重译" in pr, "")
    check("④ 明说不要凑数", "不要凑数" in pr, "")

    # ---- ⑤ 回包解析: 围栏/废话都容忍, 幻觉编号与空说明丢弃 ----
    content = ("好的, 以下是审核结果:\n```json\n"
               '[{"id":"s12","kind":"数字","level":"高","note":"原文 0.69 被译成 0.63",'
               '"quote":"0.69 accuracy"},'
               '{"id":"#S13","kind":"漏译","level":"低","note":"半句没译"},'
               '{"id":"S99","kind":"错译","level":"高","note":"幻觉段号"},'
               '{"id":"S14","kind":"术语","level":"高","note":"   "},'
               '{"id":"S12","kind":"数字","level":"高","note":"原文 0.69 被译成 0.63"}]'
               "\n```\n希望有帮助。")
    items = RV.parse_items(content, ["S12", "S13", "S14"])
    check("⑤ 围栏与前后废话都容忍", len(items) == 2, items)
    check("⑤ 编号大小写/井号归一", [x[0] for x in items] == ["S12", "S13"], items)
    check("⑤ 幻觉段号被丢弃", all(x[0] in ("S12", "S13") for x in items), items)
    check("⑤ 空说明被丢弃", all(x[3].strip() for x in items), items)
    check("⑤ 同段同类型同说明去重", len(items) == 2, items)
    check("⑤ level 非 高 一律记 中", [x[2] for x in items] == ["高", "中"], items)
    check("⑤ 类型归一到枚举值",
          [x[1] for x in items] == ["数字", "漏译"], items)
    check("⑤ 坏 JSON 返回空表不抛", RV.parse_items("模型今天不想说话", ["S1"]) == [])
    long_note = RV.parse_items('[{"id":"S1","kind":"表达","note":"%s"}]' % ("长" * 900),
                               ["S1"])
    check("⑤ 超长说明被截断", len(long_note[0][3]) == RV.MAX_NOTE, len(long_note[0][3]))

    # ---- ⑥ audit: 多块聚合、高在前、单块失败不拖垮整篇 ----
    # 4 段各 400 字符 < 默认预算 14000, 只会切成 1 块, 验不到"多块"; 故把切块替成
    # 一段一块 —— 单块预算不在 audit 签名里(split_chunks 的默认参数在定义时就绑定了),
    # 改常量没用, 只能替函数。
    calls = []

    def fake_chunk(chunk, doc, terms, cfg):
        ids = [p[0] for p in chunk]
        calls.append(ids)
        if ids[0] == "S3":                           # 模拟某块网络失败
            return [], "HTTP 503 "
        return [(u, "数字" if u == "S1" else "漏译",
                 "中" if u == "S1" else "高", "说明" + u, "q" + u) for u in ids], ""

    orig_chunk, orig_split = RV._audit_chunk, RV.split_chunks
    RV._audit_chunk = fake_chunk
    RV.split_chunks = lambda pairs, budget=0: [[p] for p in pairs]
    try:
        pairs = [("S1", "a" * 200, "b" * 200), ("S2", "a" * 200, "b" * 200),
                 ("S3", "a" * 200, "b" * 200), ("S4", "a" * 200, "b" * 200)]
        r = RV.audit(pairs, cfg=DUMMY_CFG, workers=2, log=lambda m: None)
    finally:
        RV._audit_chunk, RV.split_chunks = orig_chunk, orig_split
    check("⑥ 一段一块地并发审", sorted(x[0] for x in calls) == ["S1", "S2", "S3", "S4"],
          calls)
    check("⑥ 高严重度排在前", [x[2] for x in r["items"]][:1] == ["高"], r["items"])
    check("⑥ 高之内仍保持块序(稳定排序)", [x[0] for x in r["items"]][:2] == ["S2", "S4"],
          r["items"])
    check("⑥ 未完成的块被计数并点名",
          r["n_failed"] == 1 and any("未完成" in l for l in r["logs"]), r["logs"])
    check("⑥ 没有结论的块不产生条目",
          all(x[0] != "S3" for x in r["items"]), r["items"])
    check("⑥ 统计字段齐全", r["n_units"] == 4 and r["chars"] == 1600 and r["secs"] >= 0,
          r)
    check("⑥ 明确交出模型名(付费调用要能追)",
          r["model"] == "test-model", r["model"])
    check("⑥ 审计不改动传进来的段(只读)",
          pairs[0] == ("S1", "a" * 200, "b" * 200), pairs[0])

    # 空 pairs 明确报错(不是"0 条存疑", 那是两回事)
    check("⑥ 没有段落时明确报错",
          RV.audit([], cfg=DUMMY_CFG)["ok"] is False)

    # ---- ⑦ 报告: 落盘 + 结论行 + 只审不改的声明 ----
    md = RV.report_md({"items": [("S45", "数字", "高", "0.69 写成 0.63", "C∈{2,4}")],
                       "model": "m", "n_units": 10, "n_chunks": 1, "n_failed": 0,
                       "secs": 1.5}, doc="[文档] X", stem="正文 payload_demo")
    check("⑦ 报告头写明范围与耗时",
          "10 段 / 1 块" in md and "耗时: 1.5 秒" in md, md[:400])
    check("⑦ 报告列出段号/类型/原文片段",
          "#S45" in md and "数字·高" in md and "C∈{2,4}" in md, md)
    check("⑦ 报告声明只审不改", "只审不改" in md, md)
    check("⑦ 无存疑时报告也明确写出来",
          "未报出语义问题" in RV.report_md(
              {"items": [], "model": "m", "n_units": 1, "n_chunks": 1,
               "n_failed": 0, "secs": 0.1}), "")
    p = RV.write_report(md, stem="正文 payload_demo")
    check("⑦ 报告落盘到 review 目录",
          p and os.path.exists(p) and "语义审核_" in os.path.basename(p), p)

    # ---- ⑧ 面板接线: 回包的译文与载荷的原文按编号配成对(配错=审核结果全错位) ----
    write_env_files()
    seen = {}

    def fake_audit(pairs, doc="", terms=None, cfg=None, workers=3, log=None):
        seen["pairs"] = list(pairs)
        seen["doc"] = doc
        return {"ok": True, "items": [("S2", "表达", "高", "三维卷积", "{v0}D")],
                "n_units": len(pairs), "n_chunks": 1, "n_failed": 0, "chars": 10,
                "secs": 0.1, "model": "m", "report": "", "logs": []}

    orig_audit = PN.rv.audit
    PN.rv.audit = fake_audit
    try:
        gate_ran = []
        orig_gates = PN.sandbox_gates
        PN.sandbox_gates = lambda *a, **k: gate_ran.append(1) or (True, [], "")
        rr = PN.review(REPLY)
        PN.sandbox_gates = orig_gates
    finally:
        PN.rv.audit = orig_audit
    check("⑧ 回包被切成 (编号, 原文, 译文) 三段对",
          seen.get("pairs") == [("S1", "The model reaches 0.69 accuracy.", "模型的准确率达到 0.63。"),
                                ("S2", "We use {v0}D convolution for the encoder.", "编码器使用三维卷积。")],
          seen.get("pairs"))
    check("⑧ 原文取自载荷而不是回包(否则无从比对)",
          "0.69" in seen["pairs"][0][1] and "0.63" in seen["pairs"][0][2], seen.get("pairs"))
    check("⑧ 抬头取自载荷的 [文档] 行", seen.get("doc") == "[文档] Demo 2026 第1页",
          seen.get("doc"))
    check("⑧ 审核不跑门禁(沙箱一次都不起)", gate_ran == [], gate_ran)
    check("⑧ 条目与报告路径回给前端",
          rr["items"][0][0] == "S2" and rr["report"].endswith(".md"), rr)
    check("⑧ 编号不齐 -> 明确报错, 不花钱",
          "error" in PN.review("#S1\n只有一段。\n"))
    check("⑧ 空粘贴 -> 明确报错", "error" in PN.review("   "))

    print(f"\n结果: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
