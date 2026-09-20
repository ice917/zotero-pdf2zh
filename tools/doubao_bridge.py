# -*- coding: utf-8 -*-
"""doubao_bridge.py — pdf2zh ↔ 豆包桌面版 本地 MCP 桥 (STDIO, 零第三方依赖)

注册方式 (豆包「技能·连接器·伙伴 → 新建自定义连接器」):
  传输类型: STDIO
  命令:     <项目所用 python.exe, 如 .../envs/zotero-pdf2zh-venv/python.exe>
  参数:     <项目根>/tools/doubao_bridge.py
  环境变量: P2Z_PROJ=<项目根>   (不设则退回 D:\\zotero-pdf2zh)

暴露九个工具:
  list_inbox()                 列出待译 payload (<项目根>/inbox)
  get_payload(name)            读取 payload 全文 (编号段落包, #S1..#Sn, ⋮ 为跨页断点)
  list_reports(kind, limit)    列出质检/体检报告 (<项目根>/server/translated/review),
                               每条附一行结论(门禁判定 PASS/FAIL / 推荐跳页数)。
                               这是"矫正"的入口: 不先知道哪篇没过、为什么没过,
                               就无从改起。**不传 kind 时列全部类型** —— 交件被拒时
                               要看的是『门禁拒收』(逐段清单), 缺省藏起任何一类
                               都会让豆包"只看到失败、看不到哪几段"
  get_report(name)             读取一份报告全文(问题清单: 哪页、什么断言、原文片段)
  list_results(name, limit)    列出已经交过的译文 (<项目根>/out, 按时间倒序, 附段数)。
                               与 list_reports 配对: 报告说"哪儿错了", 这里给"上一版
                               长什么样" —— 两个都看得见, 改稿才是**改动**而不是重抄
  get_result(name)             读回一份已交译文全文(通常是豆包自己上一轮的稿子)。
                               改稿必须在它上面改: 看不到上一版就只能拿 inbox 原文
                               整篇重译, 实测那样会洗掉大部分已定稿段落并重新引入
                               公式字形块破坏
  submit_result(name, text)    保存译文到 <项目根>/out, 返回段号统计。
                               文件名统一落成 `<名>.doubao.txt` —— 与 tools/adopt.py
                               的 deliver 自动发现规则 (out/<name>.doubao*.txt) 对齐;
                               否则豆包交的件 deliver 认不到, 纯豆包环境就断在这
  merge_result(name, patch)    按段号把补丁并进**已有的**交件 (2026-09-20)。
                               只改了几段时用它, 不要整篇重吐 —— 整篇重吐要再吐全篇,
                               输出一长就撞上输出上限被截断, 下一轮又冒出一批"只译了
                               开头"的新段(实测自锁回路); 且替换会静默不生效(实测按段号
                               补 28 段, 脚本写错、没落地就交了)。这里逐段回读比对,
                               没生效当场报错; 未点到的段一个字节都不动
  search_term(term)            术语证据检索(等价于本地版搜索工具): 命中 Zotero 库 PDF 原文
                               上下文 + OpenAlex 学术文献, 返回证据供裁决; 只出证据,
                               不改译文、不写术语表

协议: JSON-RPC 2.0, stdin/stdout 每行一条消息; 日志只走 stderr。
"""
import json
import os
import re
import sys
import time
import traceback

# 路径**不焊死在本机** —— 与 tools/adopt.py / tools/seg_export.py 同一套环境变量
# 约定 (P2Z_PROJ / P2Z_INBOX)。桥是这条链上最容易被"换台电脑就废"的一环:
# 别的用户把项目装在别的盘符时, 焊死的 D:\zotero-pdf2zh 会让所有工具全部指向空目录。
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
INBOX = os.environ.get("P2Z_INBOX", os.path.join(PROJ, "inbox"))
OUTDIR = os.path.join(PROJ, "out")
# 报告目录: 与 tools/post_check.py 的 review_dir() 同一处 (server/translated/review)。
# 加这个常量是为了让豆包**看得见问题**: 矫正的前提是先拿到问题清单, 而清单就落在
# 这个目录里 —— 旧桥四个工具全指向 inbox/out, 豆包面前只有一篇原文, 不知道
# 自己哪一段被门禁判死。
REVIEW = os.environ.get("P2Z_REVIEW",
                        os.path.join(PROJ, "server", "translated", "review"))
