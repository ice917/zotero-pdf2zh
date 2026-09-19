# -*- coding: utf-8 -*-
"""doubao_bridge.py — pdf2zh ↔ 豆包桌面版 本地 MCP 桥 (STDIO, 零第三方依赖)

注册方式 (豆包「技能·连接器·伙伴 → 新建自定义连接器」):
  传输类型: STDIO
  命令:     <项目所用 python.exe, 如 .../envs/zotero-pdf2zh-venv/python.exe>
  参数:     <项目根>/tools/doubao_bridge.py
  环境变量: P2Z_PROJ=<项目根>   (不设则退回 D:\\zotero-pdf2zh)

暴露四个工具:
  list_inbox()                 列出待译 payload (<项目根>/inbox)
  get_payload(name)            读取 payload 全文 (编号段落包, #S1..#Sn, ⋮ 为跨页断点)
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
# 别的用户把项目装在别的盘符时, 焊死的 D:\zotero-pdf2zh 会让四个工具全部指向空目录。
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
INBOX = os.environ.get("P2Z_INBOX", os.path.join(PROJ, "inbox"))
OUTDIR = os.path.join(PROJ, "out")
MAX_READ = 4 * 1024 * 1024

S_LINE = re.compile(r"(?m)^\s*#S(\d+)\s*$")
_DOUBAO_SUFFIX = re.compile(r"\.doubao\d*$")


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
    _log("stdio 启动, inbox=%s out=%s" % (INBOX, OUTDIR))
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
