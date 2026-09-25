# -*- coding: utf-8 -*-
"""翻译中继面板  v3  (本机网页版, 零依赖)

管线: mk_job.py(装配) -> [任意网页 AI] -> 本面板 -> check_job.py -> mk_appendix.py

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

  [v28.59] 补上门禁够不到的最后一层: **④ 语义审核**(手动点按钮, 硅基流动
  DeepSeek-V3.2, 只审不改) —— 漏译/错译/数字/术语/指代/表达, 逐段「原文↔译文」核对,
  结论进存疑清单, 可一键复制给翻译方返工。三条边界: 审核者必须是**与翻译方无关的第三方**
  (relay_spec 第 4 条: 被翻译方不得自校); 它**只报不改**(生成式改写不可信, 见润色钩子
  的墓志铭); 它**不参与放行**(门禁是硬门, 存疑清单只给人裁决)。后端见 reviewer.py。

  [v28.75] ④ 的条目分**两类去向**: 译文侧存疑(漏译/错译/数字/术语/指代/表达) -> 可一键
  复制的返工单; **源文缺陷**(错的根在原文自己: 老扫描件的形近混淆 `011`=on、缺字、
  断字) -> 只给人做原文校勘, **不进**返工单 —— 译者被载荷规则要求逐字符照抄原文数字与
  字母(机检按字形落点验收), 发给翻译方等于要他改一个改不动的东西(改了破坏字形回锚)。
  切分点在 reviewer.split_src, 前端 SRC_KIND/rvSplit 与它同口径。

  [v28.77] 头部「体检」: 定位收束成"认知劳动(查词/裁决/积累)归用户, 结构劳动(格式/渲染/
    链接)归工具"之后, 结构陷阱得让用户**自己够得着**, 不能一卡住就开终端找维护者。体检把
    实测踩过的静默陷阱一键变成红灯: 术语双表漏同步(换引擎那个词静默不生效) / config.toml
    的 Ital 吞斜体 / 8890·60642 新旧实例共存 / ④·ledger 目录不可写。全部只读, 一切输入
    可注入(测试离线跑), netstat 跑不动就降级 warn 不崩。自愈(heal_render 一族)下一步挂进
    面板, 准入条件是**透明**: 只动结构层不碰译文一个字, 每次都出报告(治了哪页、按哪条判据)。

  [v28.78] 「结构自愈」区(概念链接区下方, 出稿之后用): 把两件成品级自愈挂进面板 ——
    heal_render(治"文字没翻错、版面坏了"的结构伤: 旋转文本/文献区错位; --dry-run 只看
    伤情不落盘)与 relink_pages(链接热区按锚文本重搜搬矩形, 未命中保持原矩形不死链)。
    判据全在工具里, 面板只做三件事: 路径守卫(成品必须落在自动探测候选里, 与概念链接
    同一道门)/组参数/一字不改转述报告 —— 透明是准入条件(relay_spec 9.6 红线)。
    dedouble_sweep(要 cache 库+fingerprint)与 force_rerender(引擎级重渲染, 要异步与
    进度)形态不同, 是第二期, 不硬塞进同一个按钮。

  [v28.62] 翻译方做成**可换**: 剪贴板这条路本来就不绑豆包 —— 真契约只有"编号守恒 +
  占位符原位"两条, 载荷自带抬头(文档名/术语表/规则), 所以换家**不用改提示词**; 别家的
  "包装"(代码围栏/首尾客套/把编号列成 Markdown 清单)由 watch_clip.parse_units 剥掉。
  换家之后补三件事, 都在头部「翻译方」下拉的射程内:
    归因 —— ③ 检查 / ⑤ 出稿各往工作目录的 vendor_ledger.tsv 追加一行(哪家在什么任务上
            过没过、待看几条), "换了家到底行不行"从此有据可查, 不靠印象;
    说人话 —— 报错明细的责任归属按实际翻译方显示(原来是写死的"豆包的问题");
    守红线 —— ④ 审核者与翻译方**同源则拒审**(翻译方选 DeepSeek 网页版而审核者也是
            DeepSeek 时, 审核就退化成自我确认; 判据 reviewer.conflicts, 服务器端拦,
            前端禁用按钮只是提示)。

**试过但否掉的读数**(别重新起头): 拉丁碎片保真 —— 实测误报 105/115
("Subfamily"->"亚科"被判成"丢了拉丁串"), 字面上分不开"该保留的学名"与"该翻译的英文词"。
**长度比当判据**(v30.5 撤下, 见下条): 同一病根 —— 拿读数冒充判断。

  [v30.5] 把「读数」与「判断」的层界收回来(用户实测: 正文作业的待看清单里冒出
    「▲ 高 末条偏短 长度比 0.38 < 全批中位 0.39」, 而回包是完整的; 全表又按比值升序把
    小标题全顶到最上面 —— 最不该看的排在最显眼处)。病根不是阈值调得不巧, 是**第三层的
    读数被塞进了第二层的待看清单**并配上了"高/中", 于是读数获得了判断的外观:
      · 参照错了 —— 比值大小由**源串自身性质**决定(正文里的小标题
        "Acknowledgements"→"致谢" 天然 0.13), 拿它跟**整篇**中位数比, 是把"类别差异"
        读成"长度异常"; `过短` 的阈值恰是 0.5×中位(≈0.14) —— 正好压在小标题那一档, 会
        把标题全点成嫌疑。
      · 阈值不是阈值 —— `末条 < 全批中位` 按定义就是整篇约 50% 的事件, 拿它当"高"警,
        误报率天然 ~50%; 旧注释"健康回包末条通常在中位附近, 故不误报"是**单样本**校准
        (一次实测 0.33 vs 0.27), 0.38 vs 0.39 当场推翻。
    处置: 长度比**只作读数** —— 全表一列(标「读数」) + 顶部中位 + 末条原文/译文并排;
      待看清单只留**源串自锚、零阈值**的判据(现有唯一一条: 同一原文必须同一译文);
      全表改回**文档序**(= manifest 顺序), 不再拿读数当排序键。
    为什么不损失已证实的检出: 长度比分不开"被截断"与"本来就短"(台账 §四实测: 1 字截断
      0.27 vs 中位 0.27 漏报), 而 §五记的**一致性零阈值就抓到过同一次截断**
      (`Two pollinators` 一次命中 7 个单元) —— 原两条长度比条目对那次截断是**冗余的**,
      对完整回包则**纯误报**。删读数的判断化, 不动任何已证实的判据。
    权限边界没变: 待看清单非空仍不阻断 ⑤(读数不判定); ④ 语义审核仍是"漏译/错译"那一层
      的正解 —— 长度比从来只是它的拙劣替身。

  [v28.69→v28.71] 三步之间补上"**这一篇该做的都做了吗**"这一环(用户实测: 表格做完直接去渲染,
    表格页仍是英文; 随即又有真洞: 硬拦截只看"你碰过的", 拦不住"你压根没做的那块表格")。
    v28.69 的做法是**内存里的本轮清单 + 硬拦截 + 逃生门**, v28.71 整个推翻, 理由分两层:
      其一, 清单记的是鼠标动作(① 复制过谁), 重启面板就失忆, 也管不了没碰过的那块;
      其二 —— 更关键 —— **硬拦截本身就拦错了对象**: 查依赖, 渲染一路(server/patches/engine/
        seg_*)从不读表格产物, table_*.zh.tsv / notes_zh.json 只有 mk_appendix.py(排附录)与
        mk_ledger.py(底片)在读, P2Z_TABLE_DIR 也只有表格工具自己用; PDF 的表格页**本来就
        保持英文**, 表格译文走附录 DOCX 这条路。所以"表格没做完就渲染"不会让 PDF 出问题,
        只意味着附录还没生成(随时可重做, 不是单程票)—— 拿它硬拦是自作主张。
    本篇待办 —— 操作区下方一行列出"这一篇该做的每一块"及状态(✓ 已出稿 / ⟳ 要重做 / ○ 未出稿)。
                 判据落在**产物**上(watch_clip.released: 产物齐且不比输入旧 = done; 比输入旧 =
                 stale, 装配过新一轮要重做; 缺 = todo), 不落内存 —— 重启面板、隔天回来都不失忆。
    篇归属   —— 表格/表注 manifest 里由装配器烙的篇名(mk_job.py --paper / mk_notes_job.py 沿用
                 同目录 job_manifest.json)"与正文那篇是否一致"; 老目录没烙(空)则按"一个工作目录
                 = 一篇"当本篇的并标 unlabeled 让人核, 烙了但不一致的标 foreign(那是别人的表)。
    提醒而非拦截 —— ⑤ 出稿的是**会触发重渲染**的任务(正文)时, 若这一篇还有没出稿的: 黄条提醒
                 + 台账记成"已出稿(缺: X)", 照旧渲染。顺序随用户(任意顺序), 齐了就过, 缺了提醒。
                 表格/表注的 ⑤ 只是排版, 不提醒。逃生门那颗按钮随之删除 —— 没有拦截就不需要它。
    外发台账 —— ① 复制仍落一行(阶段「复制」): 与 ③/⑤ 两行合起来, 一轮的完整轨迹
                 (发了哪几块、谁翻的、过没过、出没出稿)全在 vendor_ledger.tsv 里。
    (底片失败即中止出稿是 [v28.70] 的判, 与本条无关, 仍然硬拦 —— 见 _commit / LedgerFailed。)

网页版特有的两条契约:
  退出: 两条腿 ——
        ① **主动告别**: 页面关窗前 sendBeacon 打 /api/bye -> 进 15s 宽限期, 期内有新心跳
           (F5 刷新 / 别的页面还活着)则告别作废, 否则退出。
        ② **干完收摊**(v28.79+): ⑤ 出稿成功**且这一篇该做的都出齐了** -> 3s 后自动关停
           (判据见 paper_done / stop_after_done)。失败、或还有块没出稿, 一律不退 ——
           那时你要改稿重贴、或接着把表格做完。
        [v28.79] **原先那条"心跳静默 IDLE_LIMIT=1800s 当窗口已关"的兜底已删掉**。
        它本质是个**估**: 估窄了把"切去豆包翻长文"的用户误杀(30s 实测踩过 ——
        用户切回来服务器已经自杀了, 前端还把连接失败谎报成"剪贴板里没有文本"),
        估宽了窗口真死了还要霸着 60642; 而放宽到多少都只是把同一个错误推远。
        **② 就是那条兜底真正想近似的东西**: 它想说的不是"你多久没动静了", 而是"活干完了吧"。
        换成⑤ 成功这个**确定性信号**之后, 时限怎么设就不再是问题 —— 也不用再删一次。
        连带的 /api/idle 端点、头部倒计时与暂停按钮一并删除。
  改动作废: /api/check 记下当时文本的 sha1, /api/commit 发现文本变了直接拒绝 ——
            "确认"不可能按在过期内容上(服务器端强制, 不只靠前端禁用按钮)。

用法:
  python panel.py                        启动面板(自动开窗)
  python panel.py --takeover             60642 上有自家旧实例时, 先请它走再起(见下)
  python panel.py --selftest [回包文件]   无界面自检(跑 analyse 并打印待看清单)
  python panel.py --announce <文件>       把实际端口写到该文件(服务端拉起时用它回话; 见 announce_port)

为什么有 --takeover: 旧实例没退干净时, 新面板会**静默**回落随机端口, 而用户眼前那扇
窗连的还是旧进程(吃的旧代码) —— 体检里那条"端口·面板共存"只是只读提醒, 不会替你动手。
接管做成**显式命令**而不是自动: 那扇旧窗里可能正压着一份还没出稿的回包(内容只在
浏览器里), 自动杀会把它丢掉 —— 只有人知道该不该动手。

**逐步骤操作手册见 relay_spec.md 第 9 节**(启动/① → ⑤ 各步做什么与不做什么/退出规则/
出问题先查什么)。本文件头只讲设计取舍与契约, 那节讲怎么用。

  [v28.72] **编号按工序重排**。原来是 ① 复制 ② 粘贴 ③ 检查 **④ 出稿 ⑤ 报错明细 ⑥ 语义审核** ——
    ④⑤⑥ 三个号与工序反着: 审核本该在出稿**之前**(出了稿再审, 审出问题就得重做), 而报错明细
    根本不是一步操作(它是 ③ 的输出物, 只在门禁 FAIL 时现身)。现在: **④ 语义审核 → ⑤ 出稿**,
    报错明细**不占号**。编号与按钮行左→右一致, 也就不用再解释"为什么 6 排在 4 前面"。

  [v28.73] **概念链接区**。原版超链接是**继承来的**(引文锚 -> 文献表, 学名 -> 附录), 读者只能点
    原书给的那几条 —— 而"读到这里我需要一点背景"这种需求原书不会替你想到, 位置该由读者定。
    面板只接一条动线: **点选成品页面的文字 -> 填网址 -> 预检(不落盘) -> 装入 + 变蓝**。
    判据一条都不重写(命中几处 / 该不该给"第几处" / 压住了谁), 全由 tools/user_links.py 说了算 ——
    两边各判一套, 迟早分叉成两个答案。装完**自动接着跑 style_links**(新锚不变蓝 = 读者看不出能点,
    功能等于没做), 这步顺序由代码保证, 不靠人记。
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
import reviewer as rv            # ④ 语义审核(硅基流动): 只审不改, 与豆包无关的第三方

PH = re.compile(r"\{S\d{3}\}|\{v\d+\}")   # 表格/正文两种占位符都剥掉再比长度

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


# ---------------- 翻译方(v28.62): 剪贴板这条路本来就不绑豆包 ----------------
# 真契约只有两条 —— **编号守恒 + 占位符原位**(违反会被 ③ 当场抓住, 不是静默出错);
# 载荷自带抬头(文档名/术语表/规则), 所以**换家不用改提示词**。任何网页 AI 都能接。
# 选择落在**工作目录**(与主题文件同级, 数据侧不入仓库), 这里只解决"换家之后"的三件事:
#   归因: ③ 检查 / ⑤ 出稿各往台账追加一行 -> "换了家之后到底行不行"靠台账回答, 不靠感觉
#   说人话: 报错明细的责任归属按实际翻译方显示, 不再是写死的"豆包的问题"
#   守红线: ④ 审核者与翻译方**同源则拒审**(relay_spec §4.5: 被翻译方不得自校)
# 族名靠 reviewer.family_of 按名字认("硅基流动 API(DeepSeek)" -> deepseek), 认不出算不同源。
VENDORS = ("豆包", "ChatGPT", "Claude", "Kimi", "DeepSeek 网页版",
           "硅基流动 API(DeepSeek)", "其他网页 AI")
VENDOR_FILE = os.path.join(wc.D, ".panel_vendor")
VENDOR_LEDGER = os.path.join(wc.D, "vendor_ledger.tsv")


def _load_vendor():
    try:
        v = io.open(VENDOR_FILE, encoding="utf-8").read().strip()
        if v in VENDORS:
            return v
    except Exception:
        pass
    return VENDORS[0]


def _save_vendor(name):
    if name not in VENDORS:
        return False
    try:
        with io.open(VENDOR_FILE, "w", encoding="utf-8") as f:
            f.write(name)
        return True
    except OSError:
        return False


def rv_conflict(vendor=None):
    """审核者与翻译方**同源**则返回族名(如 deepseek), 否则空串(空串一律放行)。"""
    return rv.conflicts(rv.family_of(vendor or _load_vendor()))


# ---------------- 概念链接(用户自定义, v28.73) ----------------
# 原版超链接是**继承来的**(引文锚 -> 文献表, 学名 -> 附录), 读者只能点原书给的那几条;
# 而"读到这里我需要一点背景"这种需求原书不会替你想到 —— 位置该由读者定。面板这一区就干
# 这一件事, 但**判据一条都不重写**: 命中几处、该不该给"第几处"、压住了谁, 全由
# tools/user_links.py 说了算(两处各判一套, 迟早分叉成两个答案)。面板只接这条动线:
#   点选页面文字 -> 规格文件 -> 预检(不落盘) -> 装入 + 变蓝(顺序由代码保证)。
UL_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UL_SCRIPT = os.path.join(UL_TOOLS, "user_links.py")
UL_STYLE = os.path.join(UL_TOOLS, "style_links.py")
UL_SPEC = os.path.join(wc.PROJ, "user_links.json")
UL_KINDS = (("mono", "-mono.pdf"), ("dual", "-dual.pdf"))


def ul_body():
    """当前正文任务(出稿那一个) —— 篇名/侧车的唯一来源, 与表格侧同一口径。"""
    return next((j for j in wc.available_jobs() if _is_render(j)), None)


def ul_task():
    """本篇任务名(规格 tasks 段的键)。取不到篇名时退回载荷名 —— 与表格侧同一个兜底。"""
    b = ul_body()
    return (wc.paper_of(b) if b else "") or wc.BODY_NAME


def ul_pdfs():
    """成品候选(mono/dual) —— 只列真存在的那些, 前端也只能从这里挑。"""
    stem = os.path.splitext(wc.BODY_PDF)[0]
    return [{"kind": k, "name": os.path.basename(stem + s), "path": stem + s}
            for k, s in UL_KINDS if os.path.exists(stem + s)]


def ul_sidecar():
    b = ul_body()
    return (b or {}).get("sidecar") or ""


def ul_load():
    """规格文件 -> dict。缺文件/坏文件都给一份最小骨架(口径说明见 user_links.py 模块头)。"""
    try:
        with io.open(UL_SPEC, encoding="utf-8") as f:
            spec = json.load(f)
        if isinstance(spec, dict):
            spec.setdefault("global", [])
            spec.setdefault("tasks", {})
            return spec
    except Exception:
        pass
    return {"_readme": ["本文件由面板『概念链接』区维护; 字段口径与工序顺序见 "
                        "tools/user_links.py 模块头。"],
            "global": [], "tasks": {}}


def ul_save(spec):
    """先写 .tmp 再 replace —— 面板崩在写一半时不能留下半个规格。"""
    tmp = UL_SPEC + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, UL_SPEC)


def ul_run(*args):
    """子进程跑 user_links.py / style_links.py, 返回 (rc, stdout+stderr)。"""
    env = dict(os.environ, P2Z_PROJ=wc.PROJ)
    p = subprocess.run([sys.executable] + list(args), cwd=UL_TOOLS,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


# ------------------------------------------------------- 结构自愈 (v28.78)
# v28.77 体检条目里说好的下一步, 准入条件只有一条: **透明** —— 只动结构层(版面/链接
# 热区), 不碰译文一个字; 每次都出报告(治了哪页、按哪条判据)。治什么、怎么治、什么算
# "没治好"全在工具里写死(heal_render 落盘后还重开核对链接数; relink 未命中的锚保持
# 原矩形), 面板不重写任何判据 —— 与概念链接区同一条纪律。
HEAL_TOOLS = ("heal_render.py", "relink_pages.py")


def heal_report_path(pdf, tool):
    """报告落**成品旁边**(审计轨迹跟着产物走, 不进临时目录 —— 隔天回看还在)。"""
    tag = "heal" if "heal" in os.path.basename(tool) else "relink"
    return os.path.splitext(pdf)[0] + ".%s.自愈报告.txt" % tag


def heal_run(tool, pdf, dry=False, runner=None, pdfs=None, original=None):
    """自愈执行体(处理器只调它) -> dict; 带 "error" 即失败。

    只做三件事:
      ① 路径守卫: 成品必须落在自动探测的那几个里 —— 会改 PDF 的工具, 不许浏览器
         指哪儿就改哪儿(与 _ul_target 同一道门);
      ② 组参数: --target/--original/--report 由面板补齐; dry 只对 heal_render 有意义
         (兼任排版伤体检: 退出码 1 = 有伤未治); dual 成品给 heal_render 加 --dual;
         relink 的侧车拿得到就传; 报告一律写到成品旁边(见 heal_report_path);
      ③ 转述: 工具的 stdout+stderr 与报告文件**一字不改**带回去 —— 工具说治了什么
         就是治了什么, 面板不描补、不吞错。"""
    tool = (tool or "").strip()
    if tool not in HEAL_TOOLS:
        return {"error": "不认识的自愈工具: %r(可用: %s)。" % (tool, " / ".join(HEAL_TOOLS))}
    cands = list(pdfs if pdfs is not None else ul_pdfs())
    if not cands:
        return {"error": "还没找到成品 PDF —— 先出稿(⑤), 再来自愈。"}
    hit = next((p for p in cands if p["path"] == (pdf or "")), None)
    if not hit:
        return {"error": "这份成品不见了或不在可治的那几份里 —— 重开面板再看。"}
    ori = original if original is not None else wc.BODY_PDF
    if not os.path.exists(ori):
        return {"error": "找不到原版 PDF: %s —— 没有原版就没有对照, 结构伤治不了。" % ori}
    if dry and tool != "heal_render.py":
        return {"error": "链接重定位没有只看模式 —— 它本来就不死链(未命中的锚保持原矩形)。"}
    rpt = heal_report_path(hit["path"], tool)
    args = [tool, "--target", hit["path"], "--original", ori, "--report", rpt]
    if tool == "heal_render.py":
        if hit["kind"] == "dual":
            args.append("--dual")
        if dry:
            args.append("--dry-run")
    else:
        sc = ul_sidecar()
        if sc:
            args += ["--sidecar", sc]
    rc, out = (runner or ul_run)(*args)
    report = ""
    try:
        with io.open(rpt, encoding="utf-8") as f:
            report = f.read()
    except OSError:
        pass
    return {"tool": tool, "pdf": hit["path"], "rc": rc, "dry": bool(dry),
            "out": out, "report": report, "report_path": rpt}


def ledger(stage, job_label, n_units, n_suspect, verdict, note=""):
    """台账追加一行 TSV。**旁证, 不是门禁** —— 失败只吞掉, 绝不影响主流程。

    列: 时间/阶段/任务/翻译方/单元/待看/结论/备注。"待看"= ③ 里机器判不了、留给人的条数
    (换家之后"哪家强"就看这两列: 门禁过没过、待看几条)。缺编号连任务都认不出时, 单元记 0、
    结论记「编号不齐」、备注带一行诊断。
    """
    try:
        fresh = not os.path.exists(VENDOR_LEDGER)
        with io.open(VENDOR_LEDGER, "a", encoding="utf-8") as f:
            if fresh:
                f.write("时间\t阶段\t任务\t翻译方\t单元\t待看\t结论\t备注\n")
            f.write("%s\t%s\t%s\t%s\t%d\t%d\t%s\t%s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), stage, job_label,
                       _load_vendor(), n_units, n_suspect, verdict,
                       str(note).replace("\t", " ").replace("\n", " ")[:200]))
    except OSError:
        pass


def short_of(label):
    """任务短名(下拉里的标签可能带论文全名, 待办行里放不下): "正文 Li _ - 2026 - …" -> "正文"。"""
    return label.split(" ")[0]


def _is_render(job):
    """会**触发重渲染**的任务(正文) —— 只有它出稿那一刻值得提一句"这一篇还有没出稿的"。

    [v28.71 改判] 原来这里写的是"渲染是回不了头的那一步, 所以表格必须先出稿"。**那是错的**:
    查过依赖 —— 渲染一路(server/patches/engine/seg_*)从不读表格产物, `table_*.zh.tsv` /
    `notes_zh.json` 只有 mk_appendix.py(排附录 DOCX)与 mk_ledger.py(底片)在读, `P2Z_TABLE_DIR`
    也只有表格工具自己用。PDF 里的表格页**本来就保持英文**, 表格译文走附录 DOCX 这条路。
    所以"表格没做完就渲染"不会让 PDF 出问题, 只意味着附录还没生成(随时可重做, 不是单程票)。
    于是硬拦截降级为**提醒**: 任意顺序 · 齐了就过 · 缺了提醒(见 paper_tasks / pending_of)。
    """
    return bool(job.get("render"))


def paper_tasks():
    """**这一篇**该做的全部任务 -> [{"label","short","state","why","paper","foreign",
    "unlabeled","self"}]。

    列表 = 正文 + 工作目录里的表格正文/表注:
      归属 —— 表格 manifest 里由装配器烙的篇名(mk_job.py --paper)与本篇一致; 老目录没烙(空)
              则按"一个工作目录 = 一篇"当成本篇的, 标 unlabeled 让人核; 烙了但**不一致**的
              仍列出并标 foreign(那是别人的表, 不该算成"你欠的")。
      状态 —— 产物判据(wc.released): done / stale(装配过新一轮, 要重做) / todo。

    [v28.71] 判据从"你复制过的做完了吗"换成"这一篇该做的都做了吗": 前者靠 ① 记录鼠标动作,
    重启面板就失忆, 也拦不住"压根忘了做表格"; 后者落在产物上, 天然持久、跨篇可分。
    """
    jobs = wc.available_jobs()
    body = next((j for j in jobs if _is_render(j)), None)
    if body is None:
        return []
    bp = wc.paper_of(body)
    out = []
    for j in jobs:
        tp = wc.paper_of(j)
        st, why = wc.released(j)
        out.append({"label": j["label"], "short": short_of(j["label"]),
                    "state": st, "why": why, "paper": tp,
                    "foreign": bool(not _is_render(j) and tp and bp and tp != bp),
                    "unlabeled": bool(not _is_render(j) and not tp),
                    "self": j is body})
    return out


def pending_of(todos):
    """这一篇该做而**还没出稿**的(排除正在出稿的那一个, 也排除烙着别篇的) —— 出稿时据
    此提醒, 不拦。

    foreign(烙的篇名与正文那篇对不上)是**别人的表**, 不该算成"你欠的": 待办行里仍列出让人
    看得见(标"另一篇的"), 但不进提醒、也不进台账的「缺: X」。
    """
    return [t for t in todos if t["state"] != "done" and not t["self"] and not t["foreign"]]


def paper_done(todos):
    """**这一篇该做的都出过稿了吗** —— ⑤ 出稿后要不要收摊, 就看这一个判据(见 stop_after_done)。

    为什么不是"⑤ 一成功就收": 面板是多格的(正文 + 表格正文/表注), 顺序随人 —— 正文出完稿、
    表格还没做就关窗, 等于把人手里的活收走。所以判据是"**整篇齐了**"而不是"这一步过了"。
    与 pending_of 的差别: 那个排除 self(出稿时提醒"还有 X 没出稿"用, 正在出的那块当然不算),
    这里恰恰要算上正在出的那块 —— 它是收尾那一步, 出完才算齐。

    foreign(烙着别篇的表)不算本篇欠的, 与 pending_of 同一口径。列表为空(这个目录里没有
    正文任务)也**不算齐** —— 那种面板没有"这一篇"可言, 关不关不由这里决定。
    """
    return bool(todos) and not any(t["state"] != "done" and not t["foreign"] for t in todos)


def note_sent(job, n_units):
    """① 复制成功时记一笔**外发历史**("这一轮我把哪几块发出去了")。

    与 ③ 检查/⑤ 出稿那两行合起来能还原一轮的完整轨迹; **旁证, 不是门禁** —— 台账写不进去
    不影响复制本身。
    """
    ledger("复制", job["label"], n_units, 0, "已复制", "")
    return paper_tasks()


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
    落盘只发生在"确认"那一步。正文任务的门禁(seg_import)会写 out/ 与返工单, 额外把
    P2Z_PROJ 也指向 tmp 隔离(见 watch_clip.body_imported 的约定); 它的 manifest 是 inbox
    里的绝对路径, 只读不拷。
    """
    tmp = tempfile.mkdtemp(prefix="p2z_panel_")
    try:
        man = wc.manifest_path(job)
        if man and not os.path.isabs(job.get("manifest", "")):
            shutil.copy(man, tmp)
        wc.write_resp(os.path.join(tmp, job["resp"]), job, ids, got)
        env = dict(os.environ, P2Z_TABLE_DIR=tmp)
        if job.get("sidecar"):
            env["P2Z_PROJ"] = tmp
        p = subprocess.run(job["cmd"](), cwd=tmp, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        out = (p.stdout or "").splitlines()
        # [v28.79] 正文若走 adopt 回路, ③ 这里只跑得到第一道门(seg_import); 出稿那一步
        # 还有第二道门(deliver/import: ⋮ 断点 / 只译半截 / 逐段不变量)。不点一句, 用户
        # 会以为"③ 过了"就是全过。
        if p.returncode == 0 and wc.argv_list(job.get("gate_after")):
            out.append("  · 注: ③ 只跑第一道门; ⑤ 确认出稿时还会另过 adopt 的"
                       " deliver/import(⋮ 断点对账 / 只译半截 / 逐段不变量)。")
        note = ""
        if p.returncode != 0:
            out, note = _persist_rework(tmp, out)
        return p.returncode == 0, out, note
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_REWORK_LINE = re.compile(r"返工单:\s*(\S.*?)\s*$")

# 归谁的责任: 命中这些的失败**不是翻译方的错** —— 环境/工具侧(引擎没接线、载荷或附件
# 读不到、门禁脚本自己崩了)。把它们误报成"翻译方的错"会让用户拿着返工单去问翻译方,
# 白跑一轮还在原地。其余带 FAIL 行的译者侧失败(字形丢失、段数不符、⋮ 错位)才是翻译方的。
# (v28.62 之前这里写死"豆包"; 现在翻译方可以是任意网页 AI, 故只报**角色**, 具体是谁
#  由 panel 的 报错明细卡片按用户选中的翻译方补上。)
_TOOL_FAIL_MARK = ("未接线", "找不到载荷原文", "读不到", "Traceback", "退出码",
                   "manifest", "sidecar", "无法校验")


def blame_of(gate_out, ok):
    """失败归谁: "翻译方" / "工具" / ""(没失败)。

    判据顺序要紧: 先看有没有**译者侧的 FAIL 行**(译者侧 = 带 FAIL 前缀、且行内不含
    工具标记), 有就是翻译方的; 再看工具标记。反过来先扫全局工具标记会误判 ——
    正常的 PASS 路径日志里也常出现 "读 manifest 完成" 这种行, 一旦和 FAIL 行同时
    出现, 全局扫描会把翻译方的错报成工具故障(实测这条误判过), 用户就被误导去查环境。
    """
    if ok:
        return ""
    lines = [ln.strip() for ln in (gate_out or [])]
    for ln in lines:
        if ln.startswith("FAIL") and not any(m in ln for m in _TOOL_FAIL_MARK):
            return "翻译方"
    return "工具"                      # 工具标记命中, 或无 FAIL 行的非零退出(脚本自己出错)


def _persist_rework(tmp, lines):
    """把沙箱里的返工单搬到**真实 inbox**, 返回 (改写后的日志行, 单子正文)。

    返工单是**给用户/豆包看的产物**, 不是门禁痕迹 —— 但检查阶段整个 tmp 目录
    随即被删, 单子上印的路径就成了死链(实测: 面板提示
    `返工单: ...\\Temp\\p2z_panel_xxx\\inbox\\<名>.rework.md`, 用户按它去读时
    文件已不存在)。其余门禁产物(zh TSV / check_report)照旧只落 tmp、随目录删除,
    只有这份单子必须活过这次检查。
    正文一并返回: 面板要把它显示出来、并让用户**一键复制**给翻译方(网页 AI 没有桥,
    读不到 inbox 里的文件, 只能粘贴文本)。
    """
    notes = []
    for root, _dirs, files in os.walk(tmp):
        for f in files:
            if f.endswith(".rework.md"):
                notes.append(os.path.join(root, f))
    if not notes:
        return lines, ""
    mapping, body = {}, ""
    try:
        os.makedirs(wc.INBOX, exist_ok=True)
        for src in notes:
            dst = os.path.join(wc.INBOX, os.path.basename(src))
            shutil.copyfile(src, dst)
            mapping[src] = dst
            if not body:
                body = io.open(src, encoding="utf-8").read()
    except OSError:
        return lines, ""
    if not mapping:
        return lines, ""
    out = []
    for ln in lines:
        m = _REWORK_LINE.search(ln)
        if m and m.group(1) in mapping:
            out.append("返工单(已保留): %s" % mapping[m.group(1)])
        else:
            out.append(ln)
    out.append("提示: 返工单只列「必须改」的段与缺失字形 —— 上面「报错明细」里可一键"
               "复制给翻译方, 让它只改那些段, 其余段务必照抄不要重译。")
    return out, body


def analyse(text):
    """纯逻辑, 不碰 Tk(便于 --selftest): 识别任务 -> 沙箱跑门禁 -> 算待看清单。

    返回 {"error": ...} 或 {"ok", "job", "ids", "got", "rows", "median", "items",
                          "n_dup", "n_incons", "last_ratio", "gate_out", "last"}
    items 每项 = (级别, 类型, 说明, 单元号串)；**只放源串自锚的判据**(见 docstring [v30.5])——
    长度比一类**读数**一律不进 items, 只作 rows / median / last_ratio 显示给人看。
    rows 按 manifest 顺序(文档序), 不按读数排序。
    """
    if not text.strip():
        return {"error": "粘贴区是空的。"}
    if any(m in text for m in wc.PREAMBLE_MARK):
        return {"error": "这是「发出去」的待译文本(含【待译】/【任务】标记), 不是翻译方的回包。"}

    hit = wc.match_job(text, log=lambda *a: None)
    if not hit:
        diag = []
        for job in wc.available_jobs():
            ids = wc.manifest_ids(job)
            got = wc.parse_units(text, ids, job["pre"])
            miss = [i for i in ids if i not in got]
            extra = sorted(i for i in got if i not in ids)
            diag.append("「%s」期望 %d 条 / 读到 %d 条%s%s"
                        % (job["label"], len(ids), len(got),
                           ("  缺 " + _brief(miss)) if miss else "",
                           ("  多 " + _brief(extra)) if extra else ""))
        return {"error": "没有识别到完整回包(编号不齐)。", "diag": diag}

    job, ids, got = hit
    orig = {u["id"]: u["orig"] for u in wc.manifest_units(job)}
    ok, out, note = sandbox_gates(job, ids, got)

    rows = [(_ratio(orig[i], got[i]), i, orig[i], got[i]) for i in ids]   # 文档序, 不排序
    med = statistics.median(r[0] for r in rows) if rows else 0.0
    last_id = ids[-1]
    last_r = rows[-1][0]

    # ---------------- 待看清单: 只放**源串自锚**的判据。正常回包应为空。 ----------------
    # 长度比(rows / med / last_r)是**读数**: 只在全表那一列与顶部显示, 不产生任何条目 ——
    # 它的参照(整篇中位数)跨了源串类别, 且"低于中位"按定义就是整篇约 50% 的事件。见 [v30.5]。
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

    items.sort(key=lambda x: 0 if x[0] == "高" else 1)     # 稳定排序: 高在前, 组内保原序
    return {"ok": ok, "job": job, "ids": ids, "got": got, "rows": rows, "median": med,
            "items": items, "n_dup": len(dup), "n_incons": len(incons),
            "last_ratio": last_r, "gate_out": out, "rework": note,
            "blame": blame_of(out, ok),
            "last": (last_id, orig[last_id], got[last_id])}


# 抬头里**只有页码**的形态: 导出没给 --doc 时 RULES 就写 "[文档] 第1-3页"(抬头宁缺勿谎,
# 见 seg_export v28.1)。可这个抬头对审核者是**零信息** —— 每篇都一样, 篇名/学科先验等于
# 没给(实测三篇载荷首行全如此)。
_DOC_PAGES_ONLY = re.compile(r"^第[\d\s,，\-—~～至]*页$")


def _doc_of(job):
    """载荷抬头的 [文档] 行 —— 给审核者一点篇名/学科先验, 术语判断更准。取不到返回 ""。

    [v28.75] 抬头只有页码时**回落篇名**(wc.paper_of: 正文任务 = 载荷名/原文 stem;
    表格任务 = 装配器烙进 manifest 的篇名)。连篇名都没有就原样退回 —— 不编造。
    """
    p = job.get("payload") or job.get("job") or ""
    p = p if os.path.isabs(p) else os.path.join(wc.D, p)
    try:
        with io.open(p, encoding="utf-8") as f:
            first = f.readline().strip()
    except OSError:
        return ""
    if not first.startswith("[文档]"):
        return ""
    head = first[len("[文档]"):].strip()
    if head and not _DOC_PAGES_ONLY.match(head):
        return first                       # 抬头里已有篇名, 原样用
    name = wc.paper_of(job)
    if not name:
        return first
    return ("[文档] %s %s" % (name, head)) if head else ("[文档] %s" % name)


def review(text):
    """纯逻辑(便于 --selftest/单测): 回包 -> (原文, 译文) 对照 -> 交 ④ 语义审核。

    与 analyse 的区别: **不跑门禁** —— 审核不需要、也不该等沙箱; 只要求编号齐全,
    因为编号不齐连段都对不上, 审出来的条目落不到任何一段上。

    审核者是**与翻译方无关的第三方**(硅基流动), 只报不改: 返回的存疑清单给用户裁决,
    不参与"能不能出稿"的判定 —— 放行权仍在本机门禁(relay_spec 第 4 条红线)。

    **同源拒审**: 翻译方由面板选(可以是任意网页 AI), 一旦与审核者是同一家模型(例如
    用户选了 DeepSeek 网页版, 而审核者也是 DeepSeek-V3.2), 审核就退化成"自己给自己
    打分", 此处直接拒绝 —— 拦在联网之前, 不花这笔钱(判据见 reviewer.conflicts)。
    """
    if not text.strip():
        return {"error": "粘贴区是空的。"}
    fam = rv_conflict()
    if fam:
        return {"error": "红线: 审核者(硅基流动 %s)与翻译方(%s)同源 —— 被翻译方不得自校。"
                         "换一个翻译方, 或换一个审核者再点 ④。"
                         % (rv.config()["model"], _load_vendor())}
    hit = wc.match_job(text, log=lambda *a: None)
    if not hit:
        return {"error": "没有识别到完整回包(编号不齐) —— 先点 ③ 检查, 看缺哪些编号。"}
    job, ids, got = hit
    orig = {u["id"]: u["orig"] for u in wc.manifest_units(job)}
    pairs = [(i, orig[i], got[i]) for i in ids]
    if not any(o for _, o, _ in pairs):
        return {"error": "manifest/payload 里取不到原文 —— 没有对照就没有审核, "
                         "只能靠门禁与读数核对。"}
    doc = _doc_of(job)
    r = rv.audit(pairs, doc=doc, terms=rv.load_terms())
    if not r.get("ok"):
        return {"error": r.get("err") or "审核未完成。"}
    r["job"] = job["label"]
    r["vendor"] = _load_vendor()            # 溯源: 审核报告与卡片都写明"谁译的、谁审的"
    r["report"] = rv.write_report(rv.report_md(r, doc=doc, stem=job["label"]),
                                  stem=job["label"])
    return r


def _review_src(label):
    """入表时记进 `# 来源` 的篇名 —— 优先取**这次审核的那篇**(前端把 job 标签带回来),
    取不到退回正文任务。写不出来就留空, append_terms 会写成「篇名未记」(不编造)。"""
    try:
        hit = next((j for j in wc.available_jobs() if j.get("label") == label), None)
        return wc.paper_of(hit) if hit else wc.paper_of(wc.JOBS[2])
    except Exception:                                       # noqa: BLE001
        return ""


# ------------------------------------------------------- 面板体检 (v28.77)
# 定位的落点: 认知劳动(查词/裁决/积累)归用户, 结构劳动(格式/渲染/链接)归工具 —— 那结构
# 劳动得让用户**自己够得着**, 不能一卡住就来找维护者开终端。体检是第一件: 把实测踩过的
# 每个"静默陷阱"(改了没生效 / 漏同步不报错 / 新旧实例共存)变成一键红灯。
# 全部只读(可写探针也只写一个临时文件、即写即删), 不跑门禁、不起沙箱、不碰翻译产物。
# 没查的两类(如实写在改动记录 v28.77, 不是遗漏): 缓存同名重交的 force 需求要读 pdf2zh
# 缓存库, 不是只读能判的; "旧页面连着已退出的实例"是浏览器侧的事, 服务器看不见。


def _netstat_text():
    """netstat -ano 全量输出; 跑不动(非 Windows / 被策略禁)返回 None —— 体检降级, 不崩。"""
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                           capture_output=True, text=True, timeout=15)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _listening_pids(text, port):
    """netstat 输出里在 port 上 LISTENING 的 PID 集合。
    行样例: `TCP  0.0.0.0:60642  0.0.0.0:0  LISTENING  21156`(IPv6 是 [::]:60642)。"""
    pids = set()
    for ln in (text or "").splitlines():
        if "LISTENING" not in ln:
            continue
        if str(port) not in re.findall(r":(\d+)\s", ln):
            continue
        tail = ln.split()[-1]
        if tail.isdigit():
            pids.add(int(tail))
    return pids