MAX_READ = 4 * 1024 * 1024

S_LINE = re.compile(r"(?m)^\s*#S(\d+)\s*$")
_DOUBAO_SUFFIX = re.compile(r"\.doubao\d*$")
# out/ 下的交件: `<论文名>.doubao[序号].txt`。第 1 组是论文名, 第 2 组是轮次号。
# 只认这个形态 —— out/ 里还堆着 seg_import/seg_inject 的中间产物
# (.imported.json / .merged.txt / .raw.txt), 一律不能当"上一版交件"甩给豆包。
_RESULT_FILE = re.compile(r"^(.+?)\.doubao(\d*)\.txt$")

# review/ 下按前缀区分七种报告; 默认取第一种 —— 门禁报告才有 PASS/FAIL 判定
# 「门禁拒收」(2026-09-20 加) 是 deliver 阶段内容门禁拦下交件时落的清单: 哪几段
# 错位、哪几段数字/引用对不上。它比「翻译后质检」更靠前 —— 那份是出 PDF 之后的
# 质检, 而这份交件根本没进渲染。豆包改稿要照的就是它。
REPORT_KINDS = ("翻译后质检", "门禁拒收", "翻译前体检", "审校报告", "存疑清单",
                "术语查证报告", "解析沙盘")
_VERDICT = re.compile(r"(?m)^.*(?:门禁判定|推荐 skipLastPages).*$")
LIST_LIMIT_DEFAULT = 20
LIST_LIMIT_MAX = 100


def _log(msg):
    sys.stderr.write("[pdf2zh-bridge] %s\n" % msg)
    sys.stderr.flush()


def _safe_name(name):
    n = os.path.basename(str(name or "").strip())
    if not re.fullmatch(r"[A-Za-z0-9_\-. ]{1,80}", n):
        raise ValueError("非法文件名: %r (仅限字母数字-_. 与空格)" % n)
    return n


def _result_name(name):
    """交件文件名规范化 → `<stem>.doubao.txt`。

    为什么要规范化: tools/adopt.py 的 deliver 缺省只认 `out/<name>.doubao*.txt`
    (多轮定稿靠 .doubao/.doubao2/.doubao3 区分)。而豆包最常见的提交名是
    inbox 里那个原名 (`egophys2026.txt`) —— 直接落盘就落成 `out/egophys2026.txt`,
    deliver 找不到 → 断在交件这一步。

    实测 (2026-09-19 豆包日志): 之所以历史上没断, 是因为人在对话框里额外叮嘱了
    "存成 egophys2026.doubao.txt"。**依赖人记得说, 不是契约** —— 这个函数把它变成契约。

    已带 `.doubao` / `.doubao2` / `.doubao3` 的名字原样保留(不叠成 .doubao.doubao)。
    """
    stem, _ext = os.path.splitext(name)
    if not _DOUBAO_SUFFIX.search(stem):
        stem += ".doubao"
    return stem + ".txt"


def tool_list_inbox(args):
    os.makedirs(INBOX, exist_ok=True)
    items = []
    for fn in sorted(os.listdir(INBOX)):
        p = os.path.join(INBOX, fn)
        if os.path.isfile(p):
            items.append("%s  (%d 字节)" % (fn, os.path.getsize(p)))
    body = "\n".join(items) if items else "(空)"
    return "inbox/ 共 %d 个文件:\n%s" % (len(items), body)


def tool_get_payload(args):
    fn = _safe_name(args.get("name"))
    p = os.path.join(INBOX, fn)
    if not os.path.isfile(p):
        raise FileNotFoundError("不存在: %s (用 list_inbox 查看可用文件)" % fn)
    with open(p, "r", encoding="utf-8-sig") as f:
        return f.read(MAX_READ)


def _report_names(kind=None):
    """review/ 下的报告文件名, 按修改时间倒序(最新在前)。

    kind 是文件名前缀(如 "翻译后质检")。不传则返回该目录全部 .md。
    """
    if not os.path.isdir(REVIEW):
        return []
    names = [n for n in os.listdir(REVIEW) if n.endswith(".md")]
    if kind:
        names = [n for n in names if n.startswith(kind)]
    names.sort(key=lambda n: os.path.getmtime(os.path.join(REVIEW, n)), reverse=True)
    return names


