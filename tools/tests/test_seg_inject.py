# -*- coding: utf-8 -*-
"""seg_inject 文档指纹探测 + 注入自检单元测试 (v28.21+, 2026-09-19)

被锁死的缺陷 (doc_fp 世代漂移 -> 静默失效):
  同一段落在库里躺着**多代** doc_summary_fp (配置改动让摘要键漂移, 每代各存一行,
  见 cache.py `_DOC_SUMMARY_PARAM`)。`detect_fp` 用"投票取票数最多者"探测文档
  指纹, 新旧世代覆盖同样的段 -> **平票** -> 旧写法 `max(votes, key=votes.get)` 由
  字典插入序决定, 返回先查到的**旧世代**。接着 UPDATE 只落在被遮蔽的旧行上:
  本脚本照常打印"已更新 N 行"、结论 PASS, 而渲染器读的是新一代那一行 -> PDF
  毫无变化。实测 Zhang 2026: 38bc(旧, id 6693~6875) 与 6c71(新, id 6879~7065)
  各 5 票 -> 取到 38bc, 只有手工 `--fp 6c71...` 才生效。

修法 (本次):
  ① 投票单元改成 (票数, 该 fp 命中行的最大 id), 平票取"最后写入"的世代。
     依据: cache.set() 恒为 INSERT / ON CONFLICT REPLACE(不原地 UPDATE), 故 id
     单调 = 写入时序; 且渲染器回查时 ③④ 档索引正是"按 id 升序写覆盖" ——
     **探测的口径就是回查的口径**。
  ② 注入后自检: 回读该段 id 最大的那一行(`newest_row`), 译文没变就判 FAIL ——
     不再允许"报 OK 但渲染器读不到"。

本测试锁 7 段语义 (内存 sqlite + 合成侧车结构, 不碰真实 cache.v1.db):
  ① 平票 -> 取 id 大者 (旧写法取 id 小者 = 复现缺陷)
  ② 票数优先于行号 (覆盖更广的指纹不能被"更新"压过)
  ③ 判据是"最后写入"而非"名字看起来更新" —— 反向构造仍取 id 大者
  ④ 自检正例: 写进 id 最大行 -> newest_row 译文已是新译文
  ⑤ 自检反例: 写进被遮蔽旧行 -> newest_row 译文未变 (注入静默失效被抓住)
  ⑥ 该段无行 -> newest_row 返回 None (调用方不误判)
  ⑦ 无票 / 段过短不参与投票 -> None (回落 Cactaceae 默认的行为不变)

运行: venv python test_seg_inject.py, 退出码 0=全过
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import seg_inject as SJ  # noqa: E402  模块级只定义常量/正则, 导入不写库

FP_OLD = "docsummary:38bc38bc38bc38bc:1111aaaa"
FP_NEW = "docsummary:6c71c61ab046e9b9:f9ac3711"

# >= 80 字才参与投票 (detect_fp 的门槛), 且两段必须互不相同
LONG_A = ("Attention-based policy learning has become a standard recipe in "
          "vision-language-action modelling from human video.")
LONG_B = ("Contrastive latent action pretraining aligns the latent space of a "
          "policy with the temporal structure of demonstrations.")
SHORT = "Too short to vote."

OLD_TRANS = "旧译文"
NEW_TRANS = "新译文"


def new_db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE _translationcache ("
                "id INTEGER NOT NULL PRIMARY KEY, translate_engine VARCHAR(20), "
                "translate_engine_params TEXT, original_text TEXT, translation TEXT)")
    return con


def add_row(con, rid, text, fp, translation=OLD_TRANS):
    """行号自己给, 才能构造"谁后写"的各种组合。"""
    con.execute(
        "INSERT INTO _translationcache"
        " (id, translate_engine, translate_engine_params, original_text, translation)"
        " VALUES (?,?,?,?,?)",
        (rid, "pdf2zh",
         json.dumps({"service": "silicon", "doc_summary_fp": fp}, ensure_ascii=False),
         text, translation))


def pages_of(*raws):
    """单页多段: detect_fp 走 pages[page]["segs"][seg]["raw"]。"""
    return {1: {"segs": [{"raw": r} for r in raws]}}


def man_of(*seg_idx):
    return {"items": [{"parts": [{"page": 1, "seg": i}]} for i in seg_idx]}


def main():
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  PASS " + name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, detail))

    # ---- ① 平票: 旧世先后写先后各一行, 应取 id 大者 ----
    con = new_db()
    add_row(con, 1, LONG_A, FP_OLD)
    add_row(con, 2, LONG_A, FP_NEW)
    got = SJ.detect_fp(con.cursor(), pages_of(LONG_A), man_of(0))
    check("① 平票取最后写入的世代 (id 2)", got == FP_NEW, "得到 %s" % got)

    # ---- ② 票数优先: FP_OLD 两行 vs FP_NEW 一行(且 id 更大) ----
    con = new_db()
    add_row(con, 1, LONG_A, FP_OLD)
    add_row(con, 2, LONG_A, FP_OLD)
    add_row(con, 9, LONG_B, FP_NEW)
    got = SJ.detect_fp(con.cursor(), pages_of(LONG_A, LONG_B), man_of(0, 1))
    check("② 票数多者胜 (2 票的旧世代)", got == FP_OLD, "得到 %s" % got)

    # ---- ③ 判据是"最后写入", 不是"名字像新世代" ----
    con = new_db()
    add_row(con, 1, LONG_A, FP_NEW)
    add_row(con, 2, LONG_A, FP_OLD)
    got = SJ.detect_fp(con.cursor(), pages_of(LONG_A), man_of(0))
    check("③ 取 id 大者 (2), 与 fp 字面新旧无关", got == FP_OLD, "得到 %s" % got)

    # ---- ④⑤ 自检: 写进最新行 vs 写进被遮蔽行 ----
    con = new_db()
    add_row(con, 1, LONG_A, FP_OLD)   # 被遮蔽的旧世代
    add_row(con, 2, LONG_A, FP_NEW)   # 渲染器实际读到的一行
    cur = con.cursor()
    cur.execute("UPDATE _translationcache SET translation=? WHERE original_text=?"
                " AND translate_engine_params LIKE ?",
                (NEW_TRANS, LONG_A, "%" + FP_OLD + "%"))
    newest = SJ.newest_row(cur, LONG_A)
    check("⑤ 写进旧世代 -> 最新行译文未变 (自检可抓)", newest[0] == 2 and newest[1] == OLD_TRANS,
          repr(newest))
    cur.execute("UPDATE _translationcache SET translation=? WHERE original_text=?"
                " AND translate_engine_params LIKE ?",
                (NEW_TRANS, LONG_A, "%" + FP_NEW + "%"))
    newest = SJ.newest_row(cur, LONG_A)
    check("④ 写进最新世代 -> 最新行译文已更新", newest[0] == 2 and newest[1] == NEW_TRANS,
          repr(newest))
    check("④ 最新行带回该行的世代参数 (dry 自检据此判作用域)",
          FP_NEW in (newest[2] or ""), repr(newest[2]))

    # ---- ⑥ 该段无行 ----
    check("⑥ 无该段 -> newest_row 返回 None",
          SJ.newest_row(con.cursor(), "不存在的段") is None)

    # ---- ⑦ 无票 / 段过短 ----
    con = new_db()
    check("⑦ 空库 -> None (调用方回落默认)", SJ.detect_fp(con.cursor(), pages_of(LONG_A), man_of(0)) is None)
    add_row(con, 1, SHORT, FP_OLD)
    check("⑦ 段短于 80 字不参与投票 -> None",
          SJ.detect_fp(con.cursor(), pages_of(SHORT), man_of(0)) is None)

    print("\nseg_inject 文档指纹探测 + 注入自检单元测试: %d PASS / %d FAIL" % (passed, failed))
    sys.stdout.flush()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