def _glossary_rows(path, expect_header):
    """术语表 -> (entries, bad_lines, has_comment, header_ok); 读不到返回 None。
      entries    [(en, zh)] —— 与读取端同口径(跳 `#`、按逗号分列、两列都要非空);
      bad_lines  行号: 非注释却拆不出两个非空字段 —— 三处读取端一律**静默跳过**,
                 这条词等于没写, 谁也不报错(手工补词漏个逗号就是这个形态);
      has_comment 是否有 `#` 行 —— terms.csv 合法(来源注释), terms.babeldoc.csv 不合法
                 (BabelDOC 解析器会把 `#` 行当词条吞掉, 见 v28.76);
      header_ok  首个非空行是否符合该表自己的表头约定: terms.csv **无**表头
                 (`english,chinese` 会被当成词条、还随审核提示词注入), babeldoc **必须**
                 `source,target` 开头(没有表头它会把首条词条当表头丢掉)。"""
    try:
        with io.open(path, encoding="utf-8") as f:
            lines = [ln.rstrip("\r\n").lstrip("\ufeff") for ln in f]
    except OSError:
        return None
    entries, bad_lines, has_comment, header_ok = [], [], False, True
    first = True
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s:
            continue
        if first:
            first = False
            low = s.lower()
            header_ok = (low == "source,target") if expect_header \
                else (low not in ("source,target", "english,chinese"))
            if low in ("source,target", "english,chinese"):
                continue        # 表头行不当词条: babeldoc 的合法表头; terms.csv 出现
                                # 表头已由 header_ok 报警, 别再让它进词条污染对账
        if s.startswith("#"):
            has_comment = True
            continue
        parts = [x.strip() for x in s.split(",")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            entries.append((parts[0], parts[1]))
        else:
            bad_lines.append(i)
    return entries, bad_lines, has_comment, header_ok


def health_checks(terms=None, terms_babeldoc=None, cfg_toml=None, netstat=None,
                  review_dir=None, ledger_dir=None, own_pid=None):
    """体检 -> [(name, status, detail)]; status ∈ ok / warn / bad。

    每一项都对应一次**实测踩过的静默陷阱**(出处见改动记录 v28.77)。一切输入可注入
    (测试离线跑), 缺省取真实路径 —— 面板里只有一个调用: _health()。
    [v28.76] 是写入侧一次写齐两份表, 这里是**读取侧对账**: 写入侧再稳, 也挡不住有人
    手工只改了一份 —— 漏的那份静默失效, 换个引擎那个词就不生效, 且无处报错。"""
    out = []

    def add(name, status, detail):
        out.append((name, status, detail))

    # ① 术语表·双表同步 + 各自格式口径
    files = rv.terms_files()
    t_path = terms if terms is not None else files[0]
    b_path = terms_babeldoc if terms_babeldoc is not None else files[1]
    t = _glossary_rows(t_path, False)
    b = _glossary_rows(b_path, True)
    if t is None:
        add("术语表·1.x 读不到", "bad",
            "%s 读不到 —— 1.x 这边的词全不生效, 且不报错。" % os.path.basename(t_path))
    if b is None:
        add("术语表·BabelDOC 读不到", "bad",
            "%s 读不到 —— 换 BabelDOC 引擎时术语全不生效, 且不报错。" % os.path.basename(b_path))
    if t is not None and b is not None:
        if b[2]:
            add("术语表·BabelDOC 掺了注释行", "bad",
                "`#` 行会被 BabelDOC 的解析器当词条收进去(它不认注释) —— 那行注释会变成一条假术语。")
        td = {e.lower(): z for e, z in t[0]}
        bd = {e.lower(): z for e, z in b[0]}
        clash = sorted(e for e in set(td) & set(bd) if td[e] != bd[e])
        only_t = sorted(set(td) - set(bd))
        only_b = sorted(set(bd) - set(td))
        if clash:
            add("术语表·两表译名打架", "bad",
                "同词不同译 %d 个(如 %s) —— 换个引擎, 同一个词就换了个中文名。"
                % (len(clash), " / ".join("%s: %s|%s" % (e, td[e], bd[e]) for e in clash[:3])))
        elif only_t or only_b:
            add("术语表·双表同步", "warn",
                "1.x %d 条 ↔ BabelDOC %d 条%s%s —— 漏的那份**静默失效**: 走那个引擎, 这些词不生效。"
                % (len(t[0]), len(b[0]),
                   (", 1.x 独有: " + " / ".join(only_t[:5])) if only_t else "",
                   (", BabelDOC 独有: " + " / ".join(only_b[:5])) if only_b else ""))
        else:
            add("术语表·双表同步", "ok",
                "1.x %d 条 ↔ BabelDOC %d 条, 译名一致。" % (len(t[0]), len(b[0])))
        for label, rows, header_ok, why in (
                ("1.x", t, t[3],
                 "首行像是表头 —— terms.csv 是**无表头**格式, `english,chinese` 会被当成词条, 且每次审核都随提示词注入。"),
                ("BabelDOC", b, b[3],
                 "没有 `source,target` 表头 —— BabelDOC 解析器会把首条词条当表头丢掉。")):
            if not header_ok:
                add("术语表·%s 表头口径" % label, "warn", why)
            if rows[1]:
                add("术语表·%s 坏行" % label, "warn",
                    "第 %s 行拆不出「原词, 译名」两列 —— 读取端一律静默跳过, 这条词等于没写。"
                    % "、".join(str(x) for x in rows[1][:5]))

    # ② config.toml 的 Ital 陷阱(v8 实测: .*Ital 匹配 NimbusRomNo9L-ReguItal,
    #    整段斜体被静默吞成"公式", 段落凭空消失)
    cfg = cfg_toml if cfg_toml is not None else os.path.join(
        wc.PROJ, "server", "config", "config.toml")
    try:
        with io.open(cfg, encoding="utf-8") as f:
            hit = next((ln.strip() for ln in f
                        if "formular_font_pattern" in ln and "Ital" in ln), None)
        add("config.toml·Ital 陷阱",
            "bad" if hit else "ok",
            ("formular_font_pattern 含 Ital(%s…) —— 整段斜体会被静默吞成公式。把 Ital 从模式里拿掉。"
             % hit[:60]) if hit else "formular_font_pattern 不含 Ital, 斜体段不会被吞。")
    except OSError:
        add("config.toml·Ital 陷阱", "ok",
            "没有 %s —— 沿用引擎默认, 这条陷阱不成立。" % cfg)

    # ③ 端口共存(8890: 翻译服务, SO_REUSEADDR 让"重启"变成新旧共存、服务的还是旧的)
    text = _netstat_text() if netstat is None else netstat
    if text is None:
        add("端口·实例共存", "warn",
            "netstat 跑不动 —— 新旧实例共存这条查不了(重启翻译服务前自己看一眼)。")
    else:
        pids = _listening_pids(text, 8890)
        if len(pids) > 1:
            add("端口·翻译服务共存", "bad",
                "%d 个进程同时在 8890 监听 —— SO_REUSEADDR 让新旧共存, **正在服务的是旧的**; "
                "重启并没有替换它。先杀旧 PID(%s)再起。"
                % (len(pids), ", ".join(str(p) for p in sorted(pids))))
        elif not pids:
            add("端口·翻译服务共存", "warn",
                "8890 上没有监听 —— 翻译服务没起(要渲染/force 重渲染才需要, 不急)。")
        else:
            add("端口·翻译服务共存", "ok", "8890 单实例监听, 没有旧实例共存。")
        mine = _listening_pids(text, 60642)
        others = mine - {own_pid if own_pid is not None else os.getpid()}
        if others:
            add("端口·面板共存", "warn",
                "60642 上还有别的面板实例(PID %s) —— 连着 60642 的旧窗多半是它的, "
                "新代码它吃不到(旧进程里还是旧代码)。认准横幅里的地址再用。"
                % ", ".join(str(p) for p in sorted(others)))
        else:
            add("端口·面板共存", "ok",
                "60642 上只有本面板, 没有旧实例。" if mine
                else "60642 现在空着(本面板在随机端口), 没有旧实例。")

    # ④ 可写探针: ④ 落报告与 ⑤ 出稿各有一个落点, 写不进去就是半路硬停
    for label, d in (("④ 审核报告目录", review_dir if review_dir is not None else rv.REVIEW_DIR),
                     ("底片 ledger 目录", ledger_dir if ledger_dir is not None
                      else os.path.join(wc.PROJ, "ledger"))):
        probe = None
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".p2z_health_probe")
            with io.open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.unlink(probe)
            add("可写·%s" % label, "ok",
                "%s 可写 —— ④ 落报告/⑤ 出稿不会被目录权限半路拦下。" % d)
        except OSError as e:
            add("可写·%s" % label, "bad",
                "%s 写不进去(%s) —— 到用的时候会在半路报这个错, 先修目录。" % (d, e))
            if probe and os.path.exists(probe):
                try:
                    os.unlink(probe)
                except OSError:
                    pass
    return out