def _headline(path):
    """抽报告里那一行结论(门禁判定 / 推荐跳页); 抽不到返回空串。

    只为让 list_reports 一眼看出"哪篇没过" —— 实测 review/ 里有 272 份报告,
    全读完再判断会撑爆上下文。只读头部 6KB: 判定行一律在开头, 而质检报告
    后面的断言明细可以到十几 KB。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            head = f.read(6000)
    except OSError:
        return ""
    m = _VERDICT.search(head)
    return m.group(0).lstrip("- ").strip() if m else ""


_ALL_KIND_SHOWN = 3      # 概览模式(不传 kind)下, 每类最多列几份


def _list_one_kind(kind, limit):
    """单类报告的清单文本, 每条附一行结论; 读某份全文用 get_report。"""
    names = _report_names(kind)
    if not names:
        return "「%s」: 0 份" % kind
    shown = names[:limit]
    lines = ["%s  (%d 字节)  %s" % (n, os.path.getsize(os.path.join(REVIEW, n)),
                                    _headline(os.path.join(REVIEW, n)))
             for n in shown]
    tail = ("" if len(names) <= limit
            else "\n… 另有 %d 份更早的未列出 (调大 limit 可取, 上限 %d)"
                 % (len(names) - limit, LIST_LIMIT_MAX))
    return ("「%s」共 %d 份, 最近 %d 份 (用 get_report 读全文):\n%s%s"
            % (kind, len(names), len(shown), "\n".join(lines), tail))


def tool_list_reports(args):
    """列出质检/体检报告, 每条附一行结论。

    这是"豆包能矫正"的关键一环: 矫正的前提是先知道**哪篇没过、为什么**。
    没有这个工具时, 豆包面前只有 inbox/ 里的原文 —— 活儿它干得了, 但它不知道
    自己上一轮哪一段被门禁判死了, 只能整篇重抄。

    [2026-09-20 修] 缺省**不再替豆包选一类**。旧版缺省取 REPORT_KINDS[0]
    『翻译后质检』, 于是 deliver 阶段落的『门禁拒收』(逐段清单: 哪几段留空/
    半截/错位) 在缺省调用里根本列不出来。实测 SILAGE: 豆包只看到一份泛泛的
    『翻译后质检 FAIL』(那是出 PDF **之后**的质检), 看不到那 30 段半截的明细,
    于是连着三轮把那批段原样重交 —— 回路卡死不收敛, 而"报告已落盘"这件事在
    代码与台账两侧都显示为正常。缺省必须"先让豆包看见有什么", 不是替它选一类。
    """
    raw = str(args.get("kind") or "").strip()
    try:
        limit = int(args.get("limit") or LIST_LIMIT_DEFAULT)
    except (TypeError, ValueError):
        limit = LIST_LIMIT_DEFAULT
    limit = max(1, min(limit, LIST_LIMIT_MAX))

    if not raw:
        return ("review/ 报告概览 (不传 kind 列全部类型, 每类最近 %d 份; "
                "只看一类请传 kind):\n\n%s"
                % (_ALL_KIND_SHOWN,
                   "\n\n".join(_list_one_kind(k, _ALL_KIND_SHOWN)
                               for k in REPORT_KINDS)))
    if raw not in REPORT_KINDS:
        raise ValueError("未知报告类型: %s (可选: %s)"
                         % (raw, " / ".join(REPORT_KINDS)))
    return _list_one_kind(raw, limit)


def tool_get_report(args):
    """读取一份报告全文 = 问题清单(哪页 / 什么断言 / 原文片段)。

    name 从 list_reports 的结果里原样复制。这里**不套 _safe_name**: 报告名带
    中文和空格, 会被它的 [A-Za-z0-9_.-] 白名单整体挡掉。改成"只认目录里真实
    存在的名字" —— name 必须先命中 os.listdir 的某一项(或唯一子串), 路径由
    目录项拼出, 所以 `..\\x.md` 这类名字天然匹配不上, 不构成越权。
    """
    raw = str(args.get("name") or "").strip()
    if not raw:
        raise ValueError("name 为空 (先用 list_reports 取文件名)")
    names = _report_names()
    hit = [n for n in names if n == raw] or [n for n in names if raw in n]
    if not hit:
        raise FileNotFoundError("review/ 下没有匹配 %r 的报告 (先用 list_reports 查看)"
                                % raw)
    if len(hit) > 1:
        raise ValueError("%r 匹配到 %d 份报告, 请写全名:\n%s"
                         % (raw, len(hit), "\n".join("  " + n for n in hit[:10])))
    with open(os.path.join(REVIEW, hit[0]), "r", encoding="utf-8-sig") as f:
        return f.read(MAX_READ)


def _result_names(stem=None):
    """out/ 下的交件文件名, 按修改时间倒序(最新在前)。

    stem 是论文名(如 "wang2026")。比对上放宽成前缀匹配 —— 交件名由 submit_result
    规范化而来, 但历史文件是人手起的, 别因为大小写/多一个空格就一条都列不出来。
    """
    if not os.path.isdir(OUTDIR):
        return []
    names = [n for n in os.listdir(OUTDIR) if _RESULT_FILE.match(n)]
    if stem:
        names = [n for n in names if n.lower().startswith(stem.lower() + ".doubao")]
    names.sort(key=lambda n: os.path.getmtime(os.path.join(OUTDIR, n)), reverse=True)
    return names


def _seg_count(path):
    """数一份交件里的 #S 段号个数; 读不动返回 -1。

    列交件必须带段数: 同一篇在 out/ 下有多轮(.doubao/.doubao2/.doubao3/.doubao4),
    光看字节数分不清哪份是全的、哪份是残件 —— 而"该在上一版基础上改"这件事,
    前提就是先认出哪份是全量上一版。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return len(S_LINE.findall(f.read(MAX_READ)))
    except OSError:
        return -1


