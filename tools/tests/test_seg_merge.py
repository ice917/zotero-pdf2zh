# -*- coding: utf-8 -*-
"""seg_merge 按段号合稿单元测试 (2026-09-20, SILAGE 第四轮)

被锁死的缺陷(豆包自述第 3 条): 上一轮按段号补 28 段时, 替换脚本的 fills 字典
写错, **改完没生效就提交了** —— 全程没有任何东西告诉他"你的替换没落地",
直到人去追问才发现那 28 段原文未动。

本测试锁八条语义:
  ① 正常合稿: 点到的段真变了, **未点到的段字节一个都不动**(按正文区间切片, 不重排全文)
  ② 补丁里没有 #S编号 块 -> 拒绝执行, 正式交件不动(宁可不动, 不许空跑当成功)
  ③ 补丁点了交件里没有的段号 -> 拒绝执行并点名, 交件不动(段号写错/交件拿错)
  ④ 交件里段号重复 -> 拒绝执行(门禁的 parse_blocks 只认最后一条, 重复会让
     回读校验与实际提交对不上 —— 正是本工具要杜绝的静默错)
  ⑤ 回读校验哨兵 verify_applied: 拿"没生效的文本"喂它必须报出全部段号,
     拿"生效后的文本"喂它必须为空 —— 这条判据就是"静默失败"的唯一哨兵
  ⑥ 字节保真: 文件末尾原本没有换行就不许添; 带 BOM 的交件合稿后 BOM 还在
  ⑦ 补丁新文本与原文一模一样 -> 不算失败, 但必须⚠️点名(等于没改)
  ⑧ 交件候选不唯一 -> 拒绝执行并提示 --delivery(合错一份整轮白干)

运行: venv python test_seg_merge.py, 退出码 0=全过
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOLS, "seg_merge.py")

if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import seg_merge as SM    # noqa: E402  (它内部复用 adopt 的解析/自查口径)

# 两段都 >=40 字符(adopt._GAP_MIN_SRC), 才会进长度比自查
L1 = ("Artificial intelligence is transforming how we conduct scientific research "
      "in modern laboratories around the world today, enabling faster discovery.")
L2 = ("The proposed memory efficient optimizer reduces the gradient storage "
      "requirement substantially, which allows training larger models without "
      "additional hardware investment.")
LONG_S1 = "人工智能正在改变我们在世界各地现代实验室中开展科学研究的方式，使发现更快。"
LONG_S1_FIX = ("人工智能正在改变我们在世界各地现代实验室中开展科学研究的方式，"
               "让科学发现的速度显著加快。")
LONG_S2 = "所提出的内存高效优化器大幅降低了梯度存储需求，使得无需额外硬件投入即可训练更大的模型。"


def make_root(tmp):
    root = tempfile.mkdtemp(dir=tmp)
    os.makedirs(os.path.join(root, "inbox"), exist_ok=True)
    os.makedirs(os.path.join(root, "out"), exist_ok=True)
    return root


def write_payload(root, name, blocks):
    with open(os.path.join(root, "inbox", name + ".txt"), "w", encoding="utf-8") as f:
        for k, raw in blocks:
            f.write("#S%d\n%s\n" % (k, raw))


def write_delivery(root, fname, body, bom=False, newline="\n"):
    p = os.path.join(root, "out", fname)
    data = body.replace("\n", newline).encode("utf-8")
    with open(p, "wb") as f:
        f.write((b"\xef\xbb\xbf" + data) if bom else data)
    return p


def run(root, args):
    env = dict(os.environ, P2Z_PROJ=root)
    proc = subprocess.run([sys.executable, SCRIPT] + args, env=env, cwd=root,
                          capture_output=True)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


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

    with tempfile.TemporaryDirectory(prefix="p2z_seg_merge_") as tmp:
        base = "#S1\n%s\n#S2\n%s\n" % (LONG_S1, LONG_S2)
        patch_s1 = "#S1\n%s\n" % LONG_S1_FIX

        # ① 正常合稿: 点到的段真变了, 未点到的段字节一个都不动
        root = make_root(tmp)
        write_payload(root, "demo", [(1, L1), (2, L2)])
        d = write_delivery(root, "demo.doubao.txt", base)
        p = os.path.join(root, "patch.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(patch_s1)
        before = open(d, "rb").read()
        rc, out, err = run(root, ["--name", "demo", "--patch", p])
        after = open(d, "rb").read()
        check("① 合稿成功(退出码 0)", rc == 0, (rc, out[-400:], err[-400:]))
        check("① 点到的段真的变了",
              LONG_S1_FIX.encode("utf-8") in after and LONG_S1.encode("utf-8") not in after,
              after[:200])
        check("① 未点到的段字节一个都不动",
              before.split(b"#S2")[1] == after.split(b"#S2")[1],
              (before.split(b"#S2")[1], after.split(b"#S2")[1]))
        check("① 终端报出替换生效段数", "替换生效: 1 段" in out and "未生效: 0 段" in out, out)
        check("① 合稿后跑长度比自查(与门禁同一份实现)",
              "长度比自查" in out and "长段 2 个" in out, out)

        # ② 补丁里没有 #S 编号块 -> 拒绝执行, 交件不动
        root = make_root(tmp)
        write_payload(root, "demo", [(1, L1), (2, L2)])
        d = write_delivery(root, "demo.doubao.txt", base)
        p = os.path.join(root, "patch.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("这里只是散文, 一个段号行都没有\n")
        before = open(d, "rb").read()
        rc, out, err = run(root, ["--name", "demo", "--patch", p])
        check("② 补丁没有 #S 编号块 -> 拒绝执行", rc == 1 and "没有任何 #S编号 块" in out,
              (rc, out[-300:]))
        check("② 被拒时交件一个字节不动", open(d, "rb").read() == before, "")

        # ③ 补丁点了交件里没有的段号 -> 拒绝并点名
        root = make_root(tmp)
        write_payload(root, "demo", [(1, L1), (2, L2)])
        d = write_delivery(root, "demo.doubao.txt", base)
        p = os.path.join(root, "patch.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("#S99\n这一段的段号根本不在交件里。\n")
        before = open(d, "rb").read()
        rc, out, err = run(root, ["--name", "demo", "--patch", p])
        check("③ 段号不存在 -> 拒绝执行并点名 #S99", rc == 1 and "#S99" in out, (rc, out[-300:]))
        check("③ 被拒时交件一个字节不动", open(d, "rb").read() == before, "")

        # ④ 交件里段号重复 -> 拒绝执行 (parse_blocks 只认最后一条, 静默错的温床)
        root = make_root(tmp)
        write_payload(root, "demo", [(1, L1), (2, L2)])
        d = write_delivery(root, "demo.doubao.txt",
                           "#S1\n%s\n#S1\n%s\n#S2\n%s\n" % (LONG_S1, LONG_S1, LONG_S2))
        p = os.path.join(root, "patch.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(patch_s1)
        before = open(d, "rb").read()
        rc, out, err = run(root, ["--name", "demo", "--patch", p])
        check("④ 交件段号重复 -> 拒绝执行并说明后果",
              rc == 1 and "出现两次" in out and "唯一" in out, (rc, out[-400:]))
        check("④ 被拒时交件一个字节不动", open(d, "rb").read() == before, "")

        # ⑤ 回读校验哨兵: 这条判据是"静默失败"的唯一哨兵, 单独单元测
        patch = {1: LONG_S1_FIX}
        check("⑤ 没生效的文本必须被哨兵抓住",
              SM.verify_applied(base, patch) == [1], SM.verify_applied(base, patch))
        applied = "#S1\n%s\n#S2\n%s\n" % (LONG_S1_FIX, LONG_S2)
        check("⑤ 生效后的文本哨兵为空", SM.verify_applied(applied, patch) == [],
              SM.verify_applied(applied, patch))
        check("⑤ 多段补丁里只漏一段也要点名",
              SM.verify_applied(applied, {1: LONG_S1_FIX, 2: "被漏掉的段"}) == [2], "")

        # ⑥ 字节保真: 末尾无换行不许添, BOM 不许丢
        root = make_root(tmp)
        write_payload(root, "demo", [(1, L1), (2, L2)])
        d = write_delivery(root, "demo.doubao.txt",
                           "#S1\n%s\n#S2\n%s" % (LONG_S1, LONG_S2))   # 末尾无换行
        p = os.path.join(root, "patch.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(patch_s1)
        rc, out, err = run(root, ["--name", "demo", "--patch", p])
        data = open(d, "rb").read()
        check("⑥ 末尾原本没有换行就不添", rc == 0 and not data.endswith(b"\n"), data[-40:])
        root = make_root(tmp)
        write_payload(root, "demo", [(1, L1), (2, L2)])
        d = write_delivery(root, "demo.doubao.txt", base, bom=True)
        p = os.path.join(root, "patch.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(patch_s1)
        rc, out, err = run(root, ["--name", "demo", "--patch", p])
        check("⑥ 带 BOM 的交件合稿后 BOM 还在",
              rc == 0 and open(d, "rb").read().startswith(b"\xef\xbb\xbf"), "")

        # ⑦ 补丁新文本与原文一模一样 -> 不算失败, 但必须点名(等于没改)
        root = make_root(tmp)
        write_payload(root, "demo", [(1, L1), (2, L2)])
        d = write_delivery(root, "demo.doubao.txt", base)
        p = os.path.join(root, "patch.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("#S1\n%s\n" % LONG_S1)          # 与交件里那段一字不差
        rc, out, err = run(root, ["--name", "demo", "--patch", p])
        check("⑦ 原样重发不算失败但必须⚠️点名",
              rc == 0 and "一模一样" in out and "#S1" in out, (rc, out[-400:]))

        # ⑧ 交件候选不唯一 -> 拒绝执行, 要求显式指定
        root = make_root(tmp)
        write_payload(root, "demo", [(1, L1), (2, L2)])
        write_delivery(root, "demo.doubao.txt", base)
        write_delivery(root, "demo.doubao2.txt", base)
        p = os.path.join(root, "patch.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(patch_s1)
        rc, out, err = run(root, ["--name", "demo", "--patch", p])
        check("⑧ 交件候选不唯一 -> 拒绝并提示 --delivery",
              rc == 1 and "候选 2 份" in out and "--delivery" in out, (rc, out[-400:]))

    print("\n结果: %d PASS / %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
