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

两个引擎的入口不同(见 engine.py 节头):
  pdf2zh (1.x)  POST 本机服务端 /translate(异步任务, 轮询 taskId);
  next          pdf2zh_next CLI **子进程**(没有服务端) —— 上游 ignore_error=True,
                内部出错退出码仍是 0, 所以判成败**只看产物**; 额外两条:
                  · 段表由 config 的 [translation].working_dir 决定(v28.45): 上游
                    2.9.0 只在 debug=true 时写 translate_tracking.json, 而 debug 会把
                    调试图层**烘进产物**并给产物名加 .debug —— 交付件不能带。所以走
                    配置键, 不带 --debug; 段表根与第一公里(server)读同一份 config;
                  · config 的 [translation] ignore_cache 必须 false: 为 true 时上游
                    **既不读也不写**缓存, 改缓存行 = 白改。本工具在临时副本上强制置
                    false, 生产 config 一个字节不动。

用法 (解释器同 tools/tests/run_all.py; 仓库无 venv 目录, 用绝对路径):
  PY = D:/Users/<user>/anaconda3/envs/zotero-pdf2zh-venv/python.exe
  & $PY tools/force_rerender.py --pdf "D:/zotero-pdf2zh/server/translated/xxx.pdf"
  & $PY tools/force_rerender.py --pdf "..." --no-force   # 走正常去重(不加急)
  默认轮询到任务结束; --no-wait 只提交不等待(仅 1.x)。

  next 画像(缺省要 P2Z_ENGINE=next):
  & $PY tools/force_rerender.py --pdf "..." --service siliconflow ^
        --config "D:/zotero-pdf2zh/server/config/config.toml" ^
        --output "D:/zotero-pdf2zh/server/translated"
  段表路径由 config 的 [translation].working_dir 推出(engine.next_working_dir()), 也可用
  --tracking 直接指定。
  可执行文件按 "与本解释器同级的 zotero-pdf2zh-next-venv" 约定找, 或用 --exe /
  P2Z_NEXT_EXE 指定。
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:8890"

# [v28.39] 引擎画像: 本工具 POST 的是 **1.x 服务端**(engine=pdf2zh, 8890)。next 画像下
# 该服务端不接这条产线, 走 pdf2zh_next CLI 子进程(render_next), 不是同一套入口。
import engine as _ENG                                     # noqa: E402
_PROF = _ENG.active()

# 项目根(与 adopt/seg_* 同一约定): next 的 config 与产物目录默认落在这里
PROJ = os.environ.get("P2Z_PROJ", r"D:\zotero-pdf2zh")
NEXT_VENV = "zotero-pdf2zh-next-venv"

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


# ---------------------------------------------------------------- next 画像
def next_exe(explicit=""):
    """pdf2zh_next 可执行文件: 显式参数 > P2Z_NEXT_EXE > "同级 venv" 约定。

    不写死绝对路径(入库脚本不许含用户名/venv 硬编码): "与本解释器同级的
    zotero-pdf2zh-next-venv" 与 server.py 的 venv_name 是同一套约定, 换 conda 根
    跟着走; 换机/自定义安装用 --exe 或 P2Z_NEXT_EXE。
    """
    for c in (explicit, os.environ.get("P2Z_NEXT_EXE", "")):
        if c:
            return c
    win = os.name == "nt"
    return os.path.join(os.path.dirname(sys.prefix), NEXT_VENV,
                        "Scripts" if win else "bin",
                        "pdf2zh_next.exe" if win else "pdf2zh_next")


def _off_in_config(text):
    """把 [translation] 段的 ignore_cache 改成 false(只动这一行, 其余字节不变)。

    上游 `BaseTranslator.translate()` 头一行就是 `if not (self.ignore_cache ...)` ——
    为 true 时**既不读也不写**缓存。后果不是"慢", 是第二公里整个失效: 改缓存行 -> 重渲染
    -> 产物照旧, 而且不报任何错、不留任何痕迹(本机 server/config/config.toml 现值就是
    true, 官方对照跑留下的)。所以这里必须自己钉住。
    -> (新文本, 原值 or None)
    """
    head = re.search(r"(?m)^\[translation\]\s*$", text)
    if not head:
        return text, None
    seg = text[head.end():]
    nxt = re.search(r"(?m)^\[", seg)
    end = head.end() + (nxt.start() if nxt else len(seg))
    body = text[head.end():end]
    m = re.search(r"(?m)^([ \t]*ignore_cache[ \t]*=[ \t]*)(\S+)([ \t]*)$", body)
    if m:
        body = body[:m.start()] + m.group(1) + "false" + m.group(3) + body[m.end():]
    else:
        body = "\nignore_cache = false" + body
    return text[:head.end()] + body + text[end:], (m.group(2) if m else None)


