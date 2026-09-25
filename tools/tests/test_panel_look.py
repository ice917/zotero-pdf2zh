# -*- coding: utf-8 -*-
"""面板「待看清单」判据测试 (v30.5, 2026-09-24)

要解决的**用户侧**问题: 正文作业的待看清单里冒出「▲ 高 末条偏短 长度比 0.38 < 全批中位
0.39」, 而回包是完整的; 全表又按比值升序把小标题(Acknowledgements/Discussion…)全顶到
最上面 —— 最不该看的排在最显眼处。病根不是阈值调得不巧, 是**第三层的读数被塞进了第二层
的待看清单**并配上了"高/中", 读数于是获得了判断的外观:
  · 参照跨了源串类别 —— 比值大小由源串自身性质决定(小标题"Acknowledgements"→"致谢"
    天然 0.13), 拿它跟**整篇**中位数比, 是把"类别差异"读成"长度异常"; `过短` 的阈值恰是
    0.5×中位(≈0.14), 正好压在小标题那一档, 会把标题全点成嫌疑;
  · 阈值不是阈值 —— `末条 < 全批中位` 按定义就是整篇约 50% 的事件, 拿它当"高"警, 误报率
    天然 ~50%(旧口径"故不误报"是单样本校准, 0.38 vs 0.39 当场推翻)。

本件锁死处置(判据与依据见改动记录 v30.5):
  ① 健康回包(含小标题 + 末条低于中位) -> 待看清单**必须为空**(读数的判断化已撤);
  ② 同一原文译成两种 -> 恰 1 条「高·一致性」(源串自锚、零阈值的判据仍在, 未被误伤);
  ③ 待看清单只允许「一致性」这一类 —— 锁"只放源串自锚判据"的层界;
  ④ 全表 = **文档序**(manifest 顺序), 不再拿读数当排序键;
  ⑤ 读数仍在(每行比值 / 中位 / 末条比), 只是不再判定;
  ⑥ 源码守卫: `SUSPECT_K` 与 `"过短"`/`"末条偏短"` 两个 kind 标签不得复活
     (docstring 里以「」或 `` 引用的历史不拦, 只拦**代码里重新加回来**)。

全部离线: 任务识别 / manifest / 门禁三处注入, 不碰真工作目录、不起真门禁子进程。

运行: venv python test_panel_look.py, 退出码 0=全过
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
TABLE_PIPE = os.path.join(TOOLS, "table_pipe")
for p in (TOOLS, TABLE_PIPE):
    if p not in sys.path:
        sys.path.insert(0, p)

# 环境必须在 import 之前落定(panel / watch_clip 都在**模块层**读这些变量);
# 顺手摘掉密钥 —— 测试绝不打真接口。
_TMP = tempfile.mkdtemp(prefix="p2z_look_")
os.environ["P2Z_PROJ"] = _TMP
os.environ["P2Z_TABLE_DIR"] = os.path.join(_TMP, "work")
os.environ["P2Z_INBOX"] = os.path.join(_TMP, "inbox")
os.environ["P2Z_BODY_NAME"] = "look_demo"
os.environ["P2Z_BODY_PDF"] = os.path.join(_TMP, "demo.pdf")
for k in ("SILICON_API_KEY", "SILICON_MODEL", "SILICON_BASE_URL"):
    os.environ.pop(k, None)

import panel as PN        # noqa: E402

JOB = {"label": "正文 look_demo", "pre": "S", "blocks": True, "render": True,
       "paper": "look_demo", "manifest": "look_demo_manifest.json"}


# ---- 合成单元: 形态照正文作业 —— 小标题(天然低比) + 句子 + 末条(略低于中位)。
# 比值一律由被测代码自己算(_ratio), 这里不预置数字, 免得测试与实现对不上。
_HEAD = ("S1", "Acknowledgements", "致谢")                                  # ~0.13 小标题
_UNIT_A = ("S4", "Two pollinators were observed on the same flower head.",
           "在同一朵花头上观察到两种传粉者。")

HEALTHY = [
    _HEAD,
    ("S2", "Introduction and scope of this study on bees", "本研究关于蜜蜂的范围引言"),
    ("S3", "The flowers were visited by bees throughout the season.", "整个花期内蜜蜂都在访问这些花。"),
    _UNIT_A,
    ("S5", "Pollinators differ between the two study sites.", "两个研究地点之间的传粉者不同。"),
    ("S6", "Discussion", "讨论"),                                            # 小标题
    ("S7", "Statistical analysis of the survey data", "调查数据的统计分析"),   # 末条: 低于中位
]

# 乙 = 甲 + 把 S5 的原文改成与 S4 相同而译文不同 -> 一致性必中(零阈值判据仍在)
INCONSISTENT = HEALTHY[:3] + [
    ("S4", _UNIT_A[1], _UNIT_A[2]),
    ("S5", _UNIT_A[1], "在同一花头观察到两种传粉者。"),
] + HEALTHY[-2:]


def run_look(units):
    """注入任务识别/manifest/门禁三处, 只跑 analyse() 的待看清单这一层。"""
    PN.wc.manifest_units = lambda job: [{"id": u[0], "orig": u[1]} for u in units]
    got = {u[0]: u[2] for u in units}
    PN.wc.match_job = lambda text, log=None: (JOB, [u[0] for u in units], got)
    PN.sandbox_gates = lambda job, ids, g: (True, [], "")
    text = "\n".join("%s\t%s" % (u[0], u[2]) for u in units)
    return PN.analyse(text)


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

    # ---- 甲: 健康回包 —— 含小标题 + 末条低于中位, 待看清单必须为空
    a = run_look(HEALTHY)
    if "error" in a:
        print("  FAIL 甲 analyse 报错: %s" % a["error"])
        return 1
    asrt = [r[0] for r in a["rows"]]
    ids = [r[1] for r in a["rows"]]
    print("  甲 长度比 %s | 中位 %.3f | 末条 %.3f"
          % (["%.3f" % x for x in asrt], a["median"], a["last_ratio"]))

    # 前提: 这一批**确实**同时含"低比小标题"和"末条低于中位" —— 否则下面的断言没有牙
    check("甲 前提: 小标题 S1 低于 0.5×中位(旧 `过短` 会点它)",
          asrt[0] < 0.5 * a["median"], "S1=%.3f 0.5×中位=%.3f" % (asrt[0], 0.5 * a["median"]))
    check("甲 前提: 末条低于全批中位(旧 `末条偏短` 会点它)",
          a["last_ratio"] < a["median"],
          "末条=%.3f 中位=%.3f" % (a["last_ratio"], a["median"]))
    check("① 健康回包: 待看清单为空(读数不再判定)", a["items"] == [], a["items"])
    check("① 空清单不改门禁结论", a["ok"] is True and a["n_incons"] == 0, a.get("blame"))

    # ---- ④ 全表 = 文档序(manifest 顺序), 不是比值序
    check("④ 全表按文档序(== manifest 顺序)",
          ids == [u[0] for u in HEALTHY], "实际 %s" % ids)
    check("④ 全表不再按比值升序(守卫有牙: 序列与其有序副本不同)",
          asrt != sorted(asrt), "比值序列已是有序的, 该断言失去意义: %s" % asrt)

    # ---- ⑤ 读数仍在 —— 撤的是"判定", 不是读数
    check("⑤ 每行都带长度比读数", len(asrt) == len(HEALTHY) and all(x > 0 for x in asrt))
    check("⑤ 顶部中位与末条比仍是读数",
          a["median"] > 0 and a["last_ratio"] == asrt[-1],
          "中位=%s 末条=%s" % (a["median"], a["last_ratio"]))
    check("⑤ 末条原文/译文并排仍在(人眼抓截断的唯一入口)",
          a["last"] == (HEALTHY[-1][0], HEALTHY[-1][1], HEALTHY[-1][2]), a["last"])

    # ---- 乙: 同一原文译成两种 -> 一致性仍精准命中(且只此一条)
    b = run_look(INCONSISTENT)
    if "error" in b:
        print("  FAIL 乙 analyse 报错: %s" % b["error"])
        return 1
    b_asrt = [r[0] for r in b["rows"]]
    print("  乙 待看 %s" % (b["items"],))
    check("② 同一原文两种译法 -> 恰 1 条待看", len(b["items"]) == 1, b["items"])
    if b["items"]:
        lv, kind, msg, uids = b["items"][0]
        check("② 判据是「高·一致性」且点名两个单元",
              lv == "高" and kind == "一致性" and set(uids.split()) == {"S4", "S5"},
              (lv, kind, uids))
        check("② 说明里带上原文与两种译法",
              "却译成 2 种" in msg and "Two pollinators" in msg, msg)
    check("③ 待看清单只允许「一致性」这一类(层界: 只放源串自锚判据)",
          set(k for _, k, _, _ in b["items"]) <= {"一致性"},
          [k for _, k, _, _ in b["items"]])
    check("③ 乙里那条低于中位的末条仍不进清单(误报形状再锁一次)",
          b["last_ratio"] < b["median"]
          and all(b["last"][0] not in u.split() for _, _, _, u in b["items"]),
          "末条=%s 中位=%s" % (b["last_ratio"], b["median"]))
    check("③ 乙里的小标题也不进清单",
          all("S1" not in u.split() for _, _, _, u in b["items"]), b["items"])
    check("③ 不一致只影响读数不改门禁结论", b["ok"] is True and b["n_incons"] == 1,
          (b["ok"], b["n_incons"]))
    check("④ 乙的全表同样是文档序", [r[1] for r in b["rows"]] == [u[0] for u in INCONSISTENT])
    check("⑤ 乙的读数照旧", b_asrt != sorted(b_asrt) and b["median"] > 0)

    # ---- ⑥ 源码守卫: 别把"读数当判据"重新加回来
    with open(PN.__file__, encoding="utf-8") as f:
        src = f.read()
    check("⑥ SUSPECT_K(相对中位的阈值)已删除", "SUSPECT_K" not in src, "")
    check("⑥ kind 标签 \"过短\" 不得复活", '"过短"' not in src, "")
    check("⑥ kind 标签 \"末条偏短\" 不得复活", '"末条偏短"' not in src, "")
    check("⑥ 长度比列标了「读数」(前端不再像告警)",
          "长度比<span" in PN.PAGE and "读数" in PN.PAGE, "")

    print(f"\n待看清单判据测试: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
