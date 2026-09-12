# -*- coding: utf-8 -*-
"""force 重渲染: 缓存手术后把库内修复真正落到 PDF。

为什么要工具化:
  直提 /translate 必须带"全插件同等 config 字段" —— 少传 service/targetLang
  会让 Config 静默回落 bing(targetLang 也变 zh-Hans), 缓存键全变 → 整文档
  重译(2026-09-10 实测 341 段 / 4.5 分钟)。把字段固化在此, 免得每次手写
  JSON 踩同一个坑。

判据 (零额外成本的自检):
  - 全缓存命中: 渲染秒级~数十秒完成, 且产物里能看到库内手改的串;
  - 一旦"新串没落页"或耗时数分钟, 基本就是 config 没对齐(换键重译)。

用法 (解释器同 tools/tests/run_all.py; 仓库无 venv 目录, 用绝对路径):
  PY = D:/Users/<user>/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  & $PY tools/force_rerender.py --pdf "D:/zotero-pdf2zh/server/translated/xxx.pdf"
  & $PY tools/force_rerender.py --pdf "..." --no-force   # 走正常去重(不加急)
  默认轮询到任务结束; --no-wait 只提交不等待。
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:8890"

# 与 Zotero 插件直提时等价的配置; 缺一不可(见模块 docstring 的事故)
PLUGIN_CONFIG = {
    "engine": "pdf2zh",
    "service": "silicon",
    "sourceLang": "en",
    "targetLang": "zh-CN",
    "threadNum": 4,
    "skipLastPages": 0,
    "noWatermark": True,
    "mono": True,
    "dual": True,
    "skipSubsetFonts": True,
    "asyncJob": True,
}


def post_json(path, payload, timeout=120):
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(path, timeout=30):
    with urllib.request.urlopen(HOST + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def health():
    try:
        return get_json("/health")
    except Exception as exc:
        return {"error": str(exc)}


def find_task(task_id):
    """在活跃任务列表里找本任务; 不在则返回 None(已完成 30s 后清理)。"""
    data = get_json("/api/tasks")
    for t in data.get("tasks", []):
        if t.get("taskId") == task_id:
            return t
    return None


def historical(task_id):
    data = get_json("/api/history")
    for h in data.get("history", []):
        if h.get("taskId") == task_id:
            return h
    return None


def main():
    ap = argparse.ArgumentParser(description="force 重渲染(缓存手术后落产物)")
    ap.add_argument("--pdf", required=True, help="原文 PDF 绝对路径")
    ap.add_argument("--service", default=PLUGIN_CONFIG["service"])
    ap.add_argument("--force", dest="force", action="store_true", default=True)
    ap.add_argument("--no-force", dest="force", action="store_false",
                    help="走正常去重(不加急)")
    ap.add_argument("--timeout", type=int, default=900, help="等待上限秒数")
    ap.add_argument("--no-wait", action="store_true", help="只提交不等待")
    args = ap.parse_args()

    pdf = os.path.abspath(args.pdf)
    if not os.path.exists(pdf):
        print("找不到原文: " + pdf)
        return 1
    h = health()
    if "error" in h:
        print("服务未就绪(%s): 先运行 logs/launch.ps1" % h["error"])
        return 1

    cfg = dict(PLUGIN_CONFIG)
    cfg["service"] = args.service
    cfg["force"] = bool(args.force)
    name = os.path.basename(pdf)
    cfg["fileName"] = name
    with open(pdf, "rb") as f:
        cfg["fileContent"] = base64.b64encode(f.read()).decode("ascii")

    t0 = time.time()
    print("提交: %s (force=%s, service=%s, %d 字节)"
          % (name, args.force, args.service, os.path.getsize(pdf)))
    try:
        resp = post_json("/translate", cfg)
    except urllib.error.HTTPError as exc:
        print("HTTP %s: %s" % (exc.code, exc.read().decode("utf-8", "replace")[:400]))
        return 1
    print("响应:", json.dumps(resp, ensure_ascii=False)[:300])

    task_id = resp.get("taskId")
    if not task_id or args.no_wait:
        return 0

    last = ""
    while time.time() - t0 < args.timeout:
        time.sleep(3)
        t = find_task(task_id)
        if t is None:
            hist = historical(task_id)
            if hist:
                print("状态: %s  %s" % (hist.get("status"), hist.get("message", "")))
                for p in (hist.get("filePaths") or hist.get("fileList") or []):
                    print("  产物:", p)
                ok = hist.get("status") == "success"
                print("耗时 %.0f 秒 -> %s" % (time.time() - t0, "成功" if ok else "失败"))
                return 0 if ok else 1
            print("任务已从活跃列表消失且无历史记录(可能刚完成), 请查 /api/history")
            return 0
        line = "%s %s%% %s" % (t.get("status"), t.get("progress"), t.get("message", ""))
        if line != last:
            print("  [%.0fs] %s" % (time.time() - t0, line))
            last = line
    print("等待超时(%ss), 任务仍在跑: %s" % (args.timeout, task_id))
    return 1


if __name__ == "__main__":
    sys.exit(main())
