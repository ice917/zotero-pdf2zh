# -*- coding: utf-8 -*-
"""doubao_bridge.py — pdf2zh ↔ 豆包桌面版 本地 MCP 桥 (STDIO, 零第三方依赖)

注册方式 (豆包「技能·连接器·伙伴 → 新建自定义连接器」):
  传输类型: STDIO
  命令:     <项目所用 python.exe, 如 .../envs/zotero-pdf2zh-venv/python.exe>
  参数:     <项目根>/tools/doubao_bridge.py
  环境变量: P2Z_PROJ=<项目根>   (不设则退回 D:\\zotero-pdf2zh)

暴露六个工具:
  list_inbox()                 列出待译 payload (<项目根>/inbox)
  get_payload(name)            读取 payload 全文 (编号段落包, #S1..#Sn, ⋮ 为跨页断点)
  list_reports(kind, limit)    列出质检/体检报告 (<项目根>/server/translated/review),
                               每条附一行结论(门禁判定 PASS/FAIL / 推荐跳页数)。
                               这是"矫正"的入口: 不先知道哪篇没过、为什么没过,
                               就无从改起
  get_report(name)             读取一份报告全文(问题清单: 哪页、什么断言、原文片段)
  submit_result(name, text)    保存译文到 <项目根>/out, 返回段号统计。
                               文件名统一落成 `<名>.doubao.txt` —— 与 tools/adopt.py
                               的 deliver 自动发现规则 (out/<name>.doubao*.txt) 对齐;
                               否则豆包交的件 deliver 认不到, 纯豆包环境就断在这
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

# review/ 下按前缀区分六种报告; 默认取第一种 —— 门禁报告才有 PASS/FAIL 判定
REPORT_KINDS = ("翻译后质检", "翻译前体检", "审校报告", "存疑清单",
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


def tool_list_reports(args):
    """列出质检/体检报告, 每条附一行结论。

    这是"豆包能矫正"的关键一环: 矫正的前提是先知道**哪篇没过、为什么**。
    没有这个工具时, 豆包面前只有 inbox/ 里的原文 —— 活儿它干得了, 但它不知道
    自己上一轮哪一段被门禁判死了, 只能整篇重抄。
    """
    kind = str(args.get("kind") or REPORT_KINDS[0]).strip()
    if kind not in REPORT_KINDS:
        raise ValueError("未知报告类型: %s (可选: %s)"
                         % (kind, " / ".join(REPORT_KINDS)))
    try:
        limit = int(args.get("limit") or LIST_LIMIT_DEFAULT)
    except (TypeError, ValueError):
        limit = LIST_LIMIT_DEFAULT
    limit = max(1, min(limit, LIST_LIMIT_MAX))

    names = _report_names(kind)
    if not names:
        return "review/ 下没有「%s」报告。目录: %s" % (kind, REVIEW)
    shown = names[:limit]
    lines = ["%s  (%d 字节)  %s" % (n, os.path.getsize(os.path.join(REVIEW, n)),
                                    _headline(os.path.join(REVIEW, n)))
             for n in shown]
    tail = ("" if len(names) <= limit
            else "\n… 另有 %d 份更早的未列出 (调大 limit 可取, 上限 %d)"
                 % (len(names) - limit, LIST_LIMIT_MAX))
    return ("review/「%s」共 %d 份, 最近 %d 份 (用 get_report 读全文):\n%s%s"
            % (kind, len(names), len(shown), "\n".join(lines), tail))


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


def tool_submit_result(args):
    fn = _safe_name(args.get("name"))
    text = str(args.get("text") or "")
    if not text.strip():
        raise ValueError("text 为空, 拒绝保存")
    os.makedirs(OUTDIR, exist_ok=True)
    fn = _result_name(fn)
    p = os.path.join(OUTDIR, fn)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    segs = S_LINE.findall(text)
    tail = ",".join(segs) if segs else "未检测到(若本批含段号则异常)"
    stats = "已保存: %s (%d 字符), 段号 #S: %s" % (p, len(text), tail)
    _log(stats)
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
                       "默认看『翻译后质检』(门禁报告, 判定行是 PASS/FAIL)。kind 可选: "
                       "翻译后质检 / 翻译前体检 / 审校报告 / 存疑清单 / 术语查证报告 / 解析沙盘",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "报告类型, 默认『翻译后质检』"},
                "limit": {"type": "integer", "description": "最多列几份(按时间倒序), 默认 20, 上限 100"},
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
        "name": "submit_result",
        "description": "提交译文: 保存到 out/ 并返回统计。每段译文行首保留 #S编号, 跨页断点处保留 ⋮。"
                       "name 直接填 get_payload 取件时那个**原名**即可 (如 egophys2026.txt) —— "
                       "落盘时会自动规范成 `原名.doubao.txt`, 交给 adopt 的 deliver 认领; "
                       "若要交多轮定稿, 用 `egophys2026.doubao2.txt` 这样带序号的名字",
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
    "submit_result": tool_submit_result,
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
