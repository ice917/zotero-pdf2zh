# -*- coding: utf-8 -*-
"""doubao_bridge 契约单元测试 (v28.3→v28.6, 2026-09-19)

被锁死的缺陷（都只在"别人电脑上只有豆包"时才会暴露）:

  ① 路径焊死本机: `INBOX = r"D:\\zotero-pdf2zh\\inbox"` —— 别的用户把项目装在
     别的盘符, 全部工具指向空目录。修法: 与 adopt/seg_export 共用
     P2Z_PROJ / P2Z_INBOX 环境变量约定。

  ② 交件命名与下游对不上: 桥按 `out/<name>` 原样落盘, 而 tools/adopt.py 的
     deliver 缺省只认 `out/<name>.<族>*.txt`(族见 tools/result_naming.py)。豆包
     最常见的提交名是 inbox 里那个原名 → 落成 `out/egophys2026.txt` → deliver
     认不到, 断在交件这一步。实测 2026-09-19 豆包日志: 历史上没断是因为**人在
     对话框里额外叮嘱了** "存成 egophys2026.doubao.txt" —— 依赖人记得说, 不是契约。
     修法: 落盘前统一规范成 `<stem>.<族>.txt`(已带族+轮次的原样保留)。
     [v28.68] 缺省族由 `doubao` 改 `webai`(粘贴通道不绑厂商); 旧的 `.doubao*`
     一个字节不动、照样被认 —— 改名断掉在跑的论文是本末倒置。

  ③ [v28.4] 豆包看不见问题: 桥只有 inbox/out 两个视野, 而"哪儿错了"写在
     server/translated/review/*.md 里。豆包因此只能整篇重抄、不能矫正。
     修法: 加 list_reports / get_report 两个**只读**工具(默认路径与
     tools/post_check.py 的 review_dir() 同源)。

  ④ [v28.6] 豆包看不见自己上一版: 能读 review/(问题清单)了, 但读不到 out/ 里
     自己交出去的稿子 —— 于是每一次"按报告改"实际都退化成整篇重译。实测
     wang2026: doubao4 与上一版 doubao3 在 266 段里有 206 段不同, 并因此新踩
     4 处公式字形块, 被 import 门禁整篇退回。修法: 加 list_results / get_result
     两个**只读**工具, 并在 submit_result 回执里附上与上一版的改动段数
     (改动面过大即提示"你在重译", 让这件事对豆包自己可见)。

本测试锁五点:
  ① `_result_name` 的规范化边界(原名/带序号/无扩展名/多段扩展名) + `_stem_of` 取篇名
     + v28.68 新旧两族的互认(新件缺省 webai / 旧件 .doubao* 原样保留)
  ② 真跑一次 STDIO JSON-RPC 往返: 十一工具齐全 + 交件落到规范名 + 缺件报错不崩
     + 未知方法回 -32601 + stdout 洁净(日志不许混进协议通道)
     + merge_result 只动点到段(未点到段逐字不变) / 名字歧义与段号不存在都当场报错
     + get_payload 的 from_seg/to_seg 只取那一段号区间(分批翻)
     + start_delivery 开底稿 / 拒覆盖已有交件 / force 重开
     + selfcheck 空骨架判 FAIL、填满后判 PASS(判据与 deliver 同源)
  ③ 全流程锁在 P2Z_PROJ 沙箱内 —— 真项目的 inbox/out 一个字节都不碰
  ④ report 两工具: 判定行抽取 / kind 过滤 / limit 截断 / 子串定位 / 歧义报错 /
     路径穿越名进不来
  ⑤ results 两工具: 只认交件(两族都算, 中间产物不混进来) / 段数与时间 /
     子串定位 / 歧义报错 / 路径穿越名进不来 / 交件回执的改动量自检

运行: venv python test_doubao_bridge.py, 退出码 0=全过
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
BRIDGE = os.path.join(TOOLS, "doubao_bridge.py")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import doubao_bridge as BR  # noqa: E402

PAYLOAD = "#S1\nFirst paragraph of the paper.\n#S2\nSecond paragraph with [1] citation.\n"
# 第二轮: 只在 #S2 加了半句 = 1/2 段有改动。2 段时该比例必然越过 30% 阈值,
# 所以这一条同时也锁住了"改动面过大"的分支。
PAYLOAD2 = "#S1\nFirst paragraph of the paper.\n#S2\nSecond paragraph with [1] citation, revised.\n"
# 分批交件用: 含一个**跨页合并段**(#S2 正文里带 ⋮)。两段都 >=60 字符,
# 才会进长度比自查; 数字/[n] 引用也都留着, 好让 selfcheck 的逐段不变量真跑起来。
PAYLOAD_Z = ("#S1\n"
             "The optimizer reduces the gradient storage requirement substantially "
             "for large scale training runs [1].\n"
             "#S2\n"
             "First half of the merged paragraph describing the method.\n"
             "⋮\n"
             "Second half mentions 42 layers and also [2] citations for completeness.\n")
# 填满骨架后的样子(段号守恒 / ⋮ 数量对得上 / 无留空半截 / 数字与 [n] 一个不差)
FILLED_Z = ("#S1\n"
            "该优化器在大规模训练中大幅降低了梯度存储需求 [1]，使显存占用显著下降，"
            "训练成本随之降低。\n"
            "#S2\n"
            "这是合并段的前半部分，描述方法本身。\n"
            "⋮\n"
            "后半部分提到 42 层，并为完整性引用了 [2]。\n")


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

    # ---- ① 交件命名规范化 (v28.68: 缺省族 webai, 旧族 doubao 继续认) ----
    check("① 原名补 .webai", BR._result_name("egophys2026.txt") == "egophys2026.webai.txt",
          BR._result_name("egophys2026.txt"))
    check("① payload 原名补 .webai",
          BR._result_name("payload_p2_p4.txt") == "payload_p2_p4.webai.txt",
          BR._result_name("payload_p2_p4.txt"))
    check("① .webai 不叠成 .webai.webai",
          BR._result_name("wang2026.webai.txt") == "wang2026.webai.txt",
          BR._result_name("wang2026.webai.txt"))
    check("① .webai2 保留(多轮定稿)",
          BR._result_name("wang2026.webai2.txt") == "wang2026.webai2.txt",
          BR._result_name("wang2026.webai2.txt"))
    # 旧族一路照旧: 改名不许把在跑的论文断链(旧件原样保留 = out/ 里不凭空多出候选)
    check("① 旧件 .doubao 原样保留(不叠成 .webai.doubao)",
          BR._result_name("wang2026.doubao.txt") == "wang2026.doubao.txt",
          BR._result_name("wang2026.doubao.txt"))
    check("① 旧件 .doubao2 保留",
          BR._result_name("wang2026.doubao2.txt") == "wang2026.doubao2.txt",
          BR._result_name("wang2026.doubao2.txt"))
    check("① 旧件 .doubao3 保留",
          BR._result_name("wang2026.doubao3.txt") == "wang2026.doubao3.txt",
          BR._result_name("wang2026.doubao3.txt"))
    check("① 无扩展名 → .webai.txt",
          BR._result_name("roundtrip_test") == "roundtrip_test.webai.txt",
          BR._result_name("roundtrip_test"))
    check("① 多段扩展名取最后一段",
          BR._result_name("paper.v2.txt") == "paper.v2.webai.txt",
          BR._result_name("paper.v2.txt"))
    check("① 非 .txt 也统一成 .txt(下游只认 .txt)",
          BR._result_name("note.md") == "note.webai.txt", BR._result_name("note.md"))
    check("① 规范化后仍能通过文件名白名单",
          BR._safe_name(BR._result_name("payload p2.txt")) == "payload p2.webai.txt")

    # ---- ①b 篇名归一: 交件名/载荷原名/run 名 都该归到同一个 stem ----
    check("① _stem_of 从交件名取篇名(新族)",
          BR._stem_of("payload_x.webai2.txt") == "payload_x", BR._stem_of("payload_x.webai2.txt"))
    check("① _stem_of 从交件名取篇名(旧族)",
          BR._stem_of("payload_x.doubao2.txt") == "payload_x", BR._stem_of("payload_x.doubao2.txt"))
    check("① _stem_of 从载荷原名取篇名",
          BR._stem_of("payload_x.txt") == "payload_x", BR._stem_of("payload_x.txt"))
    check("① _stem_of 从 run 名取篇名",
          BR._stem_of("payload_x") == "payload_x", BR._stem_of("payload_x"))
    check("① _stem_of 四条入口归到同一个篇名(否则 selfcheck 找不到载荷)",
          BR._stem_of("payload_x.webai2.txt") == BR._stem_of("payload_x.doubao2.txt")
          == BR._stem_of("payload_x.txt") == BR._stem_of("payload_x"))

    # ---- ②/③ 真 STDIO 往返, 全程锁在沙箱 ----
    tmp = tempfile.mkdtemp(prefix="p2z_bridge_")
    try:
        proj = os.path.join(tmp, "proj")
        inbox, outdir = os.path.join(proj, "inbox"), os.path.join(proj, "out")
        os.makedirs(inbox)
        with open(os.path.join(inbox, "payload_x.txt"), "w", encoding="utf-8") as f:
            f.write(PAYLOAD)
        # 分批交件(骨架)那条线用的载荷: 含跨页合并段, 且**没有** manifest ——
        # 顺带锁住 selfcheck 在无 manifest 时退回"载荷自身 ⋮ 计数"的那条分支。
        with open(os.path.join(inbox, "payload_z.txt"), "w", encoding="utf-8") as f:
            f.write(PAYLOAD_Z)
        # 手工放一份"上一版交件": 作为 merge_result 的合稿对象。特意不靠 submit_result
        # 现场生成 —— 那样它就与 ② 段里"交件逐字一致"的读盘断言互相干扰。
        os.makedirs(outdir)
        with open(os.path.join(outdir, "payload_y.doubao.txt"), "w", encoding="utf-8") as f:
            f.write(PAYLOAD)

        # 默认报告路径必须是 <PROJ>/server/translated/review —— 与 post_check.review_dir()
        # 同源。这里特意不设 P2Z_REVIEW, 走的就是缺省推导那条路。
        rev_default = os.path.join(proj, "server", "translated", "review")
        os.makedirs(rev_default)
        with open(os.path.join(rev_default, "翻译后质检_20260919_104922_Vaswani_等.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 翻译后质检报告（门禁）\n\n- **门禁判定: FAIL**（存在高危问题，需处理后交付）\n")

        # 通知(notifications/initialized)必须不带 id —— 带 id 会被当未知方法回 -32601,
        # 后续读取整体错位一格(2026-09-19 自检脚本踩过)。
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "get_payload", "arguments": {"name": "payload_x.txt"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "submit_result",
                        "arguments": {"name": "payload_x.txt", "text": PAYLOAD}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "get_payload", "arguments": {"name": "no_such.txt"}}},
            {"jsonrpc": "2.0", "id": 6, "method": "no/such/method"},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "list_reports", "arguments": {}}},
            # 第二轮交件: 只改 #S2 一段 —— 用来锁"回执带上一版改动量"
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
             "params": {"name": "submit_result",
                        "arguments": {"name": "payload_x.doubao2.txt", "text": PAYLOAD2}}},
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
             "params": {"name": "list_results", "arguments": {"name": "payload_x"}}},
            # merge_result (2026-09-20): 只改几段时的增量通道。三条分别锁
            # 正常合稿 / 名字歧义 / 补丁里段号在交件里不存在
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
             "params": {"name": "merge_result",
                        "arguments": {"name": "payload_y.doubao.txt",
                                      "patch": "#S2\nSecond paragraph, merged.\n"}}},
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
             "params": {"name": "merge_result",
                        "arguments": {"name": "payload_x", "patch": "#S1\nx\n"}}},
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call",
             "params": {"name": "merge_result",
                        "arguments": {"name": "payload_y.doubao.txt",
                                      "patch": "#S99\nnope\n"}}},
            # get_payload 分批读 (2026-09-20 D): 只取 #S2 那一段
            {"jsonrpc": "2.0", "id": 13, "method": "tools/call",
             "params": {"name": "get_payload",
                        "arguments": {"name": "payload_x.txt", "from_seg": 2, "to_seg": 2}}},
            # start_delivery (D): 开底稿 -> 再开必须被拒 -> force 才重开
            {"jsonrpc": "2.0", "id": 14, "method": "tools/call",
             "params": {"name": "start_delivery", "arguments": {"name": "payload_z"}}},
            {"jsonrpc": "2.0", "id": 15, "method": "tools/call",
             "params": {"name": "start_delivery", "arguments": {"name": "payload_z"}}},
            {"jsonrpc": "2.0", "id": 16, "method": "tools/call",
             "params": {"name": "start_delivery",
                        "arguments": {"name": "payload_z", "force": True}}},
            # selfcheck (B): 空骨架必须判 FAIL; 填满后必须判 PASS
            {"jsonrpc": "2.0", "id": 17, "method": "tools/call",
             "params": {"name": "selfcheck", "arguments": {"name": "payload_z"}}},
            {"jsonrpc": "2.0", "id": 18, "method": "tools/call",
             "params": {"name": "selfcheck",
                        "arguments": {"name": "payload_z", "text": FILLED_Z}}},
        ]
        env = dict(os.environ)
        env["P2Z_PROJ"] = proj
        env["P2Z_INBOX"] = inbox
        proc = subprocess.run(
            [sys.executable, BRIDGE],
            input="".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reqs),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)

        check("② 进程正常退出", proc.returncode == 0, (proc.returncode, proc.stderr[-300:]))
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        check("② stdout 每行都是合法 JSON(日志没混进协议通道)",
              all(_is_json(ln) for ln in lines), lines[:3])
        answers = {}
        for ln in lines:
            m = _try_json(ln)
            if m is not None and "id" in m:
                answers[m["id"]] = m
        check("② 十八条请求都有应答",
              sorted(answers) == list(range(1, 19)), sorted(answers))

        check("② initialize 自报桥名",
              answers[1]["result"]["serverInfo"]["name"] == "pdf2zh-bridge", answers[1])
        names = [t["name"] for t in answers[2]["result"]["tools"]]
        check("② 十一工具齐全",
              names == ["list_inbox", "get_payload", "list_reports", "get_report",
                        "list_results", "get_result", "submit_result", "merge_result",
                        "start_delivery", "selfcheck", "search_term"],
              names)
        schema = {t["name"]: t["inputSchema"] for t in answers[2]["result"]["tools"]}
        check("② get_payload 声明了 from_seg/to_seg(分批读是契约, 不是暗号)",
              {"from_seg", "to_seg"} <= set(schema["get_payload"]["properties"]), schema["get_payload"])
        check("② start_delivery 只需 name, force 可选",
              schema["start_delivery"]["required"] == ["name"]
              and "force" in schema["start_delivery"]["properties"], schema["start_delivery"])
        check("② selfcheck 只需 name, text 可选",
              schema["selfcheck"]["required"] == ["name"]
              and "text" in schema["selfcheck"]["properties"], schema["selfcheck"])
        check("② get_payload 取回原文",
              PAYLOAD == answers[3]["result"]["content"][0]["text"],
              answers[3]["result"]["content"][0]["text"][:80])

        # ---- 缺陷② 的直接锁: 交件必须落在 deliver 认得到的名字上 ----
        check("② 交件落到 <原名>.webai.txt",
              os.path.isfile(os.path.join(outdir, "payload_x.webai.txt")),
              sorted(os.listdir(outdir)))
        check("② 交件没有裸落成原名",
              not os.path.exists(os.path.join(outdir, "payload_x.txt")),
              sorted(os.listdir(outdir)))
        with open(os.path.join(outdir, "payload_x.webai.txt"), encoding="utf-8") as f:
            check("② 交件内容逐字一致", f.read() == PAYLOAD)
        check("② 交件回执含段号统计",
              "#S: 1,2" in answers[4]["result"]["content"][0]["text"],
              answers[4]["result"]["content"][0]["text"])

        # ---- 缺陷④ 的直接锁: 交件时告诉豆包"这一轮改了多少" ----
        r4 = answers[4]["result"]["content"][0]["text"]
        check("② 首轮交件没有上一版 → 回执不带差异行", "与上一版" not in r4, r4)
        r8 = answers[8]["result"]["content"][0]["text"]
        check("② 次轮回执带与上一版的改动段数(上一版是新族那件)",
              "与上一版 payload_x.webai.txt 相比: 1/2 段有改动" in r8, r8)
        check("② 改动面越过阈值时提示'在重译'", "改动面过大" in r8, r8)
        r9 = answers[9]["result"]["content"][0]["text"]
        check("⑤ list_results 列出历轮(两族并列)",
              "payload_x.doubao2.txt" in r9 and "payload_x.webai.txt" in r9, r9)
        check("⑤ list_results 带段数", "2 段" in r9, r9)

        # ---- merge_result 的直接锁: 只改点到段 / 未点到段逐字不变 / 两类拒绝 ----
        merged_path = os.path.join(outdir, "payload_y.doubao.txt")
        r10 = answers[10]["result"]["content"][0]["text"]
        with open(merged_path, encoding="utf-8") as f:
            merged = f.read()
        check("② merge_result 回执报合入段数",
              "已按段号合入 1 段" in r10 and "一个字节都没动" in r10, r10)
        check("② merge_result 只换了点到的段(#S2)",
              merged == "#S1\nFirst paragraph of the paper.\n"
                        "#S2\nSecond paragraph, merged.\n", merged)
        check("② merge_result 未点到的段逐字不变(含段间空行)",
              merged.startswith(PAYLOAD.split("#S2")[0]), merged)
        state_after = merged
        check("② merge_result 名字歧义 → 报错并列候选",
              answers[11]["result"]["isError"] is True
              and "2 份交件" in answers[11]["result"]["content"][0]["text"],
              answers[11])
        check("② merge_result 补丁段号不在交件里 → 报错",
              answers[12]["result"]["isError"] is True
              and "没有的段号" in answers[12]["result"]["content"][0]["text"]
              and "#S99" in answers[12]["result"]["content"][0]["text"],
              answers[12])
        with open(merged_path, encoding="utf-8") as f:
            check("② merge_result 被拒时交件原封不动", f.read() == state_after, "")

        # ---- get_payload 分批读: 一轮只看几百段, 不把全篇挂在上下文里 ----
        r13 = answers[13]["result"]["content"][0]["text"]
        check("② 分批读只含所取区间(#S2), 不含区外段(#S1)",
              "#S2" in r13 and "#S1" not in r13, r13[:200])
        check("② 分批读带上批注(哪一批、几段、配合哪个工具)",
              "本批" in r13 and "start_delivery" in r13, r13[:200])
        check("② 分批读的正文与原文逐字一致",
              "Second paragraph with [1] citation." in r13, r13)

        # ---- start_delivery: 开底稿(骨架) -> 拒覆盖 -> force 重开 ----
        r14 = answers[14]["result"]["content"][0]["text"]
        skel_path = os.path.join(outdir, "payload_z.webai.txt")
        check("② start_delivery 开出底稿文件", os.path.isfile(skel_path),
              sorted(os.listdir(outdir)))
        with open(skel_path, encoding="utf-8") as f:
            skel = f.read()
        check("② 骨架段号全立齐、正文全空",
              skel.count("#S1") == 1 and skel.count("#S2") == 1
              and "optimizer" not in skel and "merged paragraph" not in skel, skel)
        check("② 骨架保留 ⋮(丢了会被交付侧判「断点不符」)", skel.count("⋮") == 1, skel)
        check("② 回执报总段数与跨页合并段数",
              "段号 2 个" in r14 and "跨页合并段 1 个" in r14, r14)
        check("② 回执指明分批交件动线(get_payload -> merge_result -> selfcheck)",
              "merge_result" in r14 and "selfcheck" in r14, r14)

        check("② 已有交件 → 拒绝覆盖(骨架是底稿, 不许冲掉改好的稿子)",
              answers[15]["result"]["isError"] is True
              and "已存在" in answers[15]["result"]["content"][0]["text"],
              answers[15])
        with open(skel_path, encoding="utf-8") as f:
            check("② 被拒时底稿一个字节不动", f.read() == skel, "")
        check("② force=true 才重开",
              answers[16]["result"]["isError"] is False
              and "已开一版交件底稿" in answers[16]["result"]["content"][0]["text"],
              answers[16])

        # ---- selfcheck: 交件前自己过一遍, 判据与 deliver 同源 ----
        r17 = answers[17]["result"]["content"][0]["text"]
        check("② selfcheck 空骨架 → 判 FAIL 并点名留空的段",
              "结论: ❌" in r17 and "#S1" in r17 and "#S2" in r17, r17)
        check("② selfcheck 报出期望段号取自哪里(无 manifest 时退回载荷 ⋮ 口径)",
              "载荷自身的 ⋮ 计数" in r17, r17)
        r18 = answers[18]["result"]["content"][0]["text"]
        check("② selfcheck 填满后 → 判 PASS, 可以交",
              "结论: ✅" in r18, r18)
        check("② selfcheck 逐项 PASS(段号守恒/⋮ 对账/不变量), 一项 FAIL 都没有",
              r18.count("PASS") >= 3 and "FAIL" not in r18, r18)
        check("② selfcheck 报出长度比分布(与门禁同一份实现)",
              "长度比" in r18 and "长段 2 个" in r18, r18)
        with open(skel_path, encoding="utf-8") as f:
            check("② selfcheck 只读不写(底稿没被它改过)", f.read() == skel, "")

        check("② 缺件走 isError 不崩",
              answers[5]["result"]["isError"] is True
              and "不存在" in answers[5]["result"]["content"][0]["text"], answers[5])
        check("② 未知方法回 -32601",
              answers[6].get("error", {}).get("code") == -32601, answers[6])
        # 缺省 REVIEW 未设环境变量 → 必须自己推到 <PROJ>/server/translated/review
        check("④ 缺省报告路径 = <PROJ>/server/translated/review",
              "门禁判定: FAIL" in answers[7]["result"]["content"][0]["text"],
              answers[7]["result"]["content"][0]["text"][:120])

        # ---- ③ 沙箱隔离 ----
        check("③ inbox/out 全在沙箱内",
              os.path.isfile(os.path.join(inbox, "payload_x.txt"))
              and os.path.isfile(os.path.join(outdir, "payload_x.webai.txt")))
        check("③ 真项目 out/ 未被写入",
              not os.path.isfile(os.path.join(
                  os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh"),
                  "out", "payload_x.webai.txt")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ④ 报告两工具: 判定行抽取 / 过滤 / 截断 / 定位 / 越权 ----
    tmp2 = tempfile.mkdtemp(prefix="p2z_review_")
    real_review = BR.REVIEW
    try:
        rev = os.path.join(tmp2, "review")
        os.makedirs(rev)
        fixtures = (
            ("翻译后质检_20260919_104922_Vaswani_等.md",
             "# 翻译后质检报告（门禁）\n\n- 译文: `Vaswani …-mono.pdf`\n"
             "- **门禁判定: FAIL**（存在高危问题，需处理后交付）\n\n## 断言结果\n\n"
             "- 🔴 **[高]** 第11,12页：文献区汉化断言失败：原文的参考文献条目在译文中被翻译成中文\n"),
            ("翻译后质检_20260919_104949_Proximity_Modeling.md",
             "# 翻译后质检报告（门禁）\n\n- **门禁判定: PASS**\n"),
            ("翻译前体检_20260919_110000_Wang_等.md",
             "# 翻译前体检报告\n\n- 总页数: 19\n- 推荐 skipLastPages: **0**\n"),
            ("审校报告_20260919_090000_EgoPhys.md", "# 审校报告\n\n- 无可疑段落\n"),
            ("门禁拒收_20260920_114230_Sokolov_等.md",
             "# 门禁拒收：Sokolov 等\n\n"
             "- 门禁判定: FAIL（交件未通过 deliver 内容门禁，已拒收；任务判失败，现场保留）\n"
             "- 拒收条目: 留空 0 段 · 只译半截 30 段 · 错位带 1 条 · 逐段不变量 79 条\n"),
        )
        for fn, body in fixtures:
            with open(os.path.join(rev, fn), "w", encoding="utf-8") as f:
                f.write(body)
        BR.REVIEW = rev

        txt = BR.tool_list_reports({})
        check("④ 缺省列全部类型(一类都不藏)",
              all(("「%s」" % k) in txt for k in BR.REPORT_KINDS), txt[:300])
        check("④ 缺省可见『门禁拒收』—— 交件被拒时豆包要照的就是它",
              "门禁拒收_20260920_114230" in txt and "已拒收" in txt, txt[:300])
        check("④ 判定随列表返回: FAIL 可见", "门禁判定: FAIL" in txt, txt)
        check("④ 判定随列表返回: PASS 可见", "门禁判定: PASS" in txt, txt)
        check("④ 结论行不带 markdown 前缀", "- **门禁判定" not in txt, txt)

        pre = BR.tool_list_reports({"kind": "翻译前体检"})
        check("④ kind 可切到体检报告", "推荐 skipLastPages: **0**" in pre, pre[:160])
        check("④ 切 kind 后不混门禁报告", "门禁判定" not in pre, pre[:160])
        gate = BR.tool_list_reports({"kind": "门禁拒收"})
        check("④ kind=门禁拒收 可直达, 不再被缺省藏起",
              "门禁拒收_20260920_114230" in gate and "翻译后质检" not in gate, gate[:200])
        check("④ 未知 kind 报错不崩",
              _raises(lambda: BR.tool_list_reports({"kind": "不存在的类型"})))

        one = BR.tool_list_reports({"kind": "翻译后质检", "limit": 1})
        check("④ limit=1 只列 1 份", one.count(".md") == 1, one)
        check("④ limit 截断时提示还剩几份", "另有 1 份" in one, one)

        body = BR.tool_get_report({"name": "翻译后质检_20260919_104922_Vaswani_等.md"})
        check("④ get_report 读回全文(含问题清单)", "第11,12页" in body, body[:80])
        check("④ 门禁拒收明细可读回(逐段条目在正文里)",
              "只译半截 30 段" in BR.tool_get_report(
                  {"name": "门禁拒收_20260920_114230_Sokolov_等.md"}), "")
        check("④ 唯一子串可定位", "PASS" in BR.tool_get_report({"name": "104949"}))
        check("④ 空 name 报错不崩", _raises(lambda: BR.tool_get_report({})))
        check("④ 子串命中多份 → 报错并列候选",
              _raises(lambda: BR.tool_get_report({"name": "翻译"})))
        check("④ 不存在的报告 → 报错",
              _raises(lambda: BR.tool_get_report({"name": "nope.md"})))
        check("④ 路径穿越名进不来",
              _raises(lambda: BR.tool_get_report({"name": r"..\..\payload_x.txt"})))

        BR.REVIEW = os.path.join(tmp2, "nope")     # 目录不存在 —— 首跑还没生成报告
        empty = BR.tool_list_reports({})
        check("④ 报告目录缺失时逐类报空不崩",
              all(("「%s」: 0 份" % k) in empty for k in BR.REPORT_KINDS), empty[:200])
    finally:
        BR.REVIEW = real_review
        shutil.rmtree(tmp2, ignore_errors=True)

    # ---- ⑤ results 两工具: 只认交件 / 倒序 / 定位 / 越权 / 改动量自检 ----
    tmp3 = tempfile.mkdtemp(prefix="p2z_out_")
    real_out = BR.OUTDIR
    try:
        outd = os.path.join(tmp3, "out")
        os.makedirs(outd)
        # 复刻真实 out/ 的样子: 交件与 seg_import/seg_inject 的中间产物混在一起
        for fn, body, age in (
            ("wang2026.doubao.txt", "#S1\n最旧的一稿\n#S2\n旧2\n", 300),
            ("wang2026.doubao3.txt", "#S1\n上一版\n#S2\n上一版2\n#S3\n上一版3\n", 60),
            ("zhang2026.doubao.txt", "#S1\n别篇\n", 120),
            ("wang2026.imported.json", '{"1#1": "x"}', 30),
            ("wang2026.merged.txt", "#S1\n中间产物\n", 20),
        ):
            p = os.path.join(outd, fn)
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            old = time.time() - age
            os.utime(p, (old, old))          # 显式定死 mtime, 不靠写入顺序
        BR.OUTDIR = outd

        txt = BR.tool_list_results({})
        check("⑤ 只列交件(两族都算, 中间产物不混入)",
              "imported.json" not in txt and ".merged.txt" not in txt, txt)
        check("⑤ 段数随列表返回", "3 段" in txt and "1 段" in txt, txt)
        check("⑤ 缺省按时间倒序(上一版在前)",
              txt.index("wang2026.doubao3.txt") < txt.index("zhang2026.doubao.txt")
              < txt.index("wang2026.doubao.txt"), txt)

        only = BR.tool_list_results({"name": "wang2026"})
        check("⑤ name 只筛该篇", "zhang2026" not in only and "doubao3" in only, only)
        check("⑤ 篇名大小写不敏感", "doubao3" in BR.tool_list_results({"name": "WANG2026"}))

        one = BR.tool_list_results({"limit": 1})
        check("⑤ limit=1 只列 1 份", one.count(".txt") == 1, one)
        check("⑤ limit 截断时提示还剩几份", "另有 2 份" in one, one)

        body = BR.tool_get_result({"name": "wang2026.doubao3.txt"})
        check("⑤ get_result 读回上一版全文", "上一版3" in body, body[:80])
        check("⑤ 唯一子串可定位", "上一版3" in BR.tool_get_result({"name": "doubao3"}))
        check("⑤ 空 name 报错不崩", _raises(lambda: BR.tool_get_result({})))
        check("⑤ 子串命中多份 → 报错并列候选",
              _raises(lambda: BR.tool_get_result({"name": "wang2026"})))
        check("⑤ 不存在的交件 → 报错",
              _raises(lambda: BR.tool_get_result({"name": "nope.doubao.txt"})))
        check("⑤ 路径穿越名进不来",
              _raises(lambda: BR.tool_get_result({"name": r"..\..\payload_x.txt"})))
        check("⑤ 中间产物读不到(它不该被当成上一版)",
              _raises(lambda: BR.tool_get_result({"name": "wang2026.imported.json"})))

        # 改动量自检的另一半: 小改动必须**不**报警 —— 否则提示会被当噪音忽略
        ten = "".join("#S%d\n原先第 %d 段\n" % (i, i) for i in range(1, 11))
        ten2 = ten.replace("#S10\n原先第 10 段", "#S10\n改过的第 10 段")
        BR.tool_submit_result({"name": "diffy.doubao.txt", "text": ten})
        r = BR.tool_submit_result({"name": "diffy.doubao2.txt", "text": ten2})
        check("⑤ 小改动报段数但不提示重译",
              "1/10 段有改动" in r and "改动面过大" not in r, r)

        BR.OUTDIR = os.path.join(tmp3, "nope")     # 目录不存在 —— 首跑还没人交件
        check("⑤ out 目录缺失时只报空不崩",
              "没有任何交件" in BR.tool_list_results({}), BR.tool_list_results({}))
    finally:
        BR.OUTDIR = real_out
        shutil.rmtree(tmp3, ignore_errors=True)

    print("\n结果: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


def _try_json(line):
    try:
        return json.loads(line)
    except ValueError:
        return None


def _is_json(line):
    return _try_json(line) is not None


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
