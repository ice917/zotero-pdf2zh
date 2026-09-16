# -*- coding: utf-8 -*-
"""doubao_bridge.py — pdf2zh ↔ 豆包桌面版 本地 MCP 桥 (STDIO, 零第三方依赖)

注册方式 (豆包「技能·连接器·伙伴 → 新建自定义连接器」):
  传输类型: STDIO
  命令:     D:/Users/<user>/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  参数:     D:/zotero-pdf2zh/tools/doubao_bridge.py

暴露四个工具:
  list_inbox()                 列出待译 payload (D:/zotero-pdf2zh/inbox)
  get_payload(name)            读取 payload 全文 (编号段落包, #S1..#Sn, ⋮ 为跨页断点)
  submit_result(name, text)    保存译文到 D:/zotero-pdf2zh/out, 返回段号统计
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

INBOX = r"D:\zotero-pdf2zh\inbox"
OUTDIR = r"D:\zotero-pdf2zh\out"
MAX_READ = 4 * 1024 * 1024

S_LINE = re.compile(r"(?m)^\s*#S(\d+)\s*$")


def _log(msg):
    sys.stderr.write("[pdf2zh-bridge] %s\n" % msg)
    sys.stderr.flush()


def _safe_name(name):
    n = os.path.basename(str(name or "").strip())
    if not re.fullmatch(r"[A-Za-z0-9_\-. ]{1,80}", n):
        raise ValueError("非法文件名: %r (仅限字母数字-_. 与空格)" % n)
    return n


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
    if not re.search(r"\.(txt|md)$", fn):
        fn += ".md"
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
        "description": "列出 pdf2zh 待译目录 D:/zotero-pdf2zh/inbox 下的 payload 文件",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_payload",
        "description": "读取待译 payload 全文: 编号段落包, 每段以 #S编号 行开头; ⋮ 表示原文跨页断点",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "inbox 下的文件名, 如 payload_test.txt"}},
            "required": ["name"],
        },
    },
    {
        "name": "submit_result",
        "description": "提交译文: 保存到 D:/zotero-pdf2zh/out 并返回统计。每段译文行首保留 #S编号, 跨页断点处保留 ⋮",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "结果文件名, 如 roundtrip_test.txt"},
                "text": {"type": "string", "description": "全部文本, 含 #S 编号行"},
            },
            "required": ["name", "text"],
        },
    },
    {
        "name": "search_term",
        "description": (
            "检索术语的真实用法证据（本地搜索工具）。定稿时拿不准某个术语该怎么译就调它："
            "会去你 Zotero 库的 PDF 全文里找原文上下文，并查 OpenAlex 学术文献（标题 + 被引数），"
            "返回一份证据报告。只出证据——不改译文、不写术语表，译名仍由你裁决。"
            "一次查一个术语；中文术语不查 Zotero（其全文索引对汉字是字符级松散匹配，噪音大）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "要查的术语，如 herkogamy"},
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
