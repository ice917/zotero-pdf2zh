# -*- coding: utf-8 -*-
"""seg_export 文档抬头/术语表参数化单元测试 (v28.1→v28.8, 2026-09-19)

被锁死的缺陷: RULES 抬头硬编码 "[文档] Reproductive Biology of Cactaceae
(Desert Plants, 2009) 第{pages}页", 术语第 4 条也写死仙人掌科那 5 条。
结果是导出任何别篇论文时, 豆包都先被喂一个错误的文档先验 + 一份无关术语表。

修法: RULES 变模板({doc}/{terms}); 抬头缺省只写页码(不撒谎); 术语默认取
server/glossary/terms.csv, 空表/缺文件退化为"按学科惯例统一译名"(不误导向)。

本测试锁十条语义:
  ① 抬头可参数化, 且缺省时不再残留任何具体论文名
  ② 术语可从任意 csv 注入, 格式 en=zh 以 ；连接
  ③ 术语源缺失/置空 -> 退化句, 绝不泄出别的领域的术语
  ④ 参数化不得动到段落编号与跨页合并(既有契约原地不动)
  ⑤ [v28.8] 规则 7: 公式片段连同紧邻标点/编号是一整块, 块内逐字符照抄(通用表述,
     不举别篇的例子 —— 见 ⑨ 节)
  ⑥ [v28.10] --pages 是**真实 PDF 页码**(与质检/体检报告同一口径), 不是侧车回调
     计数; 同页的图形记录一并取回; 跨页合并按真实页相邻; manifest 仍用侧车内部
     坐标(import/inject 不受影响) —— 见 ⑩ 节
  ⑦ [v28.12] 规则 2 补: 数字与字母连写的记号(3D/2D/4×4/P1/COVID-19)整体照抄,
     不得改写成「三维/二维/四乘四」; 原文的数字字符一个都不能少(数字字形要有
     落点, 否则回锚 FAIL) —— 见 ⑪ 节
  ⑧ [v28.22] 规则 2 再补: 连写块**内**的空格与标点也是块的一部分 —— 空格不加不减
     (A100 不得写成「A 100」), 块内半角标点照抄不得换中文标点(0.5/3,000/a/b 里的
     . , / 不得写成 。，／) —— 见 ⑫ 节。战例: CLAP 收口时同一篇豆包两个交件版本
     只在两处不同(`88.8%,π0` -> 「88.8%、π0」、`A10080G` -> 「A 10080G」), 恰恰
     都被 seg_import 判"高价值字形未回锚"。
  ⑨ [v28.28] 规则 3(⋮) 补正反例 + 新增规则 8(残句碎片照译不补全) —— 见 ⑬ 节。
     战例: SILAGE 那篇交付稿 ⋮ 出现 4 处不符(deliver 门禁拦下), 且大量跨页/跨栏
     半词碎片被译者"补全"成通顺句子 -> 渲染拼接处重复、与原文对不上号。
  ⑩ [v28.28] 第 4 条(术语)末尾自带**术语查证子项**: 表里没有的专业术语先查证
     (search_term: Zotero 库 PDF 原文 + OpenAlex 学术文献), 查不到证据才按学科惯例
     定名并首次括注原文, 禁止凭直觉生造 —— 见 ⑭ 节。两条分支(有表/退化)都要带上,
     且子项**不动编号**(仍是第 4 条, 不另起第 9 条); 子项开头须点名"下述 (a)(b)(c)
     三步" —— 规则 2/3/4 各有一组 (a)(b)(c), 不点名的话"(b)"会串到上一条去。

运行: venv python test_seg_export.py, 退出码 0=全过
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOLS, "seg_export.py")

# 两页合成侧车: p2 段尾无句末标点 + p3 段首小写 -> 必合并(⋮); p2 的纯字形段被过滤
SIDECAR = [
    {"page": 2, "vars": {"0": "breeding"},
     "segs": [{"raw": "The {v0} system of"}, {"raw": "{v1}"}]},
    {"page": 3, "vars": {"0": "breeding"},
     "segs": [{"raw": "plants is complex."}, {"raw": "See {v0}."}]},
]


def make_root(tmp, glossary_rows=None):
    """造沙箱: <root>/inbox + 可选 <root>/server/glossary/terms.csv"""
    root = tempfile.mkdtemp(dir=tmp)
    os.makedirs(os.path.join(root, "inbox"), exist_ok=True)
    if glossary_rows is not None:
        gdir = os.path.join(root, "server", "glossary")
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "terms.csv"), "w", encoding="utf-8") as f:
            f.write("\n".join(glossary_rows) + "\n")
    return root


def write_sidecar(root):
    p = os.path.join(root, "latest.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for o in SIDECAR:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return p


def run(root, args):
    env = dict(os.environ, P2Z_PROJ=root)
    proc = subprocess.run([sys.executable, SCRIPT] + args, env=env, cwd=root,
                          capture_output=True)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def read_payload(root, name):
    with open(os.path.join(root, "inbox", name + ".txt"), encoding="utf-8") as f:
        return f.read()


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

    with tempfile.TemporaryDirectory(prefix="p2z_seg_export_") as tmp:
        # ---- ① 抬头参数化 ----
        root = make_root(tmp)
        side = write_sidecar(root)
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p", "--sidecar", side,
                                  "--doc", "My Paper (J, 2020)", "--terms", ""])
        txt = read_payload(root, "p") if rc == 0 else ""
        check("① 导出成功", rc == 0, (rc, out, err))
        check("① 抬头用传入值", txt.splitlines()[0] == "[文档] My Paper (J, 2020) 第2-3页",
              txt.splitlines()[0] if txt else None)

        root = make_root(tmp)
        side = write_sidecar(root)
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p", "--sidecar", side,
                                  "--terms", ""])
        txt = read_payload(root, "p") if rc == 0 else ""
        check("② 缺省抬头只写页码", txt.splitlines()[0] == "[文档] 第2-3页",
              txt.splitlines()[0] if txt else None)
        check("② 不再残留别篇论文名", "Cactaceae" not in txt and "Desert Plants" not in txt)

        # ---- ③ 术语注入 ----
        root = make_root(tmp)
        side = write_sidecar(root)
        tpath = os.path.join(root, "t.csv")
        with open(tpath, "w", encoding="utf-8-sig") as f:
            f.write("alpha,甲\n\n# 注释行\nbeta,乙\ngamma,\nbroken\n")
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p", "--sidecar", side,
                                  "--terms", tpath])
        txt = read_payload(root, "p") if rc == 0 else ""
        terms = [l for l in txt.splitlines() if l.startswith("4. 术语统一")]
        check("③ 术语行存在且单行", len(terms) == 1, terms)
        check("③ 术语格式 en=zh", "alpha=甲；beta=乙。" in (terms[0] if terms else ""), terms)
        check("③ 空值/坏行/注释被跳过",
              "gamma" not in (terms[0] if terms else "") and "broken" not in (terms[0] if terms else ""),
              terms)

        # ---- ⑭ [v28.28] 术语查证子项: 挂在第 4 条末尾, 两条分支都得有 ----
        # 缘起: 豆包侧反馈"术语拿不准时凭直觉生造译名"。项目本就有查证能力
        # (doubao_bridge 的 search_term: Zotero 库 PDF 原文 + OpenAlex 学术文献),
        # 但规则里没写, 译者不知道要查。子项**不动编号**(第 4 条本就是术语条,
        # 单列第 9 条会离术语表太远), 且明写"手边没有该工具就直接进入定名" ——
        # 载荷会发给任何人的豆包, 不能假设桥一定注册了。
        check("⑭ 子项随术语行一起注入", "术语表里**没有**的专业术语不要凭直觉定名" in txt)
        check("⑭ 先查证(点名工具与两种证据源)",
              "search_term" in txt and "Zotero 库 PDF 原文" in txt and "OpenAlex 学术文献" in txt)
        check("⑭ 明写没有工具时的退化路径", "手边没有该工具也无妨，跳过查证、直接做下面 (b) 的定名" in txt)
        check("⑭ 组名点名(防 (b) 串到上一条的 (a)(b))",
              "按下述 (a)(b)(c) 三步定" in txt)
        check("⑭ 查不到证据才按学科惯例定名", "按学科惯例给出**一个**译名" in txt)
        check("⑭ 首次出现处括注原文", "首次出现处可在译名后括注原文" in txt and "之后不再括注" in txt)
        check("⑭ 禁止生造 / 留英文蒙混",
              "不得生造" in txt and "也不要原样留英文蒙混过去" in txt)
        check("⑭ 给了自查办法", "逐个答出依据" in txt and "回原句重译" in txt)
        check("⑭ 子项是第 4 条的缩进下属项(没另起编号)",
              len([l for l in txt.splitlines() if l.startswith("4. 术语统一")]) == 1
              # [v28.29] 第 9 条已被"整段译完"占用(内容门禁战例)。这条守的是**术语查证
              # 没有被单列成条**, 不是"第 9 条必须空着" —— 故改成查"没有 9. 术语…"
              # 并确认子项仍以缩进挂在第 4 条下。
              and not any(l.startswith("9. 术语") for l in txt.splitlines())
              and any(l.startswith("   术语表里") for l in txt.splitlines()))
        check("⑭ 例子是通用词(不是论文先验)",
              "tropical cyclone" in txt and "contrastive latent action" not in txt
              and "Cactaceae" not in txt)

        # ---- ④ 术语源缺失/置空 -> 退化, 不泄别领域术语 ----
        root = make_root(tmp)
        side = write_sidecar(root)
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p", "--sidecar", side,
                                  "--terms", os.path.join(root, "nope.csv")])
        txt = read_payload(root, "p") if rc == 0 else ""
        check("④ 缺文件仍导出成功", rc == 0, (rc, out, err))
        check("④ 退化为通用句", "4. 术语统一：按学科惯例统一译名" in txt)
        tlines = [l for l in txt.splitlines() if l.startswith("4. 术语统一")]
        check("④ 不注入任何具体术语", len(tlines) == 1 and "=" not in tlines[0], tlines)
        check("⑭ 术语表落空时查证子项仍在(退化不吞子项)",
              "术语表里**没有**的专业术语不要凭直觉定名" in txt and "search_term" in txt)

        root = make_root(tmp)
        side = write_sidecar(root)
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p", "--sidecar", side,
                                  "--terms", ""])
        txt = read_payload(root, "p") if rc == 0 else ""
        check("④ 空串亦退化", "4. 术语统一：按学科惯例统一译名" in txt)

        # ---- ⑤ 默认术语表 = P2Z_PROJ/server/glossary/terms.csv ----
        root = make_root(tmp, glossary_rows=["breeding system,繁殖系统", "herkogamy,雌雄异位"])
        side = write_sidecar(root)
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p", "--sidecar", side])
        txt = read_payload(root, "p") if rc == 0 else ""
        check("⑤ 缺省读项目术语表", "breeding system=繁殖系统；herkogamy=雌雄异位。" in txt, txt)

        # ---- ⑥ 默认术语表落空必须出声(沙箱改 P2Z_PROJ 会连带默认表一起落空) ----
        root = make_root(tmp)          # 无 server/glossary/terms.csv
        side = write_sidecar(root)
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p", "--sidecar", side])
        txt = read_payload(root, "p") if rc == 0 else ""
        check("⑥ 默认表缺失仍导出成功", rc == 0, (rc, out, err))
        check("⑥ 缺省读到空表 -> 退化", "4. 术语统一：按学科惯例统一译名" in txt)
        check("⑥ 缺省表落空时告警出声", "[警告] 默认术语表不存在" in out, out)
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p2", "--sidecar", side,
                                  "--terms", ""])
        check("⑥ 显式传空串不告警(是本人意图)", "[警告] 默认术语表不存在" not in out, out)

        # ---- ⑦ 既有契约不动: 编号 + 跨页合并 + manifest ----
        root = make_root(tmp)
        side = write_sidecar(root)
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p", "--sidecar", side])
        txt = read_payload(root, "p") if rc == 0 else ""
        check("⑦ S1/S2 编号守恒", "#S1" in txt and "#S2" in txt and "#S3" not in txt)
        check("⑦ 跨页合并保留 ⋮", "The breeding system of⋮plants is complex." in txt, txt)
        check("⑦ 字形已还原", "{v0}" not in txt and "breeding" in txt)
        with open(os.path.join(root, "inbox", "p.manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
        check("⑦ manifest 段数", len(man["items"]) == 2, man)
        check("⑦ manifest 合并标记", man["items"][0]["merged"] is True, man["items"][0])
        check("⑦ manifest 跨页 parts",
              [p["page"] for p in man["items"][0]["parts"]] == [2, 3], man["items"][0])

        # ---- ⑧ 第四断言(文献区禁汉化)的口径必须写进 payload 规则 ----
        # 跨类型实跑: 6 篇的"文献区汉化"FAIL 就是规则里没这一条; 其中中段文献区
        # (Melhani p43-44/50)连 skipLastPages 都救不了, 那些段必然进 payload,
        # 只能靠规则约束翻译方。规则口径与 post_check 第四断言同源(两种体例)。
        root = make_root(tmp)
        side = write_sidecar(root)
        rc, out, err = run(root, ["--pages", "2-3", "--name", "p8", "--sidecar", side])
        txt = read_payload(root, "p8") if rc == 0 else ""
        check("⑧ 导出成功", rc == 0, (rc, out, err))
        check("⑧ 规则含文献区禁汉化", "参考文献区不得汉化" in txt, txt[:200])
        check("⑧ 两种体例都点到",
              "行首 [n] 的编号制条目" in txt and "作者-年份制条目" in txt)
        check("⑧ 规则要求整条原样", "整条原样保留" in txt)
        check("⑧ 规则编号连续",
              "5. 中文通顺为学术散文" in txt and "6. 参考文献区不得汉化" in txt
              and "7. 公式片段" in txt)
        # ---- ⑨ 公式片段整块照抄(通用表述, 不举具体例子) ----
        # wang2026 的 4 处字形块 FAIL(λ=κi0,(43) / (44).✷ / L,αi / (A.3),Φt(k)≤)
        # 都是"中文被插进了公式片段+紧邻标点的块里"。v28.1 删掉硬编码的论文先验时
        # 连这条通用约束一起删了, 规则 5"语序可按中文习惯调整"反而鼓励了这种错法。
        check("⑨ 规则含公式片段整块照抄",
              "公式片段" in txt and "一整块" in txt and "块内不得插入中文" in txt)
        check("⑨ 语序让位于整块(块外调整)", "把中文放在块外" in txt)
        check("⑨ 未混入具体论文的例子(不是论文先验)",
              "(A.3)" not in txt and "λ=κ" not in txt)

        # ---- ⑪ [v28.12] 规则 2 补"数字与字母连写记号整体照抄" ----
        # 战例: Padmaprabhan p6#4 原文 "include {v29}D fabrication"({v29}='3'),
        # 豆包译成"三维制造" → 数字字形 '3' 没有落点, import 判 "高价值字形未回锚"。
        # 这不是代码缺陷(规则 2 本就要求数字原样保留), 是规则写得不够具体: 中文习惯
        # 把 3D 说成"三维", 译者会本能地把它当词翻译。补一条通用约束, 对任意论文生效。
        check("⑪ 规则 2 点到数字字母连写记号", "数字与字母连写的记号" in txt, txt[:300])
        check("⑪ 点明不得改写成中文说法",
              "不得改写成「三维」" in txt and "四乘四" in txt)
        check("⑪ 点明数字字符一个都不能少", "一个都不能少" in txt)
        check("⑪ 例子是通用记号(不是论文先验)", "3D" in txt and "4×4" in txt)

        # ---- ⑫ [v28.22] 规则 2 再补: 连写块**内**的空格与标点也是块的一部分 ----
        # 战例(2026-09-19 CLAP 收口): 同一篇豆包两个交件版本只在两处不同, 恰好是
        #   #S36 的 `88.8%,π0` 被写成「88.8%、π0」(块内半角逗号换成中文顿号);
        #   #S48 的 `A10080G`   被写成「A 10080G」(块内被插入空格)。
        # 两处都让 seg_import 判"高价值字形未回锚" -> import FAIL。
        # ⑪ 只锁了"数字字符不能少", 没覆盖"块内标点/空格不能动", 译者按中文习惯换
        # 标点(, -> 、)或为可读性补空格时, 规则没有一条能拦。补 (a)(b) 两条通用约束。
        check("⑫ 规则 2 点明块内空格不加不减", "空格不加不减" in txt, txt[:400])
        check("⑫ 点明不得插入空格", "不得插入空格" in txt)
        check("⑫ 规则 2 点明块内标点不换形状", "标点不换形状" in txt)
        check("⑫ 点明不得换成中文标点", "不得换成全角或中文标点" in txt)
        check("⑫ 给出逐字符自查办法", "逐字符" in txt and "顺手改通顺" in txt)
        check("⑫ 例子是通用数值(不是论文先验)",
              "A100" in txt and "0.5" in txt and "3,000" in txt
              and "A10080G" not in txt and "π0" not in txt)
        check("⑫ 规则 2 仍是一条第 2 项(编号未被打乱)",
              "\n3. ⋮ 是原文分页断点" in txt and "2. 数字、拉丁学名" in txt)

        # ---- ⑬ [v28.28] 规则 3(⋮) 补正反例 + 规则 8(残句碎片照译不补全) ----
        # 战例(2026-09-20 SILAGE): 交付稿被新版 deliver 内容门禁拦下 —— ⋮ 不符 4 条,
        # 逐段不变量不符 131 条, 整段错位带 13 条。原有的规则 3 只写了一句"除此之外
        # 不得出现该符号", 既没给正例(合并段该有几个), 也没给反例(普通段一个都不许有),
        # 译者把它当成了可自由使用的分隔符。残句碎片则是另一条: 跨页/跨栏切断产生的
        # 半词段被"补全"成通顺句子, 于是渲染拼接处重复、与原文对不上号。
        # 两条都是**通用约束**, 不举该篇的例子(否则就是论文先验)。
        check("⑬ 规则 3 给正例(照载荷个数保留)", "正例" in txt and "载荷里有 n 个" in txt)
        check("⑬ 规则 3 给反例(普通段一个都不许有)",
              "反例" in txt and "一个 ⋮ 都不许有" in txt)
        check("⑬ 规则 3 点明不得自行发明断点", "不许自己发明断点" in txt)
        check("⑬ 规则 3 给出数一数的自查办法", "两个数必须相等" in txt)
        check("⑬ 规则 3 仍是第 3 项且编号未乱",
              "\n3. ⋮ 是原文分页断点" in txt and "\n4. " in txt and "\n5. 中文通顺" in txt)
        check("⑬ 规则 8 点明「残句碎片照译不补全」", "残句碎片照译不补全" in txt, txt[-600:])
        check("⑬ 规则 8 三条子项(不补全/不臆测/不拼接)",
              "不要把半个词补成一个完整的词" in txt and "不要按上下文猜一个意思填进去" in txt
              and "不要把它和相邻段合起来译" in txt)
        check("⑬ 规则 8 说明渲染层会拼回", "渲染层会按原文位置把碎片拼回去" in txt)
        check("⑬ 未混入该篇的具体碎片例子(不是论文先验)",
              "three successful" not in txt and "SILAGE" not in txt and "三倍于" not in txt)

        # ---- ⑭ [v28.29] 新增规则 9(整段译完, 不许只译开头) ----
        # 战例(2026-09-20 SILAGE 第 3/4 版交付): 43 段**只译了开头** —— 载荷 398 字交 65 字、
        # 载荷 331 字交 77 字、载荷 289 字交 68 字, 丢掉的正是带数字的后半句, 于是被内容门禁
        # 逐段拦下, 连拒三轮。规则 8 只写了"碎片不许补全", 没说"完整句不许漏译", 译者把
        # "这段是碎片"当成了"译到哪儿算哪儿"的许可。故补第 9 条, 并明写与规则 8 的界限。
        check("⑭ 规则 9 点明整段译完、不许只译开头",
              "整段译完" in txt and "不许只译开头" in txt)
        check("⑭ 规则 9 与规则 8 划清界限(碎片到那儿为止 / 完整句必须补)",
              "见规则 8" in txt and "还有完整句子没译，就必须补上" in txt)
        check("⑭ 规则 9 给出比长度的自查办法", "三到六成" in txt and "回该段重译" in txt)
        check("⑭ 规则 9 仍是第 9 项(编号未乱)",
              "\n9. 整段译完" in txt and "\n8. 残句碎片照译不补全" in txt)
        check("⑭ 未混入该篇的具体例子(不是论文先验)",
              "#S386" not in txt and "SILAGE" not in txt)

        # ---- ⑩ [v28.10] --pages 是**真实 PDF 页码**(不是侧车回调计数) ----
        # 侧车的 page 是 receive_layout 的回调计数: 图形对象(end_figure)也各占一号,
        # 故图多的论文整体漂移 —— 实测 Melhani 真实第43,44页 = 侧车第55,56条(漂 12),
        # `--pages 43-44` 取回第31,32页正文, 库内照样命中、段号照样连续(静默错)。
        # 这里锁: 选页按 pageid+1; 同页的图形记录一并取回; 跨页合并按真实页相邻。
        pid_side = [
            {"page": 1, "pageid": 0, "vars": {"0": "alpha"},
             "segs": [{"raw": "Page one text here."}]},
            {"page": 2, "pageid": 1, "vars": {"0": "fig"},
             "segs": [{"raw": "Fig one label."}]},        # 图形回调, 真实第2页
            {"page": 3, "pageid": 1, "vars": {"0": "beta"},
             "segs": [{"raw": "Page two text here."}]},   # 真实第2页的正文记录
            {"page": 4, "pageid": 2, "vars": {},
             "segs": [{"raw": "Page three text."}]},
            {"page": 5, "pageid": 4, "vars": {},
             "segs": [{"raw": "trailing text without period"}]},
            {"page": 6, "pageid": 4, "vars": {},
             "segs": [{"raw": "inside figure label."}]},  # 与上一条同真实页 -> 不合并
            {"page": 7, "pageid": 5, "vars": {},
             "segs": [{"raw": "second page tail"}]},
            {"page": 8, "pageid": 6, "vars": {},
             "segs": [{"raw": "third page head"}]},       # 真实页相邻 -> 合并
        ]
        root = make_root(tmp)
        sp = os.path.join(root, "pid.jsonl")
        with open(sp, "w", encoding="utf-8") as f:
            for o in pid_side:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")

        rc, out, err = run(root, ["--pages", "2", "--name", "q2", "--sidecar", sp, "--terms", ""])
        t2 = read_payload(root, "q2") if rc == 0 else ""
        check("⑩ 按真实页码选页", rc == 0 and "Page two text here." in t2, (rc, out, err))
        check("⑩ 同页的图形记录一并取回", "Fig one label." in t2, t2)
        check("⑩ 不越界取到下一页", "Page three text." not in t2, t2)

        rc, out, err = run(root, ["--pages", "3", "--name", "q3", "--sidecar", sp, "--terms", ""])
        t3 = read_payload(root, "q3") if rc == 0 else ""
        check("⑩ 计数坐标 4 的记录按真实第3页取回",
              "Page three text." in t3 and "Page two text here." not in t3, t3)
        check("⑩ 控制台位置标注用真实页", "p3#0" in out and "p4#0" not in out, out)
        with open(os.path.join(root, "inbox", "q3.manifest.json"), encoding="utf-8") as f:
            man3 = json.load(f)
        check("⑩ manifest 仍用侧车内部坐标(import/inject 不受影响)",
              man3["items"][0]["parts"][0]["page"] == 4, man3["items"][0])
        # [v28.13] 同一条 part 另记 true_page=真实页码: 下游(seg_import 的报错与
        # 返工单)直接取用, 不必各自再算一遍 pageid 口径 —— 双口径写在一处才不会漂。
        check("⑩ manifest 另记真实页码 true_page",
              man3["items"][0]["parts"][0]["true_page"] == 3, man3["items"][0])

        rc, out, err = run(root, ["--pages", "4", "--name", "q4", "--sidecar", sp, "--terms", ""])
        check("⑩ 真实页码缺页被点名", rc != 0 and "侧车缺页: [4]" in out, (rc, out))

        rc, out, err = run(root, ["--pages", "5-7", "--name", "q57", "--sidecar", sp, "--terms", ""])
        t57 = read_payload(root, "q57") if rc == 0 else ""
        check("⑩ 同真实页不判跨页续接",
              "trailing text without period⋮" not in t57
              and "⋮inside figure label." not in t57
              and "inside figure label." in t57, t57)
        check("⑩ 真实页相邻仍合并", "second page tail⋮third page head" in t57, t57)
        with open(os.path.join(root, "inbox", "q57.manifest.json"), encoding="utf-8") as f:
            man57 = json.load(f)
        merged57 = [it for it in man57["items"] if it["merged"]]
        check("⑩ 合并段逐条记真实页码",
              len(merged57) == 1
              and [p["true_page"] for p in merged57[0]["parts"]] == [6, 7],
              man57["items"])

    print("\n结果: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
