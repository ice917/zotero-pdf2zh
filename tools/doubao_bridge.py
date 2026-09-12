# -*- coding: utf-8 -*-
"""doubao_bridge.py — pdf2zh ↔ 豆包桌面版 本地 MCP 桥 (STDIO, 零第三方依赖)

注册方式 (豆包「技能·连接器·伙伴 → 新建自定义连接器」):
  传输类型: STDIO
  命令:     D:/Users/<user>/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  参数:     D:/zotero-pdf2zh/tools/doubao_bridge.py

暴露三个工具:
  list_inbox()                 列出待译 payload (D:/zotero-pdf2zh/inbox)
  get_payload(name)            读取 payload 全文 (编号段落包, #S1..#Sn, ⋮ 为跨页断点)
  submit_result(name, text)    保存译文到 D:/zotero-pdf2zh/out, 返回段号统计

协议: JSON-RPC 2.0, stdin/stdout 每行一条消息; 日志只走 stderr。
"""
import json
import os
import re
import sys
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
]

_DISPATCH = {
    "list_inbox": tool_list_inbox,
    "get_payload": tool_get_payload,
    "submit_result": tool_submit_result,
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