# ---------------------------------------------------------------------- 前端页面(内嵌单页, __THEME__ 由服务器替换)

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="__THEME__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>翻译中继</title>
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
/* 时间戳显示(表头「构建」): 数字等宽, 免得跳字时整行左右抖 */
.idle-time{font-variant-numeric:tabular-nums;white-space:nowrap}
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
/* 本篇待办行: ✓ 已出稿 / ⟳ 要重做(产物比输入旧) / ○ 未出稿。 */
.todorow{padding:9px 18px;font-size:12.5px}
.todorow .done{color:var(--ok)}
.todorow .stale{color:var(--primary);font-weight:700}
.todorow .todo{color:var(--warn);font-weight:700}
.todorow .tag{color:var(--muted)}
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
.banner.warn{color:var(--warn);font-weight:700}
.banner.ok{color:var(--ok)}
.banner.err{color:var(--danger)}
/* 报错明细: 只在门禁 FAIL 时出现 —— 说到「哪段、缺哪个字形」, 并可一键复制给翻译方。
   网页 AI 没有桥、读不到 inbox 里的返工单文件, 只能靠粘贴, 故正文直接摊在面板上。 */
.rw-badge{display:inline-flex;align-items:center;padding:2px 10px;border-radius:999px;
  font-size:12px;font-weight:600;vertical-align:middle}
