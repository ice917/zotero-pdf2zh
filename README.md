# zotero-pdf2zh · 网页 AI 译文采纳管线（Web-AI Adoption Pipeline）

> 基于 [guaguastandup/zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh) v4.1.7（AGPL-3.0）的改造。
> 上游解决"把 PDF 翻成中文"；本仓库的增量解决一个上游没碰的问题：**改一个词，不要重译整段**。
>
> 这条采纳回路**不绑厂商**：真契约只有两条（编号守恒 + 占位符原位），载荷自带抬头，
> 回包的包装（``` 围栏、首尾客套、编号被列成清单）会被自动清洗 —— 所以粘贴通道交给哪家
> 网页 AI 都行。本机当前默认用**豆包**（基础对话免费不限次），换 Kimi / DeepSeek / ChatGPT
> 网页版不用改提示词；交件后缀族 `doubao|webai` 新旧互认（见 `tools/result_naming.py`）。

## 一句话

把翻译产出的"所有权"从 LLM 手里拿回来：译文来源与模型解耦，外部定稿（由能通读全文的
网页 AI 给出，本机当前用豆包）
经过**段落对齐 → 数字/拉丁名回锚 → 缓存注入**三道机械工序装回原版面。
此后"改字 = 改字"：45 秒重渲染，0 次 LLM 调用，13 项版面验收断言自动把关。

> **第一次来、只想把论文跑出一份成品 PDF**：跳到 [新手照着做](#新手照着做从零到一份成品-pdfwindows)，
> 那一节是给没写过代码的人写的，全程复制粘贴。原理和实测数据都在后面。

## 三堵墙与解法

| 墙 | 解法 |
|---|---|
| 版面保真（公式/表格/双栏/跨页断句） | 版面层不动：34 页 / 154 段槽位图 + `{vN}` 字形表，公式表格原位保留；`verify_render` 13 项断言 + `post_check` 质检门禁 |
| 语境准确（`successful gamete ≠ 成功的配子`） | 整篇交给能通读全文的网页 AI（当前默认豆包基础对话，免费不限次；可换厂商），术语约束降级为译后校验清单，语境级译法只进文档级台账 |
| 迭代成本（缓存键绑参数，改词 = 重译 39 分钟） | 外部定稿注入缓存：页级→段内占位符重编号 + 文档指纹作用域，改字 = 改字 |

## 架构

```
原文 PDF ──上游converter──▶ 槽位图(页/段 + {vN}字形表) ──侧车latest.jsonl
                                │
                                │ seg_export  编号段落包(#S编号+字形还原+断词修复+跨页合并⋮)
                                ▼
                        [网页 AI 对话]  ◀── 剪贴板（人工两次粘贴，免费零额度）
                                │
                                │ seg_import  编号对账 / ⋮对账 / 高价值字形回锚 / 纯标点按契约丢弃
                                ▼
                        seg_inject   页级→段内{vN}重编号 / 文档指纹作用域 / 前置断言
                                ▼
                        缓存库 ──force──▶ 45s 渲染 ──▶ verify_render (13 断言)
```

可选自动化通道：`doubao_bridge.py` 是零依赖手写的 STDIO MCP 桥（list_inbox / get_payload /
list_reports / get_report / list_results / get_result / submit_result / search_term），注册进
任意支持 MCP 连接器的网页 AI（本机当前用豆包的「技能·连接器」）后可省掉剪贴板（消耗该家 Agent 模式额度）；
其中 `search_term` 让这位 AI**定稿时自己查证据**，
不必等人喂数据（见 [术语查证](#术语查证先拿证据再裁决)），`list_reports` / `get_report` 让它
**自己读质检报告**、知道上一轮哪一页被门禁判死，`list_results` / `get_result` 让它**读回自己上一版稿子**——
前两个给"哪儿错了"，这两个给"上一版长什么样"，两个都看得见，改稿才是改动而不是重抄一遍；
`submit_result` 的回执里附上与上一版的改动段数，改动面过大时直接提示"这一轮是在重译"。
桥的路径走环境变量（`P2Z_PROJ` / `P2Z_INBOX` / `P2Z_REVIEW`），不设则退回 `D:\zotero-pdf2zh`。

旁路（不参与主流程）：`term_verify.py` 术语证据查证——卡在某个词的译法时取证据再裁决，
详见 [术语查证](#术语查证先拿证据再裁决)。

### 自动回路：一个任务内跑完两趟（v28.23，默认关）

上面那套是手工多步（导出 → 网页 AI → import → inject → 渲染）。想让**服务端在一次任务里**
把这条回路走完（第一趟只提字 → 停下来等交稿 → 交稿后回灌 → 第二趟重渲染），把运营开关打开：

```powershell
$env:PAUSE_TRANSLATE   = "1"    # 不设 = 行为与过去完全一致（开关默认关）
$env:PAUSE_WAIT_MINUTES = "30"  # 可选：等交稿上限（默认 30 分钟）
python server.py
```

一个任务四个状态：`提字中 → 待译 → 回灌重渲染 → 完成`

1. **提字中**：引擎收到 `PAUSE_TRANSLATE=1`，只提字、不调翻译 LLM（这一趟零翻译调用），
   跑出的侧车由 `adopt export` 裁成 `inbox/<任务名>.txt`；
2. **待译**：worker 停住，每 5 秒看一次 `out/<任务名>.<族>*.txt`（族 = `doubao` / `webai`，两族同权）——网页 AI 用桥
   `submit_result` 交件，或人工把译文另存成这个名字，判据相同。只认**本次任务开始之后**
   落盘的最新一份（`out/` 里同一篇的历史交件不作数）；界面不卡，任务卡显示「待译」；
3. **回灌重渲染**：`deliver → import → inject` 把这份稿子写进缓存（就地改写第一趟留下的骨架行），
   服务端随后在同一任务里把开关还原成"关"重跑一遍 —— 全部命中缓存，零翻译调用；
4. **完成**：产物就是「网页 AI 稿 + 原版面」的中文 PDF。

等不到交稿也不会卡死：到上限走 `abort()` —— 先 `adopt rollback` 撤掉骨架行，再回落成
正常机器翻译，任务照样 `success`，产物是正常中文 PDF。

**载荷就绪会响两声**（第一趟比机器翻译快一个量级，人一转头的功夫就过去了，而"该交给网页 AI 了"
是这条链上唯一需要人立刻接手的时刻）：默认放随服务端自带的 `server/notify-complete.wav`，
想换音就设 `PDF2ZH_NOTIFY_SOUND` 指向任意 WAV（必须 16-bit PCM —— `winsound` 只吃这种）；
两个都没有时退回三声蜂鸣，全程静默失败、不影响任务。

> 自带的那声铃来自 [`CaesiumY/dding-dong`](https://github.com/CaesiumY/dding-dong)（MIT）的
> musical 音色包，取其中的 `complete.wav`。该仓库未单独标注音频素材的出处，介意可自行替换。

> 骨架行 = 第一趟按"原文 = 译文"落下的缓存行，专门给 `inject` 一个就地改写的落点
> （`seg_inject` 是 UPDATE-only：按"原文 + 文档作用域"找行，找不到就 FAIL，这条安全设计不能松）。
> 全部设计取舍见 `改动记录.md` 的 v28.23 一节。

## 新手照着做：从零到一份成品 PDF（Windows）

> 这一节假设你**没写过代码**。命令一律复制粘贴，在 **Anaconda Prompt** 里跑
> （开始菜单搜 "Anaconda Prompt" 或 "Miniconda Prompt"）。**不要用普通 PowerShell**——
> 实测它里面 `conda activate` 常常不生效，会导致 `python` 找错环境。
> 跑完你会得到一份「中文译文 + 表格还是原版排版 + 超链接能点」的 PDF。
> 为什么这么设计、踩过哪些坑，都在后面的章节，现在不用读。
>
> **先认识三个词**：**Zotero** 是你平时看论文的软件；**插件（xpi）** 是装进 Zotero 的扩展，
> 负责把论文发给翻译服务；**翻译服务器**是本仓库里的程序，真正干翻译活的那个，
> 跑起来就是一个不能关的黑窗口。

### 第 1 步：装 Miniconda（只需一次）

到 <https://docs.conda.io/en/latest/miniconda.html> 下载 Windows 版，一路「下一步」
（已经装过 Anaconda 的跳过这步）。装完从开始菜单打开 **Anaconda Prompt**
（或 **Miniconda Prompt**），验证：

```powershell
conda --version
```

能看到 `conda 24.x.x` 之类就成功了。**后面所有命令都在这个窗口里跑。**

### 第 2 步：装 Zotero 7 + 插件

1. 到 <https://www.zotero.org/download/> 装 Zotero 7。
2. 到 <https://github.com/guaguastandup/zotero-pdf2zh/releases> 下载插件 `.xpi`
   （**本仓库不含 xpi**，请从上游 Release 取）。
3. Zotero → 工具 → 插件 → 右上角齿轮 → Install Plugin From File → 选那个 `.xpi`。

### 第 3 步：把本仓库放到 `D:\zotero-pdf2zh`

```powershell
git clone <本仓库地址> D:\zotero-pdf2zh
```

没有 git 也不要紧：在仓库主页点 `Code → Download ZIP`，解压后把文件夹改名成 `zotero-pdf2zh`
放到 D 盘根目录。

**默认按 `D:\zotero-pdf2zh` 走**；装在别处也能用，但要把"项目根"在两处一起改掉：

**① 配置里的绝对路径**（`config.json` / `config.toml` 里指向项目内的术语表、字体、报告目录）。
先改模板（第 6 步会由它生成 `config.json`），把 `D:/你的路径` 换成真实路径，注意用正斜杠：

```powershell
cd D:\你的路径
(Get-Content server\config\config.json.example -Raw -Encoding UTF8) -replace 'D:/zotero-pdf2zh', 'D:/你的路径' | Set-Content server\config\config.json.example -Encoding UTF8
```

**② 环境变量 `P2Z_PROJ`** —— 工具链（`tools\*.py`）、服务端、以及引擎补丁里读项目内文件的两处
（字形校正表 `server\config\font_char_fixes.json`、字体降级台账）都认这一个变量。
**在第 7 步启动服务端的那个窗口里设一次**即可，服务端会把它传给自己拉起的子进程：

```powershell
$env:P2Z_PROJ = 'D:\你的路径'
```

不设就退回默认的 `D:\zotero-pdf2zh`（项目就装在那儿的话，这步什么都不用做）。

### 第 4 步：建翻译环境（等 5~10 分钟）

```powershell
cd D:\zotero-pdf2zh\server\warmup
.\install-with-conda.bat
```

它会自动创建 `zotero-pdf2zh-venv` 等两个 conda 环境并装好依赖。
最后看到 `Conda 环境创建并安装完成！` 就是成功了。中途失败会自动重试 3 次。

### 第 5 步：打补丁（**最容易漏的一步**）

本仓库是「覆盖式补丁集」：下面这些文件要**覆盖**到上一步建好的环境里，
否则公式保护、中文排版、缓存注入全都不生效。

```powershell
cd D:\zotero-pdf2zh

# 5.1 服务器侧（7 个文件）
Copy-Item patches\server_server.py                      server\server.py -Force
Copy-Item patches\server_utils_config.py                server\utils\config.py -Force
Copy-Item patches\server_utils_environment_lifecycle.py server\utils\environment_lifecycle.py -Force
Copy-Item patches\server_utils_execute.py               server\utils\execute.py -Force
Copy-Item patches\server_utils_task_manager.py          server\utils\task_manager.py -Force
Copy-Item patches\server_config_venv.json               server\config\venv.json -Force
Copy-Item patches\server_requirements.txt               server\requirements.txt -Force

# 5.2 翻译引擎侧（6 个文件，要落进 conda 环境里）
# 注意：这条会先打印一行 "not in git repo"，那是 babeldoc 的正常提示，不是错误。
# 因为输出里可能混入这类杂音，所以用 SITE= 标记把它从输出中挑出来。
$site = [regex]::Match((conda run -n zotero-pdf2zh-venv python -c "import pdf2zh,os;print('SITE='+os.path.dirname(os.path.dirname(pdf2zh.__file__)))" | Out-String), 'SITE=([^\r\n]+)').Groups[1].Value.Trim()
Write-Output "site=$site"   # 应该形如 D:\...\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages
Copy-Item patches\pdf2zh_converter.py    "$site\pdf2zh\converter.py"     -Force
Copy-Item patches\pdf2zh_translator.py   "$site\pdf2zh\translator.py"    -Force
Copy-Item patches\pdf2zh_cache.py        "$site\pdf2zh\cache.py"         -Force
Copy-Item patches\pdfminer_encodingdb.py "$site\pdfminer\encodingdb.py"  -Force
Copy-Item patches\pdfminer_pdffont.py    "$site\pdfminer\pdffont.py"     -Force
Copy-Item patches\pdfminer_pdfinterp.py  "$site\pdfminer\pdfinterp.py"   -Force
```

验证补丁真的进去了（四条都应该回 `True`）：

```powershell
Test-Path "$site\pdf2zh\converter.py"                            # False 说明 $site 没取到
Select-String -Path "$site\pdf2zh\translator.py" -Pattern "doc_summary_fp" -Quiet
Select-String -Path "$site\pdfminer\pdfinterp.py" -Pattern "闸门3" -Quiet   # 字体构造兜底(缺了坏字体 PDF 会崩)
Select-String -Path "server\server.py" -Pattern "自研补丁" -Quiet
```

> `$site` 只在**当前这个窗口**里有效，关掉窗口就得重跑 5.2 的第一行。

### 第 6 步：生成配置文件与交换目录

```powershell
cd D:\zotero-pdf2zh
Copy-Item server\config\config.json.example server\config\config.json
Copy-Item server\config\config.toml.example server\config\config.toml
New-Item -ItemType Directory -Force inbox, out, segflow | Out-Null
```

### 第 7 步：启动翻译服务器

```powershell
cd D:\zotero-pdf2zh\server
conda activate zotero-pdf2zh-venv
python server.py
```

`conda activate` 成功时，命令提示符前面会出现 `(zotero-pdf2zh-venv)`。
然后看到 `🌐 Server将启动在: http://127.0.0.1:8890` 就成了。
**这个窗口在翻译期间不能关。** 第 10 步的工具命令也要在这个已激活的窗口里跑
（工具依赖这个环境里的 PyMuPDF）。

### 第 8 步：在 Zotero 里配好，翻译第一篇

Zotero → 编辑 → 设置 → Zotero PDF2zh，需要填四项（界面上的措辞可能略有不同，按含义对应）：

| 要填的 | 填什么 |
|---|---|
| 服务器地址 / 端口 | `127.0.0.1` / `8890`（默认端口） |
| 翻译服务 + API Key | 任选一个服务（如 silicon / deepseek），填**你自己的** Key |
| 中文字体文件 | `D:\zotero-pdf2zh\server\fonts\NotoSerifCJKsc-Regular.otf` |
| 翻译引擎 | 选 pdf2zh（1.x）——本仓库的补丁是给它打的 |

**不填字体路径，中文会显示成方块字。**

然后右键一篇英文论文 → 用插件翻译。译文会出现在 `server\translated\`。

> ⚠️ 不要动插件设置里的「自动更新」。上游一更新就会覆盖本仓库的补丁。
> 升级上游之前请先读 `patches/PROTOCOL.md` 与 `改动记录.md` 第四节。

### 第 9 步：侧车不用再手抄（v28.9 起自动按文档归档）

每次翻译都会生成一个记录「原文 ↔ 译文 ↔ 版面位置」的文件。它由 `latest.jsonl`
（**全局单文件**，下一篇会覆盖）和一份**按文档归档件**组成，归档件自动落在这里：

```
%USERPROFILE%\.cache\pdf2zh\segflow\pdf-<原文 PDF 内容 md5 前16位>.jsonl
```

同一篇改名/重下仍是同一份（身份取内容不取文件名），换论文不会互相覆盖。
采纳管线 `tools/adopt.py export --pdf <原文.pdf>` 会自己认领它，**找不到会拒绝执行**
（不会拿"最近翻过的那一篇"顶上）。

> 仅在两种情况下需要手工 `Copy-Item "$env:USERPROFILE\.cache\pdf2zh\segflow\latest.jsonl" "segflow\论文名.jsonl"`：
> ① 该篇是 v28.9 之前翻的（没有归档件）；② 走下面第 10 步那套成品工具链、
> 想固定一份只读副本。否则直接给 `--pdf` 即可。

### 第 10 步：出成品（按顺序，整段复制）

先把前 4 行的路径改成你自己的，再整段贴进第 7 步那个窗口：

```powershell
$orig = "D:\论文\原版.pdf"                                      # 英文原文
$mono = "D:\zotero-pdf2zh\server\translated\论文名-mono.pdf"    # 第 8 步翻出来的译版
$sc   = "D:\zotero-pdf2zh\segflow\论文名.jsonl"                 # 第 9 步归档的侧车
$out  = "D:\zotero-pdf2zh\out\成品\论文名_中文版.pdf"            # 成品输出位置

cd D:\zotero-pdf2zh
New-Item -ItemType Directory -Force out\成品 | Out-Null   # backfill 不会自建目录，先建好
python tools\backfill_pages.py  --mono $mono --original $orig --out $out --sidecar $sc
python tools\relink_pages.py    --target $out --original $orig --sidecar $sc --report out\_relink.txt
python tools\resolve_links.py   --target $out --original $orig --report out\_resolve.txt
python tools\style_links.py     --target $out --sidecar $sc
python tools\verify_links.py    --target $out --report out\_links_bad.txt
```

`$out` 就是成品，可以直接拖进 Zotero 或 Edge 看。
最后一条会打印「语义命中率」，90% 以上属正常（它只校验引文落点，不改文件）。

**可选**：如果你有物种中文名数据 `out\species_zh.json`，在 `style_links` 之前加一条，
给拉丁学名加附录页和荧光笔注释（没有这个文件就跳过）：

```powershell
python tools\appendix_species.py --pdf $out --redraw
```

**顺序不能换**：`relink` 要从「还没 relink 过」的成品取锚文本；`style_links` 必须最后
（它给锚文本叠绘蓝色，文本层会多一份副本）。想重跑就从第一条重新开始。

### 常见报错对照

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `conda : 无法将"conda"项识别为...` | 在普通 PowerShell 里跑，那里没有 conda | 改用开始菜单的 Anaconda Prompt / Miniconda Prompt |
| `conda activate` 后提示符没变（前面没出现环境名） | 同上，窗口不对 | 同上 |
| 输出里出现 `not in git repo` | babeldoc 探测不到 git 版本时的正常提示，**不是错误** | 忽略 |
| 中文全是方块字 | 字体路径没填或填错 | 回第 8 步填字体文件 |
| `ModuleNotFoundError: No module named 'pymupdf'` | 没进 conda 环境 | 先 `conda activate zotero-pdf2zh-venv` |
| `端口 8890 已被占用` | 上次的服务器还活着 | 任务管理器结束所有 `python` 进程；或换 `python server.py --port 8891`，插件里端口同步改。**注意**：Windows 下新实例可能"报了占用却照常起来"，此时请求仍落到旧实例（旧代码 / 旧缓存）——务必先杀干净，再确认 `/api/history` 为空 |
| 译出来跟原文一样是英文 | 请求漏了配置，静默回落到 bing | 用 `python tools\force_rerender.py --pdf <原文.pdf> --force` 重渲染 |
| 补丁好像没生效 | 覆盖到别的环境去了 | 重跑第 5 步，用那四条 `Test-Path` / `Select-String` 验证 |

## 增量清单（相对上游 v4.1.7）

| 文件 | 作用 |
|---|---|
| `patches/pdf2zh_translator.py` | 术语表按段指纹进缓存键（改词不全量失效）+ CJK 排版清理（裸花括号脱壳）等 |
| `tools/adopt.py` | 采纳管线总调度：`export → deliver → import → inject → render → gate`，每阶段台账留有痕（`rollback` 为回路半途失败的止损坏） |
| `tools/seg_export.py` | 侧车 → 编号段落包：字形还原、PDF 断词修复、跨页续接合并 |
| `tools/seg_import.py` | 译文校验与回锚：编号/⋮/数字/拉丁名逐一对账 |
| `tools/seg_inject.py` | 缓存注入：重编号 + 文档指纹作用域 + 前置断言 + 自动备份 |
| `tools/doubao_bridge.py` | 网页 AI 本地 MCP 桥（STDIO JSON-RPC，零第三方依赖；文件名沿用历史）：inbox/payload/result 三件套 + `search_term` 术语证据检索 + `list_reports`/`get_report` 读质检报告 + `list_results`/`get_result` 读回上一版交件 |
| `tools/result_naming.py` | 交件命名契约唯一事实源：族 `doubao\|webai` 新旧互认、新件缺省 `webai`（`server/utils/result_naming.py` 是逐字节第二份） |
| `tools/force_rerender.py` | force 重渲染（防漏传 config 静默回落 bing 重译） |
| `tools/verify_render.py` | 渲染验收：新串落页 / 旧串清零，13 项断言 |
| `tools/post_check.py` | 翻译后质检门禁：汉化率 / 引用完整性 / 占位符残留 |
| `tools/pre_check.py` | 翻译前体检：文献页 / 扫描页 / 字体结构风险，坏 PDF 提交前拦截 |
| `tools/pre_render_check.py` | 渲染前预检：用译文页特征提前判文献区，FAIL 就不渲染 |
| `tools/seams_report.py` 等 | 接缝台账 / 专项审查 / 对照实验 |
| `tools/backfill_pages.py` | 成品回填：整页表格等"零可译段页"用原版页替换 |
| `tools/relink_pages.py` | 链接热区重定位：译文重排后把链接框搬到锚文本新位置 |
| `tools/resolve_links.py` | NAMED→GOTO：阅读器兼容 + 落点语义定位（顺带修原版错目标） |
| `tools/verify_links.py` | 链接落点语义校验：锚的"作者+年份" vs 落点附近条目 |
| `tools/appendix_species.py` | 物种中文名附录页 + 正文学名荧光笔注释 |
| `tools/style_links.py` | 链接可见性：锚文本原位叠绘为蓝色（无下划线） |
| `tools/species_extract.py` | 拉丁学名清单提取（斜体字体通道，表 10.2 另配结构化解析） |
| `tools/table_zh.py` | 表格页定点中文化（研究留存；本产品未采用，理由见下） |
| `tools/term_verify.py` | 术语证据查证：本地库 / OpenAlex / 维基系多源检索，出「术语 + 证据」报告（只出证据，不改译文） |

## 部署：拿到仓库后先做三件事

> 新手不必读本节——[新手照着做](#新手照着做从零到一份成品-pdfwindows) 已经把这三件事写成了可复制命令。
> 本节保留给需要分文件理解的开发者。

本仓库是**覆盖式补丁集**，不是独立可运行包——请先备好上游底座，再把补丁贴上去。

### 1）备好上游底座

- **Zotero 7 + 上游插件**：从 [guaguastandup/zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh) 的 Release 获取 xpi（本仓库不含 xpi）。
- **翻译环境**：conda 环境（Python 3.12）。依赖版本已钉死，见 `server/config/venv.json` 与 `server/requirements.txt`，按此安装即可，无需自行选版本。

### 2）把 `patches/` 覆盖到对应位置

`patches/` 下的文件按"前缀 → 目标"命名。**覆盖前请先备份原文件**：

| 补丁文件 | 覆盖到 |
|---|---|
| `patches/server_server.py` | `server/server.py` |
| `patches/server_utils_config.py` | `server/utils/config.py` |
| `patches/server_utils_environment_lifecycle.py` | `server/utils/environment_lifecycle.py` |
| `patches/server_utils_execute.py` | `server/utils/execute.py` |
| `patches/server_utils_task_manager.py` | `server/utils/task_manager.py` |
| `patches/server_config_venv.json` | `server/config/venv.json` |
| `patches/server_requirements.txt` | `server/requirements.txt` |
| `patches/pdf2zh_converter.py` | 虚拟环境 `site-packages/pdf2zh/converter.py` |
| `patches/pdf2zh_translator.py` | 虚拟环境 `site-packages/pdf2zh/translator.py` |
| `patches/pdf2zh_cache.py` | 虚拟环境 `site-packages/pdf2zh/cache.py` |
| `patches/pdfminer_encodingdb.py` | 虚拟环境 `site-packages/pdfminer/encodingdb.py` |
| `patches/pdfminer_pdffont.py` | 虚拟环境 `site-packages/pdfminer/pdffont.py` |
| `patches/pdfminer_pdfinterp.py` | 虚拟环境 `site-packages/pdfminer/pdfinterp.py` |
| `patches/PROTOCOL.md` | **不是覆盖文件**：`{vN}` 占位符协议契约，改动前必读 |

> 为什么要打补丁：上游 v4.1.7 的公式保护参数、中文字体路径与配置写入方式，默认状态下在 Windows 本地环境不能正常工作（逐项原理见 `改动记录.md` 第一节与第二节）。又因为上游升级会覆盖这些改动，本仓库同时关闭了上游的自动更新通道——因此**升级上游前请先读 `patches/PROTOCOL.md` 与 `改动记录.md` 第四节的更新决策流程**。

### 3）配置与目录

```powershell
Copy-Item server/config/config.json.example server/config/config.json
Copy-Item server/config/config.toml.example server/config/config.toml
New-Item -ItemType Directory -Force inbox, out, segflow
```

- 在 `config.json` 中填入**自己的**翻译服务 API Key——本仓库不含任何 Key。
- 中文字体：在 **Zotero 插件设置**里指定字体文件（如 `server/fonts/NotoSerifCJKsc-Regular.otf`）。
  服务器收到请求时会把该路径写进 `config.json` 的全局键 `NOTO_FONT_PATH`（手改 config.json 没用，
  它会被插件推送的请求值覆盖）。若这里留下的是官方的 Linux 路径，中文会显示成方块字。
- `server/glossary/terms.csv` 与 `guideline.txt` 是作者所用文献的术语表与翻译指南，请替换为自己的。
- `inbox/`、`out/`、`segflow/` 是交换目录，不在版本库中，需按上面的命令自行创建。

## 快速开始（单篇论文 5 步）

```powershell
# 0) 用上游流程把论文正常翻译一遍（生成缓存与侧车），并归档侧车防覆盖
#    侧车默认在 %USERPROFILE%\.cache\pdf2zh\segflow\latest.jsonl，每翻一篇会被覆盖，所以先归档
Copy-Item "$env:USERPROFILE\.cache\pdf2zh\segflow\latest.jsonl" segflow\<书名>.jsonl

# 1) 导出段落包并放入剪贴板  (--pages 写**真实 PDF 页码**, 与体检/门禁报告同一口径)
python tools/seg_export.py --pages 2-4 --name payload_p2_p4 --sidecar segflow/<书名>.jsonl
Set-Clipboard ([IO.File]::ReadAllText('inbox/payload_p2_p4.txt',[Text.Encoding]::UTF8))

# 2) 网页 AI 对话（本机当前用豆包基础对话）：粘贴 → 译完全选复制

# 3) 校验（必须 PASS）
python tools/seg_import.py --manifest inbox/payload_p2_p4.manifest.json --clip --sidecar segflow/<书名>.jsonl

# 4) 注入缓存（指纹自动探测；自动备份）
python tools/seg_inject.py --imported out/payload_p2_p4.imported.json --manifest inbox/payload_p2_p4.manifest.json --sidecar segflow/<书名>.jsonl

# 5) 渲染 + 验收
python tools/force_rerender.py --pdf <原文.pdf> --force
python tools/verify_render.py --expect '<新译关键词>' --forbid '<旧译法>'
```

## 出成品：把译文做成可交付 PDF（6 步）

渲染出来的 `-mono.pdf` 还不是能交付的东西：整页表格被重渲染打碎成竖排散字、
超链接热区随重排失效（且多数阅读器不认 NAMED 链接）、拉丁学名读者不认识。
下面 6 步把译文补成成品。**前置三样**：译版 mono、英文原版、侧车。

```powershell
$orig = "<原版.pdf>"                      # 英文原文
$mono = "<译版-mono.pdf>"                 # 上游渲染产物: server/translated/<书名>-mono.pdf
$sc   = "segflow\<书名>.jsonl"            # 侧车(归档的 latest.jsonl)
$out  = "out\成品\<书名>_中文版.pdf"

# 1) 回填: "零可译段页"(整页表格等)用原版对应页替换 —— 表格保持出版级排版
python tools/backfill_pages.py --mono $mono --original $orig --out $out --sidecar $sc

# 2) 链接热区重定位: 译文重排后, 把链接框搬到锚文本(年份/编号)的新位置
python tools/relink_pages.py --target $out --original $orig --sidecar $sc --report out\_relink.txt

# 3) NAMED 链接 -> 显式 GOTO, 并按"作者+年份"语义定位落点(顺带修原版错目标)
python tools/resolve_links.py --target $out --original $orig --report out\_resolve.txt

# 4) 落点校验(只读): 锚的"作者+年份" 是否出现在落点附近
python tools/verify_links.py --target $out --report out\_links_bad.txt

# 5) 可选: 物种中文名附录页 + 正文学名荧光笔注释(就地修改, 建议先备份)
python tools/appendix_species.py --pdf $out --data out\species_zh.json --redraw

# 6) 链接可见性: 锚文本原位叠绘为蓝色(原版观感, 不加下划线)
python tools/style_links.py --target $out --sidecar $sc
```

**为什么顺序不能乱**

- `relink` 的锚文本取自"原版同页同矩形"，所以它的输入必须是**还没 relink 过**的成品——
  重跑要从第 1 步开始，否则矩形已被搬移、锚文本取错。
- `resolve` 负责把链接写死成"目标页+坐标"；此后阅读器才有得跳。
- `style` 必须在最后：它给锚文本叠绘蓝色，文本层会出现一次重复（视觉无差异，
  但复制该年份会得到 `19901990`）。之所以不用 redaction 做"替换"，是因为
  `apply_redactions` 会清掉该页全部链接（实测 18 → 0），而它跑在挂链接之后。

**链接为什么会"乱跳"**（三个坑，本仓库都已处理）

1. 坐标系镜像：命名目标树给出的是 PDF 底部原点（y 向上），而写链接要顶部原点（y 向下）——
   不换算则**全部**落点镜像，表现就是"点哪儿都跳错"。
2. 排版漂移：目标坐标来自原版那一页，但成品重排+翻译过（文献页标题中文化、行距变化），
   同一个 y 在两版指向不同条目。
3. 原版目标树本身不可靠：实测原版 p13 有 4 条不同引文指向同一行，而那行是另一篇文献。
   所以 `resolve_links` 不信目标坐标，改用"锚的（作者姓，年份）"到成品参考文献区
   全书定位——这一步能反过来修正原版自己的错目标。

## 术语查证：先拿证据再裁决

冷门术语该翻成什么，靠猜不如靠证据。`tools/term_verify.py` 把术语丢给几个零 Key 的公开数据源，
把命中文献、被引数和**能取到原文上下文**的摘录整理成报告，供你（或让 LLM 依据证据）裁决。
它**只出证据**——不改译文、不写术语表。

```powershell
# A) 即席查证：随手查一个词，不必先有清单（默认源 zotero+openalex，都零部署零代理）
python tools\term_verify.py --term herkogamy
python tools\term_verify.py --term herkogamy --term dichogamy   # 可重复给多个
python tools\term_verify.py --term "breeding system" --per-term 8

# B) 批量查证：读翻译时产出的「存疑清单」（不带参数=取最新一份，默认源 openalex）
python tools\term_verify.py
python tools\term_verify.py server\translated\review\存疑清单_xxxx.md --limit 10
python tools\term_verify.py <清单.md> --source zotero,openalex   # 本地库 + 学术库
python tools\term_verify.py <清单.md> --source all               # 全源合并
```

报告默认落在存疑清单同目录（即席模式落在 `server\translated\review\`）；**即席模式同时打印到屏幕**。

### 数据源（`--source`，逗号可多选）

| 源 | 给什么证据 | 前置条件 |
|---|---|---|
| `zotero` | **自己库里 PDF 的原文上下文**——能直接回答"这词在本文里指什么" | Zotero 正在运行，且 设置→高级 勾选「允许本机其他应用与 Zotero 通信」 |
| `openalex` | 论文标题 + 被引数（判断"这是不是领域通用译法"） | 无（零 Key，需外网） |
| `wikipedia` | 条目摘要（定义性语境） | 需代理；**未对真实响应验证** |
| `wikidata` | 实体 + 别名 + 中文标签（可直接给中文名，仍须人工确认） | 需代理；**未对真实响应验证** |

开跑前会先用探针词真试一次各源，**剔除不可达的源并在报告头写明**；全部不可达则不产报告、退出码 2。
维基两源在国内网络不可直连（DNS 污染 / SNI 阻断），要用请先启用代理（`urllib` 自动读 `HTTPS_PROXY`）。

### 读报告时注意三点

- **带 `[弱]` 或没有摘录的条目当噪音看**——只保证"召回了"，没取到原文上下文，不足以支撑裁决；
- **中文术语不走 zotero**——Zotero 全文索引对汉字是字符级松散匹配（实测查"拓扑优化"返回 10 条，
  9 条无关），报告头会写明跳过了什么；
- **标了"循环证据"的条目不能用**——库里存着 pdf2zh 自己产出的中文译文 PDF，
  命中它的摘录等于"拿旧译法验证旧译法"。

### 让网页 AI 自己查（MCP 工具 `search_term`）

网页 AI 就是采纳流程里的那个 LLM。把 `tools/doubao_bridge.py` 注册进它的「技能·连接器」（本机当前用豆包）后，
它定稿时可以直接调 `search_term` 取证据——不必你先把术语抄成清单再喂过去。

- 一次查一个术语（`term`），可传 `sources` / `max_results`；返回**证据文本，不落文件**
- 默认源仍是 `zotero,openalex`；**建议用英文原词查**（如 `herkogamy` 而非"雌雄异位"）
- 被限流或不可达的源会先被剔除并在报告头写明；全部不可达时返回错误，而不是给一份空报告
- 与命令行版一致：只出证据，不改译文、不写术语表

#### 和网页 AI 自带联网搜索的区别（以及什么时候该调它）

豆包首战**没调**这个工具——它用自身知识 + 自带联网搜索把 `herkogamy` 答对了
（词源、分类、Webb & Lloyd 都对）。所以这里说清二者差别，免得你纠结"到底要不要调"：

| | 网页 AI 自带联网搜索 | `search_term` |
|---|---|---|
| 语料来源 | 公开互联网（网页、摘要、开放获取全文） | **你自己 Zotero 库里的 PDF 全文** + OpenAlex |
| 能否查正在译的这本书 | **不能**（付费专著公网上没有） | 能，直接给原句 |
| 给什么 | 网页内容，读完整页再总结 | 命中处**原文句子** + 出处标注（+ 被引数） |
| 可复现 | 每次可能不同，事后无法复核 | 同术语同结果，报告可存档 |
| 联网 / 隐私 | 必须联网，查询词发出去 | `zotero` 源不发一个外网包，库内容不出本机 |

**判断标准不是"这词难不难"，而是"答案是否必须来自你自己的语料"：**

- 「herkogamy 是什么意思」→ 网页 AI 自己答就够，调不调都行
- 「herkogamy 在**我正在译的这本书**里怎么用的 / 我的译名**有没有本书依据**」→ 必须调它

反过来，网页 AI 的搜索也有它更强的地方：覆盖面更广（新闻、标准文档、你库里没有的近期论文）、
零前置、更新鲜。**二者互补，不互相替代**——本项目只需它补上"私有语料"这一块，
所以工具描述里特意写明"你自带的联网搜索拿不到用户收藏的 PDF 原文"。

## 实测数据（M1，2026-09）

| 指标 | 结果 |
|---|---|
| 版面对齐 | 34 页 / 154 段，一一对应 |
| 回锚验收 | 9/9 段 PASS，数字与拉丁名 100% 原位 |
| 渲染验收 | 13/13 断言全过 |
| 改字成本 | 45 秒 / 0 次 LLM（旧管线改一个术语 ≈ 重译 39 分钟） |
| 翻译成本 | 0 元（豆包基础对话免费） |

### 成品链实测（Cactaceae 2009 专著，34 页 + 1 页附录）

| 指标 | 结果 |
|---|---|
| 超链接 | 328 条全部转显式 GOTO（Edge/Zotero 等简易阅读器均可点） |
| 热区重定位 | 254 命中 / 0 未命中（防碰撞后无两条链接抢同一字形） |
| 落点语义命中 | **279 / 303 = 92.1%**（镜像命中 **0**；余 25 条锚非年份，无法语义校验） |
| 表格页 | 8 页回填原版（旋转表头/单元格保持出版级排版） |
| 物种附录 | 68 物种；正文 171 处学名荧光笔注释；锚文本 251 段 1004 字染蓝 |

## 已知限制

- 整页大表格保持英文原样（字形保优先于翻译，表格翻译是后续课题）
- 网页 AI 标点与字形标点并存处可能产生双重标点（吸收算法待改逐字符增量）
- 纯拉丁段落不经过 CJK 清理规则
- `seg_inject` 的文档指纹默认自动探测；若探测不到会回落到作者所用文献的兜底值，换文献使用时请显式传入 `--fp`
- `style_links` 是"叠绘"而非"替换"：视觉上与原版蓝字一致，但锚文本在 PDF 文本层重复一次（复制年份会得到 `19901990`）
- 链接落点靠"（作者姓，年份）在成品文献区定位"修复；若原书引文与文献表本身就对不上（引用的年份在该作者名下不存在），则该条无法修复，只能保持原版落点。每次出成品后跑 `verify_links.py`，失配清单落在 `out\_links_bad.txt`
- `term_verify` 的维基百科 / 维基数据两源解析**未对真实响应验证**（本机 DNS 污染 / SNI 阻断），待代理可用时复核；中文术语按设计跳过 `zotero` 源，若库里收藏了中文文献则查不到
- OpenAlex 是**按 IP 限流**的：配额耗尽时（实测 `Retry-After` 给到 ≈15.8 小时）它会被自动剔除，
  报告头会写明服务器要求的等待时间；此时 `zotero` 源照常可用。配 `OPENALEX_EMAIL` 只影响
  礼貌池，**解决不了配额耗尽**
- **自动回路中途被强杀**（`PAUSE_TRANSLATE=1` 正在等交稿时直接关掉服务器 / 结束进程）：
  第一趟落下的骨架行会留在缓存里，下次正常渲染会命中它 → 产物是英文。撤掉即可：
  `python tools\adopt.py rollback --name <任务名> --pdf <原文.pdf>`
  （只删"原文 = 译文"的骨架行，不会误删真译文）

## 许可与致谢

- 本仓库基于 [guaguastandup/zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh) v4.1.7 改造，
  依 **AGPL-3.0** 同协议开源；上游改动之外的新文件由仓库作者贡献。
- 底层翻译引擎：[PDFMathTranslate (pdf2zh)](https://github.com/Byaidu/PDFMathTranslate)（AGPL-3.0）。
- 译文引擎推荐任意能通读全文的网页 AI（本机当前用豆包桌面版，基础对话免费）；本仓库与任何 AI 厂商无隶属关系。
- 仅供学习研究；请勿翻译、传播受版权保护的出版物全文。
