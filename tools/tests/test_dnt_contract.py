# -*- coding: utf-8 -*-
"""dnt_contract 契约一致性测试 (A1, 2026-09-28)

被根治的元问题: **验收物与被验物同源** —— "文献区禁汉化"判据原本只活在提示词
规则 6 里(v26.21 事故后补设), 门禁拿"代理量(密度/条目数)"从产物**反推**该豁免谁,
规格从不由独立来源**先于实现**声明。本套件守的就是"规格已先于实现、独立于实现地
落到 tools/contracts/dnt.json, 且生产/消费两侧确由它驱动"。

四类断言:
  ① 契约是**独立文件**(先于实现); ② 契约**自洽**(schema/值域, 断言从契约**派生**,
     不另行硬编码一份预期值 —— 否则又是同源自证);
  ③ 反自证守卫: 实现源码里原先内联的规则 6 正文 / 退出码字面量**已抽出**(改契约
     即改行为, 无需改代码);
  ④ **变异测试**(决定性证据): 改写契约副本 -> 断言实现行为**跟随**。这是"实现由
     契约驱动"而非"契约是实现的一件摆设"的唯一硬证据。

运行: venv python test_dnt_contract.py, 退出码 0=全过
"""
import contextlib
import itertools
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOLS, "seg_export.py")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import dnt_contract
import post_check

DEFAULT = dnt_contract.DEFAULT_PATH
_SEQ = itertools.count()

# 与 test_seg_export.py 同形: 2 页合成侧车 -> 导出 S1..S4(供 --dnt-seg 点名)
SIDECAR = [
    {"page": 2, "vars": {"0": "breeding"},
     "segs": [{"raw": "The {v0} system of"}, {"raw": "{v1}"}]},
    {"page": 3, "vars": {"0": "breeding"},
     "segs": [{"raw": "plants is complex."}, {"raw": "See {v0}."}]},
]


# ---------------------------------------------------------------- helpers
def feat(page, chars=2000, cjk=0, alnum=2000, cites=0,
         ref_entries=0, ref_zh_blocks=0, placeholders=0, error=None,
         ref_ay_entries=0, ref_zh_blocks_ay=0, residues=None, ref_heading=False):
    density = ref_entries * 1000.0 / chars if chars else 0.0
    ay_density = ref_ay_entries * 1000.0 / chars if chars else 0.0
    return {"page": page, "chars": chars, "cjk": cjk, "alnum": alnum,
            "cites": cites, "ref_entries": ref_entries,
            "ref_density": density, "ref_heading": ref_heading,
            "ref_zh_blocks": ref_zh_blocks,
            "ref_ay_entries": ref_ay_entries, "ref_ay_density": ay_density,
            "ref_zh_blocks_ay": ref_zh_blocks_ay,
            "placeholders": placeholders, "residues": list(residues or []),
            "error": error}


def mutate(tmp, fn):
    """读默认契约 -> fn 就地改写 -> 落到 tmp 下的副本, 返回其路径。"""
    with open(DEFAULT, encoding="utf-8") as f:
        c = json.load(f)
    fn(c)
    p = os.path.join(tmp, "dnt_mut_%d.json" % next(_SEQ))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    return p


@contextlib.contextmanager
def using_contract(path):
    old = os.environ.get("P2Z_DNT_CONTRACT")
    os.environ["P2Z_DNT_CONTRACT"] = path
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("P2Z_DNT_CONTRACT", None)
        else:
            os.environ["P2Z_DNT_CONTRACT"] = old


def make_root(tmp):
    root = tempfile.mkdtemp(dir=tmp)
    os.makedirs(os.path.join(root, "inbox"), exist_ok=True)
    return root