.rw-badge.bao{background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--warn)}
.rw-badge.tool{background:color-mix(in srgb,var(--danger) 20%,transparent);color:var(--danger)}
.rw-badge.warn{background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--warn)}
.rw-badge.ok{background:color-mix(in srgb,var(--ok) 20%,transparent);color:var(--ok)}
.rw-note{white-space:pre-wrap;word-break:break-word;
  font:12.5px/1.7 Consolas,"Cascadia Mono",monospace;
  background:var(--input-bg);border:1px solid var(--glass-border);border-radius:14px;
  padding:11px 13px;max-height:280px;overflow:auto;margin-top:9px}
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
/* 术语裁决入表(v28.76): ④ 报「术语」存疑 -> 人裁决 -> 就地写进术语表。两份表(1.x 的
   terms.csv 与 BabelDOC 的 terms.babeldoc.csv)格式不同, 手工维护必漏其中一份, 而漏掉的
   那份是**静默失效** —— 交给机器一次写齐。 */
.tform{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
.tex{word-break:break-all}
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
.uli{background:var(--input-bg);color:var(--text);border:1px solid var(--glass-border);border-radius:10px;
  padding:6px 10px;font:inherit;font-size:13px;outline:none}
.uli:focus{border-color:var(--primary);box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 20%,transparent)}
.ul-lines{max-height:190px;overflow:auto;border-radius:12px;border:1px solid var(--glass-border);
  background:var(--card-bg);padding:4px;margin:8px 0}
.ul-line{padding:5px 10px;border-radius:8px;cursor:pointer;font-size:13px;word-break:break-all}
.ul-line:hover{background:var(--hover)}
.ul-line.sel{background:color-mix(in srgb,var(--primary) 22%,transparent);color:var(--text)}
.ulok{color:var(--ok)} .ulerr{color:var(--danger)}
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
    <div class="ttl"><span class="dot"></span>翻译中继</div>
    <div class="wd" id="workdir"></div>
    <span class="muted idle-time" id="buildTxt" title="本面板吃的代码是哪一版的(panel.py / watch_clip.py / reviewer.py 里最新的改动时间)。60642 上若还挂着自家旧实例, 那个窗显示的会是更早的时间 —— 别对着旧窗找新功能。">—</span>
    <button class="btn small" id="healthBtn" type="button" title="把实测踩过的静默陷阱(术语双表漏同步 / config Ital 吞斜体 / 端口新旧实例共存 / 目录不可写)变成红灯 —— 只读检查, 不动任何文件">体检</button>
    <button class="btn small" id="themeBtn" type="button">切到浅色</button>
  </header>

  <section class="glass row sendrow">
    <label>任务</label>
    <div class="dd" id="taskSel">
      <button class="dd-btn" type="button" aria-haspopup="listbox" aria-expanded="false"><span class="dd-val">—</span><span class="dd-arrow">▾</span></button>
      <div class="dd-list" role="listbox" hidden></div>
    </div>
    <label>翻译方</label>
    <div class="dd" id="vendorSel" title="谁在翻译你家论文 —— 只影响台账归因、报错明细的措辞与 ④ 的同源守卫, 不影响门禁(门禁只看编号与占位符)">
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
    <button class="btn" id="reviewBtn" type="button">④ 语义审核</button>
    <button class="btn primary" id="commitBtn" type="button" disabled>⑤ 确认写入并出稿</button>
    <span class="muted" id="rvGuard"></span>
  </section>

  <div class="glass banner idle" id="banner">尚未检查。</div>

  <div class="glass row todorow" id="todoRow">
    <span class="muted">这一篇</span>
    <span id="todoStat">还没有可判的任务 —— 装配后这里会列出这一篇该做的每一块。</span>
  </div>

  <section class="glass" id="rwCard" hidden>
    <div class="row spread">
      <span class="sec-ttl">报错明细 <span class="muted">（③ 检查未过时才有 —— 它是 ③ 的输出物, 不是一步操作）</span> <span class="rw-badge" id="rwBadge"></span></span>
      <button class="btn small" id="rwCopyBtn" type="button">复制给翻译方</button>
    </div>
    <div class="muted" id="rwWhy"></div>
    <div class="rw-note" id="rwNote"></div>
  </section>

  <section class="glass" id="rvCard" hidden>
    <div class="row spread">
      <span class="sec-ttl">④ 语义审核(独立第三方) <span class="rw-badge" id="rvBadge"></span></span>
      <button class="btn small" id="rvCopyBtn" type="button">复制给翻译方</button>
    </div>
    <div class="muted" id="rvWhy"></div>
    <div id="rvList"></div>
    <div class="lids" id="rvPath"></div>
  </section>

  <section class="glass">
    <div class="row spread">
      <span class="sec-ttl">② 把翻译方的回复粘到这里</span>
      <span>
        <button class="btn small" id="clearBtn" type="button">清空</button>
      </span>
    </div>
    <textarea id="ta" spellcheck="false" placeholder="把翻译方的整段回复原样粘进来(不挑不拣, 全文粘贴) —— 代码围栏与首尾客套会自动剥掉"></textarea>
  </section>

  <section class="glass">
    <div class="seg" id="seg">
      <button data-tab="look" class="on" type="button">待看清单 (0)</button>
      <button data-tab="all" type="button">全表 (0)</button>
    </div>
    <div id="paneLook">
      <div class="emptyhint" id="emptyLook">尚未检查 —— 把翻译方的回复粘到上面, 然后点 ③ 检查。</div>
      <div id="lookList"></div>
    </div>
    <div id="paneAll" hidden>
      <div class="tblwrap"><table>
        <thead><tr><th class="c">编号</th><th class="c" title="只作读数, 不判缺陷 —— 比值大小由源串自身性质决定(小标题天然低于句子); 且「低于中位」按定义就是整篇约一半的单元">长度比<span class="muted"> 读数</span></th><th>原文</th><th>译文</th></tr></thead>
        <tbody id="tbody"></tbody>
      </table></div>
    </div>
  </section>

  <section class="glass" id="ulCard">
    <div class="row spread">
      <span class="sec-ttl">概念链接 <span class="muted">（读者入口：成品里“这段字 → 你的网址”由你定，不是继承原书给的那几条）</span></span>
      <span class="muted" id="ulStat">读规格中…</span>
    </div>
    <div class="row">
      <label>成品</label>
      <div class="dd" id="ulPdfSel" title="装到哪一份成品。双语版(dual)的页码按单语篇页数, 落到译文侧">
        <button class="dd-btn" type="button" aria-haspopup="listbox" aria-expanded="false"><span class="dd-val">—</span><span class="dd-arrow">▾</span></button>
        <div class="dd-list" role="listbox" hidden></div>
      </div>
      <label>页</label>
      <input class="uli" id="ulPage" type="number" min="1" value="1" style="width:78px">
      <button class="btn small" id="ulLinesBtn" type="button">取本页文字</button>
      <span class="muted" id="ulPageStat"></span>
    </div>
    <div class="ul-lines" id="ulLines" hidden></div>
    <div class="row">
      <label>锚</label>
      <input class="uli" id="ulAnchor" placeholder="点上面某一行的字填进来(手打要命: 差一个字就是一条死链)" style="flex:1;min-width:240px">
    </div>
    <div class="row">
      <label>网址</label>
      <input class="uli" id="ulUrl" placeholder="https://… (由你填, 机器不猜)" style="flex:1;min-width:220px">
      <label>第几处</label>
      <input class="uli" id="ulOcc" type="number" min="1" placeholder="留空=只此一处" style="width:150px">
    </div>
    <div class="row">
      <label>范围</label>
      <div class="seg" id="ulScopeSeg">
        <button data-ulscope="本篇" class="on" type="button">本篇</button>
        <button data-ulscope="全局" type="button">全局(跨篇通用)</button>
      </div>
      <button class="btn small" id="ulAddBtn" type="button">加入清单</button>
      <span style="flex:1"></span>
      <button class="btn small" id="ulCheckBtn" type="button">预检(不落盘)</button>
      <button class="btn primary small" id="ulApplyBtn" type="button">装入成品并变蓝</button>
    </div>
    <div class="muted" id="ulWhy"></div>
    <div id="ulList"></div>
    <div class="log" id="ulOut" hidden></div>
    <div class="muted">装入 = 只加你自己给的 URI 链接（原有的引文锚另说：压住同一条才删，且记账）。变蓝由 style_links 叠绘完成 ——
      顺序（user_links → style_links）由面板保证，不用你记。落在回填页（整页无译文、原封搬过来的页）上的新锚不会变蓝：
      那页的蓝字是原书自带的。</div>
  </section>

  <section class="glass" id="healCard">
    <div class="row spread">
      <span class="sec-ttl">结构自愈 <span class="muted">（出稿之后用：文字没翻错、版面坏了这类结构伤。只动版面与链接热区，不碰译文一个字；每次都出报告 —— 治了哪页、按哪条判据）</span></span>
    </div>
    <div class="row">
      <label>成品</label>
      <div class="dd" id="healPdfSel" title="治哪一份成品。治版面伤 = 旋转文本/文献区错位这类“文字没错、版面坏了”的伤；治链接伤 = 把链接热区按锚文本重搜搬回原位，未命中的保持原矩形不死链">
        <button class="dd-btn" type="button" aria-haspopup="listbox" aria-expanded="false"><span class="dd-val">—</span><span class="dd-arrow">▾</span></button>
        <div class="dd-list" role="listbox" hidden></div>
      </div>
      <button class="btn small" id="healScanBtn" type="button" title="干跑：只看伤情不落盘。有伤未治时退出码为 1 —— 是“查出伤”, 不是失败">只看伤情(不落盘)</button>
      <button class="btn small" id="healRenderBtn" type="button">治版面伤</button>
      <button class="btn small" id="healRelinkBtn" type="button">治链接伤</button>
    </div>
    <div class="rw-note" id="healOut" hidden></div>
    <div class="muted">报告落在<b>成品旁边</b>（<code>&lt;成品名&gt;.heal.自愈报告.txt</code> / <code>.relink.自愈报告.txt</code>），
      跟产物走、隔天回看还在。工具的输出与报告<b>一字不改</b>转述上来 —— 面板不重写任何判据。</div>
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
/* 本篇待办: 这一篇该做的每一块现在到哪一步了。判据在**产物**上(服务器算好 state),
   所以重启面板、隔天回来都不失忆 —— 它不是"你这轮点过什么"的记录。
   状态: done 产物齐且不比输入旧 / stale 产物比输入旧(装配过新一轮, 要重做) / todo 还没出稿。 */