def tool_list_results(args):
    """列出 out/ 下已交的译文(豆包历轮交件), 附段数与时间。

    为什么豆包需要这个: 采纳流程里的"矫正"应当是**改动**, 但旧桥只让它看得见
    inbox/ 的原文, 看不见自己上一轮交出去的稿子 —— 于是每次"矫正"都退化成整篇
    重译。实测事故 wang2026: doubao4 与上一版 doubao3 在 266 段里有 206 段不同,
    并因此新引入 4 处公式字形块破坏, 被 import 门禁整篇退回。
    先能读回上一版, 才谈得上"只改被点名的段落"。
    """
    stem = str(args.get("name") or "").strip()
    try:
        limit = int(args.get("limit") or LIST_LIMIT_DEFAULT)
    except (TypeError, ValueError):
        limit = LIST_LIMIT_DEFAULT
    limit = max(1, min(limit, LIST_LIMIT_MAX))

    names = _result_names(stem or None)
    if not names:
        return ("out/ 下没有%s交件。目录: %s"
                % ("「%s」的" % stem if stem else "任何", OUTDIR))
    shown = names[:limit]
    lines = []
    for n in shown:
        p = os.path.join(OUTDIR, n)
        nseg = _seg_count(p)
        lines.append("%s  (%d 字节, %s 段)  改于 %s"
                     % (n, os.path.getsize(p),
                        nseg if nseg >= 0 else "段数读不出",
                        time.strftime("%m-%d %H:%M",
                                      time.localtime(os.path.getmtime(p)))))
    tail = ("" if len(names) <= limit
            else "\n… 另有 %d 份更早的未列出 (调大 limit 可取, 上限 %d)"
                 % (len(names) - limit, LIST_LIMIT_MAX))
    return ("out/ 交件共 %d 份, 最近 %d 份 (用 get_result 读全文):\n%s%s"
            % (len(names), len(shown), "\n".join(lines), tail))


def tool_get_result(args):
    """读回一份已交译文(通常就是豆包自己上一轮的稿子)。

    改稿务必在**它**上面改: 看不到上一版, 只能拿 inbox 的原文重译一遍, 那一遍会把
    已定稿的段落整片洗掉, 并重新掷一次字形块/编号的骰子(实测事故见 list_results)。

    取名规则与 get_report 一致: 只认 out/ 目录里真实存在的名字(允许唯一子串),
    不套 _safe_name、不拼路径参数 —— 名字必须命中 os.listdir 的某一项,
    `..\\x` 这类天然匹配不上, 不构成越权。
    """
    raw = str(args.get("name") or "").strip()
    if not raw:
        raise ValueError("name 为空 (先用 list_results 取文件名)")
    names = _result_names()
    hit = [n for n in names if n == raw] or [n for n in names if raw in n]
    if not hit:
        raise FileNotFoundError("out/ 下没有匹配 %r 的交件 (先用 list_results 查看)"
                                % raw)
    if len(hit) > 1:
        raise ValueError("%r 匹配到 %d 份交件, 请写全名:\n%s"
                         % (raw, len(hit), "\n".join("  " + n for n in hit[:10])))
    with open(os.path.join(OUTDIR, hit[0]), "r", encoding="utf-8-sig") as f:
        return f.read(MAX_READ)