def write_sidecar(root):
    p = os.path.join(root, "latest.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for o in SIDECAR:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return p


def run_export(root, args, contract=None):
    """跑 seg_export 子进程。contract=None -> 用默认契约(清掉环境变量)。"""
    env = dict(os.environ)
    env.pop("P2Z_DNT_CONTRACT", None)
    env["P2Z_PROJ"] = root
    if contract:
        env["P2Z_DNT_CONTRACT"] = contract
    proc = subprocess.run([sys.executable, SCRIPT] + args, env=env, cwd=root,
                          capture_output=True)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def read_text(root, name):
    with open(os.path.join(root, "inbox", name + ".txt"), encoding="utf-8") as f:
        return f.read()


def read_manifest(root, name):
    with open(os.path.join(root, "inbox", name + ".manifest.json"),
              encoding="utf-8") as f:
        return json.load(f)


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

    with tempfile.TemporaryDirectory(prefix="p2z_dnt_contract_") as tmp:
        # ---------- ① 契约是独立文件(先于实现、独立于实现) ----------
        check("① 默认契约文件存在", os.path.isfile(DEFAULT), DEFAULT)
        check("① 契约在 tools/contracts/ 下(独立于任何实现模块)",
              os.path.basename(DEFAULT) == "dnt.json"
              and os.path.basename(os.path.dirname(DEFAULT)) == "contracts",
              DEFAULT)
        _man = dnt_contract.load()
        check("① 契约声明版本与标识",
              _man["contract"] == "dnt" and int(_man["version"]) >= 1, _man["contract"])

        # ---------- ② 契约自洽(断言从契约派生, 不另行硬编码预期) ----------
        c = dnt_contract.load()
        check("② 必备键齐全",
              all(k in c for k in ("contract", "version", "rule", "declaration",
                                   "verdicts", "exit_codes", "thresholds")),
              sorted(c))
        check("② verdicts 非空且为列表",
              isinstance(c["verdicts"], list) and len(c["verdicts"]) >= 1, c["verdicts"])
        check("② exit_codes 的键都在 verdicts 内",
              set(c["exit_codes"]) <= set(c["verdicts"]),
              (sorted(c["exit_codes"]), c["verdicts"]))
        _codes = [int(v) for v in c["exit_codes"].values()]
        check("② 退出码两两不同", len(_codes) == len(set(_codes)), _codes)
        _resv = set(int(v) for v in (c.get("reserved_exit_codes") or {}).values())
        check("② 退出码不占用保留码", not (set(_codes) & _resv),
              (sorted(set(_codes) & _resv), sorted(_resv)))
        check("② zh_min >= 1(存在性硬要求)",
              int(c["thresholds"]["zh_min"]) >= 1, c["thresholds"]["zh_min"])
        check("② rule.text 非空", bool(str(c["rule"].get("text", "")).strip()))
        _d = c["declaration"]
        check("② 声明词表字段齐全",
              all(k in _d for k in ("manifest_top_level_flag", "manifest_item_flag",
                                    "payload_line_prefix", "payload_seg_token_pattern")),
              sorted(_d))
        # 三值语义: 契约里 PASS/FAIL/UNVERIFIED 恰好在场(取自契约自身, 非另抄一份)
        check("② 三值 verdict 恰为 PASS/FAIL/UNVERIFIED",
              set(c["verdicts"]) == {"PASS", "FAIL", "UNVERIFIED"}, c["verdicts"])

        # ---------- ③ 反自证守卫: 实现内联字面量已抽出 ----------
        with open(os.path.join(TOOLS, "seg_export.py"), encoding="utf-8") as f:
            _seg = f.read()
        with open(os.path.join(TOOLS, "post_check.py"), encoding="utf-8") as f:
            _pc = f.read()
        check("③ seg_export 不再内联规则 6 正文(改用 {rule6} 占位)",
              "参考文献区不得汉化" not in _seg and "{rule6}" in _seg)
        check("③ seg_export 从契约取规则文本",
              "_DNT.rule_text()" in _seg)
        check("③ post_check 不再内联退出码字面量(改用 exit_code_for)",
              '{"PASS": 0, "FAIL": 1, "UNVERIFIED": 3}' not in _pc
              and "exit_code_for" in _pc)
        check("③ post_check 从契约取退出码",
              "_DNT.exit_code(" in _pc)
        check("③ post_check 不再内联 REF_ZH_MIN 常量(改用 _DNT.zh_min())",
              "REF_ZH_MIN" not in _pc and "_DNT.zh_min()" in _pc)
        check("③ post_check 从契约取 manifest 字段名",
              "_DNT.manifest_item_flag()" in _pc and "_DNT.manifest_top_level_flag()" in _pc)

        # ---------- ④ run_checks 产出的 verdict ⊆ 契约 verdicts ----------
        _vset = set(c["verdicts"])
        _iso = {"dnt_pages": set(), "covered_pages": set()}
        _o = [feat(1, chars=100)]
        _t = [feat(1, chars=100)]
        _fs, _v_pass = post_check.run_checks(_o, _t, **_iso)
        check("④ PASS 用例产出 PASS 且在契约值域", _v_pass == "PASS" and _v_pass in _vset,
              _v_pass)
        _o = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
        _t = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
        _fs, _v_unv = post_check.run_checks(_o, _t)       # 无 dnt 声明 -> 未验证
        check("④ 无声明用例产出 UNVERIFIED 且在契约值域",
              _v_unv == "UNVERIFIED" and _v_unv in _vset, _v_unv)
        _o = [feat(12, chars=3000, alnum=3000, ref_entries=14, ref_zh_blocks=6)]
        _t = [feat(12, chars=3000, alnum=3000, ref_entries=14, ref_zh_blocks=6)]
        _fs, _v_fail = post_check.run_checks(_o, _t, dnt_pages={12}, covered_pages={12})
        check("④ 汉化用例产出 FAIL 且在契约值域",
              _v_fail == "FAIL" and _v_fail in _vset, _v_fail)

        # ---------- ⑤ 变异 A: exit_codes 跟随契约 ----------
        _base_unv = post_check.exit_code_for("UNVERIFIED")
        _mut = mutate(tmp, lambda x: x["exit_codes"].__setitem__("UNVERIFIED", 7))
        with using_contract(_mut):
            check("⑤ 退出码字面量随契约(UNVERIFIED 3->7)",
                  post_check.exit_code_for("UNVERIFIED") == 7
                  and post_check.exit_code_for("PASS") == 0
                  and post_check.exit_code_for("FAIL") == 1,
                  post_check.exit_code_for("UNVERIFIED"))
            check("⑤ 码->verdict 反向映射随契约",
                  dnt_contract.verdict_for_exit_code(7) == "UNVERIFIED"
                  and dnt_contract.verdict_for_exit_code(0) == "PASS")
        check("⑤ 还原默认契约后回到 3", post_check.exit_code_for("UNVERIFIED") == 3,
              (_base_unv, post_check.exit_code_for("UNVERIFIED")))

        # ---------- ⑥ 变异 B: thresholds.zh_min 跟随契约(门槛 1->2) ----------
        _o = [feat(9, chars=3000, alnum=3000, ref_entries=13)]
        _t = [feat(9, chars=3000, alnum=3000, ref_entries=13, ref_zh_blocks=1)]
        _fs, _v = post_check.run_checks(_o, _t, dnt_pages={9}, covered_pages={9})
        check("⑥ 默认 zh_min=1: 1 条汉化即事故 -> FAIL", _v == "FAIL", _v)
        _mut = mutate(tmp, lambda x: x["thresholds"].__setitem__("zh_min", 2))
        with using_contract(_mut):
            _fs, _v2 = post_check.run_checks(_o, _t, dnt_pages={9}, covered_pages={9})
            check("⑥ zh_min=2 随契约: 1 条不再达阈 -> 不判 FAIL", _v2 == "PASS", _v2)

        # ---------- ⑦ 变异 C: manifest 字段名双侧跟随 ----------
        _mut = mutate(tmp, lambda x: (x["declaration"].__setitem__("manifest_item_flag", "dntX"),
                                      x["declaration"].__setitem__("manifest_top_level_flag", "dnt_declaredX")))
        # 消费端: 用变异字段名的 manifest, 在变异契约下被正确读出
        _mfile = os.path.join(tmp, "man_mut.json")
        with open(_mfile, "w", encoding="utf-8") as f:
            json.dump({"dnt_declaredX": True,
                       "items": [{"key": "S9", "dntX": True,
                                  "parts": [{"true_page": 9}]}]}, f, ensure_ascii=False)
        # 默认契约下: 同名 manifest 读不出声明(证明字段名确由契约给出)
        _dp, _cp, _dec = post_check.load_dnt_manifest(_mfile)
        check("⑦ 默认契约下变异字段名 manifest 读为空声明",
              _dp == set() and _dec is False, (_dp, _cp, _dec))
        with using_contract(_mut):
            _dp, _cp, _dec = post_check.load_dnt_manifest(_mfile)
            check("⑦ 变异契约下同一 manifest 被读出声明(字段名跟随契约)",
                  _dp == {9} and _cp == {9} and _dec is True, (_dp, _cp, _dec))
        # 生产端: seg_export 在变异契约下写出变异字段名
        _root = make_root(tmp)
        _side = write_sidecar(_root)
        _rc, _out, _err = run_export(_root, ["--pages", "2-3", "--name", "m1",
                                             "--sidecar", _side, "--terms", "",
                                             "--dnt-seg", "S2"], contract=_mut)
        check("⑦ 生产端导出成功", _rc == 0, (_rc, _out, _err))
        _man = read_manifest(_root, "m1")
        _items = {it["key"]: it for it in _man["items"]}
        check("⑦ 生产端写出变异顶层字段名",
              _man.get("dnt_declaredX") is True and "dnt_declared" not in _man, _man)
        check("⑦ 生产端写出变异逐段字段名",
              _items.get("S2", {}).get("dntX") is True and "dnt" not in _items.get("S2", {}),
              _items.get("S2"))
        # 往返闭环: 生产端写出的 manifest 在消费端(变异契约)被读出 —— 期望值从
        # 生产端产物**派生**(比对两侧是否同一契约, 不另行硬编码页码)。
        _expect = set()
        for _it in _man["items"]:
            if _it.get("dntX"):
                for _p in _it.get("parts") or []:
                    if _p.get("true_page") is not None:
                        _expect.add(int(_p["true_page"]))
        with using_contract(_mut):
            _dp, _cp, _dec = post_check.load_dnt_manifest(
                os.path.join(_root, "inbox", "m1.manifest.json"))
        check("⑦ 生产端产物在消费端(变异契约)闭环读出",
              _expect and _dp == _expect and _dp <= _cp and _dec is True,
              (_dp, _cp, _expect, _dec))

        # ---------- ⑧ 变异 D: payload_line_prefix 跟随契约 ----------
        _mut = mutate(tmp, lambda x: x["declaration"].__setitem__("payload_line_prefix", "[NOZH] "))
        _root = make_root(tmp)
        _side = write_sidecar(_root)
        _rc, _out, _err = run_export(_root, ["--pages", "2-3", "--name", "m2",
                                             "--sidecar", _side, "--terms", "",
                                             "--dnt-seg", "S2"], contract=_mut)
        _txt = read_text(_root, "m2") if _rc == 0 else ""
        check("⑧ 变异前缀导出成功", _rc == 0, (_rc, _err))
        check("⑧ 载荷声明行前缀随契约", "[NOZH] #S2" in _txt and "[禁翻]" not in _txt,
              _txt[:200])
        check("⑧ 控制台报告前缀随契约", "[NOZH] 1 段声明保持原文" in _out, _out)

        # ---------- ⑨ 变异 E: payload_seg_token_pattern 跟随契约 ----------
        _root = make_root(tmp)
        _side = write_sidecar(_root)
        _rc0, _out0, _err0 = run_export(_root, ["--pages", "2-3", "--name", "m3",
                                                "--sidecar", _side, "--terms", "",
                                                "--dnt-seg", "S2"])   # 默认契约
        check("⑨ 默认契约接受 S2", _rc0 == 0, (_rc0, _out0, _err0))
        _mut = mutate(tmp, lambda x: x["declaration"].__setitem__("payload_seg_token_pattern", "^ZZZ$"))
        _rc1, _out1, _err1 = run_export(_root, ["--pages", "2-3", "--name", "m3",
                                                "--sidecar", _side, "--terms", "",
                                                "--dnt-seg", "S2"], contract=_mut)
        check("⑨ 变异正则拒收同一记号(正则确由契约给出)",
              _rc1 == 1 and "非法记号" in _out1, (_rc1, _out1))

        # ---------- ⑩ fail-closed: 坏契约一律抛异常, 不静默降级 ----------
        _nope = os.path.join(tmp, "no_such_contract.json")
        with using_contract(_nope):
            _raised = False
            try:
                dnt_contract.load()
            except Exception:
                _raised = True
            check("⑩ 契约缺文件 -> 抛异常(不静默降级)", _raised)
            _raised = False
            try:
                post_check.exit_code_for("PASS")
            except Exception:
                _raised = True
            check("⑩ 消费端在坏契约下也响亮失败(fail-closed)", _raised)
        # 缺键
        _mut = mutate(tmp, lambda x: x.pop("exit_codes", None))
        with using_contract(_mut):
            _raised = False
            try:
                dnt_contract.load()
            except ValueError:
                _raised = True
            check("⑩ 缺必备键 -> ValueError", _raised)
        # 值域冲突: verdict 占用保留码 2
        _mut = mutate(tmp, lambda x: x["exit_codes"].__setitem__("UNVERIFIED", 2))
        with using_contract(_mut):
            _raised = False
            try:
                dnt_contract.load()
            except ValueError:
                _raised = True
            check("⑩ verdict 占用保留码 -> ValueError", _raised)
        # 退出码重复
        _mut = mutate(tmp, lambda x: x["exit_codes"].__setitem__("FAIL", 0))
        with using_contract(_mut):
            _raised = False
            try:
                dnt_contract.load()
            except ValueError:
                _raised = True
            check("⑩ 退出码重复 -> ValueError", _raised)
        # zh_min < 1
        _mut = mutate(tmp, lambda x: x["thresholds"].__setitem__("zh_min", 0))
        with using_contract(_mut):
            _raised = False
            try:
                dnt_contract.load()
            except ValueError:
                _raised = True
            check("⑩ zh_min<1 -> ValueError", _raised)

    print(f"\ndnt_contract 契约一致性测试: {passed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