function renderTodos(list){
  var el=$('todoStat');
  if(!list||!list.length){
    el.textContent='还没有可判的任务 —— 装配后这里会列出这一篇该做的每一块。';
    return;
  }
  el.innerHTML=list.map(function(x){
    var cls=x.state==='done'?'done':(x.state==='stale'?'stale':'todo');
    var txt=x.state==='done'?' ✓ 已出稿':(x.state==='stale'?' ⟳ 要重做':' ○ 未出稿');
    var tag=x.foreign?'<span class="tag">(另一篇的)</span>'
                     :(x.unlabeled?'<span class="tag">(未标篇名, 按本篇算)</span>':'');
    return esc(x.short)+'<span class="'+cls+'">'+txt+'</span>'+tag;
  }).join(' · ');
}
function edited(){
  commitBtn.disabled=true;commitBtn.textContent='⑤ 确认写入并出稿';
  $('rvCard').hidden=true;window.__rv='';       /* ④ 的结论是针对旧文本的, 一改即作废 */
  setBanner('idle','内容已改 —— 请重新点 ③ 检查。');
}
ta.addEventListener('input',edited);
$('sendBtn').addEventListener('click',function(){
  api('/api/send',{task:$('taskSel').value}).then(function(r){
    if(r.error){log('✗ '+r.error);return}
    renderTodos(r.todos);
    $('sendStat').textContent='已复制 '+r.n_units+' 单元 / '+r.n_chars+' 字符';
    log('已把「'+r.label+'」待译文本复制到剪贴板('+r.n_units+' 单元 / '+r.n_chars+' 字符); 粘进'
        +vendor+'即可(载荷自带抬头, 换家也不用改提示词)。');
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
  commitBtn.disabled=true;commitBtn.textContent='⑤ 确认写入并出稿';
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
  $('rwCard').hidden=true;window.__rw='';
  $('rvCard').hidden=true;window.__rv='';
}
/* 门禁 FAIL 时把「具体报错」摊开: 哪几段、缺哪个字形、责任归谁。
   归翻译方 -> 可一键复制给它返工; 归工具 -> 明说找开发者, 免得用户拿着单子白问翻译方。 */
function showRework(r){
  var card=$('rwCard');
  if(r.ok||!r.rework){card.hidden=true;window.__rw='';return}
  card.hidden=false;window.__rw=r.rework;
  var bao=r.blame==='翻译方';
  var b=$('rwBadge');
  b.className='rw-badge '+(bao?'bao':'tool');
  b.textContent=bao?('翻译方的问题 · '+vendor):'工具/环境问题';
  $('rwWhy').textContent=bao
    ?'判据: 译者侧失败(字形丢失/段数不符/断点错位) —— 把下面这段复制给'+vendor+', 让它只改点到的段。'
    :'判据: 工具/环境侧失败(引擎未接线、载荷或侧车读不到、门禁脚本自己报错) —— 交给'+vendor+'没用, 找开发者。';
  $('rwNote').textContent=r.rework;
}
$('rwCopyBtn').addEventListener('click',function(){
  if(!window.__rw)return;
  api('/api/copy',{text:window.__rw}).then(function(r){
    if(r.error){log('✗ '+r.error);return}
    log('已把报错明细复制到剪贴板('+r.n_chars+' 字符); 粘给'+vendor+', 明确要求「只改点到的段, 其余照抄」。');
  });
});
/* ④ 语义审核: 门禁之外的那一层 —— 漏译/错译/数字/术语/指代/表达。审核者是**与翻译方无关**
   的第三方(硅基流动), 只报不改: 结论进存疑清单给用户裁决, 不参与放行。手动触发, 付费调用。
   翻译方与审核者同源时服务器直接拒审(按钮也已禁用) —— 被翻译方不得自校。 */
$('reviewBtn').addEventListener('click',function(){
  if(!ta.value.trim()){log('✗ 粘贴区是空的 —— 先把'+vendor+'的回包粘到 ②。');return}
  var b=$('reviewBtn'),txt=b.textContent;
  b.disabled=true;b.textContent='审核中…';
  log('▶ 提交 ④ 语义审核: 整篇「原文↔译文」发给硅基流动(付费, 只审不改), 通常几十秒到几分钟。');
  api('/api/review',{text:ta.value}).then(function(r){
    b.textContent=txt;applyVendorGuard();       /* 恢复按钮态: 同源则该保持禁用 */
    if(r.error){
      setBanner('err','✗ 语义审核未完成: '+r.error);log('✗ '+r.error);return;
    }
    renderReview(r);
  });
});
/* 「源文缺陷」不进返工单(v28.75): 那类「错」在原文里就已经存在(老扫描件的形近字母数字
   混淆/缺字/断字), 而译者是**被规则硬性要求逐字符照抄**原文数字与字母的(机检按字形落点
   验收)。发给翻译方等于要他改一个**改不动**的东西(改了反而破坏字形回锚) —— 所以它们
   照常显示在卡片里, 但不进「一键复制给翻译方」的那段文本, 只落在报告里供原文校勘。 */
var SRC_KIND='源文缺陷';
function rvSplit(r){
  var t=[],s=[];
  r.items.forEach(function(it){(it.kind===SRC_KIND?s:t).push(it)});
  return {trans:t,src:s};
}
function rvRow(it){
  var hi=it.level==='高';
  /* 「术语」条目附一行就地入表(v28.76): 原词与译名由人裁决后填进来 —— 审核者只报不改,
     不给候选译名(见 reviewer 模块头), 所以这里不预填、不猜。 */
  var form='';
  if(it.kind==='术语'){
    form='<div class="tform">'
      +'<input class="uli tin" placeholder="原文术语(词或短语, 如 ovigerous lamellae)" style="flex:1;min-width:190px">'
      +'<input class="uli tzh" placeholder="中文译名" style="width:130px">'
      +'<button class="btn small tbtn" type="button">存进术语表</button>'
      +'<span class="muted tex"></span></div>';
  }
  return '<div class="look"><div class="lv '+(hi?'hi':'mid')+'">'+(hi?'▲':'●')+' '+it.level+'</div>'
    +'<div class="lkind">'+esc(it.kind)+'</div>'
    +'<div style="flex:1"><div class="lmsg">'+esc(it.note)+'</div>'
    +'<div class="lids">#'+esc(it.id)+(it.quote?' · 原文: '+esc(it.quote):'')
    +'</div>'+form+'</div></div>';
}
function rvText(r){
  var s=['【审核存疑清单】请只核对下面点到的段 —— 改完按原格式把**整段回复**重发一遍;'
         +'其余段逐字符照抄上一版, 不要重译、不要改动别的段、不要调整顺序。'];
  rvSplit(r).trans.forEach(function(it){
    s.push('- #'+it.id+' ['+it.kind+'·'+it.level+'] '+it.note
           +(it.quote?('   原文片段: '+it.quote):''));
  });
  return s.join('\n');
}
function renderReview(r){
  var p=rvSplit(r), n=p.trans.length, m=p.src.length;
  $('rvCard').hidden=false;
  var b=$('rvBadge');
  b.className='rw-badge '+((n||m)?'warn':'ok');
  b.textContent=(n||m)?(n+' 条存疑'+(m?(', 另有 '+m+' 条源文缺陷'):'')):'未发现问题';
  $('rvWhy').textContent='独立审核者 '+r.model+'（翻译方: '+(r.vendor||vendor)+'）: 逐段核对'
    +'「原文↔译文」的漏译/错译/数字/术语/指代/表达。编号、字形占位符、⋮ 断点、标点属'
    +'机械门禁的判据, 已排除不报。只审不改 —— 是否返工由你裁决。'
    +(m?(' 另 '+m+' 条「源文缺陷」错在**原文自己**(老扫描件的形近混淆/缺字/断字): '
        +'译者的规则要求逐字符照抄原文数字与字母, 所以这类不由译者承担, 也不要发给翻译方 —— '
        +'它们只作原文校勘线索(在报告里)。'):'')
    +(r.n_failed?(' 注意: '+r.n_failed+'/'+r.n_chunks+' 块未完成, 结论不完整。'):'');
  var list=$('rvList');list.innerHTML='';
  if(!n&&!m){
    list.innerHTML='<div class="empty">✓ 独立审核者没有报出语义问题。</div>';
  }else{
    if(n){
      p.trans.forEach(function(it){list.insertAdjacentHTML('beforeend',rvRow(it))});
    }else{
      list.insertAdjacentHTML('beforeend',
        '<div class="empty">✓ 译文侧没有报出语义问题。</div>');
    }
    if(m){
      list.insertAdjacentHTML('beforeend',
        '<div class="lmsg" style="margin:10px 0 2px">原文文本层缺陷（'+m+' 条, 不由译者承担）: '
        +'这些「错」在原文里就存在, 不要发给翻译方 —— 只能改原文或另作校勘注。</div>');
      p.src.forEach(function(it){list.insertAdjacentHTML('beforeend',rvRow(it))});
    }
  }
  window.__rv=r;
  $('rvPath').textContent=r.report?('报告: '+r.report):'';
  log('④ 语义审核完成('+r.model+'): '+r.n_units+' 段 / '+r.n_chunks+' 块 / '+r.secs+' 秒 · '
      +((n||m)?('存疑 '+n+' 条'+(m?(' + 源文缺陷 '+m+' 条'):'')):'未发现问题')
      +(r.n_failed?(' · '+r.n_failed+' 块未完成'):''));
  (r.logs||[]).forEach(function(l){log('   '+l)});
  r.items.forEach(function(it){
    log('   '+(it.level==='高'?'▲':'●')+' ['+it.kind+'·'+it.id+'] '+it.note);
  });
  if(r.items.some(function(it){return it.kind==='术语'})){
    log('   ↳ 带「术语」的条目可就地入表: 填原文术语与中文译名 -> 点「存进术语表」—— '
        +'两份术语表(1.x 与 BabelDOC)格式不同, 机器一次写齐, 不用手开 csv。');
  }
}
$('rvCopyBtn').addEventListener('click',function(){
  var r=window.__rv;
  if(!r||!r.items.length){log('✗ 没有可复制的存疑条目。');return}
  var p=rvSplit(r);
  if(!p.trans.length){
    log('✗ 只有「源文缺陷」('+p.src.length+' 条) —— 那类不由译者承担, 没有可发给翻译方的条目; '
        +'它们在报告里, 供原文校勘。');
    return;
  }
  api('/api/copy',{text:rvText(r)}).then(function(x){
    if(x.error){log('✗ '+x.error);return}
    log('已把 '+p.trans.length+' 条存疑复制到剪贴板('+x.n_chars+' 字符); 粘给'+vendor+', '
        +'明确要求「只改点到的段, 其余照抄」。'
        +(p.src.length?('另有 '+p.src.length+' 条源文缺陷**未复制** —— 错在原文, 发给翻译方没用。'):''));
  });
});
/* 术语裁决入表(v28.76): ④ 报「术语」存疑 -> 裁决完就地写表。判据全在服务器
   (reviewer.append_terms: 查重/两表同步/格式各按其表), 前端只负责把来源篇名捎回去,
   好让 `# 来源` 记的是**这次审核的那篇**而不是猜一篇。 */
$('rvList').addEventListener('click',function(e){
  var b=e.target.closest('.tbtn');if(!b||b.disabled)return;
  var box=b.closest('.tform'),en=box.querySelector('.tin').value.trim(),
      zh=box.querySelector('.tzh').value.trim(),st=box.querySelector('.tex');
  if(!en||!zh){st.textContent='✗ 原词与译名都要填。';st.className='muted ulerr';return}
  b.disabled=true;
  api('/api/termadd',{en:en,zh:zh,job:(window.__rv&&window.__rv.job)||''}).then(function(r){
    if(r.error){
      b.disabled=false;st.textContent='✗ '+r.error;st.className='muted ulerr';
      log('✗ 术语入表: '+r.error);return;
    }
    st.textContent=(r.wrote?'✓ ':'· ')+r.msg+'（表现有 '+r.n_terms+' 条）';
    st.className='muted'+(r.wrote?' ulok':'');
    log((r.wrote?'✓ ':'· ')+'术语入表: '+r.msg+'（表现有 '+r.n_terms+' 条）');
  });
});

/* 体检(v28.77): 把实测踩过的静默陷阱一键变成红灯。判据全在服务器 health_checks(只读),
   前端只如实转述 —— 一行一项, 不加不减; 红灯照说明处理, 黄灯只是提醒, 都不拦你干活。 */
$('healthBtn').addEventListener('click',function(){
  var b=this;b.disabled=true;b.textContent='体检中…';
  api('/api/health',{}).then(function(r){
    b.disabled=false;b.textContent='体检';
    if(r.error){log('✗ 体检没跑成: '+r.error);return}
    var ic={ok:'✓',warn:'⚠',bad:'✗'};
    r.items.forEach(function(it){log('  '+ic[it.status]+' ['+it.name+'] '+it.detail)});
    if(r.n_bad)log('✗ 体检: '+r.n_bad+' 项异常'+(r.n_warn?('、'+r.n_warn+' 项警告'):'')+' —— 异常项照说明处理后, 再点一次体检。');
    else if(r.n_warn)log('⚠ 体检: '+r.n_ok+' 项正常、'+r.n_warn+' 项警告 —— 警告项只是提醒, 不拦你干活。');
    else log('✓ 体检: 全部 '+r.n_ok+' 项正常。');
  });
});
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
  log((r.ok?'✓ ':'✗ ')+'「'+r.job+'」回包 '+r.n+' 单元; 门禁'+(r.ok?'全过':'未过')
      +' · 翻译方 '+vendor+' (已记台账)');
  showRework(r);
  (r.gate_out||[]).forEach(function(l){log('   '+l)});
  log('   末条 '+r.last.id+'  原文 '+JSON.stringify(r.last.orig)+'  ->  译文 '+JSON.stringify(r.last.zh));
  log('   一致性: 重复原文 '+r.n_dup+' 组, 其中译法不一致 '+r.n_incons+' 组');
  var list=$('lookList');list.innerHTML='';
  var el=$('emptyLook');
  el.hidden=r.items.length>0;
  el.className='empty';
  el.textContent='✓ 没有需要你核对的项目。\n\n'
    +'机器能判的(编号/占位符/数字/符号/代码缩写/一致性)都过了 —— 可以直接确认。\n'
    +'这里只会出现机器判不了的项, 正常回包为空。\n'
    +'长度比只是读数(全表那一列), 不作为告警 —— 小标题本来就短, 末条也常低于中位。';
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
    commitBtn.textContent='⑤ 确认写入并出稿'+(r.items.length?'（还有 '+r.items.length+' 条待看）':'');
    showTab('look');
  }else{
    log('   门禁未过, 不写入。修正后重新粘贴再检查。');
  }
}
/* ⑤ 出稿。底片(渲染前的存档)做不出来时服务器直接拦下(见 _commit), 这里只显示失败;
   **正文**出稿时若这一篇还有没出稿的, 服务器只**提醒**(日志黄条 + 台账记「缺 X」)并照旧渲染 ——
   顺序随你, 齐了就过, 缺了提醒。(v28.69 的硬拦截 + 逃生门已删: 渲染根本不读表格产物。)
   服务器那侧还多一条: 出稿成功且**这一篇都出齐了**时会安排收摊(退出腿②, 见 stop_after_done),
   响应里带 done —— 这里据此把话说明白并顺手试一次关窗。 */
function doCommit(){
  commitBtn.disabled=true;
  setBanner('busy','正在写入并排版…');
  api('/api/commit',{text:ta.value}).then(function(r){
    (r.log||[]).forEach(function(l){log(l)});
    renderTodos(r.todos);
    if(!r.ok){
      setBanner('err','✗ '+(r.error||'未出稿'));
      log('✗ '+(r.error||'未出稿(门禁未过或排版失败)。'));
      if(!r.stale)commitBtn.disabled=false;
      return;
    }
    var msg=(r.warn?'! 已出稿, 但这一篇还有没出稿的: ':'✓ 已出稿: ')+r.out;
    if(r.done)msg+=' —— 这一篇已出齐, 面板即将自动关停(这扇窗可以关了)';
    setBanner(r.warn?'warn':'ok',msg);
    log('✓ 出稿: '+r.out+'  (翻译方 '+vendor+', 已记台账)');
    if(r.done){
      /* 退出腿②(服务器的 stop_after_done 会关掉服务器)。这里顺手试着把窗口也关掉 ——
         --app 开出来的窗通常拒绝脚本关窗(不是 script 打开的), 关不掉就靠横幅告诉人;
         能关掉的话这扇窗就自己消失了。不赌它成功, 所以两条都做。 */
      log('   服务器即将关停 —— 成品已自动打开。');
      try{window.close()}catch(e){}
    }
  });
}
commitBtn.addEventListener('click',function(){doCommit()});
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
function mkDropdown(el,onPick){
  var btn=el.querySelector('.dd-btn'),val=el.querySelector('.dd-val'),list=el.querySelector('.dd-list');
  el.value='';
  function close(){el.classList.remove('open');list.hidden=true;btn.setAttribute('aria-expanded','false')}
  function choose(o,it,quiet){
    el.value=o;val.textContent=o;
    list.querySelectorAll('.dd-item').forEach(function(x){x.classList.remove('sel')});
    if(it)it.classList.add('sel');
    close();
    if(onPick&&!quiet)onPick(o);
  }
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
      it.addEventListener('click',function(){choose(o,it)});
      list.appendChild(it);
      if(i===0){el.value=o;val.textContent=o;it.classList.add('sel')}
    });
  };
  el.pick=function(o){                      /* 按值选中(用于恢复服务器记住的选择); 不回调 */
    var hit=null;
    list.querySelectorAll('.dd-item').forEach(function(x){if(x.textContent===o)hit=x});
    if(hit)choose(o,hit,true);
    return el.value;
  };
  return el;
}
var taskDD=mkDropdown($('taskSel'));
/* 翻译方: 只影响归因/措辞/守卫, 不影响门禁 —— 门禁永远只看编号与占位符。
   哪些翻译方与审核者同源, 由**服务器**算好给前端(判据在 reviewer.conflicts), 前端只做
   成员判断 —— 两处各写一套认族规则迟早会漂; 服务器端在 ④ 入口还会再拦一次(不只靠
   这里禁用按钮: 前端禁用只是提示, 真正的门在服务器)。 */
