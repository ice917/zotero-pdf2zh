# -*- coding: utf-8 -*-
"""面板体检测试 (v28.77, 2026-09-23)

要解决的**用户侧**问题: 静默陷阱(改了没生效 / 漏同步不报错 / 新旧实例共存)此前只能在
"撞上之后"按排障表倒查 —— 定位收束后(认知劳动归用户, 结构劳动归工具), 结构陷阱必须在
用户撞上**之前**自己亮红灯。本件锁死体检的四组判据与面板接线。

四组判据(每项对应一次实测踩坑, 出处见改动记录 v28.77):
  ① 术语表·双表同步与格式口径(漏的那份静默失效 / # 行入 babeldoc / 表头口径 / 坏行)
  ② config.toml 的 Ital 陷阱(整段斜体被吞成公式)
  ③ 端口共存(8890 翻译服务 SO_REUSEADDR 新旧共存 / 60642 旧面板实例)
  ④ 可写探针(④ 报告目录 / 底片 ledger 目录)

全部离线: 路径 / 表 / config / netstat 全部注入, 不跑真 netstat、不碰真术语表。

运行: venv python test_panel_health.py, 退出码 0=全过
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

# 环境必须在 import 之前落定(watch_clip / reviewer / panel 都在**模块层**读这些变量);
# 顺手摘掉密钥 —— 测试绝不打真接口。
_TMP = tempfile.mkdtemp(prefix="p2z_health_")
os.environ["P2Z_PROJ"] = _TMP
os.environ["P2Z_TABLE_DIR"] = os.path.join(_TMP, "work")
os.environ["P2Z_INBOX"] = os.path.join(_TMP, "inbox")
os.environ["P2Z_BODY_NAME"] = "payload_demo"
os.environ["P2Z_BODY_PDF"] = os.path.join(_TMP, "demo.pdf")
os.environ["P2Z_REVIEW_DIR"] = os.path.join(_TMP, "review")
os.environ.pop("SILICON_API_KEY", None)
os.environ.pop("SILICON_MODEL", None)
os.environ.pop("SILICON_BASE_URL", None)

import panel as PN        # noqa: E402
import reviewer as RV     # noqa: E402

# 合成两份表: 真实文件的形态(terms.csv LF + `#` 注释合法; babeldoc CRLF + 必带表头)
T_ALIGNED = "# 判据(v28.15)\nlamellae,瓣\novigerous lamellae,卵瓣\n"
B_ALIGNED = "source,target\r\nlamellae,瓣\r\novigerous lamellae,卵瓣\r\n"
# 净版 config(含 formular_font_pattern 但不含 Ital) + 健康 netstat(8890 单实例、60642 只有自己)
CFG_CLEAN = "formular_font_pattern = '.*NimbusRom.*'\n"
NET_OK = ("  TCP    0.0.0.0:8890    0.0.0.0:0    LISTENING    111\n"
          "  TCP    0.0.0.0:60642   0.0.0.0:0    LISTENING    999\n"
          "  TCP    [::]:60642      [::]:0       LISTENING    999\n")
OWN_PID = 999


def w(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def run(t=None, b=None, cfg="MISSING", net=NET_OK, writable=True):
    """临时目录里造一份合成环境 -> {检查名: (status, detail)}。
    cfg="MISSING" 表示不写 config.toml(文件不存在); writable=False 让 ledger 路径被
    同名文件占住(makedirs 必败)。own_pid 恒为 999, netstat 里的 60642 监听者也写成 999。"""
    with tempfile.TemporaryDirectory(prefix="p2z_hc_") as td:
        tp, bp, cp = (os.path.join(td, "terms.csv"),
                      os.path.join(td, "terms.babeldoc.csv"),
                      os.path.join(td, "config.toml"))
        if t is not None:
            w(tp, t)
        if b is not None:
            w(bp, b)
        if cfg != "MISSING":
            w(cp, cfg)
        rev, led = os.path.join(td, "rev"), os.path.join(td, "led")
        if not writable:
            w(led, "我是一个文件, 占住 ledger 目录的路径")
        items = PN.health_checks(terms=tp, terms_babeldoc=bp, cfg_toml=cp,
                                 netstat=net, review_dir=rev, ledger_dir=led,
                                 own_pid=OWN_PID)
        return {n: (s, d) for n, s, d in items}, td


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

    # ---- ① 全绿基线: 六项全 ok(双表同步 / Ital / 8890 / 60642 / 可写×2)
    h, td = run(T_ALIGNED, B_ALIGNED, cfg=CFG_CLEAN)
    check("① 双表对齐 -> ok 且报条数",
          h["术语表·双表同步"] == ("ok", "1.x 2 条 ↔ BabelDOC 2 条, 译名一致。"),
          h.get("术语表·双表同步"))
    check("① 净版 config: Ital 陷阱不成立",
          h["config.toml·Ital 陷阱"][0] == "ok", h.get("config.toml·Ital 陷阱"))
    check("① 8890 单实例 -> ok", h["端口·翻译服务共存"][0] == "ok",
          h.get("端口·翻译服务共存"))
    check("① 60642 只有本面板 -> ok", h["端口·面板共存"][0] == "ok",
          h.get("端口·面板共存"))
    check("① 全绿基线恰好六项、零警告零异常",
          len(h) == 6 and all(s == "ok" for s, _ in h.values()), h)
    check("① terms.csv 的合法 `#` 注释不被误报",
          "术语表·BabelDOC 掺了注释行" not in h and "术语表·1.x 坏行" not in h, h)
    check("① 探针即写即删, 不留文件",
          not os.path.exists(os.path.join(td, "rev", ".p2z_health_probe"))
          and not os.path.exists(os.path.join(td, "led", ".p2z_health_probe")), td)

    # ---- ② 术语表: 漏同步是静默失效, 格式错会被解析器吃掉
    h, _ = run(T_ALIGNED, "source,target\r\nlamellae,瓣\r\n")
    s, d = h["术语表·双表同步"]
    check("② BabelDOC 少一个词 -> warn 且点名漏的词",
          s == "warn" and "ovigerous lamellae" in d, (s, d))
    h, _ = run(T_ALIGNED, "source,target\r\nlamellae,瓣\r\novigerous lamellae,载卵片\r\n")
    s, d = h["术语表·两表译名打架"]
    check("② 同词两译 -> bad(换引擎同词换名)", s == "bad" and "载卵片" in d, (s, d))
    h, _ = run(T_ALIGNED, "source,target\r\n# 来源: 甲\r\nlamellae,瓣\r\novigerous lamellae,卵瓣\r\n")
    check("② babeldoc 掺 `#` 行 -> bad(BabelDOC 会当词条吞)",
          h.get("术语表·BabelDOC 掺了注释行", ("", ""))[0] == "bad", h)
    h, _ = run("# 判据\nlamellae,瓣\novigerous lamellae 漏逗号\n", B_ALIGNED)
    s, d = h["术语表·1.x 坏行"]
    check("② 拆不出两列的行 -> warn 带行号(静默跳过=词白写)",
          s == "warn" and "3" in d, (s, d))
    h, _ = run("english,chinese\nlamellae,瓣\novigerous lamellae,卵瓣\n", B_ALIGNED)
    check("② terms.csv 出现表头行 -> warn(会被当词条)",
          h["术语表·1.x 表头口径"][0] == "warn", h.get("术语表·1.x 表头口径"))
    h, _ = run("# 判据\nlamellae,瓣\novigerous lamellae,卵瓣\n",
               "lamellae,瓣\r\novigerous lamellae,卵瓣\r\n")
    check("② babeldoc 缺表头 -> warn(首条词条会被当表头丢)",
          h["术语表·BabelDOC 表头口径"][0] == "warn", h.get("术语表·BabelDOC 表头口径"))
    h, _ = run(None, B_ALIGNED)
    check("② 1.x 表读不到 -> bad(这边的词全不生效)",
          h["术语表·1.x 读不到"][0] == "bad", h.get("术语表·1.x 读不到"))
    with tempfile.TemporaryDirectory(prefix="p2z_hc_par_") as td2:
        tp = os.path.join(td2, "t.csv")
        w(tp, T_ALIGNED)
        check("② 解析口径与 reviewer.load_terms 一致",
              PN._glossary_rows(tp, False)[0] == RV.load_terms(tp),
              (PN._glossary_rows(tp, False)[0], RV.load_terms(tp)))

    # ---- ③ config.toml 的 Ital 陷阱
    h, _ = run(T_ALIGNED, B_ALIGNED, cfg="formular_font_pattern = '.*,.*Ital.*'\n")
    s, d = h["config.toml·Ital 陷阱"]
    check("③ formular_font_pattern 含 Ital -> bad(斜体被吞成公式)",
          s == "bad" and "斜体" in d, (s, d))
    h, _ = run(T_ALIGNED, B_ALIGNED, cfg="MISSING")
    check("③ config 不存在 -> ok(默认无此陷阱)",
          h["config.toml·Ital 陷阱"][0] == "ok", h.get("config.toml·Ital 陷阱"))

    # ---- ④ 端口共存
    net2 = ("  TCP    0.0.0.0:8890    0.0.0.0:0    LISTENING    111\n"
            "  TCP    0.0.0.0:8890    0.0.0.0:0    LISTENING    222\n"
            "  TCP    0.0.0.0:60642   0.0.0.0:0    LISTENING    %d\n" % OWN_PID)
    h, _ = run(T_ALIGNED, B_ALIGNED, net=net2)
    s, d = h["端口·翻译服务共存"]
    check("④ 8890 两个 PID -> bad(服务的还是旧的)", s == "bad" and "SO_REUSEADDR" in d, (s, d))
    h, _ = run(T_ALIGNED, B_ALIGNED, net="")
    check("④ 8890 无监听 -> warn(服务没起, 不急)",
          h["端口·翻译服务共存"][0] == "warn", h.get("端口·翻译服务共存"))
    net3 = ("  TCP    0.0.0.0:8890    0.0.0.0:0    LISTENING    111\n"
            "  TCP    0.0.0.0:60642   0.0.0.0:0    LISTENING    777\n")
    h, _ = run(T_ALIGNED, B_ALIGNED, net=net3)
    check("④ 60642 被别的面板实例占 -> warn(旧窗连的是旧代码)",
          h["端口·面板共存"][0] == "warn", h.get("端口·面板共存"))
    saved = PN._netstat_text
    PN._netstat_text = lambda: None          # netstat 跑不动
    h, _ = run(T_ALIGNED, B_ALIGNED, net=None)
    PN._netstat_text = saved
    check("④ netstat 跑不动 -> 降级 warn 不崩, 不报端口结论",
          h["端口·实例共存"][0] == "warn"
          and "端口·翻译服务共存" not in h and "端口·面板共存" not in h, h)

    # ---- ⑤ 可写探针
    h, _ = run(T_ALIGNED, B_ALIGNED, writable=False)
    check("⑤ ledger 路径被文件占住 -> bad(出稿会半路硬停)",
          h["可写·底片 ledger 目录"][0] == "bad", h.get("可写·底片 ledger 目录"))
    check("⑤ 另一个目录照常 ok(失败只报失败的那个)",
          h["可写·④ 审核报告目录"][0] == "ok", h.get("可写·④ 审核报告目录"))

    # ---- ⑥ 面板接线
    with open(PN.__file__, encoding="utf-8") as f:
        PNSRC = f.read()
    check("⑥ 后端挂上 /api/health + health_checks + _health",
          '"/api/health"' in PNSRC and "def health_checks" in PNSRC
          and "def _health" in PNSRC, "")
    check("⑥ 体检异常兜底转述(体检不许挂面板)", "体检没跑成" in PNSRC, "")
    check("⑥ 头部有体检按钮(只读检查, 不动文件)",
          'id="healthBtn"' in PN.PAGE and "体检" in PN.PAGE, "")
    check("⑥ 前端一行一项如实转述 + 三色结论行",
          "r.items.forEach" in PN.PAGE and "全部" in PN.PAGE and "项异常" in PN.PAGE, "")

    print(f"\n体检测试: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
