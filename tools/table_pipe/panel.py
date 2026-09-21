# -*- coding: utf-8 -*-
"""翻译中继面板  v3  (本机网页版, 零依赖)

管线: mk_job.py(装配) -> [豆包网页版] -> 本面板 -> check_job.py -> mk_appendix.py

为什么做这个: watch_clip.py 是"复制即执行", 没有确认环节。实测(2026-09-21)把真实回包的
**末行截断 3 字符**, `k115\t两种传粉者` 变成 `k115\t两种` —— 编号齐全 / 占位符未丢 / 非空,
**G1-G5 全过**, 监听器直接跑完出稿, 附录里那一格就是错的。这类缺陷**判据够不到**
(门禁的锚点是编号与占位符, "末行被吃掉一截但还剩内容"两者都不触发), 唯一的拦截点是
把"提交"这个动作交回给人 —— 而且人得**看得见**末行是不是被截。

**v3 为什么是网页而不是 tkinter**(2026-09-21 评审): tkinter 画不出圆角 / 毛玻璃 /
鼠标光影 —— 那是渲染层的天花板, 不是设计稿的问题。用户要的是苹果"液态玻璃":
圆角浮动卡片、玻璃模糊、指针到哪光影跟到哪。这是浏览器 CSS 的主场, 于是界面层
整体换成本机网页: stdlib http.server 起 127.0.0.1, 前端单页 HTML 内嵌, 优先用
Edge/Chrome 的 --app 模式开**无浏览器框**的窗口(看起来就是原生弹窗)。
样式配方取自用户自己的 ice917/weather-app(glass 变量/环境光斑/24px 圆角) +
Apple WWDC25 Liquid Glass 官方设计原则(透镜高光/静止时安静、交互时点亮/弹性微动)。

  三层分工(v2 定稿, 不变):
  第一层  机器已保证的(编号/占位符/数字/±/非空/代码缩写) -> 人不看, 只给顶部计数
  第二层  只给人看机器判不了的 -> **待看清单**(正常回包为**空**)
  第三层  剩下的用"外行也能判"的读数兜底(一致性: 同一原文必须同一译文 —— 零阈值纯机械)

**试过但否掉的读数**(别重新起头): 拉丁碎片保真 —— 实测误报 105/115
("Subfamily"->"亚科"被判成"丢了拉丁串"), 字面上分不开"该保留的学名"与"该翻译的英文词"。

网页版特有的两条契约:
  退出: 页面关窗前 sendBeacon 打 /api/bye -> 进 15s 宽限期, 期内有新心跳(F5 刷新 /
        别的页面还活着)则告别作废, 否则退出; 兜底是心跳, 收过心跳后
        IDLE_LIMIT(600s) 没再收到也算"窗口已关"。两条阈值都要按最坏用户行为定 ——
        30s 心跳会把"切去豆包翻页"误杀(实测); 立即退会把 F5 刷新和"验收浏览器先关"
        变成自杀(实测)。
  改动作废: /api/check 记下当时文本的 sha1, /api/commit 发现文本变了直接拒绝 ——
            "确认"不可能按在过期内容上(服务器端强制, 不只靠前端禁用按钮)。

用法:
  python panel.py                        启动面板(自动开窗)
  python panel.py --selftest [回包文件]   无界面自检(跑 analyse 并打印待看清单)
"""
import hashlib
import io
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_clip as wc          # 复用识别/落盘/门禁/剪贴板全链, 不重复实现

PH = re.compile(r"\{S\d{3}\}")
SUSPECT_K = 0.5      # 长度比低于"全批中位数"的此倍 -> 列入待看(读数, 不判定)

# 主题选择落在**工作目录**(数据侧, 不进仓库); 具体配色在前端 CSS 变量里。
THEME_FILE = os.path.join(wc.D, ".panel_theme")


def _load_theme():
    try:
        t = io.open(THEME_FILE, encoding="utf-8").read().strip()
        if t in ("dark", "light"):
            return t
    except Exception:
        pass
    return "dark"


def _save_theme(name):
    try:
        with io.open(THEME_FILE, "w", encoding="utf-8") as f:
            f.write(name)
    except Exception:
        pass


def _brief(seq, n=12):
    s = " ".join(seq[:n])
    return s + ("…" if len(seq) > n else "")


def _ratio(orig, zh):
    """去掉占位符后按字符数比 —— 占位符长度固定, 不剥掉会掩盖真实的短译。"""
    o = len(PH.sub("", orig).strip())
    z = len(PH.sub("", zh).strip())
    return z / o if o else 0.0