var vendor='',rvConflicts=[];
var vendorDD=mkDropdown($('vendorSel'),function(v){
  vendor=v;
  applyVendorGuard();
  api('/api/vendor',{vendor:v}).then(function(r){
    if(r.error){log('✗ '+r.error);return}
    rvConflicts=r.rv_conflicts||rvConflicts;applyVendorGuard();
    log('翻译方已切到「'+v+'」'+((r.rv_conflicts||[]).indexOf(v)>=0
        ?' —— 与审核者同源, ④ 已禁用':'(台账与报错明细的归因都按它记)'));
  });
});
function applyVendorGuard(){
  var hit=rvConflicts.indexOf(vendor)>=0;
  $('reviewBtn').disabled=hit;
  $('reviewBtn').title=hit?('审核者与「'+vendor+'」同源, 拒审 —— 被翻译方不得自校')
                          :'独立第三方审校「原文↔译文」; 只审不改, 不参与放行';
  $('rvGuard').textContent=hit?('✗ 审核者与「'+vendor+'」同源, 已禁用 ④'):'';
}
/* 面板构建时间: 给"旧窗 = 旧代码"这个坑一个自查口径 —— 60642 上若还挂着自家旧实例,
   那扇窗显示的是更早的时间。时间由服务器给(只有它知道自己吃的是哪几份文件)。 */
function renderBuild(b){$('buildTxt').textContent=b?('构建 '+b):''}
/* 载荷就绪(v28.79): 铃响之后若面板**已经在跑**, 服务端没有可靠办法隔进程把窗口提到
   前台, 于是这一段由**页面自己**接手 —— 每 5s 的心跳顺带捎回最新的 .ready.json,
   ready_at 一变就 window.focus() + 横幅。浏览器若不理会 focus() 也不会坏, 只是没弹到
   最前(所以不写 Win32 SetForegroundWindow 那种绑窗口标题的脆东西)。
   只认**近 10 分钟**的标记: 手动打开面板时, inbox 里那份陈年标记不该诈尸叫一遍。 */
var readySeen=null;
function renderReady(r){
  if(!r||!r.ready_at)return;
  if(r.ready_at===readySeen)return;
  readySeen=r.ready_at;
  if(!((Date.now()-Date.parse(r.ready_at))<600000))return;
  try{window.focus()}catch(e){}
  if(banner.className.indexOf('idle')>=0)
    setBanner('ok','🔔 载荷已就绪：'+r.name+'（'+r.segments+' 段 / 覆盖 '+r.pages+' 页）—— '
                   +'① 复制待译文本交给翻译方, 拿回包后 ③ 检查 → ④ 审核 → ⑤ 确认出稿。');
  log('🔔 载荷已就绪: '+r.name+' ('+r.segments+' 段 / 覆盖 '+r.pages+' 页, '+r.ready_at+')'
     +' —— 出稿在 ⑤, 这里只是把窗口叫到前面。');
}
function ping(){
  fetch('/api/ping').then(function(r){return r.json()})
    .then(function(s){if(s)renderReady(s.ready)}).catch(function(){});
}
document.addEventListener('visibilitychange',function(){
  // 从豆包切回来立即心跳唤醒, 不等被浏览器节流的 setInterval
  if(!document.hidden)ping();
});
window.addEventListener('pagehide',function(){navigator.sendBeacon('/api/bye')});
/* ---- 概念链接: 把"成品里选中的那段字 -> 你自己的网址"装进成品 ----
   动线为什么长这样: 锚文本必须**从成品页面上点选**。手打一个中文概念词, 差一个字就是一条
   死链, 而"差一个字"光看屏幕看不出来 —— 所以先「取本页文字」, 点一行填进来, 再裁到要解释
   的那个词。判据(命中几处/该不该给第几处/压住了谁)一律由 tools/user_links.py 说了算,
   面板不另判一套: 两处各写一份, 迟早分叉成两个答案。 */
