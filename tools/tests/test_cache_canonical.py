# -*- coding: utf-8 -*-
"""v23 缓存规范化单元测试 (12 例)

运行: 用 zotero-pdf2zh-venv 的 python 执行本文件, 退出码 0=全过
    & <venv>/python.exe test_cache_canonical.py

覆盖: 编号平移命中 / 重映射正确性 / 译文token乱序往返 / 交叉引用 /
LLM自造token保留 / 旧形态回查 / 表变更后不误命中 / 后缀隔离 / 纯文本往返 /
[v24-A] 历史形态 ladder 带每段 v23s 后缀 / 索引命中回写迁移 /
[v27] 退役参数投影(润色世代零重译) / 投影门控 / 取最新 / polish_reflect 门闩契约 /
[v28.21] 文档画像投影(配置改动不再整篇换键重译) / 画像投影门控 / 多代画像取最新 /
         前瞻段(v23s 单独在场)不误关门控 / v23s 未被剔除
"""
import sys, os, json

# 允许从任意 python 启动: 注入解释器自身的 site-packages (换台电脑自动跟随;
# 显式指定别的环境用 PDF2ZH_VENV_SITE)
_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    os.path.join(sys.prefix, "Lib", "site-packages"),
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

from pdf2zh.cache import TranslationCache, _TranslationCache, init_test_db, clean_test_db