def _blocks(text):
    """把交付文本拆成 {段号: 该段正文}。`#S编号` 行本身不算正文。"""
    out, cur = {}, None
    for line in text.splitlines():
        m = S_LINE.match(line)
        if m:
            cur = int(m.group(1))
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _prev_round(stem, fn):
    """同一篇的上一版交件路径(排除 fn 自己); 没有则 None。"""
    for n in _result_names(stem):
        if n != fn:
            return os.path.join(OUTDIR, n)
    return None


def _diff_line(prev, text):
    """本次交件与上一版相比改了几段 —— 交给豆包的自我核对; 失败返回空串。

    为什么值得算: "矫正"和"重译"从交付文本上看不出区别, 直到门禁把整篇退回。
    实测 wang2026 那一轮, 豆包以为在"按报告改", 实际 266 段里改了 206 段, 并因此
    新踩 4 处公式字形块。有这一行, 它交完立刻能自己发现"改面过大 = 我在重译"。
    只做提示: 任何异常都不影响交件本身。
    """
    try:
        with open(prev, "r", encoding="utf-8-sig") as f:
            a = _blocks(f.read(MAX_READ))
        b = _blocks(text)
    except OSError:
        return ""
    if not a or not b:
        return ""
    diff = [k for k in set(a) | set(b) if a.get(k, "") != b.get(k, "")]
    line = "\n与上一版 %s 相比: %d/%d 段有改动" % (os.path.basename(prev), len(diff), len(b))
    if len(diff) > len(b) * 0.3:
        line += (" —— 改动面过大, 若本意是只修几处, 说明这一轮是在重译。"
                 "请先用 get_result 读回上一版, 在它上面改")
    return line


def tool_submit_result(args):
    fn = _safe_name(args.get("name"))
    text = str(args.get("text") or "")
    if not text.strip():
        raise ValueError("text 为空, 拒绝保存")
    os.makedirs(OUTDIR, exist_ok=True)
    fn = _result_name(fn)
    p = os.path.join(OUTDIR, fn)
    # 先认上一版再由它算差异: 写盘之后最新的那份就成了自己, 认出来没有意义
    prev = _prev_round(_RESULT_FILE.match(fn).group(1), fn)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    segs = S_LINE.findall(text)
    tail = ",".join(segs) if segs else "未检测到(若本批含段号则异常)"
    stats = "已保存: %s (%d 字符), 段号 #S: %s" % (p, len(text), tail)
    if prev:
        stats += _diff_line(prev, text)
    _log(stats)
    return stats


def _resolve_result(raw):
    """把豆包给的名字解析成 out/ 里**真实存在的那一份**(唯一); 返回 (文件名, 全路径)。

    与 get_result 同一口径(只认 listdir 命中的名字, 不拼路径), 额外允许直接给 payload
    原名 —— 名字经 _result_name 规范化后再找一遍, 这样"egophys2026.txt"也认。
    """
    names = _result_names()
    hit = [n for n in names if n == raw] or [n for n in names if raw in n]
    if not hit:
        norm = _result_name(_safe_name(raw))
        hit = [n for n in names if n == norm]
    if not hit:
        raise FileNotFoundError("out/ 下没有匹配 %r 的交件 (先用 list_results 查看)" % raw)
    if len(hit) > 1:
        raise ValueError("%r 匹配到 %d 份交件, 请写全名:\n%s"
                         % (raw, len(hit), "\n".join("  " + n for n in hit[:10])))
    return hit[0], os.path.join(OUTDIR, hit[0])


