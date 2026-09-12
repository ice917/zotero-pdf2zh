# -*- coding: utf-8 -*-
"""军师层单元测试: 锚点校验 / JSON 提取 / 图例 / 分块预算 / 页边界 (纯函数, 不触网不入库)"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategist import (
    tokens_ok, extract_json_array, canon_remap, legend_of, load_segments,
    droppable_of, is_junk_glyph, build_chunk_text, PAGE_BREAK_MARK,
)


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

    raw = "根据该遗传学观点{v26}，自交个体平均{v27}拥有{v28}三个成功的"

    # 锚点校验
    check("锚点: 占位符一致", tokens_ok(raw, "根据该观点{v26}，平均{v27}拥有{v28}三个成功的"))
    check("锚点: 无占位符段", tokens_ok("普通段落", "普通段落的译文"))
    check("锚点: 少一个占位符拒绝", not tokens_ok(raw, "根据该观点{v26}，平均{v27}拥有三个成功的"))
    check("锚点: 多一个占位符拒绝", not tokens_ok(raw, "根据{v30}该观点{v26}，平均{v27}拥有{v28}三个成功的"))
    check("锚点: 编号改动拒绝", not tokens_ok(raw, "根据该遗传学观点{v26}，自交个体平均{v27}拥有{v29}三个成功的"))

    # JSON 提取
    check("JSON: 纯数组", extract_json_array('[{"idx": 3, "revised": "x"}]') == [{"idx": 3, "revised": "x"}])
    check("JSON: 围栏包裹", extract_json_array('```json\n[{"idx": 1}]\n```') == [{"idx": 1}])
    check("JSON: 前后闲话", extract_json_array('好的，以下是修正：\n[{"idx": 2}]\n希望有帮助') == [{"idx": 2}])
    check("JSON: 非法返回 None", extract_json_array("我找不到问题") is None)
    check("JSON: 截断返回 None", extract_json_array('[{"idx": 3, "rev') is None)

    # canon 重排 (v24b.1 写库前必须: current 序号 -> canon 序号)
    seq = {"{v5}": "{v0}", "{v2}": "{v1}"}
    check("canon: 基本重排", canon_remap("甲{v5}乙{v2}丙", seq) == "甲{v0}乙{v1}丙")
    check("canon: 乱序current重排", canon_remap("乙{v2}甲{v5}", seq) == "乙{v1}甲{v0}")
    check("canon: 未知token原样保留", canon_remap("甲{v9}", seq) == "甲{v9}")
    check("canon: 无token原样", canon_remap("普通段落", seq) == "普通段落")
    check("canon: 重复token同映射", canon_remap("甲{v5}乙{v5}", seq) == "甲{v0}乙{v0}")

    # [v24b.2] 占位符图例 (字形盲区的零成本替代: {vN} 背后是什么)
    check("图例: 命名字形", legend_of(
        {"trans": "这种{v6}：{v7}优势{v9}", "vars": {"6": "3", "7": "2", "9": ""}})
        == "{v6}=3 {v7}=2")
    check("图例: 无vars返回空", legend_of({"trans": "甲{v6}乙"}) == "")
    check("图例: 只列译文用到的",
          legend_of({"trans": "甲{v3}", "vars": {"3": "x", "8": "y"}}) == "{v3}=x")
    check("图例: 按序号排序",
          legend_of({"trans": "甲{v10}乙{v2}", "vars": {"2": "a", "10": "b"}})
          == "{v2}=a {v10}=b")
    check("图例: 纯标点噪声过滤",
          legend_of({"trans": "甲{v1}乙{v2}丙{v3}",
                     "vars": {"1": ",", "2": "3", "3": ")."}}) == "{v2}=3")
    check("图例: 长值截断",
          legend_of({"trans": "甲{v1}", "vars": {"1": "x" * 50}})
          == "{v1}=" + "x" * 30 + "…")

    # [v24b.2] 侧车载入: 图例按页携带 (同号跨页含义可不同)
    import json as _json, tempfile
    fd, _p = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(_p, "w", encoding="utf-8") as f:
        f.write(_json.dumps({"page": 1, "pageid": 1, "vars": {"1": "3"},
                             "segs": [{"raw": "a{v1}", "trans": "甲{v1}"}]},
                            ensure_ascii=False) + "\n")
        f.write(_json.dumps({"page": 2, "pageid": 2,
                             "segs": [{"raw": "b", "trans": "乙"}]},
                            ensure_ascii=False) + "\n")
        f.write(_json.dumps({"pageid": 3,      # [v26-L1] 老侧车: 无 page 键
                             "segs": [{"raw": "c", "trans": "丙"}]},
                            ensure_ascii=False) + "\n")
    _segs = load_segments(_p)
    os.remove(_p)
    check("载入: 段seq连续", [s["seq"] for s in _segs] == [0, 1, 2])
    check("载入: 图例随页携带",
          _segs[0]["vars"] == {"1": "3"} and _segs[1]["vars"] == {})
    check("载入: 页号随行导出(缺省None)", [s["page"] for s in _segs] == [1, 2, None])

    # [v26-L2] 锚点子集语义: 只对"排版残渣"占位符放宽为可删
    d3 = "甲{v6}乙{v7}丙{v9}丁"
    check("锚点: droppable 内可删", tokens_ok(d3, "甲{v6}乙丙{v9}丁", droppable={"{v7}"}))
    check("锚点: droppable 外不可删", not tokens_ok(d3, "甲乙{v7}丙{v9}丁", droppable={"{v7}"}))
    check("锚点: droppable 空集等于严格", not tokens_ok(d3, "甲{v6}乙丙{v9}丁", droppable=set()))
    check("锚点: droppable 仍禁新增",
          not tokens_ok(d3, "甲{v6}乙{v7}丙{v9}丁{v1}", droppable={"{v7}"}))
    check("锚点: droppable 仍禁复制",
          not tokens_ok(d3, "甲{v6}{v6}乙{v7}丙{v9}丁", droppable={"{v7}"}))
    check("锚点: allow_drop 优先于 droppable",
          tokens_ok(d3, "甲{v6}乙丙丁", allow_drop=True, droppable={"{v7}"}))

    # [v26-L2] 排版残渣字形判定 (与渲染层 _strip/_split 的删除集对齐)
    check("渣字形: 连字符族", all(is_junk_glyph(c) for c in ("-", "\u00ad", "\u2013")))
    check("渣字形: 句读与圆括号", all(is_junk_glyph(c) for c in (".", ",", ";", ":", "(", ")")))
    check("渣字形: 混合残渣", is_junk_glyph(").") and is_junk_glyph("-("))
    check("渣字形: 数字不收", not is_junk_glyph("3") and not is_junk_glyph("2"))
    check("渣字形: 方括号引用标号不收", not is_junk_glyph("[12]"))
    check("渣字形: 英文不收", not is_junk_glyph("Arias") and not is_junk_glyph("et al."))
    check("渣字形: 空白/空串是渣", is_junk_glyph(" ") and is_junk_glyph(""))
    check("渣字形: cid 解码", is_junk_glyph("(cid:45)") and not is_junk_glyph("(cid:51)"))

    seg_d = {"trans": "甲{v6}乙{v7}丙{v9}丁",
             "vars": {"6": "3", "7": "-", "9": "(cid:41)"}}
    check("可删集: 只收残渣(数字不收)", droppable_of(seg_d) == {"{v7}", "{v9}"})
    check("可删集: 无vars退回严格", droppable_of({"trans": "甲{v6}"}) == set())

    # [v26-L1] 送审文本组装: 段号 / 图例 / 可删清单 / 跨页页边界标记
    ch = [
        {"seq": 0, "raw": "a", "trans": "甲{v1}", "vars": {"1": "3"}, "page": 1},
        {"seq": 1, "raw": "b", "trans": "乙{v2}", "vars": {"2": "-"}, "page": 1},
        {"seq": 2, "raw": "c", "trans": "丙", "vars": {}, "page": 2},
    ]
    txt = build_chunk_text(ch)
    check("送审文本: 段号前缀", txt.startswith("[S0]") and "[S1]" in txt and "[S2]" in txt)
    check("送审文本: 图例注入", "⟨占位符内容: {v1}=3⟩" in txt)
    check("送审文本: 可删清单注入", "⟨可删占位符: {v2}⟩" in txt)
    check("送审文本: 跨页恰好一个页边界", txt.count(PAGE_BREAK_MARK) == 1)
    check("送审文本: 页边界夹在跨页两段之间",
          txt.index("[S1]") < txt.index(PAGE_BREAK_MARK) < txt.index("[S2]"))
    check("送审文本: 同页不插标记",
          build_chunk_text(ch[:2]).count(PAGE_BREAK_MARK) == 0)
    check("送审文本: 无页号不插标记",
          build_chunk_text([{**ch[0], "page": None},
                            {**ch[2], "page": None}]).count(PAGE_BREAK_MARK) == 0)

    print(f"\n军师校验单元测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
