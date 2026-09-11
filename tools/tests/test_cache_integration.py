# -*- coding: utf-8 -*-
"""v23 缓存集成测试 (合成数据, 临时库, 不触碰真实缓存)

模拟 v22→v23 迁移场景: 旧形态(raw键)条目 → 快照/移参 → 四条断言
① v22 兼容: 编号未变时原样返回
② 编号平移: 经旧形态规范化索引命中且重映射正确 (级联免疫核心)
③ 规范化写入后再平移: 仍命中
④ 术语表按段后缀: 局部变更只作废含该术语的段落

运行: 用 venv python 执行, 退出码 0=全过
"""
import sys, os, re, json, hashlib, tempfile

_VENV_SITE = os.environ.get(
    "PDF2ZH_VENV_SITE",
    r"D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages",
)
if _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

from pdf2zh.cache import _TranslationCache, TranslationCache, db as global_db


def main():
    tmp = os.path.join(tempfile.gettempdir(), "v23_regression.db")
    global_db.init(tmp, pragmas={"journal_mode": "wal", "busy_timeout": 1000})
    global_db.connect(reuse_if_open=True)
    global_db.create_tables([_TranslationCache], safe=True)

    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS {name}")
        else:
            failed += 1
            print(f"  FAIL {name} {detail}")

    shift = lambda s, k: re.sub(
        r"\{v(\d+)\}", lambda m: "{v%d}" % (int(m.group(1)) + k), s)

    try:
        # 合成"真实感"段落 (含占位符/公式/标点混合)
        samples = [
            ("The first three Lie{v3}derivative levels{v4} taken together{v5}",
             "前三个李{v3}导数层级{v4}，合起来{v5}"),
            ("an Extended Kalman Filter {v10}EKF{v11} is designed on the Euler{v12}discretized",
             "设计了一个扩展卡尔曼滤波器{v10}（EKF{v11}），针对欧拉{v12}离散化"),
            ("the matrix is rank{v20}deficient at the calibrated values {v21}",
             "矩阵在校准值{v21}处是秩{v20}亏的"),
            ("Observability analysis {v30} and convergence theory {v31} surround it",
             "可观测性分析{v30}与收敛性理论{v31}围绕它展开"),
        ]
        PARAMS = {"lang_in": "en", "lang_out": "zh", "model": "deepseek-ai/DeepSeek-V3.2",
                  "polish": "on", "old_fp": "x"}
        c = TranslationCache("silicon-copy", dict(PARAMS))

        # 预置 v22 形态旧条目 (raw 键)
        for orig, trans in samples:
            _TranslationCache.create(
                translate_engine="silicon-copy",
                translate_engine_params=c.translate_engine_params,
                original_text=orig,
                translation=trans,
            )
        # 模拟 v23 init: 快照旧形态 → 移除扰动参数
        c.snapshot_legacy_key()
        c.remove_param("old_fp")

        # ① 编号未变: 原样返回
        ok1 = sum(1 for orig, trans in samples if c.get(orig) == trans)
        check(f"① v22兼容 编号未变原样返回 ({ok1}/{len(samples)})", ok1 == len(samples))

        # ② 编号平移+3: 旧形态规范化索引命中 + 重映射正确
        ok2 = 0
        for orig, trans in samples:
            got = c.get(shift(orig, 3))
            exp = shift(trans, 3)
            if got != exp:
                print("     ②详:", repr((got or "")[:60]), "!=", repr(exp[:60]))
            else:
                ok2 += 1
        check(f"② 编号平移+3 命中且重映射 ({ok2}/{len(samples)})", ok2 == len(samples))

        # ③ 规范化写入后, 再平移+5 仍命中
        ok3 = 0
        for orig, trans in samples:
            c.set(shift(orig, 3), shift(trans, 3))
            got = c.get(shift(orig, 5))
            exp = shift(trans, 5)
            if got == exp:
                ok3 += 1
            else:
                print("     ③详:", repr((got or "")[:60]), "!=", repr(exp[:60]))
        check(f"③ 规范化写入后再平移+5 ({ok3}/{len(samples)})", ok3 == len(samples))

        # ④ 术语表按段后缀语义
        def suffix_for(text, terms):
            def _norm(s):
                return re.sub(r"[^a-z0-9]", "", s.lower())
            tn = _norm(re.sub(r"\{v\d+\}", "", text))
            matched = sorted((k, v) for k, v in terms.items()
                             if _norm(k) and _norm(k) in tn)
            if not matched:
                return "#g23:-"
            blob = json.dumps(matched, ensure_ascii=False, sort_keys=True)
            return "#g23:" + hashlib.md5(blob.encode("utf-8")).hexdigest()[:10]

        cg = TranslationCache("gloss-copy", {"lang_in": "en", "lang_out": "zh"})
        terms_v1 = {"in-silico": "计算机模拟", "rain gauge": "雨量计"}
        terms_v2 = {"in-silico": "计算机模拟", "rain gauge": "雨量计改良版"}
        p_a = "calibrated in-silico experiments against data"
        p_b = "validated against rain gauge measurements"
        for p in (p_a, p_b):
            cg.set(p, f"译文[{p[:12]}]", key_suffix=suffix_for(p, terms_v1))
        hit_a = cg.get(p_a, key_suffix=suffix_for(p_a, terms_v2))
        hit_b = cg.get(p_b, key_suffix=suffix_for(p_b, terms_v2))
        check("④a 不含变更术语的段落仍命中", hit_a is not None, repr(hit_a))
        check("④b 含变更术语的段落正确失效", hit_b is None, repr(hit_b))
    finally:
        global_db.close()
        for sfx in ("", "-wal", "-shm"):
            p = tmp + sfx
            if os.path.exists(p):
                os.remove(p)

    print(f"\n集成测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