def _pdf_pages(pdf):
    """页数(算 --pages 用); 读不出来返回 0 —— 由调用方拒收, 不静默按全篇跑。"""
    try:
        from pypdf import PdfReader
        return len(PdfReader(pdf).pages)
    except Exception:
        return 0


def _counters(out):
    """pdf2zh_next 退出时自己打的读数 -> (总调用, 缓存命中), 取不到返回 None。

    这是本画像唯一"零额外成本"的判据: 全命中 = 这次渲染没花 LLM 的钱。行会被 rich 按
    终端宽度折断("translate cache call count:" 与数字分行) —— 我们给子进程钉了
    COLUMNS=200, 但仍兼容折行形态(数字允许落在下一行)。
    """
    def grab(label):
        m = re.search(label + r":[ \t]*(\d+)", out)
        if not m:
            m = re.search(label + r":[ \t]*\n[ \t]*(\d+)[ \t]*$", out, re.M)
        return int(m.group(1)) if m else None
    return grab("translate call count"), grab("translate cache call count")


def _tail(out, n=12):
    """失败时给一眼现场(最后 n 行非空行)。"""
    ls = [x.rstrip() for x in (out or "").splitlines() if x.strip()]
    return "\n".join("  | " + x for x in ls[-n:])


def _probe_before_render(tk):
    """渲染前零成本自检: 段表里的 prompt 在缓存库里命中多少行。

    命中 0 不等于失败(可能就是第一次渲染), 但意味着**这次要整篇重译**: 既花钱又慢,
    而且多半是引擎/模型/语言跟上次不一致 —— 必须在提交前说出来。
    """
    if not tk or not os.path.exists(tk):
        print("  自检: 没有段表(%s), 跳过缓存探针 —— 换篇论文后 working 下的旧段表会被"
              "覆盖, 用 --tracking 指回导出这份载荷时的那一份" % (tk or "未指定"))
        return
    try:
        d = _ENG.load_tracking(tk)
    except (IOError, ValueError) as e:
        print("  自检: 读不了段表(%s), 跳过缓存探针" % e)
        return
    prompts = []
    for pool in ("page", "cross_page"):
        for grp in (d.get(pool) or []):
            for p in ((grp or {}).get("paragraph") or []):
                for t in ((p or {}).get("llm_translate_trackers") or []):
                    s = (t or {}).get("input") or ""
                    if s and s not in prompts:
                        prompts.append(s)
    if not prompts:
        print("  自检: 段表里没有 LLM prompt(非 LLM 通道? 或空壳段表), 跳过缓存探针")
        return
    r = _ENG.probe_prompts(prompts)
    print("  自检: 缓存命中 %d/%d 段 %s" % (r["hit"], r["texts"], r["rows"] or "{}"))
    if r["hit"] == 0:
        print("  ⚠ 0 命中 —— 这次渲染会**整篇重译**(既花钱又慢)。先核对: 引擎(%s)、"
              "模型/语言是否与上次渲染一致, 以及 config 的 ignore_cache(本工具已在临时"
              "副本里置 false)。" % "/".join(_PROF.cache_engines))
        for m in r["miss"][:2]:
            print("    未命中样例: %r" % m)