var ul={pdfs:[],picked:'',task:'',global:[],own:[],scope:'本篇'};
function ulMsg(s,cls){var el=$('ulStat');el.className='muted'+(cls?' '+cls:'');el.textContent=s}
function ulShow(t){var el=$('ulOut');el.hidden=!t;el.textContent=t||''}
function ulWhy(){
  $('ulWhy').textContent=ul.scope==='全局'
    ?'全局: 跨篇通用(锚按全篇找, “第几处”是全篇第几次出现; 不许带页码) —— 比如“基因漂变 → 某个百科”。'
    :'本篇: 只在这一篇生效(锚按该页找, “第几处”是该页第几次出现) —— 比如“图 3 里那个概念”。';
}
function ulRender(){
  var rows=[];
  function row(e,scope,i){
    var where=scope==='全局'?'全篇':('第'+e.page+'页');
    var occ=e.occurrence?('，第'+e.occurrence+'处'):'';
    return '<div class="look"><span class="lv '+(scope==='全局'?'mid':'hi')+'">'+scope+'</span>'
      +'<div class="lmsg"><b>'+esc(e.anchor)+'</b><span class="muted"> '+where+occ+'</span>'
      +'<div class="lids">'+esc(e.url)+'</div></div>'
      +'<button class="btn small" type="button" data-uldel="'+scope+':'+i+'">删</button></div>';
  }
  (ul.global||[]).forEach(function(e,i){rows.push(row(e,'全局',i))});
  (ul.own||[]).forEach(function(e,i){rows.push(row(e,'本篇',i))});
  $('ulList').innerHTML=rows.length?rows.join('')
    :'<div class="emptyhint">还没有绑定。取本页文字 -> 点一行 -> 填网址 -> 「加入清单」。</div>';
}
function ulAbsorb(s){
  ul.task=s.task||ul.task;ul.global=s.global||[];ul.own=s.own||[];
  ulRender();
  ulMsg('本篇「'+ul.task+'」 已绑定 '+ul.own.length+' 条 | 全局 '+ul.global.length+' 条');
}
function ulLines(){
  var pg=parseInt($('ulPage').value||'0',10);
  if(!ul.picked||!pg){return}
  api('/api/ullines',{target:ul.picked,page:pg}).then(function(r){
    if(r.error){ulMsg('✗ '+r.error,'ulerr');ulShow(r.out||'');return}
    $('ulPageStat').textContent='成品共 '+r.pages+' 页'+(r.dual?'（双语版, 页码按单语数）':'');
    if(pg>(r.pages||0)){$('ulPage').value=r.pages||1;return}
    $('ulLines').hidden=false;
    window.__ulLines=r.lines||[];
    $('ulLines').innerHTML=window.__ulLines.map(function(t,i){
      return '<div class="ul-line" data-ulline="'+i+'">'+esc(t)+'</div>'
    }).join('')||'<div class="emptyhint">这一页没有可取的文字。</div>';
  });
}
var ulPdfDD;
$('ulScopeSeg').addEventListener('click',function(e){
  var b=e.target.closest('button');if(!b)return;
  ul.scope=b.getAttribute('data-ulscope');
  this.querySelectorAll('button').forEach(function(x){x.classList.toggle('on',x===b)});
  ulWhy();
});
$('ulLinesBtn').addEventListener('click',ulLines);
$('ulLines').addEventListener('click',function(e){
  var d=e.target.closest('[data-ulline]');if(!d)return;
  $('ulAnchor').value=(window.__ulLines||[])[parseInt(d.getAttribute('data-ulline'),10)]||'';
  this.querySelectorAll('.ul-line').forEach(function(x){x.classList.remove('sel')});
  d.classList.add('sel');
  ulMsg('已填入整行 —— 请裁到要解释的那个词再「加入清单」: 多一个字就是一个不同的锚。');
});
$('ulList').addEventListener('click',function(e){
  var b=e.target.closest('button[data-uldel]');if(!b)return;
  var v=b.getAttribute('data-uldel').split(':');
  api('/api/uldel',{scope:v[0],idx:parseInt(v[1],10)}).then(function(r){
    if(r.error){ulMsg('✗ '+r.error,'ulerr');return}
    ulAbsorb(r);ulMsg('已删掉 1 条(成品若已装过, 得重新「装入成品并变蓝」才跟得上)');
  });
});
$('ulAddBtn').addEventListener('click',function(){
  var anchor=$('ulAnchor').value.trim(),url=$('ulUrl').value.trim();
  if(!anchor){ulMsg('✗ 锚文本是空的 —— 先「取本页文字」点一行。','ulerr');return}
  if(!/^https?:\/\//.test(url)){ulMsg('✗ 网址要以 http:// 或 https:// 开头。','ulerr');return}
  api('/api/uladd',{scope:ul.scope,anchor:anchor,url:url,
                    page:parseInt($('ulPage').value||'0',10),occurrence:$('ulOcc').value})
    .then(function(r){
      if(r.error){ulMsg('✗ '+r.error,'ulerr');return}
      ulAbsorb(r);
      $('ulAnchor').value='';$('ulUrl').value='';$('ulOcc').value='';
      $('ulLines').querySelectorAll('.ul-line').forEach(function(x){x.classList.remove('sel')});
      ulMsg('已进清单 —— 点「预检」看它装不装得上(此刻还一个字都没写进成品)。');
    });
});
$('ulCheckBtn').addEventListener('click',function(){
  ulShow('预检中…');
  api('/api/ulcheck',{target:ul.picked}).then(function(r){
    ulShow(r.out||'');
    ulMsg(r.ok?'✓ 预检通过 —— 可以装。':'✗ 预检没过 —— 照下面点名的条目改, 成品一个字都没动。',
          r.ok?'ulok':'ulerr');
  });
});
$('ulApplyBtn').addEventListener('click',function(){
  ulShow('装入中…');
  api('/api/ulapply',{target:ul.picked}).then(function(r){
    ulShow(r.out||'');
    ulMsg(r.ok?'✓ 已装入并变蓝。':'✗ 装入没过 —— 照上面输出看哪一条卡住了(没过就不落盘)。',
          r.ok?'ulok':'ulerr');
  });
});
api('/api/ulstate').then(function(s){
  if(s.error){ulMsg('✗ '+s.error,'ulerr');return}
  ul.pdfs=s.pdfs||[];ul.picked=s.picked||'';ul.task=s.task||'';
  ul.global=s.global||[];ul.own=s.own||[];
  ulWhy();ulRender();
  if(!ul.pdfs.length){
    ulMsg('还没找到成品 PDF（server/translated 下的 <篇名>-mono.pdf / -dual.pdf）—— 先出稿, 再来装链接。','ulerr');
    ['ulLinesBtn','ulAddBtn','ulCheckBtn','ulApplyBtn'].forEach(function(i){$(i).disabled=true});
    return;
  }
  ulPdfDD=mkDropdown($('ulPdfSel'),function(v){
    var hit=ul.pdfs.filter(function(p){return p.name===v})[0];
    ul.picked=hit?hit.path:'';ulLines();
  });
  ulPdfDD.setOptions(ul.pdfs.map(function(p){return p.name}));
  ulMsg('本篇「'+ul.task+'」 已绑定 '+ul.own.length+' 条 | 全局 '+ul.global.length+' 条');
  ulLines();
});

/* 结构自愈(v28.78): 两件成品级自愈 —— 治版面伤(heal_render) / 治链接热区(relink_pages)。
   判据全在工具里: **只动版面与链接热区, 不碰译文一个字**, 每次必出报告(治了哪页、按哪条
   判据)。面板只做三件事: 组参数(成品/原版/报告由后端补齐)、转发、把工具的输出与报告
   **一字不改**转述上来 —— 不描补、不吞错。透明是准入条件, 见 relay_spec 9.6 红线。 */
var healDD=null,healPicked='',healPdfs=[];
function healMsg(t){var el=$('healOut');el.hidden=false;el.textContent=t}
function healAsk(tool,dry){
  if(!healPicked){healMsg('还没找到成品 PDF —— 先出稿(⑤), 再来自愈。');return}
  var b=$(dry?'healScanBtn':(tool==='heal_render.py'?'healRenderBtn':'healRelinkBtn'));
  if(b){b.disabled=true}
  api('/api/heal',{tool:tool,pdf:healPicked,dry:!!dry}).then(function(r){
    if(b){b.disabled=false}
    if(r.error){healMsg('✗ '+r.error);log('✗ 结构自愈: '+r.error);return}
    log((r.dry?'只看伤情':'治'+(r.tool==='heal_render.py'?'版面伤':'链接伤'))+' → rc='+r.rc+' · '+r.pdf);
    (r.out||'').split(/\r?\n/).forEach(function(l){if(l)log('   '+l)});
    if(r.report){log('   报告全文: '+r.report_path)}
    var tail=r.report?r.report.split(/\r?\n/).filter(function(l){return l.trim()}).slice(-8).join('\n'):'';
    var head=r.dry?(r.rc===1?'⚠ 发现排版伤(还没治 —— 只看模式不落盘)。':'✓ 排版无伤, 不用治。')
                  :(r.rc?'✗ 工具没跑成(退出码 '+r.rc+'), 照上面的输出查。':'✓ 完成。');
    healMsg(head+'\n'+(r.out||'')+(tail?'\n—— 报告末几行 ——\n'+tail:''));
  });
}
api('/api/ulstate').then(function(s){
  healPdfs=s.pdfs||[];healPicked=s.picked||'';
  if(!healPdfs.length){
    healMsg('还没找到成品 PDF —— 先出稿(⑤), 再来自愈。');
    ['healScanBtn','healRenderBtn','healRelinkBtn'].forEach(function(i){$(i).disabled=true});
    return;
  }
  healDD=mkDropdown($('healPdfSel'),function(v){
    var h=healPdfs.filter(function(p){return p.name===v})[0];
    healPicked=h?h.path:'';
  });
  healDD.setOptions(healPdfs.map(function(p){return p.name}));
});
$('healScanBtn').addEventListener('click',function(){healAsk('heal_render.py',true)});
$('healRenderBtn').addEventListener('click',function(){healAsk('heal_render.py',false)});
$('healRelinkBtn').addEventListener('click',function(){healAsk('relink_pages.py',false)});
api('/api/state').then(function(s){
  taskDD.setOptions(s.jobs||[]);
  vendorDD.setOptions(s.vendors||[]);
  vendor=vendorDD.pick(s.vendor||'');       /* 服务器记住的选择(工作目录里的 .panel_vendor) */
  rvConflicts=s.rv_conflicts||[];applyVendorGuard();
  $('workdir').textContent='工作目录 '+(s.workdir||'');
  renderTodos(s.todos);
  theme=s.theme||theme;applyTheme();
  renderBuild(s.build);renderReady(s.ready);
  log('翻译方「'+vendor+'」; 审核者 '+s.rv_model+' —— ④ 只审不改, 且与翻译方同源时拒审。');
  ping();setInterval(ping,5000);
});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------- 本机服务器(零依赖 stdlib)

BYE_GRACE = 15       # 收到告别后的宽限秒数(退出腿①, 见模块头"退出"契约)
DONE_GRACE = 3       # ⑤ 出稿收摊前的宽限秒数(退出腿②): 留几秒让"✓ 已出稿"回到页面上
STATE = {"checked": None, "sha": None, "ping": None, "bye_at": None}
LOCK = threading.Lock()
SRV = {"h": None}    # main() 起的那个服务器; 收摊腿要它才关得掉(见 stop_after_done)


def build_stamp():
    """本面板吃的是哪一版代码 —— 取三份关键文件里**最新的** mtime, 格式 "09-23 20:15"。

    [v28.79] 存在的理由只有一个: "60642 上那扇窗是旧进程(吃的旧代码)"这个坑, 光靠
    "记得去体检里看"是拦不住的 —— 用户会对着旧窗找新功能。把构建时间摆在表头上,
    与被起出来的那一刻的磁盘对不上, 一眼就能看出。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    newest = 0.0
    for f in (os.path.abspath(__file__),
              os.path.join(here, "watch_clip.py"),
              os.path.join(here, "reviewer.py")):
        try:
            newest = max(newest, os.path.getmtime(f))
        except OSError:
            pass
    return time.strftime("%m-%d %H:%M", time.localtime(newest)) if newest else ""


def announce_port(path, port):
    """[v28.79+] 把"我起在哪个端口"写下来, 只给服务器看。写不成就静静算了。

    为什么需要它: 服务端是**分离进程**拉起本面板的, 拿不到退出码, 也读不到这扇窗的
    终端输出 —— 于是它没法回答两个必答的问题: "你起来了没"、"你到底在哪个端口"
    (60642 被别人占着时本面板会回落随机端口, 服务端若照旧报 60642 就是指错门)。
    这一个文件同时解决两件事, 顺带让服务端能认出"已经跑着的、只是不在 60642 上的"
    那一份, 不再每次提字都弹一扇新窗。

    面板本体**不依赖**它: 路径空/写不动/目录不存在, 都只是少一条给服务器回话的线。
    """
    if not path:
        return
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump({"port": int(port), "pid": os.getpid(), "build": build_stamp(),
                       "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)
    except Exception:                            # noqa: BLE001 —— 见 docstring
        pass


def payload_ready():
    """inbox 里最新的载荷就绪标记 -> {"name","segments","pages","ready_at"}; 没有则 None。

    [v28.79] 铃响那段动线的**后半截**: 服务端响铃(server_server._notify_payload_ready
    落 .ready.json)时若面板已经在跑, 它没有可靠办法隔进程把窗口提到前台 —— 于是由页面
    自己每 5s 从心跳里捎回这份标记, ready_at 一变就 window.focus() + 横幅。
    只挑**最新的一份**: 载荷是逐篇的, 用户此刻该看的是刚出炉那一篇。
    """
    best = None
    try:
        for f in os.listdir(wc.INBOX):
            if not f.endswith(".ready.json"):
                continue
            p = os.path.join(wc.INBOX, f)
            try:
                t = os.path.getmtime(p)
            except OSError:
                continue
            if best is None or t > best[0]:
                best = (t, p)
    except OSError:
        return None
    if not best:
        return None
    try:
        with io.open(best[1], encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        return None
    return {"name": d.get("name") or "", "segments": d.get("segments") or 0,
            "pages": d.get("pages") or "", "ready_at": d.get("ready_at") or ""}


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
            jobs = [j["label"] for j in wc.available_jobs()]
            # 正文任务排最前 -> 前端下拉默认选中正文(最新论文载荷), 而不是第一个表格任务
            jobs.sort(key=lambda s: 0 if s.startswith("正文") else 1)
            self._json({"jobs": jobs,
                        "vendors": list(VENDORS), "vendor": _load_vendor(),
                        # 同源族算在服务器(判据唯一), 前端只做成员判断; rv_model 供日志/卡片显示
                        "rv_conflicts": [v for v in VENDORS if rv_conflict(v)],
                        "rv_model": rv.config()["model"],
                        "workdir": wc.D, "theme": _load_theme(),
                        "todos": paper_tasks(),   # 这一篇该做的每一块到哪一步了(判据在产物上)
                        "build": build_stamp(),   # 本面板吃的是哪一版代码(旧实例自查)
                        "ready": payload_ready()})  # 页面一开就能看见最近那份载荷就绪标记
        elif path == "/api/ping":
            with LOCK:
                STATE["ping"] = time.time()
            # 心跳顺带把"载荷就绪"捎回去: 前端每 5s 已经在打这个接口, 不必另开一条轮询
            # (服务端响铃时若本面板已在跑, 提到台前那一步只能靠页面自己 —— 见 renderReady)
            self._json({"ok": True, "ready": payload_ready()})
        elif path == "/api/ulstate":
            self._ul_state()
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        b = self._body()
        if path == "/api/vendor":
            if not _save_vendor(b.get("vendor")):
                self._json({"error": "不是已知的翻译方。"})
                return
            # 回带同源清单: 换家后 ④ 的可用性跟着变, 前端不必自己维护一套认族规则
            self._json({"ok": True, "vendor": _load_vendor(), "rv_model": rv.config()["model"],
                        "rv_conflicts": [v for v in VENDORS if rv_conflict(v)]})
        elif path == "/api/send":
            self._send(b)
        elif path == "/api/build":
            self._build()
        elif path == "/api/bye":
            with LOCK:                       # 页面关窗前告别(sendBeacon) -> 进宽限期,
                STATE["bye_at"] = time.time()  # 不是立即退(F5 刷新/别的页面关闭也会发)
            self._json({"ok": True})
        elif path == "/api/check":
            self._check(b)
        elif path == "/api/review":
            self._review(b)
        elif path == "/api/termadd":
            self._term_add(b)
        elif path == "/api/health":
            self._health()
        elif path == "/api/heal":
            self._heal(b)
        elif path == "/api/commit":
            self._commit(b)
        elif path == "/api/copy":
            # 把面板上显示的返工提示词写进剪贴板 —— 走服务器端 Win32(与 ① 同一条路),
            # 网页 AI 读不到 inbox 文件, 只能靠粘贴。空文本不覆盖剪贴板。
            t = b.get("text") or ""
            if not t.strip():
                self._json({"error": "没有可复制的内容。"})
            elif wc.write_clip(t):
                self._json({"ok": True, "n_chars": len(t)})
            else:
                self._json({"error": "剪贴板被别的程序占住, 稍后再试。"})
        elif path == "/api/theme":
            if b.get("theme") in ("dark", "light"):
                _save_theme(b["theme"])
            self._json({"ok": True})
        elif path == "/api/uladd":
            self._ul_add(b)
        elif path == "/api/uldel":
            self._ul_del(b)
        elif path == "/api/ullines":
            self._ul_lines(b)
        elif path == "/api/ulcheck":
            self._ul_check(b)
        elif path == "/api/ulapply":
            self._ul_apply(b)
        else:
            self._json({"error": "not found"}, 404)

    # -------------------------------------------------------------- 动作
    # ---- 概念链接(见模块头 [v28.73] 段): 前端只负责"点选 + 填网址", 判据全在 tools/user_links.py ----
    def _ul_target(self, b):
        """前端给的成品路径必须**落在自动探测到的那几个里** —— 跑的是会改 PDF 的工具,
        不许浏览器指哪儿就改哪儿。"""
        want = b.get("target") or ""
        hit = next((p for p in ul_pdfs() if p["path"] == want), None)
        if not hit:
            self._json({"error": "这份成品不见了或不在可装的那几份里 —— 重开面板再看。"})
            return None
        return hit["path"]

    def _ul_state(self):
        spec = ul_load()
        pdfs = ul_pdfs()
        self._json({"ok": True, "pdfs": pdfs, "picked": pdfs[0]["path"] if pdfs else "",
                    "task": ul_task(), "spec": UL_SPEC,
                    "global": list(spec.get("global") or []),
                    "own": list((spec.get("tasks") or {}).get(ul_task()) or [])})

    def _ul_lines(self, b):
        target = self._ul_target(b)
        if not target:
            return
        pg = int(b.get("page") or 0)
        args = [UL_SCRIPT, "--target", target, "--page", str(pg), "--list-lines"]
        dual = target.endswith("-dual.pdf")
        if dual:
            args.append("--dual")
        rc, out = ul_run(*args)
        try:
            obj = json.loads(out.strip().splitlines()[0])
        except Exception:
            self._json({"error": "第 %d 页取不到文字" % pg, "out": out})
            return
        obj.update(ok=True, dual=dual)
        self._json(obj)

    def _ul_add(self, b):
        anchor = (b.get("anchor") or "").strip()
        url = (b.get("url") or "").strip()
        if not anchor:
            self._json({"error": "锚文本是空的 —— 先「取本页文字」点一行。"})
            return
        if not re.match(r"^https?://\S+$", url):
            self._json({"error": "网址要以 http:// 或 https:// 开头。"})
            return
        spec = ul_load()
        e = {"anchor": anchor, "url": url}
        if str(b.get("occurrence") or "").strip():
            try:
                e["occurrence"] = int(b["occurrence"])
            except (TypeError, ValueError):
                self._json({"error": "“第几处”要么留空, 要么是正整数。"})
                return
        if (b.get("scope") or "本篇") == "全局":
            # 全局条目**不许带 page**(它跨篇用; 要在某一页生效请改成本篇) —— 口径同 user_links.py
            spec["global"].append(e)
        else:
            try:
                e["page"] = int(b.get("page") or 0)
            except (TypeError, ValueError):
                e["page"] = 0
            if e["page"] < 1:
                self._json({"error": "本篇条目要给页码(从上面「页」那一栏来)。"})
                return
            spec.setdefault("tasks", {}).setdefault(ul_task(), []).append(e)
        ul_save(spec)
        self._ul_state()

    def _ul_del(self, b):
        try:
            idx = int(b.get("idx"))
        except (TypeError, ValueError):
            self._json({"error": "删哪一条没点清。"})
            return
        spec = ul_load()
        arr = (spec["global"] if b.get("scope") == "全局"
               else spec.setdefault("tasks", {}).get(ul_task()) or [])
        if not (0 <= idx < len(arr)):
            self._json({"error": "这条已经不在了(重开面板看看)。"})
            return
        del arr[idx]
        ul_save(spec)
        self._ul_state()

    def _ul_check(self, b):
        target = self._ul_target(b)
        if not target:
            return
        args = [UL_SCRIPT, "--target", target, "--spec", UL_SPEC, "--task", ul_task(), "--check"]
        if target.endswith("-dual.pdf"):
            args.append("--dual")
        rc, out = ul_run(*args)
        self._json({"ok": rc == 0, "out": out})

    def _ul_apply(self, b):
        target = self._ul_target(b)
        if not target:
            return
        dual = target.endswith("-dual.pdf")
        args = [UL_SCRIPT, "--target", target, "--spec", UL_SPEC, "--task", ul_task()]
        if dual:
            args.append("--dual")
        rc, out = ul_run(*args)
        if rc != 0:
            self._json({"ok": False, "out": out})       # 没过就一个字都没写, 照输出改就行
            return
        # 顺序在这里被**代码**钉死(user_links -> style_links): 新锚必须跟着一起变蓝,
        # 否则读者看不出那儿能点, 功能等于没做。侧车拿得到就传 —— style_links 靠它跳过
        # 回填页(那些页的蓝字是原书自带的, 再叠一遍只会多出一份重复文本)。
        sargs = [UL_STYLE, "--target", target]
        sc = ul_sidecar()
        if sc:
            sargs += ["--sidecar", sc]
        if dual:
            sargs.append("--dual")
        rc2, out2 = ul_run(*sargs)
        self._json({"ok": rc2 == 0, "out": out + "\n\n" + out2})

    def _build(self):
        """重新装配: 在**工作目录**里子进程跑 mk_job.py(它模块层无条件 main()),
        更新 job_doubao.txt / job_manifest.json / job_audit.txt。装错/失败不落盘由
        mk_job 自己保证(它先全读后一次性写)。"""
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mk_job.py")
        # 顺手把"这批表属于哪一篇"烙进 job_manifest.json(v28.71) —— 面板判"这一篇该做的都做了
        # 吗"要靠它分篇。篇名取当前正文那篇; 取不到就不传(mk_job 留空, 不编造), 面板按
        # "一个工作目录 = 一篇"兜底并标未标篇名。
        env = dict(os.environ, P2Z_TABLE_DIR=wc.D)
        body = next((j for j in wc.available_jobs() if _is_render(j)), None)
        paper = wc.paper_of(body) if body else ""
        if paper:
            env["P2Z_BODY_NAME"] = paper
        p = subprocess.run([sys.executable, script], cwd=wc.D, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env)
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
        jobs = wc.available_jobs()
        if not jobs:
            self._json({"error": "没有任何任务 manifest, 无法复制。先装配或设环境变量(P2Z_TABLE_DIR/P2Z_PROJ)。"})
            return
        job = next((j for j in jobs if j["label"] == b.get("task")), None)
        if job is None:
            # 没传/没选中时默认取「正文」任务(最新论文载荷), 而不是第一个表格任务
            job = next((j for j in jobs if j["label"].startswith("正文")), jobs[0])
        path = job["job"] if os.path.isabs(job["job"]) else os.path.join(wc.D, job["job"])
        if not os.path.exists(path):
            self._json({"error": "找不到 %s —— 先跑 mk_job.py 装配待译文本。" % path})
            return
        text = io.open(path, encoding="utf-8").read()
        n = len(wc.manifest_ids(job))
        if not wc.write_clip(text):
            self._json({"error": "剪贴板被别的程序占住, 稍后再试。"})
            return
        # 复制成功才记账: 落一行外发台账("这块发出去了"), 回带本篇待办供前端刷新
        self._json({"label": job["label"], "n_units": n, "n_chars": len(text),
                    "todos": note_sent(job, n)})

    def _check(self, b):
        text = b.get("text", "")
        r = analyse(text)
        if "error" in r:
            # 认不出回包 = 这一家连"编号守恒"都没做到, 是要记进台账的证据(换家之后能回看)
            ledger("检查", "?", 0, 0, "编号不齐", (r.get("diag") or [r["error"]])[0])
            self._json({"error": r["error"], "diag": r.get("diag", [])})
            return
        ledger("检查", r["job"]["label"], len(r["ids"]), len(r["items"]),
               "过" if r["ok"] else "未过", r.get("blame", ""))
        lid, lo, lz = r["last"]
        if r["ok"]:
            with LOCK:                        # 记住"这次检查"的内容指纹, commit 时必须对得上
                STATE["checked"] = (r["job"], r["ids"], r["got"])
                STATE["sha"] = _sha(text)
        self._json({
            "ok": r["ok"], "job": r["job"]["label"], "n": len(r["ids"]),
            "vendor": _load_vendor(),
            "median": round(r["median"], 3), "n_dup": r["n_dup"], "n_incons": r["n_incons"],
            "last": {"id": lid, "orig": lo, "zh": lz}, "gate_out": r["gate_out"],
            "rework": r.get("rework", ""), "blame": r.get("blame", ""),
            "items": [{"lv": lv, "kind": k, "msg": m, "ids": u}
                      for lv, k, m, u in r["items"]],
            "rows": [{"ratio": round(rt, 3), "id": uid, "orig": o, "zh": z}
                     for rt, uid, o, z in r["rows"]]})

    def _review(self, b):
        """④ 语义审核: 只读 —— 不落任何产物、不动已检查状态(审核不改门禁结论)。
        慢(整篇要几十秒到几分钟), 但本服务器是 ThreadingHTTPServer, 心跳照走。"""
        r = review(b.get("text", ""))
        if "error" in r:
            self._json({"error": r["error"]})
            return
        # 键名与前端**逐字对齐**(r.n_units): 这里曾发 "n", 而前端读 r.n_units —— 段数
        # 在日志里打成 undefined 谁也不会发现(那行不是门禁, 只是读数)。见 test_reviewer ⑨。
        self._json({"ok": True, "job": r["job"], "n_units": r["n_units"], "vendor": r["vendor"],
                    "n_chunks": r["n_chunks"], "n_failed": r["n_failed"],
                    "secs": r["secs"], "model": r["model"], "report": r["report"],
                    "logs": r["logs"],
                    "items": [{"id": u, "kind": k, "level": lv, "note": n, "quote": q}
                              for u, k, lv, n, q in r["items"]]})

    def _term_add(self, b):
        """④ 报「术语」存疑 -> 人裁决 -> 入表。写盘判据全在 reviewer.append_terms
        (查重 / 两份表同步 / 各按自己的格式写); 这里只做两件事: 把**这次审核的那篇**
        当来源记下来, 并如实转述结果(哪份写了、哪份没写 —— 别让第二份静默漏掉)。"""
        st, msg = rv.append_terms(b.get("en"), b.get("zh"),
                                  src=_review_src(b.get("job") or ""))
        if st == "error":
            self._json({"error": msg})
            return
        try:
            n = len(rv.load_terms())
        except Exception:                                   # noqa: BLE001
            n = 0
        self._json({"ok": True, "wrote": st == "written", "msg": msg, "n_terms": n})

    def _health(self):
        """体检: 判据全在 health_checks(只读), 这里只做两件事 —— 数出红黄绿灯,
        以及兜底转述: **体检自己绝不能把面板挂了**(异常如实报, 不带崩面板)。"""
        try:
            items = health_checks()
        except Exception as e:                              # noqa: BLE001
            self._json({"error": "体检没跑成: %r" % e})
            return
        n_bad = sum(1 for _, s, _ in items if s == "bad")
        n_warn = sum(1 for _, s, _ in items if s == "warn")
        self._json({"ok": True,
                    "items": [{"name": n, "status": s, "detail": d}
                              for n, s, d in items],
                    "n_bad": n_bad, "n_warn": n_warn,
                    "n_ok": len(items) - n_bad - n_warn})

    def _heal(self, b):
        """结构自愈: 判据全在工具里(只动结构层, 不碰译文一个字), 处理器只转发与转述
        —— heal_run 报什么就回什么, 出错照样如实带 error, 不替工具描补。"""
        self._json(heal_run(b.get("tool"), b.get("pdf"), dry=bool(b.get("dry"))))

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
        todos = paper_tasks()
        miss = pending_of(todos)          # 这一篇还没出稿的: **只提醒, 不拦**(v28.71, 理由见 _is_render)
        names = "、".join(t["short"] + ("(要重做)" if t["state"] == "stale" else "") for t in miss)
        if not _is_render(job):
            miss = []                     # 提醒只在渲染那一步有意义(它是这轮的收尾动作)
        logs = []
        if miss:
            logs.append("! 这一篇还有没出稿的: %s" % names)
            logs.append("  不影响这次渲染(PDF 的表格页本来就保持英文, 表格译文走附录 DOCX);"
                        " 只是附录还没生成、渲染前的底片里「表格与表注」那节会是空的 ——"
                        " 之后单独把表格做完即可, 顺序随你。")
        try:
            out = wc.run_job(job, ids, got, log=logs.append)
        except wc.LedgerFailed as e:
            # 底片没做出来 = 渲染的存档点缺了 -> **不出稿**(v28.70)。放它渲染的后果是: PDF 照样
            # 出来且与正常那份无异, 想补底片只能重走一遍, 白多一份 PDF 等人去删。
            wc.beep(False)
            ledger("出稿", job["label"], len(ids), 0, "未出稿", "底片没做成: %s" % e)
            logs.append("✗ %s" % e)
            logs.append("✗ 已拦下出稿(渲染一步没走)。回包已落盘 —— 修好后重新点 ⑤ 即可,"
                        " 不用重贴、不用重跑 ③。")
            self._json({"ok": False, "log": logs, "todos": paper_tasks(),
                        "error": "底片没做成, 已拦下出稿: %s —— 修好后重新点 ⑤(回包已落盘)。" % e})
            return
        wc.beep(bool(out))
        if not out:
            ledger("出稿", job["label"], len(ids), 0, "出稿失败", "排版/注入未过")
            self._json({"ok": False, "log": logs, "todos": paper_tasks(),
                        "error": "未出稿(门禁未过或排版失败)。"})
            return
        ledger("出稿", job["label"], len(ids), 0,
               "已出稿" + ("(缺: %s)" % names if miss else ""), out)
        with LOCK:                            # 已落盘, 防双击重复出稿
            STATE["checked"] = None
        if miss:
            logs.append("! 已记台账: 这一篇还有 %s 没出稿。" % names)
        # 退出腿②: 这一篇该做的都出齐了 -> 面板收摊(判据与理由见 paper_done / stop_after_done)。
        # 放在**出稿成功之后**才算: 门禁没过、底片没做成、排版失败都在上面各就各位地早退了 ——
        # 那些情况要么得改稿重贴, 要么得重跑, 关窗等于把活收走。
        todos = paper_tasks()
        done = paper_done(todos)
        if done:
            logs.append("✓ 这一篇该做的都出齐了 —— 面板即将自动关停(这扇窗可以关了)。")
            stop_after_done()
        try:
            os.startfile(out)
        except Exception as e:
            logs.append("(自动打开失败: %r)" % e)
        self._json({"ok": True, "log": logs, "out": out, "todos": todos,
                    "warn": names if miss else "", "done": done})


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


def stop_after_done(delay=DONE_GRACE):
    """退出腿②: ⑤ 出稿且整篇出齐后, 过 delay 秒把面板关掉(判据见 paper_done 与 _commit)。

    为什么要 delay: `_commit` 的响应还在路上 —— 立刻 shutdown 会让浏览器收到连接中断,
    用户看不到"✓ 已出稿: <成品路径>"那一句, 台账也少写一行。这几秒就是留给那句话的。

    为什么另起一条腿、不并进 _watchdog: 看门狗 5 秒一跳, 并进去最坏要等 5 秒以上才关,
    而"出稿完盯着屏幕等窗口消失"很怪; 而且 ① 段的退出契约测试是靠"看门狗空转"跑的,
    动它的节拍会把那条用例一起改坏。

    只关**自己**那个服务器对象, 不发信号、不杀进程 —— 与 --takeover 同一条纪律:
    这个进程里可能还有一份没出稿的回包, 只有它自己知道什么时候能退。
    """
    def _go():
        time.sleep(delay)
        h = SRV.get("h")
        if h is not None:
            h.shutdown()
    threading.Thread(target=_go, daemon=True).start()


def _watchdog(srv):
    """退出腿①(主动告别)的看门狗。另一条腿(② 干完收摊)在 stop_after_done, **不在这里** ——
    那一条是确定事件触发的, 塞进这个 5 秒节拍只会变钝(理由见 stop_after_done)。

    主动 —— 页面关窗前 sendBeacon 打 /api/bye, 进 BYE_GRACE 秒宽限期。宽限内若收到
            新心跳(F5 刷新后新页面起来 / 还有别的页面活着), 告别作废; 否则退出。
            不能收到告别就立即退: F5 刷新会发 pagehide, 别的标签页(比如验收用的
            自动化浏览器)关闭也会发 —— 立即退会把还活着的窗口晾成死页面
            (2026-09-21 实测踩过)。

    [v28.79] 这里原先还有一条"收过心跳后 IDLE_LIMIT(1800s) 没再收到 = 窗口已关"的兜底,
    已按用户要求删掉 —— 理由是它本质是个**估**: 估窄了误杀"切去豆包翻长文"的用户
    (30s 版实测踩过), 估宽了窗口真死了还得霸着 60642, 而用户说"30 分钟远远不够", 放宽到
    多少都只是把同一个错误推远。同理删掉了配套的 /api/idle 暂停入口与头部倒计时:
    一条要被用户手动压住的自动行为, 不如不要。

    那条兜底真正想近似的东西(不是"你多久没动静了", 而是"活干完了吧")现在由**确定性信号**
    回答: ⑤ 出稿且整篇出齐 -> 自己收摊(见 stop_after_done)。正常动线走完就不会留下常驻
    进程; 剩下的窟窿只有"浏览器崩了、回包还没提交过"这种半路夭折 —— 处理办法不是再加回
    一条估算, 而是**让下次启动看得见**: main() 在 60642 被自家旧实例占住时会点出 PID 与
    构建时间, 并给出 --takeover(见模块头)。"""

    while True:
        time.sleep(5)
        with LOCK:
            p, bye_at = STATE["ping"], STATE["bye_at"]
        if bye_at is not None:
            if p is not None and p > bye_at:
                with LOCK:                   # 告别后仍有心跳 -> 有活页面, 告别作废
                    STATE["bye_at"] = None
            elif time.time() - bye_at > BYE_GRACE:   # 宽限期内毫无心跳 -> 真关窗了
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


PANEL_PORT = 60642     # 固定端口: 用户/自检都记这个地址


def _panel_state(timeout=0.6):
    """60642 上若有一个**自家面板**在跑, 返回它的 /api/state; 否则 None。

    只探"端口有没有人监听"是不够的: 被别的程序占着时会误判成"面板已在跑", 于是既不起
    新的、也不提示, 用户守着别人的端口等窗口。判据用面板自己的钥匙(jobs), 对不上就当
    我们没有实例 —— 宁可多起一个(它会落随机端口), 不要少起一个。
    """
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/state" % PANEL_PORT,
                                   timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    return d if isinstance(d, dict) and "jobs" in d else None


def _old_panel_pids():
    """60642 上的监听 PID(排除自己) —— 只用来**报给用户看**, 不据此杀进程。"""
    return _listening_pids(_netstat_text() or "", PANEL_PORT) - {os.getpid()}


def _ask_old_panel_to_quit(timeout=20.0):
    """请 60642 上的自家旧实例走 —— 走它自己的告别腿: POST /api/bye, 它 15s 宽限后自退。
    轮询到端口真的空出来为止; 返回 "" 表示已腾空, 否则返回失败原因。

    为什么是"请它走"而不是 taskkill: 那扇旧窗里可能正压着一份**还没出稿的回包**(内容
    只在浏览器 DOM 里), 从上往下杀会把它一起丢掉; 而告别本来就是它设计好的退场方式,
    顺带也不会多造一条"杀进程"的路径。
    """
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/api/bye" % PANEL_PORT, data=b"{}",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception as e:                       # noqa: BLE001
        return "告别没送到旧实例上(%r)" % e
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        if not _panel_state(timeout=0.4):
            return ""
    return "旧实例 %g 秒内没退出" % timeout


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        f = sys.argv[i + 1] if len(sys.argv) > i + 1 else "job_response.tsv"
        return selftest(os.path.join(wc.D, f))
    if not wc.check_workdir():
        return 2
    announce = ""
    if "--announce" in sys.argv:        # [v28.79+] 服务端拉起来的: 用它回话(见 announce_port)
        i = sys.argv.index("--announce")
        announce = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
    takeover = "--takeover" in sys.argv
    old = _panel_state()
    if old:
        pids = ", ".join(str(p) for p in sorted(_old_panel_pids())) or "?"
        print("⚠️ 60642 上已经有本面板的实例(PID %s, 构建 %s)。"
              % (pids, old.get("build") or "未知"))
        if takeover:
            print("   --takeover: 先请它走(走它自己的告别腿)…")
            why = _ask_old_panel_to_quit()
            print(("   ✗ %s —— 旧窗里可能还压着没出稿的回包, 本实例这次落随机端口。"
                   % why) if why else "   ✓ 旧实例已退, 60642 腾空。")
        else:
            print("   它吃的就是那一版代码; 那扇窗里若还压着没出稿的回包, 先在它上面出稿。")
            print("   要换成本实例(含最新改动): 关掉旧窗后重跑本命令, 或加 --takeover。")
            print("   本实例这次会落在随机端口 —— 表头的「构建」时间对得上就是新的。")
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", PANEL_PORT))    # 固定端口: 用户/自检都记这个地址
    except OSError:
        sock.bind(("127.0.0.1", 0))             # 没腾出来(别的程序/没退干净)则回落随机
        print("⚠️ 60642 没腾出来, 本实例落在随机端口(地址见下): 认准地址再用。")
    port = sock.getsockname()[1]
    sock.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = "http://127.0.0.1:%d/" % port
    announce_port(announce, port)   # 先回话再看门: 服务端在等着这个端口号(见 announce_port)
    SRV["h"] = srv                  # 退出腿② 要它才关得掉本进程的服务器(见 stop_after_done)
    threading.Thread(target=_watchdog, args=(srv,), daemon=True).start()
    print("翻译中继面板 v3  %s   (构建 %s)" % (url, build_stamp()))
    print("关窗即退出; ⑤ 出稿且整篇出齐后也会自己收摊。也可 Ctrl+C。")
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