def sandbox_gates(job, ids, got):
    """在**临时目录**里跑**同一份**门禁代码 -> (ok, 输出行)。

    检查阶段不落任何文件: manifest 与回包都拷进 tmp, 子进程的 P2Z_TABLE_DIR 指向 tmp,
    于是它回填的 zh TSV / check_report.txt / notes_zh.json 全落在 tmp 里, 随后整目录删掉。
    落盘只发生在"确认"那一步。
    """
    tmp = tempfile.mkdtemp(prefix="p2z_panel_")
    try:
        shutil.copy(os.path.join(wc.D, job["manifest"]), tmp)
        with io.open(os.path.join(tmp, job["resp"]), "w", encoding="utf-8") as f:
            for uid in ids:
                f.write("%s\t%s\n" % (uid, got[uid]))
        p = subprocess.run(job["cmd"](), cwd=tmp, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=dict(os.environ, P2Z_TABLE_DIR=tmp))
        return p.returncode == 0, (p.stdout or "").splitlines()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def analyse(text):
    """纯逻辑, 不碰 Tk(便于 --selftest): 识别任务 -> 沙箱跑门禁 -> 算待看清单。

    返回 {"error": ...} 或 {"ok", "job", "ids", "got", "rows", "median", "items",
                          "n_dup", "n_incons", "last_ratio", "gate_out", "last"}
    items 每项 = (级别, 类型, 说明, 单元号串)
    """
    if not text.strip():
        return {"error": "粘贴区是空的。"}
    if any(m in text for m in wc.PREAMBLE_MARK):
        return {"error": "这是「发出去」的待译文本(含【待译】/【任务】标记), 不是豆包的回包。"}

    hit = wc.match_job(text, log=lambda *a: None)
    if not hit:
        diag = []
        for job in wc.JOBS:
            ids = wc.manifest_ids(job["manifest"])
            got = wc.parse_units(text, ids, job["pre"])
            miss = [i for i in ids if i not in got]
            extra = sorted(i for i in got if i not in ids)
            diag.append("「%s」期望 %d 条 / 读到 %d 条%s%s"
                        % (job["label"], len(ids), len(got),
                           ("  缺 " + _brief(miss)) if miss else "",
                           ("  多 " + _brief(extra)) if extra else ""))
        return {"error": "没有识别到完整回包(编号不齐)。", "diag": diag}

    job, ids, got = hit
    orig = {u["id"]: u["orig"] for u in wc.manifest_units(job["manifest"])}
    ok, out = sandbox_gates(job, ids, got)

    rows = [(_ratio(orig[i], got[i]), i, orig[i], got[i]) for i in ids]
    rows.sort(key=lambda r: (r[0], r[1]))
    med = statistics.median(r[0] for r in rows) if rows else 0.0

    # ---------------- 待看清单: 只放机器判不了的。正常回包应为空。 ----------------
    items = []

    # 1) 一致性: 同一原文必须同一译文。纯机械判断 —— 不需要专业知识就能定夺。
    by = {}
    for i in ids:
        by.setdefault(orig[i], []).append(i)
    dup = {k: v for k, v in by.items() if len(v) > 1}
    incons = []
    for k, gids in dup.items():
        zh = sorted(set(got[i] for i in gids))
        if len(zh) > 1:
            incons.append((k, gids, zh))
    for k, gids, zh in sorted(incons):
        items.append(("高", "一致性", "%r 出现 %d 次却译成 %d 种: %s"
                      % (k[:60], len(gids), len(zh), "  /  ".join(zh)), " ".join(gids)))

    # 2) 长度比异常短(成句丢失级)
    short = [r for r in rows if r[0] < SUSPECT_K * med]
    for ratio, uid, o, z in short:
        items.append(("中", "过短", "长度比 %.2f, 全批中位 %.2f" % (ratio, med), uid))

    # 3) 末条完整性: 复制截断几乎总落在末尾。**单靠长度比分不开"被截断"与"本来就短"**
    #    (实测: 截断后 0.13 vs 全批最小 0.15, 没有能分开两者的阈值), 所以只对**末条**
    #    这个高危位置放宽到"低于全批中位" —— 健康回包末条通常在中位附近, 故不误报。
    last_id = ids[-1]
    last_r = _ratio(orig[last_id], got[last_id])
    if last_r < med and last_id not in set(r[1] for r in short):
        items.append(("高", "末条偏短", "长度比 %.2f < 全批中位 %.2f —— 复制被截断的典型信号"
                      % (last_r, med), last_id))

    items.sort(key=lambda x: 0 if x[0] == "高" else 1)     # 稳定排序: 高在前, 组内保原序
    return {"ok": ok, "job": job, "ids": ids, "got": got, "rows": rows, "median": med,
            "items": items, "n_dup": len(dup), "n_incons": len(incons),
            "last_ratio": last_r, "gate_out": out,
            "last": (last_id, orig[last_id], got[last_id])}