def tool_merge_result(args):
    """按段号把补丁并进已有交件 —— **只改了少数几段时用这个, 不要整篇重吐**。

    为什么单开一个工具: submit_result 收的是**全文**。门禁每轮只点名几十段, 而"整篇重交"
    要把 ~790 段再吐一遍, 输出一长就撞上模型输出上限被截断 —— 下一轮又冒出一批"只译了
    开头"的新段(实测自锁回路)。更坏的是替换会**静默不生效**: 实测按段号补 28 段, 脚本的
    替换逻辑写错、改完没落地就交了, 全程无人察觉。所以这里写回前逐段回读比对,
    没生效当场报出段号; 要么全部生效, 要么交件完全没动。

    patch 只写要改的段(格式与交件相同, 只少掉不改的段); 未在 patch 里出现的段
    **一个字节都不动**(不是重排全文, 不会顶掉 ⋮ 断点或段间空行)。
    返回值附"长度比自查": 仍偏低(15%~30%)的段一并列出来 —— 与门禁报告同一口径。
    """
    raw = str(args.get("name") or "").strip()
    if not raw:
        raise ValueError("name 为空 (先用 list_results 取文件名)")
    patch_text = str(args.get("patch") or "")
    if not patch_text.strip():
        raise ValueError("patch 为空, 拒绝合稿")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import seg_merge as SM          # 懒加载: 桥本身零依赖, 只有这条路径需要它

    patch = SM.parse_patch(patch_text)
    fn, path = _resolve_result(raw)
    ok, info = SM.merge_file(path, patch)
    if not ok:
        raise ValueError("%s (交件 %s 未被改动)" % (info["error"], fn))
    stats = ("已按段号合入 %d 段到 %s; 未点到的段一个字节都没动 (字节 %d -> %d)"
             % (info["applied"], fn, info["bytes_before"], info["bytes_after"]))
    if info["noop"]:
        stats += ("\n⚠️ 这些段的新文本与原文**一模一样**(等于没改, 确认是有意为之?): %s"
                  % ", ".join("#S%d" % k for k in info["noop"]))
    stats += "\n" + "\n".join(SM.audit_text(_RESULT_FILE.match(fn).group(1), info["text"]))
    _log("合稿 %s: %d 段生效, %d 段未改动" % (fn, info["applied"], len(info["noop"])))
    return stats


def tool_search_term(args):
    """术语证据检索——给豆包一个可调用的"搜索工具"（等价于本地版 tavily）。

    为什么放在这个桥里: 豆包就是采纳流程里的那个 LLM, 它定稿时拿不准某个术语
    该怎么译, 可以自己调这个工具取证据, 而不是等人喂数据。

    返回的是证据文本(不落文件, 避免每问一次就往 review/ 丢一份报告)。默认源
    zotero,openalex —— 都零部署、零代理; zotero 需要 Zotero 正在运行且开启了
    本地 API(设置→高级), 它给的是**自己库里 PDF 的原文上下文**, 命中率最高。
    证据只供裁决: 不改译文、不写术语表(与 term_verify 的"只读"原则一致)。
    """
    term = " ".join(str(args.get("term") or "").split())
    if not term:
        raise ValueError("term 为空")
    if len(term) > 80:
        raise ValueError("term 过长(%d 字符), 请一次查一个术语" % len(term))
    spec = str(args.get("sources") or "zotero,openalex").strip()
    try:
        per = int(args.get("max_results") or 5)
    except (TypeError, ValueError):
        per = 5
    per = max(1, min(per, 10))

    # 延迟导入: 保持桥启动开销为零, 且 term_verify 若缺失只影响这一个工具。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import term_verify as tv

    try:
        sources = tv.resolve_sources(spec)
    except ValueError as exc:
        raise ValueError("%s (可选: %s)" % (exc, ", ".join(tv.SOURCES)))

    # 开跑前剔除不可达源: 否则被阻断的源会让这一次查证空转几十秒
    alive, dead = tv.preflight_sources(sources)
    if not alive:
        raise RuntimeError("检索源均不可达: %s"
                           % "; ".join("%s(%s)" % (n, w) for n, w in dead))
    _log("查证 %r 源=%s 每源最多 %d 条" % (term, "+".join(alive), per))

    t0 = time.time()
    # max_retries=1: 交互式工具要快速失败。实测 OpenAlex 被限流(HTTP 429)时,
    # 默认的 4 次重试 + 2s/4s/8s 退避会把这个工具卡 16.7s 才返回;
    # 快速失败后仍是"zotero 有结果就照常给, openalex 那行如实报失败"。
    res = tv.search_multi(term, alive, max_results=per, max_retries=1)
    results = [{"term": term, "para": "-", "note": "MCP 工具 search_term", "res": res}]
    report = tv.build_report("MCP 工具 search_term", results, time.time() - t0,
                             sources=alive, dropped=dead)
    _log("查证 %r 完成: ok=%s 命中 %d 条, %.1fs"
         % (term, res.get("ok"), len(res.get("papers") or []), time.time() - t0))
    return report


