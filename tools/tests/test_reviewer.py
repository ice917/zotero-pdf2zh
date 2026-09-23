# -*- coding: utf-8 -*-
"""④ 语义审核单元/集成测试 (v28.59→v28.76, 2026-09-21→23)

要解决的**用户侧**问题: 门禁只判机器判得了的东西(编号/字形/占位符/⋮), 语义错译
(半句丢失、否定颠倒、数字单位错位、术语漂移)一概够不到; 面板的「待看清单」也只给
读数(长度比/一致性)。用户只能自己通读整篇去发现 —— 本件把审核者补进门禁之外那一层。

四条边界(本测试逐条锁死):
  1. **审核者是第三方**: 翻译方是豆包, 审核走硅基流动 —— 被翻译方不得自校
     (relay_spec 第 4 条红线);
  2. **只报不改**: 审核者绝不产出改写稿(生成式改写不可信, 润色钩子退役的教训);
  3. **不参与放行**: 存疑清单只给人裁决, 放行权仍在本机门禁;
  4. **[v28.75] 分清责任**: 错的根在译文, 还是在**原文自己**(老扫描件的形近混淆/
     缺字/断字)? 后者单列「源文缺陷」, 且**不进**发给翻译方的返工单 —— 译者被规则
     要求逐字符照抄原文数字与字母, 要他改反而破坏字形回锚(见 ⑨ 节)。
  5. **[v28.76] 审核者不裁决, 但裁决要有落点**: ④ 报出「术语」存疑 -> 人在面板上
     填原词与译名 -> 一次写齐两份格式不同的术语表(见 ⑪ 节)。审核者**不给候选译名**
     (只报不改), 所以前端不预填、不猜。

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

    # ---- ⑨ [v28.75] 责任划分: 错在原文 vs 错在译文 ----
    #   缘起(实测 Johnson 23 条存疑): 其中 15 条的"错"在**原文文本层**就已存在(老扫描件的
    #   形近字母数字混淆 `011`=on / `110`=no / `III`=in / `IS`=15 / `340/00`=34‰、缺字
    #   `nutnt~vely`、断字 `lao mellae`=lamellae), 译文只是**照抄**(而规则 2/9 硬性要求
    #   逐字符照抄数字与字母, 机检按字形落点验收)。审核者却把它们报成「数字」「错译」并
    #   说"译文未识别为年份" —— 等于让用户拿着返工单去找翻译方改一个**改不动**的东西,
    #   改了反而破坏字形回锚。故单列「源文缺陷」类, 并明确它不进返工单。
    check("⑨ 源文缺陷成为独立类型", "源文缺陷" in RV.KINDS, RV.KINDS)
    check("⑨ 「源文」/「笔误」标签归到源文缺陷",
          RV._norm_kind("源文缺陷") == "源文缺陷"
          and RV._norm_kind("笔误") == "源文缺陷", "")
    check("⑨ 复合标签(原文数字笔误)不被退回译文侧数字错",
          RV._norm_kind("原文数字笔误") == "源文缺陷", RV._norm_kind("原文数字笔误"))
    check("⑨ 译文侧各类型不受影响",
          [RV._norm_kind(k) for k in ("漏译", "错译", "数字", "术语", "指代", "表达")]
          == ["漏译", "错译", "数字", "术语", "指代", "表达"], "")

    r2 = RV._prompt([("S1", "There was 110 infection.", "有 110 例感染。")])
    check("⑨ 提示词讲清判据: 逐字比 -> 错在原文里就已经存在",
          "错在原文" in r2 and "逐字比" in r2, r2[:300])
    check("⑨ 提示词明说译者被要求逐字符照抄(故不该指责译者)",
          "逐字符照抄" in r2 and "不是译文错误" in r2, "")
    check("⑨ 提示词禁止建议按推测原意改写译文(会破坏字形回锚)",
          "破坏字形回锚" in r2, "")
    check("⑨ 输出枚举里带上源文缺陷",
          "|源文缺陷" in r2, r2[-400:])
    check("⑨ 文后参考文献表条目不入清单(D 类 S36 的实质: 照抄即正确)",
          "参考文献表" in r2 and "不算问题" in r2, "")
    check("⑨ 引证式主语(作者(年份))不算错译",
          "作者(年份)" in r2, "")

    # split_src: 两类切干净, 顺序不变
    mix = [("S1", "数字", "高", "译文把 0.69 写成 0.63", "0.69"),
           ("S2", "源文缺陷", "高", "原文 340/00 疑为 34‰", "340/00"),
           ("S3", "漏译", "高", "后半句没译", "x"),
           ("S4", "源文缺陷", "中", "原文 lao mellae 疑为 lamellae", "lao mellae")]
    tr, sr = RV.split_src(mix)
    check("⑨ split_src 切出译文侧两条", [x[0] for x in tr] == ["S1", "S3"], tr)
    check("⑨ split_src 切出源文缺陷两条", [x[0] for x in sr] == ["S2", "S4"], sr)
    check("⑨ split_src 对空/None 不抛", RV.split_src(None) == ([], []), "")

    # report_md: 两类分节落盘, 结论行点名源文缺陷条数
    md2 = RV.report_md({"items": mix, "model": "m", "n_units": 4, "n_chunks": 1,
                        "n_failed": 0, "secs": 1.0}, doc="[文档] X", stem="正文 x")
    check("⑨ 报告分两节(译文侧 / 原文文本层缺陷)",
          "## 存疑清单(译文侧)" in md2
          and "## 原文文本层缺陷" in md2, md2)
    check("⑨ 结论行点名源文缺陷条数与归属",
          "存疑 4 条(其中源文缺陷 2 条, 不由译者承担)" in md2, md2[:400])
    check("⑨ 源文缺陷节明说不要发给翻译方",
          "不要**把本节当返工单" in md2 and "改原文" in md2, "")
    check("⑨ 两类条目各归各节, 不串",
          md2.index("#S1") < md2.index("#S3") < md2.index("## 原文文本层缺陷")
          < md2.index("#S2") < md2.index("#S4"), "")
    md3 = RV.report_md({"items": [("S1", "源文缺陷", "高", "原文笔误", "q")],
                        "model": "m", "n_units": 1, "n_chunks": 1, "n_failed": 0,
                        "secs": 0.1})
    check("⑨ 只有源文缺陷时译文侧也不留空(写明无)",
          "(无 —— 译文侧没有报出语义问题)" in md3, md3)
    check("⑨ 只有源文缺陷时报告不写「未报出语义问题」",
          "未报出语义问题" not in md3, md3)

    # 面板前端: 源文缺陷照常显示, 但**不进**一键复制的返工单
    check("⑨ 前端按 SRC_KIND 分流",
          "SRC_KIND='源文缺陷'" in PN.PAGE and "rvSplit(r)" in PN.PAGE, "")
    check("⑨ 复制文本只取译文侧(源文缺陷不进返工单)",
          "rvSplit(r).trans.forEach" in PN.PAGE, "")
    check("⑨ 只有源文缺陷时复制按钮拒发并说明去向",
          "只有「源文缺陷」" in PN.PAGE and "没有可发给翻译方的条目" in PN.PAGE, "")
    check("⑨ 卡片里源文缺陷节写明不由译者承担",
          "原文文本层缺陷（" in PN.PAGE and "不由译者承担" in PN.PAGE, "")
    check("⑨ 审核完成日志报段数用 n_units(旧版写 r.n 会打成 undefined)",
          "r.n_units+' 段" in PN.PAGE, "")

    # ---- ⑩ [v28.75] 审核者的文档先验: 抬头只有页码时回落篇名 ----
    #   实测三篇载荷首行全是 "[文档] 第1-3页"(导出没给 --doc, 抬头宁缺勿谎) —— 对审核者
    #   是**零信息**: 每篇抬头一模一样, "所在文档"这条先验等于没发出去, 而它正是术语
    #   判断(下面那些 340/00 / lamellae 之类的歧义)唯一能借的语境。
    with tempfile.TemporaryDirectory(prefix="p2z_rv_doc_") as td:
        pp = os.path.join(td, "p.txt")
        jb = {"payload": pp, "paper": "Johnson - INFECTION POTENTIAL"}

        def put(line):
            with open(pp, "w", encoding="utf-8") as f:
                f.write(line + "\n#S1\nbody\n")
            return PN._doc_of(jb)

        check("⑩ 抬头只有页码 -> 补上篇名",
              put("[文档] 第1-3页") == "[文档] Johnson - INFECTION POTENTIAL 第1-3页",
              put("[文档] 第1-3页"))
        check("⑩ 页码形态的变体都认(逗号/连字符/至)",
              put("[文档] 第1, 3-5页") == "[文档] Johnson - INFECTION POTENTIAL 第1, 3-5页",
              put("[文档] 第1, 3-5页"))
        check("⑩ 抬头已有篇名 -> 原样不改",
              put("[文档] Demo 2026 (Desert Plants 2009) 第1页")
              == "[文档] Demo 2026 (Desert Plants 2009) 第1页", "")
        check("⑩ 没有 [文档] 行 -> 不编造, 返回空",
              put("任务说明") == "", put("任务说明"))
        put("[文档] 第1-3页")
        check("⑩ 连篇名都没有 -> 退回原样(不写半截抬头)",
              PN._doc_of({"payload": pp, "paper": ""}) == "[文档] 第1-3页",
              PN._doc_of({"payload": pp, "paper": ""}))

    # ---- ⑪ [v28.76] 术语裁决入表: 一次裁决写齐两份**格式不同**的表 ----
    #   ④ 报出「术语」存疑之后的落点。此前全是手工: 人裁决完开 terms.csv 追加一行, 再
    #   **记得**同步 terms.babeldoc.csv —— 两表格式不同(1.x 的 terms.csv 无表头、可带 `#`
    #   注释、LF; BabelDOC 的 terms.babeldoc.csv 带表头、不能掺注释、CRLF), 漏写一份不报
    #   任何错, 换个引擎那个词就不生效。本节的断言就是这条底线。
    T1 = "# 来源: 甲 —— 2026-01-01 ④ 语义审核术语裁决\nlamellae,瓣\n"
    T2 = "source,target\r\nlamellae,瓣\r\n"

    def mk(tmp, t1=T1, t2=T2):
        p1, p2 = os.path.join(tmp, "terms.csv"), os.path.join(tmp, "terms.babeldoc.csv")
        for p, t in ((p1, t1), (p2, t2)):
            if t is not None:
                with open(p, "w", encoding="utf-8", newline="") as f:
                    f.write(t)
        return [p1, p2]

    def rb(p):
        with open(p, "rb") as f:
            return f.read()

    with tempfile.TemporaryDirectory(prefix="p2z_rv_ta_") as td:
        f1, f2 = mk(td)
        st, msg = RV.append_terms("ovigerous lamellae", "卵瓣", src="Johnson - INFECTION",
                                  day="2026-09-23", paths=[f1, f2])
        check("⑪ 新词 -> written 且点名两份表",
              st == "written" and "terms.csv" in msg and "terms.babeldoc.csv" in msg, (st, msg))
        check("⑪ 两份表都写进去了(少一份就是静默失效)",
              "ovigerous lamellae,卵瓣\n".encode("utf-8") in rb(f1)
              and "ovigerous lamellae,卵瓣\r\n".encode("utf-8") in rb(f2), (rb(f1), rb(f2)))
        check("⑪ 各按自己的换行形态写(1.x 全 LF / BabelDOC 全 CRLF, 不把整文件 diff 弄脏)",
              b"\r\n" not in rb(f1) and b"#" not in rb(f2)
              and b"\n" not in rb(f2).replace(b"\r\n", b""), (rb(f1)[-40:], rb(f2)[-40:]))
        check("⑪ 读取端看得见新词(注释行不可见, 不因注释丢失词条)",
              ("ovigerous lamellae", "卵瓣") in RV.load_terms(f1), RV.load_terms(f1))
        check("⑪ 报的是第一份表的条数口径(两表都有则一条不重)",
              len(RV.load_terms(f1)) == 2, RV.load_terms(f1))

        # 同一次审核连着加词: 来源注释只写一次(它写在词条前面, 按「最后一行」判会永远不成立)
        st2, _ = RV.append_terms("egg lamellae", "卵瓣", src="Johnson - INFECTION",
                                 day="2026-09-23", paths=[f1, f2])
        with open(f1, encoding="utf-8") as f:
            raw1 = f.read()
        check("⑪ 同来源同日再加词 -> 不重复写 `# 来源`",
              st2 == "written" and raw1.count("# 来源:") == 2, raw1)
        check("⑪ 但词条仍然写进去了", ("egg lamellae", "卵瓣") in RV.load_terms(f1), raw1)
        st3, _ = RV.append_terms("ova", "卵", src="另一篇", day="2026-09-23", paths=[f1, f2])
        with open(f1, encoding="utf-8") as f:
            check("⑪ 换来源 -> 另起一条 `# 来源`", st3 == "written" and f.read().count("# 来源:") == 3, "")

        # 来源篇名里带逗号(实测篇名常带逗号)不能把注释写成两列 -> 读取端会当词条收进去
        st4, _ = RV.append_terms("pollinator", "传粉者", src="Smith, J. - Desert Plants",
                                 day="2026-09-23", paths=[f1, f2])
        with open(f1, encoding="utf-8") as f:
            cmt = [ln.rstrip("\n") for ln in f if ln.startswith("# 来源:") and "Smith" in ln]
        check("⑪ 来源里的逗号被换成空格(注释行不能是 csv 两列)",
              st4 == "written" and len(cmt) == 1 and "," not in cmt[0]
              and ("pollinator", "传粉者") in RV.load_terms(f1), cmt)
        check("⑪ 篇名没记 -> 写成「篇名未记」而不编造",
              RV.append_terms("stigma", "柱头", src="", day="2026-12-31", paths=[f1, f2])[0] == "written"
              and "篇名未记" in open(f1, encoding="utf-8").read(), "")

        # 去重: 按小写比(同一个词的大小写变体不该并存)
        st5, msg5 = RV.append_terms("Ovigerous Lamellae", "卵瓣", day="2026-09-23", paths=[f1, f2])
        check("⑪ 大小写变体算同一个词 -> exists, 不重复写",
              st5 == "exists" and "卵瓣" in msg5, (st5, msg5))
        st6, msg6 = RV.append_terms("ovigerous lamellae", "卵瓣(另一个译名)", day="2026-09-23",
                                    paths=[f1, f2])
        check("⑪ 一个原词两个译名 -> error 并报出表里现有的译名",
              st6 == "error" and "卵瓣" in msg6 and "另一个译名" not in msg6, (st6, msg6))
        check("⑪ 拒写时表没被改动",
              len(RV.load_terms(f1)) == 6, RV.load_terms(f1))

        # 拒绝项: 半角逗号会拆列、换行会拆行(表按行解析)、超长是"整句原文"误粘
        bad = [("", "译名"), ("x", ""), ("a,b", "译名"), ("x", "y,z"),
               ("a\nb", "译名"), ("x", "y" * 61), ("x" * 121, "y")]
        check("⑪ 空原词/空译名/半角逗号/换行/超长 一律 error",
              all(RV.append_terms(a, b, paths=[f1, f2])[0] == "error" for a, b in bad),
              [RV.append_terms(a, b, paths=[f1, f2]) for a, b in bad])
        check("⑪ 超长的说明说清「整句匹配不上」(而不是只说太长)",
              "匹配不上" in RV.append_terms("x" * 200, "y", paths=[f1, f2])[1], "")
        check("⑪ 拒绝项一条也没落进表", len(RV.load_terms(f1)) == 6, RV.load_terms(f1))

    # 第二份表读不到 -> 仍算写成, 但必须在回执里点名(别让第二份静默漏掉)
    with tempfile.TemporaryDirectory(prefix="p2z_rv_ta2_") as td:
        f1, f2 = mk(td, t2=None)
        st, msg = RV.append_terms("nectar", "花蜜", src="X", day="2026-09-23", paths=[f1, f2])
        check("⑪ 第二份表缺失 -> 仍 written, 但回执点名哪份没写",
              st == "written" and "未写" in msg and "terms.babeldoc.csv" in msg, (st, msg))
        check("⑪ 缺失的那份不被顺手新建(宁可缺, 不造一个半成品表)",
              not os.path.exists(f2), os.path.exists(f2))
        check("⑪ 第一份照样写进去了", ("nectar", "花蜜") in RV.load_terms(f1), "")
    with tempfile.TemporaryDirectory(prefix="p2z_rv_ta3_") as td:
        st, msg = RV.append_terms("nectar", "花蜜", paths=[os.path.join(td, "terms.csv"),
                                                           os.path.join(td, "terms.babeldoc.csv")])
        check("⑪ 两份表都不在 -> error 不谎报成功", st == "error", (st, msg))

    # 结尾形态: 原文件没有收尾换行的, 追加时补一个 —— 否则两行粘成一条, 读取端丢一条
    with tempfile.TemporaryDirectory(prefix="p2z_rv_ta4_") as td:
        f1, f2 = mk(td, t1="# 判据\nlamellae,瓣", t2="source,target\r\nlamellae,瓣")
        st, _ = RV.append_terms("ova", "卵", day="2026-09-23", paths=[f1, f2])
        check("⑪ 缺收尾换行时先补换行(不把两行粘成一条)",
              st == "written" and ("ova", "卵") in RV.load_terms(f1)
              and len(RV.load_terms(f1)) == 2, (rb(f1), RV.load_terms(f1)))
        check("⑪ CRLF 表补的是 CRLF",
              rb(f2).endswith(b"ova,\xe5\x8d\xb5\r\n") and b"\n" not in rb(f2).replace(b"\r\n", b""),
              rb(f2))

    check("⑪ terms_files 默认就是那两份(1.x 在前, 去重以它为准)",
          [os.path.basename(p) for p in RV.terms_files()] == ["terms.csv", "terms.babeldoc.csv"]
          and os.path.join("server", "glossary") in RV.terms_files()[0], RV.terms_files())

    # 面板接线: 后端路由/处理器 + 前端表单与事件(前端只把来源篇名捎回去, 判据全在服务器)
    with open(PN.__file__, encoding="utf-8") as f:
        PNSRC = f.read()
    check("⑪ 后端挂上 /api/termadd 路由与 _term_add 处理器",
          '"/api/termadd"' in PNSRC and "def _term_add" in PNSRC, "")
    check("⑪ 处理器把判据交给 reviewer.append_terms(不在面板里重写一遍)",
          "rv.append_terms(" in PNSRC, "")
    check("⑪ 来源取的是**这次审核那篇**(reviewer 只用 label 找不到才退正文任务)",
          "def _review_src" in PNSRC and "j.get(\"label\") == label" in PNSRC, "")
    check("⑪ 前端只给「术语」条目挂表单",
          "it.kind==='术语'" in PN.PAGE and "class=\"tform\"" in PN.PAGE, "")
    check("⑪ 前端表单有原词/译名两格与按钮",
          "原文术语(词或短语" in PN.PAGE and "存进术语表" in PN.PAGE
          and "closest('.tbtn')" in PN.PAGE, "")
    check("⑪ 前端把 job 标签捎回去(来源篇名不靠猜)",
          "api('/api/termadd'" in PN.PAGE and "window.__rv.job" in PN.PAGE, "")
    check("⑪ 前端如实转述回执(哪份没写 / 现表条数)",
          "r.n_terms" in PN.PAGE and "r.error" in PN.PAGE, "")
    check("⑪ 审核完成时提示可就地入表",
          "可就地入表" in PN.PAGE, "")

    print(f"\n结果: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