# ---------------------------------------------------------------------- 前端页面(内嵌单页, __THEME__ 由服务器替换)

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="__THEME__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>表格翻译中继</title>
<style>
:root{
  color-scheme:light;
  --primary:#0ea5e9; --primary-dark:#0284c7;
  --bg-start:#e0f2fe; --bg-end:#f0f9ff;
  --glass-bg:rgba(255,255,255,.62); --glass-border:rgba(255,255,255,.8);
  --card-bg:rgba(255,255,255,.5);
  --text:#0f172a; --text2:#475569; --muted:#7c8aa0;
  --shadow:0 10px 40px -10px rgba(14,165,233,.25);
  --spec:rgba(14,165,233,.10); --radius:22px;
  --ok:#059669; --warn:#b45309; --danger:#dc2626;
  --input-bg:rgba(255,255,255,.6); --hover:rgba(14,165,233,.10);
  --dd-bg:rgba(248,250,252,.94);
}
[data-theme="dark"]{
  color-scheme:dark;
  --primary:#38bdf8; --primary-dark:#0ea5e9;
  --bg-start:#0f172a; --bg-end:#1e293b;
  --glass-bg:rgba(30,41,59,.66); --glass-border:rgba(255,255,255,.09);
  --card-bg:rgba(30,41,59,.5);
  --text:#f1f5f9; --text2:#cbd5e1; --muted:#64748b;
  --shadow:0 10px 40px -10px rgba(0,0,0,.5);
  --spec:rgba(255,255,255,.12);
  --ok:#34d399; --warn:#fbbf24; --danger:#f87171;
  --input-bg:rgba(15,23,42,.55); --hover:rgba(56,189,248,.12);
  --dd-bg:rgba(22,32,47,.94);
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{min-height:100%}
body{
  font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  color:var(--text);
  background:linear-gradient(160deg,var(--bg-start),var(--bg-end)) fixed;
  overflow-x:hidden;
}
.orb{position:fixed;border-radius:50%;filter:blur(100px);opacity:.5;pointer-events:none;z-index:0}
.orb1{width:320px;height:320px;top:-90px;right:-70px;background:radial-gradient(circle,rgba(56,189,248,.55),transparent 70%);animation:orbFloat 18s ease-in-out infinite alternate}
.orb2{width:260px;height:260px;bottom:8%;left:-90px;background:radial-gradient(circle,rgba(14,165,233,.4),transparent 70%);animation:orbFloat 22s ease-in-out -6s infinite alternate}
.orb3{width:190px;height:190px;top:42%;right:12%;background:radial-gradient(circle,rgba(125,211,252,.45),transparent 70%);animation:orbFloat 20s ease-in-out -11s infinite alternate}
@keyframes orbFloat{from{transform:translate(0,0) scale(1)}to{transform:translate(30px,-24px) scale(1.08)}}
main{position:relative;z-index:1;max-width:1060px;margin:0 auto;padding:18px 16px 30px;display:flex;flex-direction:column;gap:14px}
.glass{
  position:relative;background:var(--glass-bg);
  -webkit-backdrop-filter:blur(20px) saturate(160%);backdrop-filter:blur(20px) saturate(160%);
  border:1px solid var(--glass-border);border-radius:var(--radius);
  box-shadow:var(--shadow),inset 0 1px 0 rgba(255,255,255,.22);
}
.glass::after{
  content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:radial-gradient(420px circle at var(--mx,50%) var(--my,50%),var(--spec),transparent 42%);
  opacity:0;transition:opacity .25s ease;
}
.glass:hover::after{opacity:1}
header.glass{display:flex;align-items:center;gap:12px;padding:13px 18px}
.ttl{display:flex;align-items:center;gap:9px;font-weight:700;font-size:16px;white-space:nowrap}
.dot{width:10px;height:10px;border-radius:50%;background:linear-gradient(135deg,var(--primary),var(--primary-dark));box-shadow:0 0 10px var(--primary)}
.wd{flex:1;color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right}
section.glass{padding:14px 18px}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.spread{justify-content:space-between;margin-bottom:10px}
.muted{color:var(--muted);font-size:12.5px}
.sec-ttl{font-weight:600;font-size:13.5px}
.btn{
  font:inherit;cursor:pointer;border-radius:999px;padding:8px 18px;
  border:1px solid var(--glass-border);background:var(--card-bg);color:var(--text);
  -webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);
  transition:transform .12s ease,box-shadow .2s ease,background .2s ease;
}
.btn:hover{transform:translateY(-1px);background:var(--hover)}
.btn:active{transform:scale(.97)}
.btn:disabled{opacity:.45;cursor:not-allowed;transform:none}
.btn.primary{
  background:linear-gradient(135deg,var(--primary),var(--primary-dark));
  color:#fff;border:none;box-shadow:0 6px 20px -6px var(--primary);
}
.btn.primary:hover{box-shadow:0 8px 26px -6px var(--primary)}
.btn.small{padding:5px 13px;font-size:12.5px}
/* 自定义下拉: 原生 <select> 的弹层是操作系统画的, 圆角/毛玻璃都改不了(颜色也仅部分
   可控, 实测 Edge/Win 不跟 color-scheme) —— 要"我们的圆角风格"只能自绘。 */
.dd{position:relative}
/* 下拉列表会被后面的玻璃卡片盖住: backdrop-filter 让每张卡片自成层叠上下文,
   后绘制的卡片压过先绘制卡片里的绝对定位子元素 —— 把本行整体抬一层。 */
