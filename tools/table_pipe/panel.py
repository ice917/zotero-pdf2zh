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
        ② **完工关面板**(v36.4): 概念链接区那颗「完工, 关面板」按钮 -> 关掉自己的服务器。
           **只有人按了才关**(或关窗走 ①)。
        [v36.4] **原先的"⑤ 出稿且整篇出齐 -> 3s 自动收摊"(v28.79)已删掉**。
        理由是它与概念链接区**天生冲突**: 加链接必须在成品出来之后(锚要落在成品印出来的
        中文上、"第几处"要在成品里数), 而"整篇出齐"恰恰就是成品刚出来的那一刻 ——
        留着自动收摊, 这条动线在第一步就被掐断, 人得重开面板才能接着做。
        代价说白: 那条腿原是为了"正常动线走完不留常驻进程", 现在**收场交给人** ——
        要么点②, 要么关窗(走①)。不肯按也不肯关窗时进程会留着, 这是新口径下的已知代价;
        不拿"估一个时限"去堵, 理由见下。原判据 paper_done 留着 —— 它现在只用来在出稿
        日志里说"这一篇齐了", 不再驱动任何自动行为。
        [v28.79] **心跳静默 IDLE_LIMIT=1800s 当"窗口已关"的兜底也早已删掉**。
        它本质是个**估**: 估窄了把"切去豆包翻长文"的用户误杀(30s 实测踩过 ——
        用户切回来服务器已经自杀了, 前端还把连接失败谎报成"剪贴板里没有文本"),
        估宽了窗口真死了还要霸着 60642; 而放宽到多少都只是把同一个错误推远。
        那条兜底真正想近似的东西**不是**"你多久没动静了", 而是"活干完了吧"。v28.79 曾用
        "⑤ 出稿且整篇出齐"这个**确定性信号**接过它, v36.4 又按上面的理由退掉 —— 所以现在
        这条动线上**没有**任何自动退出: 退出只有①关窗与②按按钮两条, 都得人来。
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
    面板只接一条动线: **取该页逐段「原文↔译文」-> 点右边中文(或划一小段) -> 填网址 ->
    预检(不落盘) -> 装入 + 变蓝**。判据一条都不重写(命中几处 / 该不该给"第几处" / 压住了谁),
    全由 tools/user_links.py 说了算 —— 两边各判一套, 迟早分叉成两个答案。装完**自动接着跑
    style_links**(新锚不变蓝 = 读者看不出能点, 功能等于没做), 这步顺序由代码保证, 不靠人记。
    [v28.85] 取字的来源从"成品页的文本行"换成**本篇段表(侧车)**: 成品页文本层是排版后的
    行、中英混排且不标语言, 在文献页/回填页上取字取到的必然是英文; 段表的 trans 是渲染时的
    最终态, 且一份文件就有 页码+原文+译文。
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
UL_SAVEAS_TAG = ".links"    # [v36.4] 「另存为」件: <stem>-mono.links.pdf
# 为什么带这个尾巴: ① 它一眼看得出"这份带链接", 与干净成品并排放着不会认错;
# ② 它以 .links.pdf 结尾而**不是** -mono.pdf, 所以 ul_pdfs() 列不到它 —— 挑成品时
# 不会被它污染("拿带链接的那份再去装一遍"这条路从入口上就不存在)。


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


# [v36.2] 三个**写**端点的留痕。落在 logs/ 下(logs/ 整个目录在 .gitignore 里, 不会弄脏仓库)。
UL_AUDIT = os.path.join(wc.PROJ, "logs", "user_links_audit.log")


def ul_counts(spec):
    """(global 条数, 本篇条数)。"""
    return (len(spec.get("global") or []),
            len((spec.get("tasks") or {}).get(ul_task()) or []))


def ul_echo_check(arr, idx, b):
    """[v36.3] 回显校验: 要删/改的那一条, **必须还是前端当时看见的那一条**。返回错误话或 None。

    为什么非有不可 —— `_ul_del`/`_ul_edit` 此前只认下标(idx), 不认身份。而概念链接区的
    清单**只在加载时与 ulAbsorb()(用户自己增删改之后)刷新**: 页面开着不动, 清单就冻着
    (全文件 setInterval 只有 5s 的 ping, 不碰清单)。冻结的清单 + 事后盘上变过 =>
    同一下标指的是**点击者从没看见过的那一条**, 于是"删第 0 行"删掉了盘上第 0 行 ——
    静默且不可逆。
    (2026-09-26 实发: 一份只有 1 条的规格被一次点击清空, 真条目靠快照才捞回来。
      机制已定 = 冻结清单 + 陈旧下标; 那一下是**谁**点的当时无留痕, 不可考。)

    判据取 (anchor, url) **全等**: 前端回显它那一行的这两个字段, 与盘上 arr[idx] 比。
    这两项都相同的两条, 内容完全一样, 删哪条结果一致 —— 所以这道判据到此为止是够的。
    缺字段 = 页面是旧的(新前端必带) -> 一并拒绝, 免得留下一条不校验的旁路。

    失败方向永远是"不动盘": 拒绝只是让用户按 F5 刷新, 猜错就是丢资产 —— 两者不对称。
    """
    if "oldanchor" not in b:
        return "面板页面是旧的(缺回显字段)—— 按 F5 刷新页面再操作。"
    if not (0 <= idx < len(arr)):
        return "这条已经不在了(按 F5 刷新页面看看)。"
    got = arr[idx] or {}
    if (str(got.get("anchor") or "") != str(b.get("oldanchor") or "")
            or str(got.get("url") or "") != str(b.get("oldurl") or "")):
        return ("清单已经过期 —— 你要动的那一条跟盘上第 %d 条对不上(盘上是『%s』)。"
                "按 F5 刷新页面, 看清了再来; 盘上**一个字都没动**。"
                % (idx + 1, str(got.get("anchor") or "")[:40]))
    return None


def ul_digest():
    """规格文件当下的 sha1 前 12 位 —— 留痕要能回答"那一下之后盘上到底是什么"。"""
    try:
        with io.open(UL_SPEC, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:12]
    except OSError:
        return "-"


