# -*- coding: utf-8 -*-
"""军师层单元测试: 锚点校验 / JSON 提取 / 分块预算 (纯函数, 不触网不入库)"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategist import (
    tokens_ok, extract_json_array, canon_remap, legend_of, load_segments,
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
        f.write(_json.dumps({"pageid": 1, "vars": {"1": "3"},
                             "segs": [{"raw": "a{v1}", "trans": "甲{v1}"}]},
                            ensure_ascii=False) + "\n")
        f.write(_json.dumps({"pageid": 2,
                             "segs": [{"raw": "b", "trans": "乙"}]},
                            ensure_ascii=False) + "\n")
    _segs = load_segments(_p)
    os.remove(_p)
    check("载入: 段seq连续", [s["seq"] for s in _segs] == [0, 1])
    check("载入: 图例随页携带",
          _segs[0]["vars"] == {"1": "3"} and _segs[1]["vars"] == {})

    print(f"\n军师校验单元测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