def render_next(args):
    """next 画像: 起 pdf2zh_next CLI 子进程重渲染 —— 第二公里"落到页上"那一步。

    与 1.x 的三处不同全是引擎性质, 不是实现取舍:
      1. **没有服务端**: 不需要 8890 在跑, 也没有 taskId 可轮询 —— 直接起子进程;
      2. **判成败只能看产物**: 上游 ignore_error=True, 内部出错也退 0;
      3. **缓存开关与段表根都在 config 上**: ignore_cache 见 _off_in_config; 段表根见
         [translation].working_dir(v28.45 配置键) —— 于是**不再传 --debug**: debug 会把
         调试图层(页码/段号标签、彩色框)烘进产物, 还给产物名加 .debug, 交付件不能带。
    产物行(`产物: <path>`)与耗时行(`耗时 N 秒 -> ...`)与 1.x **同格式** —— adopt 的
    stage_render 靠它们记台账, 不必分两套解析。
    """
    pdf = os.path.abspath(args.pdf)
    if not os.path.exists(pdf):
        print("找不到原文: " + pdf)
        return 1
    exe = next_exe(args.exe)
    if not os.path.exists(exe):
        print("FAIL: 找不到 pdf2zh_next 可执行文件: %s\n"
              "      用 --exe 指定, 或设 P2Z_NEXT_EXE(缺省按'与本解释器同级的 %s'找)"
              % (exe, NEXT_VENV))
        return 1
    cfg = os.path.abspath(args.config) if args.config else \
        os.path.join(PROJ, "server", "config", "config.toml")
    if not os.path.exists(cfg):
        print("FAIL: 找不到 next 的 config: %s\n"
              "      用 --config 指定(生产那份 = server/config/config.toml)" % cfg)
        return 1
    outdir = os.path.abspath(args.output) if args.output else \
        os.path.join(PROJ, "server", "translated")
    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    stem = os.path.splitext(os.path.basename(pdf))[0]
    # 段表根 = config 的 [translation].working_dir(v28.45 配置键; 上游 babeldoc 再拼
    # <stem>)。缺省根是上游 debug 用的 ~/.cache/babeldoc/working —— 只对旧读数有效,
    # 本工具已不带 --debug, 所以那种情况下段表不会落盘(下面会告警, 不静默)。
    tk = args.tracking or os.path.join(
        _ENG.next_working_dir(cfg), stem, "translate_tracking.json")
    _probe_before_render(tk)

    cmd = [exe, pdf, "--" + (args.service or "siliconflow"), "--output", outdir,
           "--lang-in", args.lang_in, "--lang-out", args.lang_out]
    if args.skip_last:
        total = _pdf_pages(pdf)
        end = total - int(args.skip_last)
        if total < 1:
            print("FAIL: 读不出原文页数(pypdf), 无法换算 --skip-last; 去掉该参数按全篇跑")
            return 1
        if end < 1:
            print("FAIL: --skip-last %d 会跳过全部 %d 页(与 1.x 同一条边界守卫)"
                  % (args.skip_last, total))
            return 1
        cmd += ["--pages", "1-%d" % end]
    if args.no_wait or not args.force:
        print("  (注: --no-wait/--no-force 是 1.x 服务端的异步任务与去重口径, next 上没有"
              "对应物, 已忽略)")
    if exe.lower().endswith(".py"):
        # 让回归能拿一个假引擎把整条出口跑通(见 tools/tests/test_engine.py ㉜):
        # 真实调用不会走这条。
        cmd = [sys.executable] + cmd

    # config 用**临时副本**: 子进程的 ConfigManager 会规范化写回, 直接给生产那份会把
    # 它改脏(1.x 试点同样的坑, 见改动记录 v28.37)。
    tmpdir = tempfile.mkdtemp(prefix="p2z_next_render_")
    try:
        with open(cfg, encoding="utf-8") as f:
            text = f.read()
        text, old = _off_in_config(text)
        if old is None:
            print("  注: config 里没有 ignore_cache 键, 已在临时副本里补 false")
        elif old.lower() == "true":
            print("  注: config 的 ignore_cache = true(上游此时既不读也不写缓存), 已在"
                  "临时副本里置 false —— 生产 config 未改动")
        tcfg = os.path.join(tmpdir, "config.toml")
        with open(tcfg, "w", encoding="utf-8") as f:
            f.write(text)
        cmd += ["--config-file", tcfg]

        print("提交: %s (%s / %s, skipLast=%d, 输出 %s)"
              % (os.path.basename(pdf), args.service or "siliconflow", args.lang_out,
                 args.skip_last, outdir))
        t0 = time.time()
        # COLUMNS/NO_COLOR: rich 会把读数行按终端宽度折断, 折了就读不出 cache 命中数;
        # PYTHONIOENCODING: Windows 中文控制台的默认编码会把日志抓成乱码。
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   COLUMNS="200", NO_COLOR="1")
        try:
            pr = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=args.timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            print(_tail(out))
            print("等待超时(%ss), 子进程已终止" % args.timeout)
            return 1
        except OSError as exc:
            print("FAIL: 起不了 pdf2zh_next: %s" % exc)
            return 1
        out = (pr.stdout or "") + (pr.stderr or "")
        secs = int(time.time() - t0)
    finally:
        try:
            os.remove(os.path.join(tmpdir, "config.toml"))
            os.rmdir(tmpdir)
        except OSError:
            pass

    call, hit = _counters(out)
    if call is not None and hit is not None:
        print("  读数: 调用 %d / 缓存命中 %d%s"
              % (call, hit, "(全命中, 0 LLM)" if hit >= call - 1 else " -> 有未命中"))
    # 产物 = 唯一可信的成败判据(上游出错退 0): 新鲜(mtime 在本次开始之后)的 mono/dual。
    # 命名两个引擎不一样, 都得认: 1.x = `<stem>-mono.pdf`; next = `<stem>.no_watermark.
    # zh-CN.mono.pdf`(中段随 watermark_output_mode 与 lang 变)。只认前者 = 渲染明明成功
    # 却报"没等到新产物"。
    fresh = []
    for n in sorted(os.listdir(outdir)):
        if not n.startswith(stem):
            continue
        low = n.lower()
        if not low.endswith(("-mono.pdf", ".mono.pdf", "-dual.pdf", ".dual.pdf")):
            continue
        p = os.path.join(outdir, n)
        if os.path.getmtime(p) >= t0 - 5:
            fresh.append(p)
    if not fresh:
        print(_tail(out))
        print("FAIL: 没等到新产物(上游出错也退 0, 只能看产物): %s\\%s*[-.]mono.pdf"
              % (outdir, stem))
        return 1
    for p in fresh:
        print("产物: %s" % p)
    if os.path.exists(tk):
        print("  段表: %s" % tk)
    elif not args.tracking:
        print("  ⚠ 段表没落盘(%s): config 的 [translation].working_dir 没设(或 pdf2zh_next "
              "配置层补丁被覆盖)时就会这样 —— 第二公里拿不到段身份。修法: 在该 config 的 "
              "[translation] 下加 working_dir = \"D:\\\\<目录>\"(见 patches/pdf2zh_next_*.py)"
              % tk)
    print("耗时 %d 秒 -> 成功" % secs)
    return 0