TOOLS = [
    {
        "name": "list_inbox",
        "description": "列出 pdf2zh 待译目录 inbox 下的 payload 文件",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_payload",
        "description": "读取待译 payload 全文: 编号段落包, 每段以 #S编号 行开头; ⋮ 表示原文跨页断点。"
                       "若某段里有拿不准的术语, 先用 search_term 取该术语在用户库里的原文证据再定译法",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "inbox 下的文件名, 如 payload_test.txt"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_reports",
        "description": "列出质检报告, 每条附一行结论。**改稿前先看这里** —— 报告里写着哪篇/哪页/哪段被门禁判死"
                       "(如「第11,12页: 文献区汉化断言失败」), 照着改才叫矫正; 不看就整篇重交, 等于重抄一遍。"
                       "**不传 kind 时列出全部类型的概览(每类最近 3 份, 一类都不藏)** —— 上一条交件被拒时, "
                       "要看的是『门禁拒收』: 那是逐段清单(哪几段留空/只译半截/整段错位), "
                       "比『翻译后质检』更靠前 —— 后者是出 PDF 之后的质检, 而被拒的交件根本没进渲染。"
                       "kind 可选: 翻译后质检 / 门禁拒收 / 翻译前体检 / 审校报告 / 存疑清单 / 术语查证报告 / 解析沙盘",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "报告类型; 不传 = 列全部类型概览"},
                "limit": {"type": "integer", "description": "该类最多列几份(按时间倒序), 默认 20, 上限 100; 不传 kind 时按每类 3 份"},
            },
            "required": [],
        },
    },
    {
        "name": "get_report",
        "description": "读一份报告全文 = 问题清单: 哪一页、哪条断言、什么证据。"
                       "name 从 list_reports 的结果里原样复制(报告名含中文与空格)。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "报告文件名, 如 翻译后质检_20260919_104922_Vaswani….md"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_results",
        "description": "列出 out/ 下**已经交过的译文**(按时间倒序, 附带段数与时间)。"
                       "**改稿/矫正前先看这里** —— 与 list_reports 配对: 报告说'哪儿错了', "
                       "这里给'上一版长什么样'。两个都能看见, 改稿才是改动; 只看得见 inbox 原文, "
                       "每一次'改'都会变成整篇重译, 而重译会把已定稿的段落洗掉、并重新踩公式字形块。"
                       "name 传论文名可只看该篇的历轮; 不传则列全部",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "论文名, 如 wang2026(只看该篇); 留空列全部"},
                "limit": {"type": "integer", "description": "最多列几份(按时间倒序), 默认 20, 上限 100"},
            },
            "required": [],
        },
    },
    {
        "name": "get_result",
        "description": "读回一份已交译文全文(通常是你自己上一轮的稿子)。"
                       "**改稿必须在它上面改, 不要从 inbox 的原文重译** —— 重译一遍等于把上一版"
                       "整片作废, 已定稿的段落全丢, 还会重新引入公式字形块/编号错误。"
                       "name 从 list_results 的结果里原样复制。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "交件文件名, 如 wang2026.doubao3.txt"}},
            "required": ["name"],
        },
    },
    {
        "name": "submit_result",
        "description": "提交译文: 保存到 out/ 并返回统计。每段译文行首保留 #S编号, 跨页断点处保留 ⋮。"
                       "name 直接填 get_payload 取件时那个**原名**即可 (如 egophys2026.txt) —— "
                       "落盘时会自动规范成 `原名.doubao.txt`, 交给 adopt 的 deliver 认领; "
                       "若要交多轮定稿, 用 `egophys2026.doubao2.txt` 这样带序号的名字。"
                       "返回值会附上与上一版相比的改动段数: 若提示'改动面过大', 说明这一轮是在重译, "
                       "应当先用 get_result 读回上一版再改",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "结果文件名, 通常就是 payload 原名, 如 payload_test.txt"},
                "text": {"type": "string", "description": "全部文本, 含 #S 编号行"},
            },
            "required": ["name", "text"],
        },
    },
    {
        "name": "merge_result",
        "description": "按段号把补丁并进已有交件 —— **只改了几段时用这个, 不要整篇重吐**。"
                       "门禁每轮只点名几十段; 整篇重交要把全篇再吐一遍, 输出一长就撞上输出上限被截断, "
                       "下一轮又冒出一批'只译了开头'的新段(SILAGE 第四轮 43 段就是这么来的)。"
                       "这里只把 patch 里的段并进去, 未点到的段**一个字节都不动**, "
                       "且写回前逐段回读比对 —— 没生效会当场报错, 不会再有'以为改了实际没改'。"
                       "patch 只写要改的段, 格式与交件相同(#S编号 行 + 该段正文), 例如: "
                       "#S386\\n新译文……\\n#S442\\n另一段新译文……"
                       "返回: 生效段数 + 仍未改的段 + 长度比自查(哪些段还是偏短)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "要改的交件名(list_results 里的名字, 或 payload 原名), 如 egophys2026.doubao.txt"},
                "patch": {"type": "string",
                          "description": "只含要改的段: #S编号 行 + 该段新译文; 没出现的段一律不动"},
            },
            "required": ["name", "patch"],
        },
    },
    {
        "name": "search_term",
        "description": (
            "从**用户自己的文献库**取术语的用法证据。这不是通用搜索——"
            "你自带的联网搜索拿不到用户收藏的 PDF 原文，所以别用自己的知识或联网搜索代替它。"
            "它做两件事：① 在用户 Zotero 库已收藏 PDF 的全文里找到该术语，"
            "摘出它出现的那句原文上下文；② 查 OpenAlex 学术文献的标题与被引数。"
            "返回一份可直接引用的证据报告（含来源标注）。"
            "调用时机：用户要你核实某个术语的用法/译法、给某个译名找依据、"
            "或者说了「查证」「找证据」「看看原文里是怎么用的」「这词在文献里怎么用的」时。"
            "只出证据——不改译文、不写术语表，译名仍由用户裁决。"
            "一次查一个术语；中文术语不查 Zotero（其全文索引对汉字是字符级松散匹配，噪音大）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "要查的术语，一次一个，如 herkogamy"},
                "sources": {"type": "string",
                            "description": "检索源，逗号分隔或 all。默认 zotero,openalex（都零部署零代理）；"
                                           "wikidata 能直接给中文名，wikipedia 给定义性语境（后两者需代理）"},
                "max_results": {"type": "integer",
                                "description": "每个源最多返回几条，默认 5，上限 10"},
            },
            "required": ["term"],
        },
    },
]