def ul_audit(evt, before, after, entry=None, **extra):
    """写端点留痕: 一行一条 JSON。

    [v36.2] **为什么非补不可**: 2026-09-26 09:08:23 真规格被清成空模板, 事后**全无痕迹** ——
    当时连"是谁、哪一下"都问不出, 丢的那条只能从快照捞回来(见 改动记录.md v36.1 末节)。
    (机制已在 v36.3 定位: 冻结清单 + 陈旧下标, 见 ul_echo_check; 那一下是谁点的仍不可考 ——
      正是"无留痕"本身把可考性断了, 这就是本段存在的理由。)
    三个写端点此前一个日志都没有, 等于"改了用户的资产, 却问不出是谁、哪一下、删了哪一条"。
    所以留痕里**必须带被动的条目原文**: add 写进去的是什么 / edit 改前是什么 / del 删掉的是哪一条 ——
    只记"条目数 1 -> 0"照样答不出"丢的是哪条"。
    **留痕是旁路, 失败不许影响主流程**: 整段吞异常(与 ul_save 的原子性同级要求)。
    """
    try:
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(),
               "evt": evt, "spec": UL_SPEC, "panel_proj": wc.PROJ,
               "before": list(before), "after": list(after),
               "entry": entry, "spec_sha1_12": ul_digest()}
        rec.update(extra)
        d = os.path.dirname(UL_AUDIT)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(UL_AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
    """**这一篇该做的都出过稿了吗** —— ⑤ 出稿的日志里说"这一篇齐了"就靠它。

    [v36.4] 它**不再驱动任何自动行为**。v28.79 曾用它当"整篇出齐就自动收摊"的判据, 那条腿
    已按本文件头的理由删掉(加概念链接必须在出稿之后, 自动收摊会把这条动线在第一步掐断)。
    现在它只决定日志那句话 —— 判据口径本身没变, 所以留着, 不删。

    为什么不是"⑤ 一成功就算": 面板是多格的(正文 + 表格正文/表注), 顺序随人 —— 正文出完稿、
    表格还没做就说"齐了", 等于把人手里的活当干完了。所以判据是"**整篇齐了**"而不是
    "这一步过了"。与 pending_of 的差别: 那个排除 self(出稿时提醒"还有 X 没出稿"用, 正在出的
    那块当然不算), 这里恰恰要算上正在出的那块 —— 它是收尾那一步, 出完才算齐。

    foreign(烙着别篇的表)不算本篇欠的, 与 pending_of 同一口径。列表为空(这个目录里没有
    正文任务)也**不算齐** —— 那种面板没有"这一篇"可言, 齐不齐不由这里决定。
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
/* [v36] 「正在改」的那一条: 加一圈警示色。只把输入框填上是不够的 —— 清单里十几条
   长得一样, 不标出来用户看不出在改哪一条, 会以为"改"是"再加一条"(实测就是这个坑)。 */
.look.editing{border-color:var(--warn);box-shadow:0 0 0 1px var(--warn) inset}
.look .lacts{display:flex;gap:6px;flex:0 0 auto}
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
.ul-lines{max-height:230px;overflow:auto;border-radius:12px;border:1px solid var(--glass-border);
  background:var(--card-bg);padding:4px;margin:8px 0;position:relative}
/* 页分组小标题: sticky 钉在列表顶上, 滚到哪页那页的标题就停在那 —— 滚动联动里
   人能看见"我现在在第几页"的另一半(spy 只改左上角读数, 这里给位置感)。
   sticky 的 offsetTop 在浏览器里会报"钉住后的位置", 所以 spy 只数普通行(.ul-seg), 不数它。
   [v28.94] 每页的标题+段行必须包在 .ul-group 里 —— sticky 的粘着范围是**包含块**:
   平铺(标题与段行同为 .ul-lines 的直接子元素)时所有标题的包含块都是整个滚动容器,
   于是滚过的标题**全部**钉在同一个 y(实测 11 页时第 1~9 页标题 relTop 全是 4.7, 一字不差),
   再配上半透明的 --card-bg(.5) 就透出下层文字 —— 用户看到的就是"第9页与第10页叠字"。
   分组后粘着范围收窄到本页, 下一组上推时把上一组的标题顶出去(sticky 原生推挤),
   列表顶任何时刻只有一个标题, 上一页的字**彻底看不见**(不是被半透明盖住)。
   ⚠ .ul-group 不许加 position(positioned 会让 .ul-seg 的 offsetParent 从 .ul-lines
   变成它, offsetTop 少掉前面各组累计量 -> ulSpy/ulJump 的坐标基准当场作废)。 */
.ul-group{position:static}
.ul-lines .ul-head{position:sticky;top:0;z-index:1;color:var(--muted);font-size:12px;
  padding:3px 10px;background:var(--card-bg);border-bottom:1px solid var(--glass-border)}
.ulnav{margin-bottom:0}
.ulnav button{min-width:32px;padding:6px 10px}
/* [v28.89] 边界态不再用 disabled(禁用按钮浏览器不派发点击, 用户点了毫无反应,
   实测 browser_click 直接报 "Element is disabled")。改 .off: 只变暗, 点击照常落地,
   由监听器回一句话 —— 「点不动」的观感根源就是这份静默。 */
.ulnav button.off{opacity:.35;cursor:not-allowed}
/* [v36.1] 范围切换也用同一套: 没成品时「本篇」变暗但**不禁用** —— 与上面 .off 的理由一样,
   点了给一句为什么(见 ulScopeSeg 的点击处理器), 不无声吞掉。 */
.seg button.off{opacity:.35;cursor:not-allowed}
.ul-seg{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:6px 10px;border-radius:8px;
  cursor:pointer;font-size:13px;word-break:break-all;border-bottom:1px solid var(--glass-border)}
/* 只有整个列表的最后一行才收掉下边框(本来的语义)。[v28.94] 分组后 .ul-seg 不再是
   .ul-lines 的直接子元素, 光写 .ul-seg:last-child 会退化成"每页末行都收边框"
   -> 页与页的断处少一条线(观感变化), 故按组来判定。 */
.ul-group:last-child .ul-seg:last-child{border-bottom:0}
.ul-seg:hover{background:var(--hover)}
.ul-seg.sel{background:color-mix(in srgb,var(--primary) 22%,transparent)}
.ul-seg .ul-src{color:var(--muted)}
.ul-seg .ul-dst{color:var(--text)}
.ul-seg.same .ul-dst{color:var(--muted);font-style:italic}
/* [v35] 定位预览: 按 ◀▶ 时把那一处所在的**整页**渲出来、目标处涂成荧光黄 —— 面板这一区
   由此第一次有了"页面"这一层。图是后端按需渲的只读快照(成品不动)。
   width/height 都留 auto + max-* 双上限: 长宽比交给图片自己, 窄屏按宽缩、高屏按高缩,
   两条都不超出 —— 不会有半张图被裁掉(黄块若恰在被裁的那半边, 定位就白做了)。
   底衬固定白: 页面本身是白的, 深色主题下若透出卡片底色, 细笔画会糊掉。 */
.ul-shot{margin:8px 0;border:1px solid var(--glass-border);border-radius:12px;
  background:var(--card-bg);overflow:hidden}
.ul-shot-hd{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:12px;
  color:var(--muted);border-bottom:1px solid var(--glass-border)}
.ul-shot-hd .ul-shot-ttl{flex:1}
.ul-shot-bd{background:#fff;display:flex;justify-content:center}
.ul-shot-bd img{display:block;max-width:100%;max-height:430px;width:auto;height:auto}
.ul-shot-err{padding:10px;color:var(--danger);font-size:13px}
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
      <div class="seg ulnav" id="ulPageNav" title="逐页翻。本篇条目记的就是这一页, 搜索也只在这一页里做">
        <button type="button" id="ulPrev" title="上一页">◀</button>
        <button type="button" id="ulNext" title="下一页">▶</button>
      </div>
      <span class="muted" id="ulPageStat">第 1 页</span>
      <button class="btn small" id="ulLinesBtn" type="button" title="重读段表(出稿后段表变了点这个)">载入对照</button>
    </div>
    <div class="ul-lines" id="ulLines" hidden></div>
    <div class="muted" id="ulSegHint"></div>
    <div class="row">
      <label>锚</label>
      <input class="uli" id="ulAnchor" placeholder="点右边中文取整段, 或在中文里划一小段再点(短锚更稳)" style="flex:1;min-width:240px">
    </div>
    <div class="row">
      <label>网址</label>
      <input class="uli" id="ulUrl" placeholder="https://… (由你填, 机器不猜)" style="flex:1;min-width:220px">
      <label>第几处</label>
      <div class="seg ulnav" id="ulOccNav" title="同一条锚在这一页出现多次时, 选第几处 —— 按 ◀▶ 会跳到那一处并把它涂成荧光黄标出来。只有一处时可不动">
        <button type="button" id="ulOccPrev" title="上一处(跳到并涂亮)">◀</button>
        <button type="button" id="ulOccNext" title="下一处(跳到并涂亮)">▶</button>
      </div>
      <span class="muted" id="ulOccStat">—</span>
    </div>
    <div class="ul-shot" id="ulShotWrap" hidden>
      <div class="ul-shot-hd"><span class="ul-shot-ttl" id="ulShotStat">—</span>
        <button class="btn small" id="ulShotClose" type="button">收起</button></div>
      <div class="ul-shot-bd"><img id="ulShot" alt="该处所在页(荧光黄高亮即这一处)"></div>
    </div>
    <div class="row">
      <label>范围</label>
      <div class="seg" id="ulScopeSeg">
        <button data-ulscope="本篇" class="on" type="button">本篇</button>
        <button data-ulscope="全局" type="button">全局(跨篇通用)</button>
      </div>
      <button class="btn small" id="ulAddBtn" type="button">加入清单</button>
      <button class="btn small" id="ulEditCancel" type="button" hidden
              title="退出编辑, 不改这一条">取消编辑</button>
      <span style="flex:1"></span>
      <button class="btn small" id="ulCheckBtn" type="button">预检(不落盘)</button>
      <button class="btn primary small" id="ulApplyBtn" type="button">装入成品并变蓝</button>
      <button class="btn small" id="ulSaveAsBtn" type="button"
              title="不动原成品: 先复制一份 &lt;篇名&gt;-mono.links.pdf, 再把链接写进副本">另存为一份带链接的</button>
      <button class="btn small" id="ulOpenBtn" type="button"
              title="打开成品所在文件夹 —— 装好之后把这一份交给 Zotero">打开文件夹</button>
      <button class="btn small" id="ulCloseBtn" type="button"
              title="我完事了: 关掉面板(服务器一起停)。不碰盘上任何文件">完工, 关面板</button>
    </div>
    <div class="muted" id="ulWhy"></div>
    <div id="ulList"></div>
    <div class="log" id="ulOut" hidden></div>
    <div class="muted">「载入对照」把<b>本篇段表（侧车）</b>逐段「原文 ↔ 译文」<b>一次载全篇、按页分组</b>——
      <b>左列灰字是原文（不能当锚）, 右列黑字才是译文</b>。<b>在列表里滚动, 左上角页码就跟着走到你正看的页</b>（◀▶ 也能翻, 跳到那一页）;
      点哪一段, 本篇条目就记那一段自己的页, 搜索只在那页里做（全局条目不吃页码）。
      纯字形段（页眉/页码/整页表格）已按导出端同一口径滤掉, 故文献页整页不出现。
      「第几处」不用手填：点完中文面板会拿这条锚去数, <b>只有一处就自动留空</b>（工具自己的默认语义即第 1 处）,
      多处才要你选, 且选不到界外去。<b>按「第几处」边上的 ◀▶ 不只是改数字</b>：
      面板会跳到那一处所在的页、并把<b>那一页渲出来、目标处涂成荧光黄</b>给你看（那图是只读快照, 成品不动）——
      「第 3 处」到底是哪一处, 不用自己去数。长锚会跨行、跨行就搜不到 —— 拿不准就在中文里<b>划一小段</b>再点（越短越稳）,
      <b>清单里每条都能「编辑」</b>：点它就把这一条回填到上面的表单（连页码与「第几处」一起），
      改完点「更新这条」——<b>就地改，位置不变</b>（不是"删了再加"，那样会跳到清单末尾，用户会以为改错了条）；
      范围也能一起改（本篇 ↔ 全局，全局条目自动不带页码）。不想改就点「取消编辑」。
      改完还要「装入成品并变蓝」一次（或「另存为一份带链接的」），成品才跟得上。
      填完先「预检」。装入 = 只加你自己给的 URI 链接（原有的引文锚另说：压住同一条才删, 且记账）。
      变蓝由 style_links 叠绘完成 —— 顺序（user_links → style_links）由面板保证, 不用你记。
      落在回填页（整页无译文、原封搬过来的页）上的新锚不会变蓝：那页的蓝字是原书自带的。
      <b>「装入」就地改上面选中的那份成品；「另存为」不动原成品</b>, 另存成
      <b>&lt;篇名&gt;-mono.links.pdf</b>（与干净成品并排放着, 一眼分得清）。
      <b>面板不会去改 Zotero 里的文件</b> —— 那边的附件归 Zotero 自己管, 从外面动它是<b>热改</b>：
      可能正被阅读器占住、可能正在同步, 而且改坏了当场看不出来。正确顺序是<b>先把链接装进成品,
      再把这一份交给 Zotero</b>（点「打开文件夹」然后拖进去, 或让 ZotMoov 移进去）——
      这样 Zotero 拿到的天生就带链接, 谁都不必去动它管的文件。装完点<b>「完工, 关面板」</b>收场。</div>
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
   [v36.4] 响应里的 done(= 这一篇出齐了)现在**只是句话**: 服务器不再据此收摊(理由见模块头
   "退出"契约 —— 成品刚出来, 正是要接着做概念链接的时候)。所以这里也**不再试关窗**。 */
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
    if(r.done)msg+=' —— 这一篇已出齐。还要加概念链接就接着做, 完事了点概念链接区的「完工, 关面板」';
    setBanner(r.warn?'warn':'ok',msg);
    log('✓ 出稿: '+r.out+'  (翻译方 '+vendor+', 已记台账)');
    if(r.done){
      /* 成品刚出来 —— **不要**收摊: 概念链接必须在这一刻之后做(见模块头)。提示一句就够。 */
      log('   成品已自动打开。下一步去「概念链接」区: 装上链接, 再把这一份交给 Zotero。');
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
   动线为什么长这样: 锚文本必须**是成品页面上真正印出来的那段中文**。手打一个中文概念词,
   差一个字就是一条死链, 而"差一个字"光看屏幕看不出来 —— 所以先「载入对照」, 点右边
   中文(或在那段中文里划一小段)填进来。
   为什么看的是段表(侧车)而不是成品页的文本行: 成品页的文本层是**排版后的行**、中英混排
   且不标语言, 在文献页/回填页上取字取到的必然是英文; 段表自带 raw/trans 逐段对照, 一份
   文件就有 页码+原文+译文, 且 trans 是渲染时的最终态。
   判据(命中几处/该不该给第几处/压住了谁)一律由 tools/user_links.py 说了算, 面板不另判
   一套: 两处各写一份, 迟早分叉成两个答案。 */
/* [v35] hitsPos: 当前口径下每一处的 {page, rect}(后端 hits_pos, 阅读序) —— ◀▶ 定位的
   唯一坐标来源; shotUrl: 预览图的 objectURL(换图前必须撤, 见 ulShotHide);
   lastAnchor: 上一次**数过**的锚, 用来判断"锚换了没有"(换了才收预览/清位置)。 */
var ul={pdfs:[],picked:'',task:'',global:[],own:[],scope:'本篇',page:1,pages:0,hits:-1,hitsDoc:undefined,occ:1,hitsPos:[],shotUrl:'',lastAnchor:null,
/* [v36] 正在改哪一条(空 idx = 没在改)。**只存"哪一条"**(范围+下标), 不存条目内容 ——
   内容始终从 ul.global/ul.own 现读, 免得两份副本各改各的(改完清单变了、副本还是旧的)。 */
edit:{scope:'',idx:-1,anchor:'',occ:undefined}};
function ulMsg(s,cls){var el=$('ulStat');el.className='muted'+(cls?' '+cls:'');el.textContent=s}
function ulShow(t){var el=$('ulOut');el.hidden=!t;el.textContent=t||''}
/* 页/第几处两处读数的**唯一**出口 —— 免得三个地方各写一套显示逻辑又各说各话。 */
function ulPagePaint(){
  /* [v36.1] 没成品就说没有页 —— 旧写法在 ul.pages=0 时显示"第 1 页", 那是一个不存在的页。 */
  $('ulPageStat').textContent=ul.pages?('第 '+ul.page+' / '+ul.pages+' 页'):'—（还没成品 PDF）';
  /* [v28.89] 边界态变暗但不禁用 —— 点了给话(见 ulGo), 不无声吞掉。 */
  var lo=ul.page<=1, hi=!!ul.pages&&ul.page>=ul.pages;
  $('ulPrev').classList.toggle('off',lo);
  $('ulNext').classList.toggle('off',hi);
  $('ulPrev').setAttribute('aria-disabled',lo?'true':'false');
  $('ulNext').setAttribute('aria-disabled',hi?'true':'false');
}
function ulOccPaint(){
  var n=ul.hits;
  if(n<0){$('ulOccStat').textContent='—'}
  else if(n===0){$('ulOccStat').textContent=(ul.scope==='全局'?'全篇':'这一页')+'找不到这条锚'}
  else if(n===1){
    /* [v28.89] 带全篇上下文: 「本页就这 1 处」而全篇还有, 就直说去「全局」能选。 */
    $('ulOccStat').textContent=(ul.scope!=='全局'&&typeof ul.hitsDoc==='number'&&ul.hitsDoc>1)
      ?('本页就这 1 处 · 全篇 '+ul.hitsDoc+' 处, 切「全局」可选'):'就这一处, 不用选';
  }
  else {$('ulOccStat').textContent='第 '+ul.occ+' / '+n+' 处'}
  var lo=!(n>1)||ul.occ<=1, hi=!(n>1)||ul.occ>=n;
  $('ulOccPrev').classList.toggle('off',lo);
  $('ulOccNext').classList.toggle('off',hi);
  $('ulOccPrev').setAttribute('aria-disabled',lo?'true':'false');
  $('ulOccNext').setAttribute('aria-disabled',hi?'true':'false');
}
/* 拿锚去数命中几处。为什么回后端数: "命中几处"是 PDF 层的读数(search_for + 并框),
   浏览器数不了; 而判据必须与门禁**同一套** —— 所以走 user_links.py --count-hits,
   面板不另判一套。数不出来(n<0)读数就留「—」—— 绝不猜一个值; 但话要说(见下)。
   数完必须**回一句话**: 0 处/1 处/多处各有各的说法(后者自己说, 前两者交给 ulOccWhy),
   沉默就会让上一条锚的话挂在状态行上。 */
function ulCount(){
  var a=$('ulAnchor').value.trim();
  /* [v35] 锚换了 -> 上次那些位置的框就不再对应当前这条锚, 立刻收掉预览。
     只收"换锚"这一种: 滚动联动/翻页也会重数(锚没变), 那里把用户正看的图抽走就是倒退。 */
  if(a!==ul.lastAnchor){ul.lastAnchor=a;ulShotHide()}
  ul.hits=-1;ul.hitsDoc=undefined;ul.hitsPos=[];ul.occ=1;ulOccPaint();
  if(!a||!ul.picked){
    /* [v28.93] 提前返回也必须说话 —— 否则 #ulStat 停在上一条锚的话: 实测清空锚框后
       它仍挂着「这条锚在当前页找不到…」。措辞不另写: ulOccWhy 是"为什么数不出来 /
       为什么没得选"的唯一来源(与 v28.91 同一条纪律)。 */
    ulOccWhy();return Promise.resolve()
  }
  return api('/api/ulhits',{target:ul.picked,anchor:a,page:ul.page,scope:ul.scope})
    .then(function(r){
      if(r.error||typeof r.hits!=='number'){
        /* [v28.91] 数不出来也得说 —— 静默 return 会让 #ulStat 停在上一条锚的旧话上。 */
        ulMsg('✗ '+(r.error||'命中处数没数出来'),'ulerr');ulShow(r.out||'');ulOccPaint();return;
      }
      ul.hits=r.hits;
      ul.hitsDoc=(typeof r.hits_doc==='number')?r.hits_doc:undefined;
      /* [v35] 每一处的位置(阅读序)。它只是**同一份 cand 的位置部分**(后端与 occ_list
         同源), 故 hits_pos[i] 与写进 JSON 的 occurrence=i+1 必然是同一处。 */
      ul.hitsPos=(r.hits_pos||[]);
      /* [v28.91] 0 处/1 处也要说。以前只在 >1 时说, 于是这两种情况下 #ulStat 残留
         上一条锚的「命中 N 处…」, 与本行的 #ulOccStat(「找不到 / 就这一处」)同屏打架。
         措辞不另写一套: ulOccWhy 就是"为什么没得选"的唯一说法(点 ◀▶ 走的也是它)。 */
      if(ul.hits>1){ulMsg('这条锚在'+(r.where||'本页')+'命中 '+ul.hits+' 处 —— 用 ◀▶ 选第几处(默认第 1 处)。')}
      else{ulOccWhy()}
      ulOccPaint();
      /* [v35] 预览开着就跟着新读数走: 重数会把 occ 打回第 1 处, 图若还是上一处那张,
         一屏之内就"读数说第 1 处、图上框的是第 3 处"(滚动过页界时会走到这里)。 */
      if(!$('ulShotWrap').hidden){if(ul.hits>0)ulShot();else ulShotHide()}
    });
}
/* [v36.1] 没成品时先解释**为什么只能攒全局** —— 否则用户会以为「本篇」坏了。 */
function ulWhy(){
  if(!ul.pdfs.length){
    $('ulWhy').textContent='还没成品 PDF ⇒ 只能攒「全局」条目(跨篇通用, 按口径本来就不吃页码); '
      +'本篇条目的页码现在无从谈起。「第几处」也数不出来 —— 都等出稿后用「编辑」补。';
    return;
  }
  $('ulWhy').textContent=ul.scope==='全局'
    ?'全局: 跨篇通用(锚按全篇找, “第几处”是全篇第几次出现; 不许带页码) —— 比如“基因漂变 → 某个百科”。'
    :'本篇: 只在这一篇生效(锚按该页找, “第几处”是该页第几次出现) —— 比如“图 3 里那个概念”。';
}
/* [v36] 退编辑态。**只清状态与按钮**, 不动输入框里的内容 —— 用户可能想拿它当"再搭一条"
   的起点(锚留着、只换网址再加), 顺手清空等于惩罚。取消时才连输入框一起清(见 ulEditCancel)。 */
function ulEditExit(){
  ul.edit={scope:'',idx:-1,anchor:'',url:'',occ:undefined};
  $('ulAddBtn').textContent='加入清单';
  $('ulEditCancel').hidden=true;
  ulRender();
}
function ulRender(){
  var rows=[];
  function row(e,scope,i){
    var where=scope==='全局'?'全篇':('第'+e.page+'页');
    var occ=e.occurrence?('，第'+e.occurrence+'处'):'';
    /* [v36] 正在改的那一条:**必须看得出来是哪一条** —— 清单十几条长得一样, 不标出来
       用户会以为"改"是"再加一条", 于是重复加。边框由 .look.editing 给, 按钮也换字。 */
    var ed=(ul.edit.scope===scope&&ul.edit.idx===i);
    return '<div class="look'+(ed?' editing':'')+'">'
      +'<span class="lv '+(scope==='全局'?'mid':'hi')+'">'+scope+'</span>'
      +'<div class="lmsg"><b>'+esc(e.anchor)+'</b><span class="muted"> '+where+occ+'</span>'
      +'<div class="lids">'+esc(e.url)+'</div></div>'
      +'<span class="lacts">'
      +'<button class="btn small" type="button" data-uledit="'+scope+':'+i+'">'
      +(ed?'改的就是它':'编辑')+'</button>'
      +'<button class="btn small" type="button" data-uldel="'+scope+':'+i+'">删</button>'
      +'</span></div>';
  }
  (ul.global||[]).forEach(function(e,i){rows.push(row(e,'全局',i))});
  (ul.own||[]).forEach(function(e,i){rows.push(row(e,'本篇',i))});
  $('ulList').innerHTML=rows.length?rows.join('')
    :'<div class="emptyhint">还没有绑定。载入对照 -> 点右边中文 -> 填网址 -> 「加入清单」。</div>';
}
function ulAbsorb(s){
  ul.task=s.task||ul.task;ul.global=s.global||[];ul.own=s.own||[];
  ulRender();
  ulMsg('本篇「'+ul.task+'」 已绑定 '+ul.own.length+' 条 | 全局 '+ul.global.length+' 条');
}
function ulLines(){
  if(!ul.picked){return}
  /* [v28.87] page=0 = 全篇一次载入、按页分组。为什么不再一页一拉: 用户要在列表里
     滚着看, 左上角页码跟着走 —— 只载一页就没有"滚过页界"这回事。段表是本地文件,
     Lee 11 页 168 段也就几十 KB; 百页级再谈懒载(挂账, 未做)。 */
  api('/api/ullines',{target:ul.picked,page:0}).then(function(r){
    if(r.error){ulMsg('✗ '+r.error,'ulerr');ulShow(r.out||'');return}
    ul.pages=r.pages||0;ulPagePaint();
    $('ulLines').hidden=false;
    var segs=r.segs||[];window.__ulSegs=segs;
    var nz=segs.filter(function(s){return s.zh&&!s.same}).length;
    $('ulSegHint').textContent='全篇 '+segs.length+' 段 · 有译文 '+nz+' 段'
      +'（原文=译文的那几段是设计如此: 器件名/单位/文献条目不译, 拿它们当锚没意义）';
    /* 按页分组: 每页一条小标题(样式钉在列表顶上), 段行跟着自己的页走。
       [v28.94] 一页包一层 .ul-group —— 让 .ul-head 的 sticky 只在本页范围内生效,
       否则滚过的标题会全叠在列表顶(用户实拍: "第9页"与"第10页"叠字)。见 CSS 处说明。
       .ul-group 必须保持非 positioned(否则 .ul-seg 的 offsetTop 基准会变)。 */
    var by={};segs.forEach(function(s,i){(by[s.page||1]=by[s.page||1]||[]).push(i)});
    var html='';
    Object.keys(by).map(Number).sort(function(a,b){return a-b}).forEach(function(pg){
      html+='<div class="ul-group"><div class="ul-head">第 '+pg+' 页</div>';
      by[pg].forEach(function(i){
        var s=segs[i],zh=(s.zh||'').trim();
        html+='<div class="ul-seg'+(s.same?' same':'')+'" data-ulseg="'+i+'"'
          +' title="点右列取整段; 在右列里划一小段再点, 就取你划的那一小段">'
          +'<span class="ul-src">'+esc(s.orig||'')+'</span>'
          +'<span class="ul-dst">'+esc(zh||'(无译文, 原样保留)')+'</span></div>';
      });
      html+='</div>';
    });
    $('ulLines').innerHTML=html
      ||'<div class="emptyhint">全篇没有可取的段（整篇是字形层/文献表, 没有能当锚的正文）。</div>';
    ulSpy();          // 载入后先对一次表(重载后 scrollTop 会归零, 页码也得归位)
  });
}
/* 联动判据与跳页余量**必须成对看**: 跳页把目标行放在"离顶 UL_LEAD 像素"处,
   联动在"离顶 UL_PAD 像素"的带子里找压在最下面的一行 —— 只有 UL_LEAD < UL_PAD,
   跳完认回来的才是**目标页**; 反过来就是认成上一页、当场退回。
   [v28.87 的 bug, 实测记录] 初版是 UL_LEAD=20 配 UL_PAD=10: 真实鼠标点 ▶ 后
   scrollTop 由 0 变成 5424(列表确实滚到了第 2 页首行 5444-20), 但页读数仍是"第 1 / 11 页"
   —— spy 算 st=5434, 第 2 页首行 5444 > 5434 不够格, 于是取到**第 1 页末行**并退回。
   表现就是"◀▶ 点不动"(◀ 在第 1 页本就是 disabled, ▶ 又被退回)。
   UL_LEAD 为什么不是 0: 页面分组标题(.ul-head, ~25px)是 sticky 钉在列表顶上的,
   目标行若贴着顶边就被自己的标题盖住 -> 留 28px 让首行从标题下沿露出来。
   余量校验: 下一行至少高 ~31px, 而判据只往上留 UL_PAD-UL_LEAD=12px, 够不开。 */
var UL_LEAD=28, UL_PAD=40;
function ulSpy(){
  var c=$('ulLines'),rows=c.querySelectorAll('[data-ulseg]');
  if(!rows.length)return;
  var st=c.scrollTop+UL_PAD,cur=parseInt(rows[0].getAttribute('data-ulseg'),10);
  for(var i=0;i<rows.length;i++){
    if(rows[i].offsetTop<=st)cur=parseInt(rows[i].getAttribute('data-ulseg'),10);
    else break;
  }
  var pg=(window.__ulSegs[cur]||{}).page;
  if(pg&&pg!==ul.page){
    ul.page=pg;ulPagePaint();
    /* [v34] 页读数一变, **命中处数就得跟着重数**: 「本篇」口径下的 hit 数是**该页**的读数
       (ulCount 把 ul.page 发给后端, 见 /api/ulhits), 不重数就会挂着上一页的数字 ——
       观感是"都滚到第 2 页了, 下面还写着第 1 页的第 3/3 处"。另两条改页的路径早就重数了
       (ulGo(◀▶) 与点段的处理器末尾都显式调 ulCount), 漏的正是这条滚动驱动的路径。
       只在「本篇」且锚与成品都在时重数: 「全局」口径的读数与页无关, 数了也是白跑一趟后端。 */
    if(ul.scope!=='全局'&&ul.picked&&$('ulAnchor').value.trim())ulCount();
  }
}
$('ulLines').addEventListener('scroll',ulSpy);
/* ◀▶ 不再重拉数据(全篇已在列表里): 直接滚到那一页首行, spy 会把读数对上。
   坐标基准只有一层: .ul-lines 设了 position:relative, 行的 offsetTop 就是相对
   容器 padding 盒的, scrollTop 直接用。UL_LEAD 必须 < UL_PAD —— 见上面的成对说明。 */
function ulJump(pg){
  var c=$('ulLines'),head=null,rows=c.querySelectorAll('[data-ulseg]');
  for(var i=0;i<rows.length;i++){
    var p=(window.__ulSegs[parseInt(rows[i].getAttribute('data-ulseg'),10)]||{}).page;
    if(p===pg){head=rows[i];break}
    if(p>pg)break;
  }
  if(head)c.scrollTop=Math.max(0,head.offsetTop-UL_LEAD);
}
/* [v36] 口径的**唯一**写法: 改 ul.scope + 点亮按钮 + 刷新说明。编辑时会用它把口径切到
   那一条自己的范围(全局条目不许带页), 所以从点击处理器里提出来共用一份。 */
function ulScopeSet(s){
  ul.scope=s;
  $('ulScopeSeg').querySelectorAll('button').forEach(function(x){
    var sc=x.getAttribute('data-ulscope');
    x.classList.toggle('on',sc===s);
    /* [v36.1] 没成品时「本篇」变暗(不禁用 —— 同 .off 的既有约定: 点了给话, 不无声吞掉)。
       正在编辑一条**已有的**本篇条目时例外: 那时它的页码来自规格本身, 是真值, 不是现编的。 */
    x.classList.toggle('off',sc!=='全局'&&!ul.pdfs.length&&!(ul.edit.scope===sc));
  });
  ulWhy();
}
var ulPdfDD;
$('ulScopeSeg').addEventListener('click',function(e){
  var b=e.target.closest('button');if(!b)return;
  var sc=b.getAttribute('data-ulscope');
  /* [v36.1] 没成品就**无从给页码**: ul.pages=0 时 ul.page 只是编辑框里那个"第 1 页",
     它不是真值(而且旧 ulGo 在 pages=0 时连上界都没有)。静默写一个编出来的页码, 是
     这一区最不能犯的错 —— 链接会安静地装到别处去。 */
  if(sc!=='全局'&&!ul.pdfs.length&&ul.edit.scope!==sc){
    ulMsg('还没成品 PDF —— 本篇条目要给真实页码, 现在给不出。先按「全局」攒(跨篇通用, 不吃页码), 出稿后用「编辑」改成本篇并补页。','ulerr');
    return;
  }
  ulScopeSet(sc);
  ulShotHide();       // [v35] 口径换了 -> 页/处都换了一套, 上一张预览作废(重数后 occ 也回第 1 处)
  ulCount();          // 口径换了(本篇=该页 / 全局=全篇), 命中处数跟着换 —— 必须重数
});
/* 翻页 [v28.87]: 全篇已载进列表, ◀▶ 只是滚到那一页(不重拉); 滚动触发 spy,
   读数由 spy 写 —— 这里只把 ul.page 先行写掉, 免得 ulCount 拿旧页去数。 */
function ulGo(d){
  /* [v36.1] 没成品时**页不存在**。而且旧写法这里没有上界(pages=0 时 hi 恒为假)——
     能一路翻到"第 9 页"却一页都不存在, 翻出来的页号还会被写进条目。挡在入口。 */
  if(!ul.pages){ulMsg('还没有成品 PDF —— 现在没有"页"可翻。','ulerr');return}
  var next=ul.page+d;
  if(next<1){ulMsg('已经是第 1 页了。');return}
  if(ul.pages&&next>ul.pages){ulMsg('已经是最后一页(第 '+ul.pages+' 页)了。');return}
  ul.page=next;ulPagePaint();
  $('ulLines').querySelectorAll('.ul-seg').forEach(function(x){x.classList.remove('sel')});
  ulJump(next);ulCount();
}
$('ulPrev').addEventListener('click',function(){ulGo(-1)});
$('ulNext').addEventListener('click',function(){ulGo(1)});
/* [v28.89] 点了必须有一句话 —— 静默是「点不动」观感的根源(实测教训)。
   每种"不能选"的状态各有一句为什么 + 下一步怎么办。
   [v28.91] 它不再只服务 ◀▶: 调用点扩到两处 —— ①点 ◀▶ 没得选时解释; ②ulCount 数完
   hits<=1 时报当前状态。所以 "为什么没得选"这句话只此一份 —— 不再在 ulCount 里另写一套措辞。
   [v28.93] 第三处: ③ulCount 的**提前返回**(锚为空 / 没选成品)也走它 —— 那条路径原先
   一声不响, 于是清空锚框后会留着上一条锚的话。 */
/* [v36] 提交按钮当下叫什么 —— 编辑态是「更新这条」, 其余时候是「加入清单」。
   ulOccWhy 要引用它: 把按钮名写死的下场是"用户在编辑, 提示却叫他去点「加入清单」"
   (实测就是这样) —— 名字都不对, 下一步该点哪里就说不清了。 */
function ulSubmitName(){return $('ulAddBtn').textContent}
function ulOccWhy(){
  if(ul.hits>1)return;
  if(!ul.picked){ulMsg('先在「成品」里选一个 PDF, 才数得出第几处。');return}
  /* [v28.93] 锚为空是**独立**的一种状态, 不能并进下面那条"还没数过": 锚空时根本没有可数
     之物, 说"还没数过"会让人以为框里有东西。排在 !ul.picked 之后 —— 成品没选是更前面的
     坎, 先解决它。 */
  if(!$('ulAnchor').value.trim()){ulMsg('锚为空 —— 先在右列中文里取字(点整段, 或划一小段再点)。');return}
  if(ul.hits<0){ulMsg('这条锚还没数过 —— 点一下右列中文(或改完锚点一下别处), 数完才知道有没有第几处。');return}
  if(ul.hits===0){ulMsg('这条锚在'+(ul.scope==='全局'?'全篇':'当前页')+'找不到 —— 锚要和右列黑字一字不差, 划一小段更稳。');return}
  if(ul.scope==='全局'){ulMsg('这条锚全篇就 1 处, 没有第几处可选 —— 直接点「'+ulSubmitName()+'」即可。');return}
  if(ul.hitsDoc>1){ulMsg('本页就这 1 处, 没得选 —— 全篇共 '+ul.hitsDoc+' 处, 切「全局」再点 ◀▶ 就能跨页选。');return}
  ulMsg('这条锚全篇就 1 处, 没有第几处可选 —— 直接点「'+ulSubmitName()+'」即可。');
}
/* [v35] 预览图的生命周期只此一处: 撤掉上一张 objectURL 再换新的。
   keepOpen=true 用于"换图不关窗"(◀▶ 连续点时画面不闪); 其余场景(换锚/换成品/收起)
   一律连面板一起收掉。
   为什么必须先撤: 每次 ◀▶ 都渲一张新 PNG, 不撤就一份份攒在内存里(1224×1584 的位图
   一张约 7.7MB, 手上按二十下就上百 MB)。 */
function ulShotHide(keepOpen){
  if(ul.shotUrl){URL.revokeObjectURL(ul.shotUrl);ul.shotUrl=''}
  var im=$('ulShot');if(im)im.removeAttribute('src');
  if(!keepOpen)$('ulShotWrap').hidden=true;
}
/* 把"第几处"那一处**指出来**: 后端渲该页 PNG(目标处涂荧光黄) -> 显示在下面。
   坐标一个都不在面板算 —— 全用后端 hits_pos 给的那四个数(与"命中几处"同一把尺子)。 */
function ulShot(){
  var p=ul.hitsPos[ul.occ-1];
  if(!p){ulMsg('这一处的坐标没拿到 —— 改一下锚再改回来, 重数一次就有。','ulerr');return}
  $('ulShotWrap').hidden=false;
  $('ulShotStat').textContent='第 '+ul.occ+' / '+ul.hits+' 处 → 第 '+p.page+' 页（黄色高亮就是这一处）';
  fetch('/api/ulshot',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({target:ul.picked,page:p.page,rect:p.rect})})
    .then(function(r){
      /* 认**内容类型**而不是 r.ok: 面板的错也是 JSON + 200(见 _json 的默认 code) ——
         照 r.ok 判, 一份 {"error":…} 会被当成图片塞进 img, 显示成一张破图, 用户看不到原因。 */
      if((r.headers.get('Content-Type')||'').indexOf('image/')!==0){
        return r.json().then(function(j){throw new Error(j.error||'这一页渲不出来')})
      }
      return r.blob();
    })
    .then(function(bl){ulShotHide(true);ul.shotUrl=URL.createObjectURL(bl);$('ulShot').src=ul.shotUrl})
    .catch(function(e){$('ulShotStat').textContent='✗ '+((e&&e.message)||'这一页渲不出来')});
}
/* ◀▶ 不再只改数字了: 它同时**把那一处指给人看** —— 跳页(段表滚到该页) + 显示涂了荧光黄的那一页。
   [边界, 有意保留] 命中 <=1 处时仍走 ulOccWhy(不显示预览): ◀▶ 的语义是"在多处之间选",
   只有一处时"选"这件事不存在 —— 那句话(v28.89/91/93 反复打磨过)比一张图更能说明白。
   跳页之后**不重数**: 「本篇」口径下所有命中本来就在同一页(不会跳), 「全局」口径的读数
   与页无关(重数纯属白跑一趟, 且会把用户刚选的第几处打回第 1 处)。 */
function ulOccGo(d){
  if(!(ul.hits>1)){ulOccWhy();return}
  var next=ul.occ+d;
  if(next<1){ulMsg('已经是第 1 处了。');return}
  if(next>ul.hits){ulMsg('已经是最后一处（共 '+ul.hits+' 处）了。');return}
  ul.occ=next;ulOccPaint();
  var p=ul.hitsPos[ul.occ-1];
  if(!p){ulShot();return}
  if(p.page!==ul.page){ul.page=p.page;ulPagePaint();ulJump(p.page)}
  ulShot();
  ulMsg('已定位到第 '+ul.occ+' / '+ul.hits+' 处 —— 第 '+p.page+' 页, 下面图里涂黄的就是它。');
}
$('ulOccPrev').addEventListener('click',function(){ulOccGo(-1)});
$('ulOccNext').addEventListener('click',function(){ulOccGo(1)});
$('ulShotClose').addEventListener('click',function(){ulShotHide()});
$('ulAnchor').addEventListener('change',function(){ulCount()});
$('ulLinesBtn').addEventListener('click',ulLines);
$('ulLines').addEventListener('click',function(e){
  var d=e.target.closest('[data-ulseg]');if(!d)return;
  var s=(window.__ulSegs||[])[parseInt(d.getAttribute('data-ulseg'),10)]||{};
  /* 划词优先: 在右列(译文)里划一小段再点, 取的就是那一小段。长锚会跨行, 跨行 search_for
     就搜不到 —— 实测 Lee: 整段命中 15.6%, 8 字窗口 84.4%, 而中文在页面文本层是 100%。 */
  var sel=window.getSelection(),picked='';
  /* [v34] 划的两头都必须在**同一段**里: 只验 anchorNode 的话, 从这一段划到下一段、
     再点回这一段, 锚就成了"两段文字拼起来的串" —— 页面上并不存在这个串, 数命中时
     要么 0 处(用户莫名其妙), 要么靠字符级兜底拼出一个跨大半页的假热区。 */
  if(sel&&!sel.isCollapsed&&d.contains(sel.anchorNode)&&d.contains(sel.focusNode)
     &&String(sel).trim()){
    picked=String(sel).trim();
  }
  var a=picked||(s.zh||'').trim();
  if(!a){ulMsg('✗ 这一段没有译文(原文原样保留), 拿它当锚没意义 —— 换一段有中文的。','ulerr');return}
  $('ulAnchor').value=a;
  /* 页跟着行走: 条目记的是**这一段自己的页**, 不依赖滚动状态(spy 可能还没追上,
     而且手敲锚的人未必滚在行所在页)。 */
  if(s.page&&s.page!==ul.page){ul.page=s.page;ulPagePaint()}
  this.querySelectorAll('.ul-seg').forEach(function(x){x.classList.remove('sel')});
  d.classList.add('sel');
  ulMsg(picked?('已填入你划的这一小段（'+picked.length+' 字）—— 越短越稳。')
              :('已填入整段译文（'+a.length+' 字）—— 若「预检」说搜不到, 就在这段中文里划一小段再点。'));
  ulCount();          // 顺手数一遍命中几处: 只有一处就自动留空, 多处才要人选
});
$('ulList').addEventListener('click',function(e){
  /* [v36] 编辑: 把这一条**回填进上方的表单**, 并把「加入清单」换成「更新这条」。
     回填的是这一条的**真实字段**(含 page 与 occurrence) —— 用户改没改、改了什么, 一眼可见。
     行内的"改的就是它"标记见 ulRender: 清单十几条长得一样, 没有标记用户会以为是"再加一条"。 */
  var eb=e.target.closest('button[data-uledit]');
  if(eb){
    var ev=eb.getAttribute('data-uledit').split(':');
    var es=ev[0],ei=parseInt(ev[1],10);
    var arr=(es==='全局'?ul.global:ul.own)||[];
    var it=arr[ei];
    if(!it){ulMsg('这条已经不在了 —— 重开面板看看。','ulerr');return}
    ul.edit={scope:es,idx:ei,anchor:it.anchor,url:it.url,occ:it.occurrence};
    $('ulAnchor').value=it.anchor||'';$('ulUrl').value=it.url||'';
    ulScopeSet(es);                 // 口径跟着这条走(全局条目本来就不带页)
    if(es!=='全局'&&it.page&&it.page!==ul.page){ul.page=it.page;ulPagePaint()}
    ulRender();
    $('ulAddBtn').textContent='更新这条';
    $('ulEditCancel').hidden=false;
    /* 数一遍当前锚: 让「第几处」/◀▶/荧光黄预览**立刻对这条锚有效**, 并把原 occurrence 认回来
       (ulCount 会把 occ 归 1, 所以得数完再摆正)。锚若被改小了命中数, 就只认得的那一个 ——
       越界由工具那边"occurrence 越界"这条门禁在「预检」里拦, 面板不替它判。 */
    ulCount().then(function(){
      if(it.occurrence&&ul.hits>1){
        ul.occ=Math.min(Math.max(1,it.occurrence),ul.hits);ulOccPaint();ulShot();
      }
      /* 这句交代必须写在**数完之后**: ulCount 内部会用它自己的话(ulOccWhy)覆盖状态行 ——
         写在外面等于刚说出口就被擦掉(实测: 进去编辑的人看到的却是"直接「加入清单」即可")。
         还要认一下用户是不是**已经取消**了: 取消后再弹这句, 就是在说一条不存在的编辑。 */
      if(ul.edit.scope===es&&ul.edit.idx===ei){
        ulMsg('正在改「'+it.anchor+'」—— 改好后点「更新这条」;不想改就点「取消编辑」。');
      }
    });
    return;
  }
  var b=e.target.closest('button[data-uldel]');if(!b)return;
  var v=b.getAttribute('data-uldel').split(':');
  /* [v36.3] 把**这一行自己的原文**一起回显给后端。后端只认下标时: 冻着的旧清单 + 盘上
     已变 = 删掉点击者从没看见过的那一条(2026-09-26 实发, 一份只有 1 条的规格被清空)。
     idx 指的就是 ul.global/ul.own 里那一行 —— 行由 ulRender 从**同一个数组**渲出, 同源。 */
  var dit=((v[0]==='全局'?ul.global:ul.own)||[])[parseInt(v[1],10)];
  if(!dit){ulMsg('这条已经不在了 —— 按 F5 刷新页面看看。','ulerr');return}
  api('/api/uldel',{scope:v[0],idx:parseInt(v[1],10),
      oldanchor:String(dit.anchor||''),oldurl:String(dit.url||'')}).then(function(r){
    if(r.error){ulMsg('✗ '+r.error,'ulerr');return}
    /* 删完之后它后面每一条的下标都往前挪了 —— 编辑态里记的那个下标**已经指到别人身上**,
       就地退出(不退的话下一次"更新"会改错条, 而且是静默的)。 */
    if(ul.edit.idx>=0)ulEditExit();
    ulAbsorb(r);ulMsg('已删掉 1 条(成品若已装过, 得重新「装入成品并变蓝」才跟得上)');
  });
});
$('ulEditCancel').addEventListener('click',function(){
  ulEditExit();
  $('ulAnchor').value='';$('ulUrl').value='';
  ul.hits=-1;ul.occ=1;ul.hitsPos=[];ulShotHide();ulOccPaint();
  ulMsg('已取消编辑, 这一条一个字都没改。');
});
$('ulAddBtn').addEventListener('click',function(){
  var anchor=$('ulAnchor').value.trim(),url=$('ulUrl').value.trim();
  if(!anchor){ulMsg('✗ 锚文本是空的 —— 先「载入对照」点右边中文。','ulerr');return}
  if(!/^https?:\/\//.test(url)){ulMsg('✗ 网址要以 http:// 或 https:// 开头。','ulerr');return}
  var ed=ul.edit.idx>=0;
  /* [v36.1] 没成品时**只许攒全局**: 本篇条目须有真实页码, 而没成品时 ul.page 只是编辑框里
     那个"第 1 页"(见 ulGo 与范围切换的同一道拦)。静默写一个编出来的页码, 正是这一区最不能
     犯的错 —— 链接会安静地装到别处去。编辑一条**已有的**本篇条目不受此限: 页码来自规格本身。 */
  if(!ed&&ul.scope!=='全局'&&!ul.pdfs.length){
    ulMsg('还没成品 PDF —— 本篇条目要给真实页码, 现在给不出。把范围切到「全局」先攒(跨篇通用, 不吃页码), 出稿后再「编辑」改成本篇补页。','ulerr');
    return;
  }
  /* 先数再提交: 手敲的锚可能还没数过(change 与点击只差一个往返), 数出来再提交,
     就不会撞上"命中 N 处请给 occurrence"那条门禁, 也不会猜一个越界的值。
     数不出来(hits<0)就 occurrence 留空 —— 保持"不给值"的原语义, **绝不猜**。
     [v36] 编辑走同一个出口: 只有端点与两个附加字段不同, 校验/计数/收尾全共用 ——
     分成两段写迟早分叉成两种行为。 */
  ulCount().then(function(){
    var occ=(ul.hits>1?ul.occ:'');
    if(ed&&ul.hits<0){
      /* [v36.1] 数不出来时**不许把 occurrence 洗掉**: 原条目上那个值是上一轮在成品上数出来的
         真值, 这一趟只是改网址, 不该因为"此刻数不了"就丢(那是静默数据丢失)。锚改了就不敢保 ——
         那个值是按旧锚数的, 对新锚没有意义, 只能先出稿。 */
      if(anchor!==ul.edit.anchor){
        ulMsg('锚改了、可现在没有成品 PDF, 数不出新锚命中几处 —— 先出稿再改锚(否则「第几处」只能乱写)。','ulerr');
        return;
      }
      occ=(ul.edit.occ===undefined||ul.edit.occ===null)?'':ul.edit.occ;
    }
    var body={scope:ul.scope,anchor:anchor,url:url,page:ul.page,occurrence:occ};
    if(ed){body.idx=ul.edit.idx;body.oldscope=ul.edit.scope;
      /* [v36.3] 回显的是**进编辑态那一刻的原文**(ul.edit 里存的), 不是现在输入框里的值 ——
         用户可能正好在改锚, 拿改后的值去校验等于自己给自己盖章。 */
      body.oldanchor=String(ul.edit.anchor||'');body.oldurl=String(ul.edit.url||'')}
    api(ed?'/api/uledit':'/api/uladd',body)
    .then(function(r){
      if(r.error){ulMsg('✗ '+r.error,'ulerr');return}
      ulEditExit();          // 先退编辑态: 否则重渲出来的行还挂着"改的就是它"
      ulAbsorb(r);
      $('ulAnchor').value='';$('ulUrl').value='';
      ul.hits=-1;ul.occ=1;ul.hitsPos=[];ulShotHide();      // [v35] 锚清了, 位置与预览一起作废
      ulOccPaint();
      $('ulLines').querySelectorAll('.ul-seg').forEach(function(x){x.classList.remove('sel')});
      ulMsg(ed?'已更新这一条(位置不变) —— 点「预检」看它还装不装得上(此刻成品还是旧的)。'
              :'已进清单 —— 点「预检」看它装不装得上(此刻还一个字都没写进成品)。');
    });
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
/* 装入: 两条去向都由**后端**算 —— 前端只报一个 mode, 不指路径(它本来就给不出路径,
   见 _ul_apply)。就地 = 改选中那份成品; 另存为 = 先复制再改副本, 原成品一个字节不动,
   于是"试错"永远伤不到干净成品, 也不需要另做一套备份。 */
function ulInstall(mode,btn){
  if(btn)btn.disabled=true;
  ulShow(mode==='saveas'?'另存为并装入中…':'装入中…');
  api('/api/ulapply',{target:ul.picked,mode:mode}).then(function(r){
    if(btn)btn.disabled=false;
    ulShow((r.file?'→ '+r.file+'\n\n':'')+(r.out||''));
    if(r.ok){
      ulMsg('✓ '+(mode==='saveas'?'已另存为带链接的一份（原成品没动）：':'已就地装入并变蓝：')
            +(r.file||''),'ulok');
    }else if(r.links_written){
      /* 链接写了、变蓝没成 —— 不许含糊成"没装", 也不许说成"全好了"。 */
      ulMsg('! 链接已写进 '+r.file+' , 但「变蓝」这步没过 —— 照上面输出看。','ulerr');
    }else{
      ulMsg('✗ 装入没过 —— 照上面输出看哪一条卡住了(没过就不落盘)。','ulerr');
    }
  });
}
$('ulApplyBtn').addEventListener('click',function(){ulInstall('inplace',this)});
$('ulSaveAsBtn').addEventListener('click',function(){ulInstall('saveas',this)});
$('ulOpenBtn').addEventListener('click',function(){
  api('/api/ulopen',{target:ul.picked}).then(function(r){
    ulMsg(r.error?('✗ '+r.error):('✓ 已打开成品所在文件夹：'+r.dir), r.error?'ulerr':'ulok');
  });
});
$('ulCloseBtn').addEventListener('click',function(){
  /* 关面板必须是人按的: 出稿不再自动收摊(理由见模块头"退出"契约 —— 加链接在出稿之后,
     自动收摊会把它掐断)。所以退出的入口就摆在这一区, 不藏。 */
  api('/api/ulclose',{}).then(function(r){
    ulMsg('✓ '+(r.out||'面板即将关停。'),'ulok');
  });
});
api('/api/ulstate').then(function(s){
  if(s.error){ulMsg('✗ '+s.error,'ulerr');return}
  ul.pdfs=s.pdfs||[];ul.picked=s.picked||'';ul.task=s.task||'';
  ul.global=s.global||[];ul.own=s.own||[];
  ulWhy();ulRender();
  if(!ul.pdfs.length){
    /* [v36.1] 「加入清单」**不再跟着禁用**。依据是后端事实: _ul_add 不调用 _ul_target,
       它只校验字段 + 写 JSON, **根本不需要成品**(见 _ul_add/_ul_entry)。原先这道禁用是前端
       多设的一道, 它把"出稿前先把锚和网址攒下来"这条本该可行的路堵死了。
       但只放开「全局」—— 本篇条目必须有真实页码, 而没成品就没有页(ulGo 与范围切换各自都拦)。
       其余按钮**照旧禁用**: 载入对照/预检/装入/另存为/打开文件夹 这五件本身都要读成品
       (「完工, 关面板」不在此列, 见下)。 */
    ulMsg('还没找到成品 PDF（server/translated 下的 <篇名>-mono.pdf / -dual.pdf）—— '
          +'定位要等出稿; 但「锚 + 网址」现在就能先攒(范围限「全局」, 不吃页码), '
          +'「第几处」出稿后用「编辑」补。','ulerr');
    /* 「完工, 关面板」**不在这张禁用表里** —— 它是退出入口, 与有没有成品无关;
       恰恰在没有成品的时刻(这一篇还没出稿)也可能想关掉面板。 */
    ['ulLinesBtn','ulCheckBtn','ulApplyBtn','ulSaveAsBtn','ulOpenBtn']
      .forEach(function(i){$(i).disabled=true});
    ulScopeSet('全局');ulPagePaint();ulOccPaint();   // 口径落到「全局」(并点亮/变暗两个按钮)
    return;
  }
  ulPdfDD=mkDropdown($('ulPdfSel'),function(v){
    var hit=ul.pdfs.filter(function(p){return p.name===v})[0];
    ul.picked=hit?hit.path:'';ulShotHide();          // [v35] 换了成品, 上一张预览是别一份成品渲的
    ulLines();ulCount();                             // 换了成品, 命中处数也要按新成品重数
  });
  ulPdfDD.setOptions(ul.pdfs.map(function(p){return p.name}));
  ulMsg('本篇「'+ul.task+'」 已绑定 '+ul.own.length+' 条 | 全局 '+ul.global.length+' 条');
  ulPagePaint();ulOccPaint();
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
CLOSE_GRACE = 3      # 点「完工, 关面板」后的宽限秒数(退出腿②): 留几秒让那句回话先回到页面上
STATE = {"checked": None, "sha": None, "ping": None, "bye_at": None}
LOCK = threading.Lock()
SRV = {"h": None}    # main() 起的那个服务器; 退出腿② 要它才关得掉(见 close_panel)


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
        elif path == "/api/uledit":
            self._ul_edit(b)
        elif path == "/api/uldel":
            self._ul_del(b)
        elif path == "/api/ullines":
            self._ul_lines(b)
        elif path == "/api/ulhits":
            self._ul_hits(b)
        elif path == "/api/ulshot":
            self._ul_shot(b)
        elif path == "/api/ulcheck":
            self._ul_check(b)
        elif path == "/api/ulapply":
            self._ul_apply(b)
        elif path == "/api/ulopen":
            self._ul_open(b)
        elif path == "/api/ulclose":
            self._ul_close()
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
        """本页逐段「原文↔译文」—— 数据源是**本篇段表(侧车)**, 不再是成品页的裸文本行。

        [v28.85] 成品页的文本层是**排版后的行**、中英混排且不标语言 —— 在整页无译文的
        文献页/回填页上取字, 取到的必然是英文; 而读者点的是中文, 锚就该落在中文上。段表自带
        raw/trans 逐段对照, 一份文件就有 页码+原文+译文, 且 trans 是**渲染时的最终态**。
        判据一条都没在面板重写 —— 纯字形段过滤、{vN} 还原、真实页码全在 tools/user_links.py。
        """
        target = self._ul_target(b)
        if not target:
            return
        pg = int(b.get("page") or 0)
        sc = ul_sidecar()
        if not (sc and os.path.exists(sc)):
            self._json({"error": "拿不到本篇段表(侧车) —— 先出稿(⑤), 段表才带最终译文。",
                        "out": sc})
            return
        rc, out = ul_run(UL_SCRIPT, "--target", target, "--sidecar", sc,
                         "--page", str(pg), "--list-segs")
        try:
            obj = json.loads(out.strip().splitlines()[0])
        except Exception:
            self._json({"error": "第 %d 页取不到对照" % pg, "out": out})
            return
        obj.update(ok=True, dual=target.endswith("-dual.pdf"))
        self._json(obj)

    def _ul_hits(self, b):
        """这条锚命中几处 —— 只读, 一个字都不写。

        为什么这一问必须回后端: "命中几处"是 **PDF 层的读数**(search_for + 碎片并框),
        浏览器那个文本层数不了; 而判据必须与门禁**同一套** —— 所以走 user_links.py 的
        --count-hits, 面板不另判一套(与 [v28.73] 那条纪律同源)。

        用法只有一个: 让「第几处」不再手填。命中 1 处 -> 前端留空(工具自己的默认语义
        就是第 1 处, 等价); 命中 >1 处 -> 前端才要人选, 且只能选 1..N, 选不到界外去。
        数不出来就**什么都不填**, 让工具在「预检」里说 —— 面板绝不猜一个值。
        [v35] 这一问现在还捎着 **hits_pos**(每一处的 页+矩形, 阅读序) —— ◀▶ 用它定位与
        画框(见 _ul_shot)。这里是**纯透传**(obj.update 后整个回给前端), 所以多一个字段
        不必改这一处; 但字段名是 front/back 的契约, 改名要先看 ulCount。
        """
        target = self._ul_target(b)
        if not target:
            return
        anchor = (b.get("anchor") or "").strip()
        if not anchor:
            self._json({"error": "锚是空的 —— 没有可数的东西。"})
            return
        scope = b.get("scope") or "本篇"
        args = [UL_SCRIPT, "--target", target, "--count-hits",
                "--anchor", anchor, "--scope", scope]
        if scope != "全局":
            args += ["--page", str(int(b.get("page") or 0))]
        if target.endswith("-dual.pdf"):
            args.append("--dual")
        rc, out = ul_run(*args)
        try:
            obj = json.loads(out.strip().splitlines()[0])
        except Exception:
            self._json({"error": "命中处数数不出来。", "out": out})
            return
        obj.update(ok=True)
        self._json(obj)

    def _ul_shot(self, b):
        """把某一页渲成 PNG 回给浏览器, 目标那一处涂成荧光黄 —— 「第几处」的定位标记。

        [v35] 为什么图片由后端出: 这一区至今**零渲染层**, 而"那一处落在页面的什么位置"
        是 **PDF 层的读数** —— 与"命中几处"同源。渲染与涂黄全在 tools/user_links.py 的
        `--render-page` 里(页码换算与 hits_pos 的那些矩形是同一把尺子), 面板只搬字节,
        不自己算坐标(否则就是第二套判据)。
        **只读**: 画的黄块只活在这次渲染出的临时 PNG 上, 成品一个字节都不动; 临时文件读完即删。
        """
        target = self._ul_target(b)
        if not target:
            return
        try:
            pg = int(b.get("page") or 0)
        except (TypeError, ValueError):
            pg = 0
        args = [UL_SCRIPT, "--target", target, "--render-page", "--page", str(pg)]
        if target.endswith("-dual.pdf"):
            args.append("--dual")
        rect = b.get("rect") or []
        if rect:
            # 给了坐标就必须是 4 个数: 静默"不画框"会把"框没了"当成正常结果回给用户
            # (他会以为是这一处本来就没框) —— 面板宁可报错也不降级。
            if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
                self._json({"error": "坐标要 4 个数(x0,y0,x1,y1)。"})
                return
            try:
                args += ["--rect", ",".join("%.2f" % float(v) for v in rect)]
            except (TypeError, ValueError):
                self._json({"error": "这一处的坐标看不懂 —— 重数一次再点。"})
                return
        fd, tmp = tempfile.mkstemp(prefix="p2z_ulshot_", suffix=".png")
        os.close(fd)
        args += ["--out", tmp]
        try:
            rc, out = ul_run(*args)
            if rc != 0:
                self._json({"error": "这一页渲不出来。", "out": out})
                return
            with io.open(tmp, "rb") as f:
                data = f.read()
        except OSError as e:
            self._json({"error": "预览图读不了: %s" % e})
            return
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        self._w(data, "image/png")

    def _ul_entry(self, b):
        """面板发来的一条规格 -> (entry, None) 或 (None, 错误话)。

        「加入」与「编辑」**共用这一份校验**: 分成两套写法的下场是同一条数据走两个
        入口判出两种结果, 而用户只在其中一个入口看到报错(与 user_links.py 那边
        "判据只写一套"同一条纪律)。校验内容与 user_links.py 的门禁**同名同义**:
        锚非空 / URL 是 http(s) / 全局不许带 page / 本篇必须有 page>=1。
        """
        anchor = (b.get("anchor") or "").strip()
        url = (b.get("url") or "").strip()
        if not anchor:
            return None, "锚文本是空的 —— 先「载入对照」点右边中文。"
        if not re.match(r"^https?://\S+$", url):
            return None, "网址要以 http:// 或 https:// 开头。"
        # e 是**新建**的 dict(不是在旧条目上改) —— 所以编辑时被删掉的字段(比如从
        # 本篇改成全局后的 page)真会消失, 不会静默残留在盘上。
        e = {"anchor": anchor, "url": url}
        if str(b.get("occurrence") or "").strip():
            try:
                e["occurrence"] = int(b["occurrence"])
            except (TypeError, ValueError):
                return None, "“第几处”要么留空, 要么是正整数。"
        if (b.get("scope") or "本篇") == "全局":
            # 全局条目**不许带 page**(它跨篇用; 要在某一页生效请改成本篇) —— 口径同 user_links.py
            return e, None
        try:
            e["page"] = int(b.get("page") or 0)
        except (TypeError, ValueError):
            e["page"] = 0
        if e["page"] < 1:
            return None, "本篇条目要给页码(从上面「页」那一栏来)。"
        return e, None

    def _ul_add(self, b):
        e, err = self._ul_entry(b)
        if err:
            self._json({"error": err})
            return
        spec = ul_load()
        before = ul_counts(spec)
        if (b.get("scope") or "本篇") == "全局":
            spec["global"].append(e)
        else:
            spec.setdefault("tasks", {}).setdefault(ul_task(), []).append(e)
        ul_save(spec)
        ul_audit("add", before, ul_counts(spec), entry=e,
                 scope=("全局" if (b.get("scope") or "本篇") == "全局" else "本篇"),
                 task=ul_task())
        self._ul_state()

    def _ul_edit(self, b):
        """改一条已绑定的条目 —— **就地替换**, 不是"删一条再加一条"。

        为什么要就地: 删+加会把它挪到清单末尾。清单的顺序是用户找它的位置,
        只改个网址就跳位, 用户会以为改错了条(与用户_links.json 里的顺序也是同一件事)。
        范围也允许改(全局 <-> 本篇): 换了范围就得**换数组**; 而该带/不该带的字段
        由 _ul_entry 重建的 e 决定 —— 全局那条不会留下旧的 page。
        """
        try:
            idx = int(b.get("idx"))
        except (TypeError, ValueError):
            self._json({"error": "改哪一条没点清。"})
            return
        e, err = self._ul_entry(b)
        if err:
            self._json({"error": err})
            return
        spec = ul_load()
        before = ul_counts(spec)
        new_scope = b.get("scope") or "本篇"
        old_scope = b.get("oldscope") or "本篇"
        old_arr = (spec["global"] if old_scope == "全局"
                   else spec.setdefault("tasks", {}).get(ul_task()) or [])
        if not (0 <= idx < len(old_arr)):
            self._json({"error": "这条已经不在了(按 F5 刷新页面看看)。"})
            return
        err = ul_echo_check(old_arr, idx, b)      # [v36.3] 与 _ul_del 同一道判据(只写一套)
        if err:
            self._json({"error": err})
            return
        was = dict(old_arr[idx])                  # [v36.2] 改前原文 —— 留痕要答得出"改了什么"
        if new_scope == old_scope:
            old_arr[idx] = e                      # 就地: 位置不变
        else:
            del old_arr[idx]
            (spec["global"] if new_scope == "全局"
             else spec.setdefault("tasks", {}).setdefault(ul_task(), [])).append(e)
        ul_save(spec)
        ul_audit("edit", before, ul_counts(spec), entry=e, was=was,
                 scope=new_scope, oldscope=old_scope, idx=idx, task=ul_task())
        self._ul_state()

    def _ul_del(self, b):
        try:
            idx = int(b.get("idx"))
        except (TypeError, ValueError):
            self._json({"error": "删哪一条没点清。"})
            return
        spec = ul_load()
        before = ul_counts(spec)
        sc = "全局" if b.get("scope") == "全局" else "本篇"
        arr = (spec["global"] if sc == "全局"
               else spec.setdefault("tasks", {}).get(ul_task()) or [])
        err = ul_echo_check(arr, idx, b)          # [v36.3] 先认身份, 再谈下标
        if err:
            self._json({"error": err})
            return
        gone = dict(arr[idx])                     # [v36.2] 删掉的**原文** —— 这是事后唯一能答"丢的是哪条"的东西
        del arr[idx]
        ul_save(spec)
        ul_audit("del", before, ul_counts(spec), entry=gone,
                 scope=sc, idx=idx, task=ul_task())
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
        """装入: 把规格里的锚写成链接, 再让它们变蓝。

        [v36.4] 两条**去向**, 都由后端自己算 —— 浏览器不能指路(理由同 _ul_target):
          inplace(默认)  就地改探测到的那份成品
          saveas        先 copy2 成 <stem>.links<ext>, 再改**副本** —— 原成品一个字节不动

        为什么**没有**"覆盖 Zotero 附件"这条去向(用户 2026-09-26 提过, 据证据否掉):
        那等于在**别的程序正管着那个文件**的时候去改它 —— 可能被阅读器占住、可能正被同步,
        而且改完 Zotero 也不知道(它自己的条目里记着 md5/mtime); 最要紧的是**失败是静默的**
        (覆盖错了哪一份、覆盖掉的是不是原文, 当场都看不出来)。改**我们自己的产物**正相反:
        原成品在盘上、丢了能重出稿, 失败必然可见。
        正确动线是**顺序**: 先把链接装进成品, **再**把这份成品交给 Zotero(拖进去 / ZotMoov
        移进去) —— 于是 Zotero 拿到的天生就带链接, 谁都不必去改它管的文件。
        """
        target = self._ul_target(b)
        if not target:
            return
        dual = target.endswith("-dual.pdf")
        mode = b.get("mode") or "inplace"
        if mode not in ("inplace", "saveas"):
            self._json({"error": "不知道要装到哪里: %r" % mode})
            return
        if mode == "saveas":
            stem, ext = os.path.splitext(target)
            work = stem + UL_SAVEAS_TAG + ext
            try:
                shutil.copy2(target, work)   # 每次都从**干净成品**复制, 不在旧副本上叠
            except Exception as e:
                self._json({"ok": False, "out": "另存为失败: %r" % e})
                return
        else:
            work = target
        args = [UL_SCRIPT, "--target", work, "--spec", UL_SPEC, "--task", ul_task()]
        if dual:
            args.append("--dual")
        rc, out = ul_run(*args)
        if rc != 0:
            # 没过就一个字都没写(user_links 自己保证)。但另存为那条路上**副本已经落了盘** ——
            # 它现在是个没人要的半成品, 删掉, 免得下次被当成"成品"看。
            if mode == "saveas":
                try:
                    os.remove(work)
                except OSError:
                    pass
            self._json({"ok": False, "out": out})       # 照输出改就行
            return
        # 顺序在这里被**代码**钉死(user_links -> style_links): 新锚必须跟着一起变蓝,
        # 否则读者看不出那儿能点, 功能等于没做。侧车拿得到就传 —— style_links 靠它跳过
        # 回填页(那些页的蓝字是原书自带的, 再叠一遍只会多出一份重复文本)。
        sargs = [UL_STYLE, "--target", work]
        sc = ul_sidecar()
        if sc:
            sargs += ["--sidecar", sc]
        if dual:
            sargs.append("--dual")
        rc2, out2 = ul_run(*sargs)
        if rc2 != 0:
            # 链接**已经写进去了**, 只是变蓝这步没过 —— 两头都不许含糊: 别说"没装",
            # 也别说"全好了"。把文件名带回去, 让人知道该看哪一份。
            self._json({"ok": False, "links_written": True, "file": work,
                        "out": out + "\n\n" + out2})
            return
        self._json({"ok": True, "mode": mode, "file": work,
                    "out": out + "\n\n" + out2})

    def _ul_open(self, b):
        """打开**成品所在文件夹** —— 装好之后要把这一份交给 Zotero, 少一步自己翻目录。

        只开文件夹、不选集: 资源管理器 `/select,` 的逗号参数在不同 shell 下行为不一,
        不值得为省一次点击去赌它。路径仍走 _ul_target —— 只开探测到的那几份所在的地方。
        """
        target = self._ul_target(b)
        if not target:
            return
        d = os.path.dirname(os.path.abspath(target))
        try:
            os.startfile(d)
        except Exception as e:
            self._json({"error": "打不开文件夹: %r" % e})
            return
        self._json({"ok": True, "dir": d})

    def _ul_close(self):
        """退出腿②: 人点了「完工, 关面板」。

        这里**只关面板**, 不碰盘上任何文件 —— 与 close_panel 的纪律一致(只关自己那个
        服务器对象, 不发信号、不杀进程)。
        """
        close_panel()
        self._json({"ok": True, "out": "面板即将关停(这扇窗可以关了)。"})

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
        # [v36.4] 这里原有一条"整篇齐了就安排收摊"(退出腿②, v28.79)。**已删** —— 收摊改由
        # 人点「完工, 关面板」触发。理由见模块头"退出"契约: 那条自动腿恰好在"成品刚出来"
        # 的那一刻开火, 而概念链接**必须先有成品才能做**, 于是它把动线掐在第一步。
        # 只删了"自动"这一层; "整篇齐了"这句话留着 —— 它现在是纯提示, 说给要收尾的人听。
        todos = paper_tasks()
        done = paper_done(todos)
        if done:
            logs.append("✓ 这一篇该做的都出齐了 —— 还要加概念链接就接着做; "
                        "完事了点「完工, 关面板」。")
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


def close_panel(delay=CLOSE_GRACE):
    """退出腿②: 人点了「完工, 关面板」-> 过 delay 秒把面板关掉。

    [v36.4] 由 stop_after_done 改名而来, **触发者变了**: 原先是"⑤ 出稿且整篇出齐"这个
    自动信号, 现在是人的一次点击(理由见模块头"退出"契约 —— 那个条件与概念链接区天生冲突:
    链接必须在出稿之后做, 而它恰在出稿那一刻触发)。"怎么关"这段机制一个字没改,
    所以原有三条理由仍然成立:

    为什么要 delay: 响应还在路上 —— 立刻 shutdown 会让浏览器收到连接中断, 用户看不到
    "面板即将关停"那一句。

    为什么另起一条腿、不并进 _watchdog: 看门狗 5 秒一跳, 并进去最坏要等 5 秒以上才关,
    而"点了按钮盯着屏幕等窗口消失"很怪; 而且 ① 段的退出契约测试是靠"看门狗空转"跑的,
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
    """退出腿①(主动告别)的看门狗。另一条腿(② 完工关面板)在 close_panel, **不在这里** ——
    那一条是人点按钮这个确定事件触发的, 塞进这个 5 秒节拍只会变钝(理由见 close_panel)。

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

    那条兜底真正想近似的东西(不是"你多久没动静了", 而是"活干完了吧"): v28.79 用"⑤ 出稿且
    整篇出齐"这个**确定性信号**回答过, v36.4 又按模块头的理由退掉了 —— 因为它与概念链接区
    天生冲突(链接必须在出稿之后才做)。所以现在**没有**自动退出, 收场是人二选一:
    点「完工, 关面板」或关窗。剩下能兜的只有"浏览器崩了、回包还没提交过"这种半路夭折 ——
    处理办法不是再加回一条估算, 而是**让下次启动看得见**: main() 在 60642 被自家旧实例
    占住时会点出 PID 与构建时间, 并给出 --takeover(见模块头)。"""

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
    SRV["h"] = srv                  # 退出腿② 要它才关得掉本进程的服务器(见 close_panel)
    threading.Thread(target=_watchdog, args=(srv,), daemon=True).start()
    print("翻译中继面板 v3  %s   (构建 %s)" % (url, build_stamp()))
    print("关窗即退出; 也可以点概念链接区的「完工, 关面板」。也可 Ctrl+C。")
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