def main():
    ap = argparse.ArgumentParser(description="force 重渲染(缓存手术后落产物)")
    ap.add_argument("--pdf", required=True, help="原文 PDF 绝对路径")
    ap.add_argument("--service", default="",
                    help="1.x 缺省 silicon; next 缺省 siliconflow(pdf2zh_next CLI 的 "
                         "服务开关 --<service>)。**必须与上次渲染那次一致** —— next 的"
                         "缓存键含引擎名, 换服务 = 整篇重译")
    ap.add_argument("--skip-last", type=int, default=0,
                    help="末尾保留页数(原样不译); 与 pre_check 推荐的 skipLastPages 同口径, "
                         "也要跟 Zotero 插件里该任务设的「最后几页跳过翻译」一致 —— "
                         "否则产出的 PDF 与用户实际拿到的那份不是同一份")
    ap.add_argument("--force", dest="force", action="store_true", default=True)
    ap.add_argument("--no-force", dest="force", action="store_false",
                    help="走正常去重(不加急)")
    ap.add_argument("--timeout", type=int, default=900, help="等待上限秒数")
    ap.add_argument("--no-wait", action="store_true", help="只提交不等待(仅 1.x)")
    # next 画像专用(1.x 上给了也无害: 那几条参数只在 render_next 里读)
    ap.add_argument("--exe", default="", help="next: pdf2zh_next 可执行文件(缺省按同级 "
                                              "venv 约定找; 给了 .py 就用本解释器起它)")
    ap.add_argument("--config", default="", help="next: config.toml(缺省 "
                                                 "<P2Z_PROJ>/server/config/config.toml)")
    ap.add_argument("--output", default="", help="next: 产物目录(缺省 "
                                                 "<P2Z_PROJ>/server/translated)")
    ap.add_argument("--tracking", default="", help="next: 段表(缺省按 PDF stem 猜 working 下"
                                                   "那一份; 只用于渲染前的缓存命中自检)")
    ap.add_argument("--lang-in", default="en")
    ap.add_argument("--lang-out", default="zh-CN",
                    help="必须与上次渲染那次一致(next 的缓存键含语言)")
    args = ap.parse_args()

    _ok, _why = _PROF.stage_ok("render")
    if not _ok:
        print("FAIL: 引擎 %s 上未接线: %s" % (_PROF.key, _why))
        return 2

    if _PROF.seg_source == "tracking_json":
        return render_next(args)

    pdf = os.path.abspath(args.pdf)
    if not os.path.exists(pdf):
        print("找不到原文: " + pdf)
        return 1
    h = health()
    if "error" in h:
        print("服务未就绪(%s): 先运行 logs/launch.ps1" % h["error"])
        return 1

    cfg = dict(PLUGIN_CONFIG)
    cfg["service"] = args.service or PLUGIN_CONFIG["service"]
    cfg["skipLastPages"] = int(args.skip_last)
    cfg["force"] = bool(args.force)
    name = os.path.basename(pdf)
    cfg["fileName"] = name
    with open(pdf, "rb") as f:
        cfg["fileContent"] = base64.b64encode(f.read()).decode("ascii")

    t0 = time.time()
    print("提交: %s (force=%s, service=%s, skipLastPages=%d, %d 字节)"
          % (name, args.force, args.service, cfg["skipLastPages"],
             os.path.getsize(pdf)))
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