def main():
    test_db = init_test_db()
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS {name}")
        else:
            failed += 1
            print(f"  FAIL {name} {detail}")

    try:
        # 用例1: 同一段, 全局编号平移 +4, 应命中并正确重映射
        c = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "model": "m1"})
        c.set("levels{v3} taken together{v5} are not sufficient{v7}",
              "层级{v3}合起来{v5}不足{v7}")
        got = c.get("levels{v7} taken together{v9} are not sufficient{v11}")
        check("编号平移命中", got is not None)
        check("重映射正确", got == "层级{v7}合起来{v9}不足{v11}", repr(got))

        # 用例2: 平移往返
        c2 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "model": "m1"})
        c2.set("filter {v10}EKF{v11} designed", "滤波器{v10}（EKF{v11}）")
        got_b = c2.get("filter {v20}EKF{v21} designed")
        check("乱序/平移往返", got_b == "滤波器{v20}（EKF{v21}）", repr(got_b))

        # 用例3: 译文 token 交叉引用(出现顺序与原文不同)
        c2.set("A {v1} B {v2} C", "甲{v2}与乙{v1}")
        got_c = c2.get("A {v8} B {v9} C")
        check("交叉引用重映射", got_c == "甲{v9}与乙{v8}", repr(got_c))

        # 用例4: LLM 自造 token 原样保留
        c2.set("text {v2} more", "文本{v2}自造{v99}")
        got_d = c2.get("text {v5} more")
        check("自造token保留", got_d is not None and "{v99}" in got_d, repr(got_d))

        # 用例5: 旧形态回查 (snapshot_legacy_key + remove_param)
        c3 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "model": "m2", "old_fp": "ABC"})
        c3.set("legacy {v0} text", "旧条目{v0}")
        c3.snapshot_legacy_key()
        c3.remove_param("old_fp")
        got_e = c3.get("legacy {v4} text")
        check("旧形态回查命中", got_e == "旧条目{v4}", repr(got_e))

        # 用例6: 术语表变更后旧形态天然失效 (fp 不同 → 不误命中)
        c4 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "old_fp": "OLD"})
        c4.set("glossary {v0} case", "旧术语{v0}")
        c4.snapshot_legacy_key()
        c4.remove_param("old_fp")
        c5 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "old_fp": "NEW"})
        c5.snapshot_legacy_key()
        c5.remove_param("old_fp")
        got_f = c5.get("glossary {v1} case")
        check("表变更后旧条目不误命中", got_f is None, repr(got_f))

        # 用例7: 后缀隔离
        c6 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh"})
        c6.set("same text {v0}", "后缀A译文{v0}", key_suffix="#g23:aaa")
        got_g = c6.get("same text {v3}", key_suffix="#g23:aaa")
        got_h = c6.get("same text {v3}", key_suffix="#g23:bbb")
        check("后缀A命中", got_g == "后缀A译文{v3}", repr(got_g))
        check("后缀B不串", got_h is None, repr(got_h))

        # 用例8: 纯文本往返
        c6.set("plain paragraph", "纯文本段落")
        check("纯文本命中", c6.get("plain paragraph") == "纯文本段落")

        # 用例9: [v24-A] 历史形态 ladder 带每段 v23s 后缀
        # 库中行以"基形态+v23s"入库 (如 '#g23:-'), 而 snapshot_prev_key 只登记
        # 基形态 → 必须补后缀才命中; 且命中后须回写迁移到当前形态。
        c7 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "model": "m3"})
        c7.set("ladder {v0} suffix text", "梯{v0}后缀", key_suffix="#g23:-")
        c7.snapshot_prev_key()                       # prev = 基形态 (无 v23s)
        c7.add_params("doc_summary_fp", "docsummary:abc12345:deadbeef")  # 键演化
        got_i = c7.get("ladder {v6} suffix text", key_suffix="#g23:-")
        check("ladder历史形态(带后缀)命中", got_i == "梯{v6}后缀", repr(got_i))
        _pj7 = c7._params_json("#g23:-")
        _mig = _TranslationCache.get_or_none(
            translate_engine="unittest", translate_engine_params=_pj7,
            original_text="ladder {v0} suffix text")
        check("ladder命中后回写迁移", _mig is not None)

        # 用例10: [v24-A] 旧行以 raw 文本入库 → 走内存索引路径, 索引命中亦须回写
        c8 = TranslationCache("unittest", {"lang_in": "en", "lang_out": "zh", "model": "m4"})
        _hist8 = c8._params_json("#g23:-")           # 基形态 + v23s
        _TranslationCache.create(
            translate_engine="unittest", translate_engine_params=_hist8,
            original_text="raw legacy {v5} alpha {v9}",   # raw (非规范化)
            translation="原样旧译{v5}甲{v9}",
        )
        c8.snapshot_prev_key()
        c8.add_params("doc_summary_fp", "docsummary:beef1234:cafe0000")
        got_j = c8.get("raw legacy {v7} alpha {v11}", key_suffix="#g23:-")
        check("raw旧行经索引命中并重映射", got_j == "原样旧译{v7}甲{v11}", repr(got_j))
        _mig8 = _TranslationCache.get_or_none(
            translate_engine="unittest", translate_engine_params=c8._params_json("#g23:-"),
            original_text="raw legacy {v0} alpha {v1}")
        check("索引命中后回写迁移", _mig8 is not None)

        # 用例11: [v27] 退役参数投影 —— 润色世代行(带 polish*+v23s)在当前世代下命中。
        # 场景: POLISH 退场后当前键不再含 polish*, 而库中既有译文全都停在润色形态上;
        # 不投影则整篇失配重译, 且豆包回灌进库的定稿译文会被新译者覆盖。
        _base11 = {"lang_in": "en", "lang_out": "zh", "model": "m5"}
        _ret11 = dict(_base11)
        _ret11.update({"polish": "on", "polish_anchor": "on", "polish_model": "doubao",
                       "polish_guideline_fp": "GUIDE-m5-随文档而变", "v23s": "#g23:-"})
        _TranslationCache.create(
            translate_engine="unittest",
            translate_engine_params=json.dumps(
                TranslationCache._sort_dict_recursively(_ret11)),
            original_text="retired {v0} projection text",
            translation="退役{v0}投影译文",
        )
        c9 = TranslationCache("unittest", dict(_base11))
        got_k = c9.get("retired {v7} projection text")
        check("退役投影命中", got_k == "退役{v7}投影译文", repr(got_k))
        _mig9 = _TranslationCache.get_or_none(
            translate_engine="unittest", translate_engine_params=c9._params_json(""),
            original_text="retired {v0} projection text")
        check("退役投影命中后回写迁移", _mig9 is not None)

        # 用例12: [v27] 门控 —— 润色重开(当前键含 polish*)时投影必须停用:
        # 否则"每篇论文一份的翻译指南"不同(口径不同)的旧润色译文会互相串。
        _base12 = {"lang_in": "en", "lang_out": "zh", "model": "m6"}
        _g1 = dict(_base12)
        _g1.update({"polish": "on", "polish_guideline_fp": "GUIDE-ONE"})
        _TranslationCache.create(
            translate_engine="unittest",
            translate_engine_params=json.dumps(
                TranslationCache._sort_dict_recursively(_g1)),
            original_text="gate {v0} text", translation="指南一译文{v0}",
        )
        _g2 = dict(_base12)
        _g2.update({"polish": "on", "polish_guideline_fp": "GUIDE-TWO"})
        c10 = TranslationCache("unittest", dict(_g2))
        check("当前世代含退役参数时投影停用(门控)",
              c10._has_retired_generation(c10._params_json("")))
        got_l = c10.get("gate {v4} text")
        check("跨指南旧润色译文本不串", got_l is None, repr(got_l))

        # 用例13: [v27] _strip_retired 纯函数口径
        check("无退役参数原样返回(逐字节不变)",
              TranslationCache._strip_retired('{"a": 1}') == ('{"a": 1}', 0))
        _s13, _n13 = TranslationCache._strip_retired(
            json.dumps({"a": 1, "polish": "on", "polish_model": "x", "v23s": "#g23:-"}))
        check("退役参数按前缀+键剔除",
              _n13 == 3 and "polish" not in _s13 and "v23s" not in _s13, repr(_s13))
        check("非法JSON原样返回",
              TranslationCache._strip_retired("not-json") == ("not-json", 0))

        # 用例14: [v27] 只带 v23s(无 polish*)的退役行同样命中
        _base14 = {"lang_in": "en", "lang_out": "zh", "model": "m7"}
        _r14 = dict(_base14)
        _r14["v23s"] = "#g23:-"
        _TranslationCache.create(
            translate_engine="unittest",
            translate_engine_params=json.dumps(
                TranslationCache._sort_dict_recursively(_r14)),
            original_text="v23s {v0} only", translation="仅后缀{v0}",
        )
        c11 = TranslationCache("unittest", dict(_base14))
        got_m = c11.get("v23s {v2} only")
        check("v23s 单独退役亦命中", got_m == "仅后缀{v2}", repr(got_m))

        # 用例15: [v27] 同段在润色世代翻过多次 → 索引按 id 升序写覆盖, 取最新
        _base15 = {"lang_in": "en", "lang_out": "zh", "model": "m8"}
        for _suf, _tr in (("#g23:old", "旧译{v0}"), ("#g23:new", "新译{v0}")):
            _p15 = dict(_base15)
            _p15.update({"polish": "on", "v23s": _suf})
            _TranslationCache.create(
                translate_engine="unittest",
                translate_engine_params=json.dumps(
                    TranslationCache._sort_dict_recursively(_p15)),
                original_text="dup {v0} para", translation=_tr,
            )
        c12 = TranslationCache("unittest", dict(_base15))
        got_n = c12.get("dup {v5} para")
        check("同段多世代取最新", got_n == "新译{v5}", repr(got_n))

        # 用例16: [v27] translator 侧契约 —— polish_reflect 登记须在 _polish_enabled
        # 门闩内 (否则退役世代的键残留 polish_reflect: 既是无意义世代维度, 又让
        # 投影形态与库中行差一维, 一行也命中不了)。
        import ast
        import pdf2zh.translator as _trmod
        _tree = ast.parse(open(_trmod.__file__, encoding="utf-8").read())
        _guard = None
        for _fn in ast.walk(_tree):
            if not (isinstance(_fn, ast.FunctionDef) and _fn.name == "__init__"):
                continue
            for _if in ast.walk(_fn):
                if not isinstance(_if, ast.If):
                    continue
                for _sub in ast.walk(_if):
                    if (isinstance(_sub, ast.Call)
                            and isinstance(_sub.func, ast.Attribute)
                            and _sub.func.attr == "add_cache_impact_parameters"
                            and _sub.args
                            and getattr(_sub.args[0], "value", None) == "polish_reflect"):
                        _guard = ast.unparse(_if.test)
                        break
                if _guard is not None:
                    break
            if _guard is not None:
                break
        check("polish_reflect 登记受门闩约束",
              _guard is not None and "self._polish_enabled" in _guard, repr(_guard))

        # 用例17: [v28.21] 文档画像投影 —— 配置改动导致摘要键漂移后, 已漂移的译文
        # 必须仍能命中。背景: 摘要落盘键与 doc_summary_fp 都由首页文本算出, 而
        # v28.21 之前那段文本带 {vN} 占位符(集合随 config 变) → 改一次配置就整篇
        # 换键重译(实测 CLAP 491 秒, 且重译盖掉了豆包回路已采纳的译文)。
        _base17 = {"lang_in": "en", "lang_out": "zh", "model": "m9"}
        _old17 = dict(_base17)
        _old17["doc_summary_fp"] = "docsummary:8bff40202e4fa36c:1a2769ea"
        _TranslationCache.create(
            translate_engine="unittest",
            translate_engine_params=json.dumps(
                TranslationCache._sort_dict_recursively(_old17)),
            original_text="drifted {v0} doc text",
            translation="漂移前译文{v0}",
        )
        c13 = TranslationCache("unittest", dict(_base17))
        c13.add_params("doc_summary_fp", "docsummary:38bc257f82804e11:9fd924bc")
        got_o = c13.get("drifted {v3} doc text")
        check("文档画像漂移后仍命中", got_o == "漂移前译文{v3}", repr(got_o))
        _mig13 = _TranslationCache.get_or_none(
            translate_engine="unittest",
            translate_engine_params=c13._params_json(""),
            original_text="drifted {v0} doc text")
        check("文档画像投影命中后回写迁移", _mig13 is not None)

        # 用例18: [v28.21] _strip_docsummary 纯函数口径
        check("带 doc fp 时剔除并报 True",
              TranslationCache._strip_docsummary(
                  json.dumps({"a": 1, "doc_summary_fp": "docsummary:x:y"}))
              == ('{"a": 1}', True))
        check("无 doc fp 原样返回(逐字节不变)",
              TranslationCache._strip_docsummary('{"a": 1}') == ('{"a": 1}', False))
        check("非法JSON原样返回",
              TranslationCache._strip_docsummary("not-json") == ("not-json", False))

        # 用例19: [v28.21] 门控 —— 当前世代含退役参数(润色重开)时, 画像投影必须
        # 一并停用: 否则"跨润色世代 + 跨文档画像"两个维度同时放宽, 旧润色译文会串。
        _base19 = {"lang_in": "en", "lang_out": "zh", "model": "m10"}
        _old19 = dict(_base19)
        _old19.update({"polish": "on",
                       "doc_summary_fp": "docsummary:aaaa1111:bbbb2222"})
        _TranslationCache.create(
            translate_engine="unittest",
            translate_engine_params=json.dumps(
                TranslationCache._sort_dict_recursively(_old19)),
            original_text="gate19 {v0} text", translation="旧润色译文{v0}",
        )
        _cur19 = dict(_base19)
        _cur19.update({"polish": "on",
                       "doc_summary_fp": "docsummary:cccc3333:dddd4444"})
        c14 = TranslationCache("unittest", dict(_cur19))
        got_p = c14.get("gate19 {v2} text")
        check("跨润色世代+跨画像不串(门控)", got_p is None, repr(got_p))

        # 用例20: [v28.21] 同段多代画像 → 索引按 id 升序写覆盖, 取最新
        _base20 = {"lang_in": "en", "lang_out": "zh", "model": "m11"}
        for _fp20, _tr20 in (("docsummary:1111aaaa:2222bbbb", "旧画像译文{v0}"),
                             ("docsummary:3333cccc:4444dddd", "新画像译文{v0}")):
            _p20 = dict(_base20)
            _p20["doc_summary_fp"] = _fp20
            _TranslationCache.create(
                translate_engine="unittest",
                translate_engine_params=json.dumps(
                    TranslationCache._sort_dict_recursively(_p20)),
                original_text="dup20 {v0} para", translation=_tr20,
            )
        c15 = TranslationCache("unittest", dict(_base20))
        c15.add_params("doc_summary_fp", "docsummary:5555eeee:6666ffff")
        got_q = c15.get("dup20 {v4} para")
        check("同段多代画像取最新", got_q == "新画像译文{v4}", repr(got_q))

        # 用例21: [v28.21] 门控只看 polish* —— v23s 单独在场(前瞻 `#la:…`)不得关门控。
        # 前瞻(v23.4)至今仍在生产 v23s, 若沿用"含任一退役参数即关门"的判据, 带前瞻的
        # 段会被误判成润色世代 → 画像投影失效(实测 CLAP 每代 13~14 段救不回来)。
        # 同时钉住反面: ④ **不**剔除 v23s —— 前瞻仍是键的一部分, 前瞻不同不该串。
        _base21 = {"lang_in": "en", "lang_out": "zh", "model": "m12"}
        _old21 = dict(_base21)
        _old21.update({"v23s": "#la:1122334455",
                       "doc_summary_fp": "docsummary:8bff40202e4fa36c:1a2769ea"})
        _TranslationCache.create(
            translate_engine="unittest",
            translate_engine_params=json.dumps(
                TranslationCache._sort_dict_recursively(_old21)),
            original_text="la {v0} fragment", translation="前瞻段译文{v0}",
        )
        _other21 = dict(_base21)
        _other21.update({"v23s": "#la:9988776655",
                         "doc_summary_fp": "docsummary:8bff40202e4fa36c:1a2769ea"})
        _TranslationCache.create(
            translate_engine="unittest",
            translate_engine_params=json.dumps(
                TranslationCache._sort_dict_recursively(_other21)),
            original_text="xla {v0} frag", translation="他前瞻译文{v0}",
        )
        _cur21 = dict(_base21)
        _cur21.update({"v23s": "#la:1122334455",
                       "doc_summary_fp": "docsummary:38bc257f82804e11:9fd924bc"})
        c16 = TranslationCache("unittest", dict(_cur21))
        check("v23s 在场不算退役世代(门控)",
              c16._has_retired_generation(c16._params_json("")) is False)
        check("polish 在场才算退役世代(门控)",
              c16._has_retired_generation(json.dumps({"polish": "on"})) is True)
        got_r = c16.get("la {v3} fragment")
        check("前瞻段跨画像世代仍命中(同前瞻)", got_r == "前瞻段译文{v3}", repr(got_r))
        got_s = c16.get("xla {v2} frag")
        check("前瞻不同不命中(v23s 未被剔除)", got_s is None, repr(got_s))
    finally:
        clean_test_db(test_db)

    print(f"\n单元测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