_DISPATCH = {
    "list_inbox": tool_list_inbox,
    "get_payload": tool_get_payload,
    "list_reports": tool_list_reports,
    "get_report": tool_get_report,
    "list_results": tool_list_results,
    "get_result": tool_get_result,
    "submit_result": tool_submit_result,
    "merge_result": tool_merge_result,
    "search_term": tool_search_term,
}


def _reply(msg_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _tool_call(msg_id, params):
    name = params.get("name")
    args = params.get("arguments") or {}
    try:
        text = _DISPATCH[name](args)
        _reply(msg_id, result={"content": [{"type": "text", "text": text}], "isError": False})
    except Exception as exc:
        _log("工具失败 %s: %s" % (name, exc))
        _reply(msg_id, result={"content": [{"type": "text", "text": "错误: %s" % exc}], "isError": True})


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _log("stdio 启动, inbox=%s out=%s review=%s" % (INBOX, OUTDIR, REVIEW))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method = msg.get("method", "")
        msg_id = msg.get("id")
        is_notification = msg_id is None
        if method == "initialize":
            _reply(msg_id, result={
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pdf2zh-bridge", "version": "0.1.0"},
            })
        elif method == "ping":
            if not is_notification:
                _reply(msg_id, result={})
        elif method == "tools/list":
            _reply(msg_id, result={"tools": TOOLS})
        elif method == "tools/call":
            _tool_call(msg_id, msg.get("params") or {})
        else:
            if not is_notification:
                _reply(msg_id, error={"code": -32601, "message": "未知方法: %s" % method})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log(traceback.format_exc())
        raise