.sendrow{z-index:20}
.dd-btn{
  display:flex;align-items:center;gap:8px;font:inherit;color:var(--text);
  background:var(--card-bg);cursor:pointer;outline:none;
  border:1px solid var(--glass-border);border-radius:999px;padding:7px 14px;
  -webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);
}
.dd-btn .dd-arrow{color:var(--muted);font-size:11px;transition:transform .15s ease}
.dd.open .dd-arrow{transform:rotate(180deg)}
.dd-list{
  position:absolute;top:calc(100% + 6px);left:0;min-width:100%;z-index:50;
  background:var(--dd-bg);  /* 雾面: 高不透明, 不透出底下卡片的字; 边缘仍带模糊 */
  -webkit-backdrop-filter:blur(20px) saturate(160%);backdrop-filter:blur(20px) saturate(160%);
  border:1px solid var(--glass-border);border-radius:14px;box-shadow:var(--shadow);
  padding:5px;
}
.dd-item{padding:7px 12px;border-radius:9px;cursor:pointer;white-space:nowrap;font-size:13.5px}
.dd-item:hover{background:var(--hover)}
.dd-item.sel{color:var(--primary);font-weight:600}
.banner{padding:13px 20px;font-weight:700;font-size:15px}
.banner.idle{color:var(--muted)}
.banner.busy{color:var(--text)}
.banner.ok{color:var(--ok)}
.banner.err{color:var(--danger)}
textarea{
  width:100%;min-height:140px;max-height:340px;resize:vertical;
  font:12.5px/1.65 Consolas,"Cascadia Mono",monospace;
  background:var(--input-bg);color:var(--text);
  border:1px solid var(--glass-border);border-radius:16px;
  padding:12px 14px;outline:none;transition:border-color .15s,box-shadow .15s;
}
textarea:focus{border-color:var(--primary);box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 20%,transparent)}
.seg{display:inline-flex;gap:4px;padding:4px;border-radius:999px;background:var(--card-bg);border:1px solid var(--glass-border);margin-bottom:12px}
.seg button{font:inherit;font-size:13px;border:none;cursor:pointer;border-radius:999px;padding:6px 16px;background:transparent;color:var(--text2);transition:background .15s,color .15s}
.seg button.on{background:linear-gradient(135deg,var(--primary),var(--primary-dark));color:#fff;box-shadow:0 4px 14px -4px var(--primary)}
.look{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;border-radius:16px;background:var(--card-bg);border:1px solid var(--glass-border);margin-top:8px;font-size:13.5px}
.lv{flex:0 0 46px;text-align:center;font-weight:700}
.lv.hi{color:var(--danger)} .lv.mid{color:var(--warn)}
.lkind{flex:0 0 64px;color:var(--text2)}
.lmsg{word-break:break-all}
.lids{color:var(--muted);font-family:Consolas,monospace;font-size:12px;margin-top:3px}
.empty{color:var(--ok);padding:16px 6px;font-size:13.5px;white-space:pre-line}
.emptyhint{color:var(--muted);padding:16px 6px;font-size:13.5px}
.tblwrap{max-height:320px;overflow:auto;border-radius:14px;border:1px solid var(--glass-border)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--glass-border);vertical-align:top;word-break:break-all}
th{color:var(--text2);font-weight:600;background:var(--card-bg);position:sticky;top:0}
td.c,th.c{text-align:center;white-space:nowrap}
td.mono{font-family:Consolas,monospace}
tr.bad td{color:var(--danger)}
tr.last td{background:color-mix(in srgb,var(--warn) 15%,transparent)}
.log{max-height:150px;overflow:auto;font:12px/1.7 Consolas,monospace;color:var(--text2);white-space:pre-wrap;margin-top:8px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:var(--glass-border);border-radius:8px;border:2px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:var(--muted);border:2px solid transparent;background-clip:content-box}
::-webkit-scrollbar-track{background:transparent}
@media (prefers-reduced-motion:reduce){.orb{animation:none}.glass::after,.btn{transition:none}}
</style>
</head>
<body>
<div class="orb orb1"></div><div class="orb orb2"></div><div class="orb orb3"></div>
<main>
  <header class="glass">
    <div class="ttl"><span class="dot"></span>表格翻译中继</div>
    <div class="wd" id="workdir"></div>
    <button class="btn small" id="themeBtn" type="button">切到浅色</button>
  </header>

  <section class="glass row sendrow">
    <label>任务</label>
    <div class="dd" id="taskSel">
      <button class="dd-btn" type="button" aria-haspopup="listbox" aria-expanded="false"><span class="dd-val">—</span><span class="dd-arrow">▾</span></button>
      <div class="dd-list" role="listbox" hidden></div>
    </div>
    <button class="btn" id="sendBtn" type="button">① 复制待译文本到剪贴板</button>
    <span class="muted" id="sendStat">—</span>
    <span style="flex:1"></span>
    <button class="btn small" id="buildBtn" type="button">重新装配</button>
  </section>

  <section class="glass row">
    <button class="btn" id="checkBtn" type="button">③ 检查(只读预览)</button>
    <button class="btn primary" id="commitBtn" type="button" disabled>④ 确认写入并出稿</button>
  </section>

  <div class="glass banner idle" id="banner">尚未检查。</div>

  <section class="glass">
    <div class="row spread">
      <span class="sec-ttl">② 把豆包的回复粘到这里</span>
      <span>
        <button class="btn small" id="clearBtn" type="button">清空</button>
      </span>
    </div>
    <textarea id="ta" spellcheck="false" placeholder="把豆包的整段回复原样粘进来(不挑不拣, 全文粘贴)"></textarea>
  </section>

  <section class="glass">
    <div class="seg" id="seg">
      <button data-tab="look" class="on" type="button">待看清单 (0)</button>
      <button data-tab="all" type="button">全表 (0)</button>
    </div>
    <div id="paneLook">
      <div class="emptyhint" id="emptyLook">尚未检查 —— 把豆包的回复粘到上面, 然后点 ③ 检查。</div>
      <div id="lookList"></div>
    </div>
    <div id="paneAll" hidden>
      <div class="tblwrap"><table>
        <thead><tr><th class="c">编号</th><th class="c">长度比</th><th>原文</th><th>译文</th></tr></thead>
        <tbody id="tbody"></tbody>
      </table></div>
    </div>
  </section>

  <section class="glass">
    <div class="sec-ttl muted">日志</div>
    <div class="log" id="log"></div>
  </section>
</main>
<script>
var $=function(id){return document.getElementById(id)};
var ta=$('ta'),logBox=$('log'),banner=$('banner'),commitBtn=$('commitBtn');
var theme=document.documentElement.getAttribute('data-theme')||'dark';
function esc(s){return String(s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function api(path,body){
  var opt=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  return fetch(path,opt).then(function(r){return r.json()})
    .catch(function(){return {net:true,error:'连不上本机服务器'}});
}
function log(msg){
  var t=new Date().toTimeString().slice(0,8);
  logBox.insertAdjacentHTML('beforeend','<div>['+t+'] '+esc(msg)+'</div>');
  logBox.scrollTop=logBox.scrollHeight;
}
function setBanner(kind,text){banner.className='glass banner '+kind;banner.textContent=text}
function edited(){
  commitBtn.disabled=true;commitBtn.textContent='④ 确认写入并出稿';
  setBanner('idle','内容已改 —— 请重新点 ③ 检查。');
}
ta.addEventListener('input',edited);
$('sendBtn').addEventListener('click',function(){
  api('/api/send',{task:$('taskSel').value}).then(function(r){
    if(r.error){log('✗ '+r.error);return}
    $('sendStat').textContent='已复制 '+r.n_units+' 单元 / '+r.n_chars+' 字符';
    log('已把「'+r.label+'」待译文本复制到剪贴板('+r.n_units+' 单元 / '+r.n_chars+' 字符); 粘进豆包即可。');
  });
});
$('clearBtn').addEventListener('click',function(){ta.value='';edited()});
$('buildBtn').addEventListener('click',function(){
  $('buildBtn').disabled=true;$('buildBtn').textContent='装配中…';
  api('/api/build').then(function(r){
    $('buildBtn').disabled=false;$('buildBtn').textContent='重新装配';
    if(r.error){log('✗ 装配失败: '+r.error);setBanner('err','✗ 装配失败: '+r.error);return}
    log('已重新装配: '+r.summary);
    (r.out||[]).forEach(function(l){if(l)log('   '+l)});
    setBanner('idle','已重新装配 —— ① 里将是新任务文本。');
  });
});
$('checkBtn').addEventListener('click',function(){
  commitBtn.disabled=true;commitBtn.textContent='④ 确认写入并出稿';
  setBanner('busy','检查中…(在临时沙箱里跑真门禁)');
  api('/api/check',{text:ta.value}).then(function(r){
    if(r.error){
      clearViews();setBanner('err','✗ '+r.error);log('✗ '+r.error);
      (r.diag||[]).forEach(function(d){log('   '+d)});
      return;
    }
    render(r);
  });
});
function segBtn(tab,n){
  document.querySelector('#seg button[data-tab="'+tab+'"]').textContent=(tab==='look'?'待看清单':'全表')+' ('+n+')';
}
function clearViews(){
  $('lookList').innerHTML='';$('tbody').innerHTML='';
  $('emptyLook').hidden=true;segBtn('look',0);segBtn('all',0);
}
function showTab(tab){
  document.querySelectorAll('#seg button').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-tab')===tab)});
  $('paneLook').hidden=tab!=='look';$('paneAll').hidden=tab!=='all';
}
$('seg').addEventListener('click',function(e){
  var b=e.target.closest('button');if(b)showTab(b.getAttribute('data-tab'));
});
function render(r){
  setBanner(r.ok?'ok':'err',
    (r.ok?'✓ ':'✗ ')+r.job+' '+r.n+'/'+r.n+' · 门禁'+(r.ok?'全过':'未过')
    +' · 重复原文 '+r.n_dup+' 组/不一致 '+r.n_incons+' · '+r.items.length+' 条待你核');
  log((r.ok?'✓ ':'✗ ')+'「'+r.job+'」回包 '+r.n+' 单元; 门禁'+(r.ok?'全过':'未过'));
  (r.gate_out||[]).forEach(function(l){log('   '+l)});
  log('   末条 '+r.last.id+'  原文 '+JSON.stringify(r.last.orig)+'  ->  译文 '+JSON.stringify(r.last.zh));
  log('   一致性: 重复原文 '+r.n_dup+' 组, 其中译法不一致 '+r.n_incons+' 组');
  var list=$('lookList');list.innerHTML='';
  var el=$('emptyLook');
  el.hidden=r.items.length>0;
  el.className='empty';
  el.textContent='✓ 没有需要你核对的项目。\n\n'
    +'机器能判的(编号/占位符/数字/符号/代码缩写/一致性)都过了 —— 可以直接确认。\n'
    +'这里只会出现机器判不了的项, 正常回包为空。';
  var bad={};
  r.items.forEach(function(it){
    it.ids.split(/\s+/).forEach(function(u){bad[u]=1});
    var hi=it.lv==='高';
    list.insertAdjacentHTML('beforeend',
      '<div class="look"><div class="lv '+(hi?'hi':'mid')+'">'+(hi?'▲':'●')+' '+it.lv+'</div>'
      +'<div class="lkind">'+esc(it.kind)+'</div>'
      +'<div style="flex:1"><div class="lmsg">'+esc(it.msg)+'</div>'
      +'<div class="lids">'+esc(it.ids)+'</div></div></div>');
    log('   '+(hi?'▲':'●')+' ['+it.kind+'] '+it.msg+' ('+it.ids+')');
  });
  if(!r.items.length)log('   没有需要你核对的项目。');
  segBtn('look',r.items.length);
  var tb=$('tbody');tb.innerHTML='';
  var lastId=r.last.id;
  r.rows.forEach(function(row){
    var cls=((bad[row.id]?'bad ':'')+(row.id===lastId?'last':'')).trim();
    tb.insertAdjacentHTML('beforeend',
      '<tr'+(cls?' class="'+cls+'"':'')+'><td class="mono c">'+esc(row.id)+'</td>'
      +'<td class="mono c">'+row.ratio.toFixed(2)+'</td>'
      +'<td>'+esc(row.orig)+'</td><td>'+esc(row.zh)+'</td></tr>');
  });
  segBtn('all',r.n);
  if(r.ok){
    commitBtn.disabled=false;
    commitBtn.textContent='④ 确认写入并出稿'+(r.items.length?'（还有 '+r.items.length+' 条待看）':'');
    showTab('look');
  }else{
    log('   门禁未过, 不写入。修正后重新粘贴再检查。');
  }
}
commitBtn.addEventListener('click',function(){
  commitBtn.disabled=true;
  setBanner('busy','正在写入并排版…');
  api('/api/commit',{text:ta.value}).then(function(r){
    (r.log||[]).forEach(function(l){log(l)});
    if(!r.ok){
      setBanner('err','✗ '+(r.error||'未出稿'));
      log('✗ '+(r.error||'未出稿(门禁未过或排版失败)。'));
      if(!r.stale)commitBtn.disabled=false;
      return;
    }
    setBanner('ok','✓ 已出稿: '+r.docx);
    log('✓ 出稿: '+r.docx);
  });
});
$('themeBtn').addEventListener('click',function(){
  theme=theme==='dark'?'light':'dark';applyTheme();
  api('/api/theme',{theme:theme});
});
function applyTheme(){
  document.documentElement.setAttribute('data-theme',theme);
  $('themeBtn').textContent=theme==='dark'?'切到浅色':'切到深色';
}
var raf=null,px=0,py=0;
document.addEventListener('pointermove',function(e){
  px=e.clientX;py=e.clientY;
  if(raf)return;
  raf=requestAnimationFrame(function(){
    raf=null;
    document.querySelectorAll('.glass').forEach(function(c){
      var r=c.getBoundingClientRect();
      if(px>=r.left&&px<=r.right&&py>=r.top&&py<=r.bottom){
        c.style.setProperty('--mx',(px-r.left)+'px');
        c.style.setProperty('--my',(py-r.top)+'px');
      }
    });
  });
});
function mkDropdown(el){
  var btn=el.querySelector('.dd-btn'),val=el.querySelector('.dd-val'),list=el.querySelector('.dd-list');
  el.value='';
  function close(){el.classList.remove('open');list.hidden=true;btn.setAttribute('aria-expanded','false')}
  btn.addEventListener('click',function(e){
    e.stopPropagation();
    var was=list.hidden;close();
    if(was){list.hidden=false;el.classList.add('open');btn.setAttribute('aria-expanded','true')}
  });
  document.addEventListener('click',function(e){if(!el.contains(e.target))close()});
  el.setOptions=function(opts){
    list.innerHTML='';
    opts.forEach(function(o,i){
      var it=document.createElement('div');
      it.className='dd-item';it.textContent=o;it.setAttribute('role','option');
      it.addEventListener('click',function(){
        el.value=o;val.textContent=o;
        list.querySelectorAll('.dd-item').forEach(function(x){x.classList.remove('sel')});
        it.classList.add('sel');close();
      });
      list.appendChild(it);
      if(i===0){el.value=o;val.textContent=o;it.classList.add('sel')}
    });
  };
  return el;
}
var taskDD=mkDropdown($('taskSel'));
function ping(){fetch('/api/ping').catch(function(){})}
window.addEventListener('pagehide',function(){navigator.sendBeacon('/api/bye')});
api('/api/state').then(function(s){
  taskDD.setOptions(s.jobs||[]);
  $('workdir').textContent='工作目录 '+(s.workdir||'');
  theme=s.theme||theme;applyTheme();
  ping();setInterval(ping,5000);
});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------- 本机服务器(零依赖 stdlib)

IDLE_LIMIT = 600     # 心跳静默多少秒算"窗口已关"
BYE_GRACE = 15       # 收到告别后的宽限秒数
STATE = {"checked": None, "sha": None, "ping": None, "bye_at": None}
LOCK = threading.Lock()


def _sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class H(BaseHTTPRequestHandler):
    server_version = "p2z-panel/3"

    def log_message(self, *a):
        pass                                     # 不往控制台倒访问日志

    def _w(self, data, ctype, code=200):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass                                   # 浏览器中途关连接, 无事

    def _json(self, obj, code=200):
        self._w(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8", code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._w(PAGE.replace("__THEME__", _load_theme()).encode("utf-8"),
                    "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json({"jobs": [j["label"] for j in wc.JOBS],
                        "workdir": wc.D, "theme": _load_theme()})
        elif path == "/api/ping":
            with LOCK:
                STATE["ping"] = time.time()
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        b = self._body()
        if path == "/api/send":
            self._send(b)
        elif path == "/api/build":
            self._build()
        elif path == "/api/bye":
            with LOCK:                       # 页面关窗前告别(sendBeacon) -> 进宽限期,
                STATE["bye_at"] = time.time()  # 不是立即退(F5 刷新/别的页面关闭也会发)
            self._json({"ok": True})
        elif path == "/api/check":
            self._check(b)
        elif path == "/api/commit":
            self._commit(b)
        elif path == "/api/theme":
            if b.get("theme") in ("dark", "light"):
                _save_theme(b["theme"])
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    # -------------------------------------------------------------- 动作
    def _build(self):
        """重新装配: 在**工作目录**里子进程跑 mk_job.py(它模块层无条件 main()),
        更新 job_doubao.txt / job_manifest.json / job_audit.txt。装错/失败不落盘由
        mk_job 自己保证(它先全读后一次性写)。"""
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mk_job.py")
        p = subprocess.run([sys.executable, script], cwd=wc.D, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, P2Z_TABLE_DIR=wc.D))
        out = (p.stdout or "").splitlines()
        if p.returncode != 0:
            self._json({"error": "mk_job.py 退出码 %d\n%s" % (p.returncode,
                        (p.stderr or "").strip()[:500])})
            return
        # 装配产物变了 -> 任务标签不变但内容已更新; 把已检查状态清掉(旧 sha 已失效)
        with LOCK:
            STATE["checked"] = None
            STATE["sha"] = None
        self._json({"ok": True, "out": out, "summary": out[0] if out else "ok"})

    def _send(self, b):
        job = next((j for j in wc.JOBS if j["label"] == b.get("task")), wc.JOBS[0])
        path = os.path.join(wc.D, job["job"])
        if not os.path.exists(path):
            self._json({"error": "找不到 %s —— 先跑 mk_job.py 装配待译文本。" % path})
            return
        text = io.open(path, encoding="utf-8").read()
        rx = re.compile(r"^%s\d{3}[\t ]" % job["pre"])
        n = sum(1 for ln in text.splitlines() if rx.match(ln))
        if not wc.write_clip(text):
            self._json({"error": "剪贴板被别的程序占住, 稍后再试。"})
            return
        self._json({"label": job["label"], "n_units": n, "n_chars": len(text)})

    def _check(self, b):
        text = b.get("text", "")
        r = analyse(text)
        if "error" in r:
            self._json({"error": r["error"], "diag": r.get("diag", [])})
            return
        lid, lo, lz = r["last"]
        if r["ok"]:
            with LOCK:                        # 记住"这次检查"的内容指纹, commit 时必须对得上
                STATE["checked"] = (r["job"], r["ids"], r["got"])
                STATE["sha"] = _sha(text)
        self._json({
            "ok": r["ok"], "job": r["job"]["label"], "n": len(r["ids"]),
            "median": round(r["median"], 3), "n_dup": r["n_dup"], "n_incons": r["n_incons"],
            "last": {"id": lid, "orig": lo, "zh": lz}, "gate_out": r["gate_out"],
            "items": [{"lv": lv, "kind": k, "msg": m, "ids": u}
                      for lv, k, m, u in r["items"]],
            "rows": [{"ratio": round(rt, 3), "id": uid, "orig": o, "zh": z}
                     for rt, uid, o, z in r["rows"]]})

    def _commit(self, b):
        with LOCK:
            checked, sha = STATE["checked"], STATE["sha"]
        if not checked:
            self._json({"ok": False, "error": "请先点 ③ 检查。"}, 400)
            return
        if _sha(b.get("text", "")) != sha:
            self._json({"ok": False, "stale": True,
                        "error": "粘贴区内容已改, 之前的检查结果作废 —— 请重新点 ③ 检查。"}, 409)
            return
        job, ids, got = checked
        logs = []
        ok = wc.run_job(job, ids, got, log=logs.append)
        wc.beep(ok)
        if not ok:
            self._json({"ok": False, "log": logs, "error": "未出稿(门禁未过或排版失败)。"})
            return
        with LOCK:                            # 已落盘, 防双击重复出稿
            STATE["checked"] = None
        try:
            os.startfile(wc.DOCX)
        except Exception as e:
            logs.append("(自动打开失败: %r)" % e)
        self._json({"ok": True, "log": logs, "docx": wc.DOCX})


def open_app(url):
    """优先 Edge/Chrome 的 --app 模式(无浏览器框, 看着就是原生弹窗); 找不到退回默认浏览器。"""
    cands = []
    for name in ("msedge", "chrome"):
        p = shutil.which(name)
        if p:
            cands.append(p)
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
            cands.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    for exe in cands:
        if os.path.exists(exe):
            try:
                subprocess.Popen([exe, "--app=" + url, "--window-size=1140,880"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                pass
    webbrowser.open(url)
    return False


def _watchdog(srv):
    """退出契约(两条腿):
    主动 —— 页面关窗前 sendBeacon 打 /api/bye, 进 BYE_GRACE 秒宽限期。宽限内若收到
            新心跳(F5 刷新后新页面起来 / 还有别的页面活着), 告别作废; 否则退出。
            不能收到告别就立即退: F5 刷新会发 pagehide, 别的标签页(比如验收用的
            自动化浏览器)关闭也会发 —— 立即退会把还活着的窗口晾成死页面
            (2026-09-21 实测踩过)。
    兜底 —— 收过心跳后 IDLE_LIMIT 秒没再收到 = 窗口已关或进程崩了, 也退出。
    阈值必须够宽: 用户去豆包翻一轮要几分钟, 期间面板窗口失焦, 浏览器会把
    setInterval 节流甚至暂停 —— 30s 那种激进阈值会把"正在用"误判成"已关"
    (2026-09-21 实测: 用户切去豆包再回来, 服务器已经自杀了, 前端还把连接失败
    谎报成"剪贴板里没有文本")。从没收到过心跳就不杀 —— 窗口可能还没开起来。"""
    while True:
        time.sleep(5)
        with LOCK:
            p, bye_at = STATE["ping"], STATE["bye_at"]
        now = time.time()
        if bye_at is not None:
            if p is not None and p > bye_at:
                with LOCK:                   # 告别后仍有心跳 -> 有活页面, 告别作废
                    STATE["bye_at"] = None
            elif now - bye_at > BYE_GRACE:   # 宽限期内毫无心跳 -> 真关窗了
                srv.shutdown()
                return
        if p is not None and now - p > IDLE_LIMIT:
            srv.shutdown()
            return


# ---------------------------------------------------------------------- 入口

def selftest(path):
    """无界面自检: 对工作目录里的回包跑一遍 analyse, 打印大读数与待看清单。"""
    text = io.open(path, encoding="utf-8").read()
    r = analyse(text)
    if "error" in r:
        print("✗ " + r["error"])
        for d in r.get("diag", []):
            print("   " + d)
        return 1
    print("%s %s %d/%d · 门禁%s · 重复原文 %d 组/不一致 %d · %d 条待你核"
          % ("✓" if r["ok"] else "✗", r["job"]["label"], len(r["ids"]), len(r["ids"]),
             "全过" if r["ok"] else "未过", r["n_dup"], r["n_incons"], len(r["items"])))
    lid, lo, lz = r["last"]
    print("末条 %s: %r -> %r  (长度比 %.2f, 中位 %.2f)" % (lid, lo, lz, r["last_ratio"], r["median"]))
    for lv, kind, msg, uids in r["items"]:
        print("  %s [%s] %s  (%s)" % ("▲" if lv == "高" else "●", kind, msg, uids))
    if not r["items"]:
        print("  (待看清单为空)")
    for ln in r["gate_out"]:
        print("   " + ln)
    return 0 if r["ok"] else 1


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        f = sys.argv[i + 1] if len(sys.argv) > i + 1 else "job_response.tsv"
        return selftest(os.path.join(wc.D, f))
    if not os.path.exists(os.path.join(wc.D, "job_manifest.json")):
        print("✗ 工作目录里没有 job_manifest.json:\n\n  工作目录 = %s\n  脚本目录 = %s\n\n"
              "数据在别处时先设环境变量再启动:\n  $env:P2Z_TABLE_DIR=\"<数据目录>\"\n"
              "  python panel.py" % (wc.D, wc.SD))
        return 2
    sock = socket.socket()                       # 让系统挑一个空闲端口, 不占固定口
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = "http://127.0.0.1:%d/" % port
    threading.Thread(target=_watchdog, args=(srv,), daemon=True).start()
    print("翻译中继面板 v3  %s" % url)
    print("关窗即退出; 心跳兜底 %d 分钟。也可 Ctrl+C。" % (IDLE_LIMIT // 60))
    if not os.environ.get("P2Z_PANEL_NO_OPEN"):  # 冒烟测试时关掉自动开窗
        open_app(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
