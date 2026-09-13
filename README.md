# zotero-pdf2zh · 豆包译文采纳管线（Doubao Adoption Pipeline）

> 基于 [guaguastandup/zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh) v4.1.7（AGPL-3.0）的改造。
> 上游解决"把 PDF 翻成中文"；本仓库的增量解决一个上游没碰的问题：**改一个词，不要重译整段**。

## 一句话

把翻译产出的"所有权"从 LLM 手里拿回来：译文来源与模型解耦，外部定稿（如豆包整篇通读的译文）
经过**段落对齐 → 数字/拉丁名回锚 → 缓存注入**三道机械工序装回原版面。
此后"改字 = 改字"：45 秒重渲染，0 次 LLM 调用，13 项版面验收断言自动把关。

## 三堵墙与解法

| 墙 | 解法 |
|---|---|
| 版面保真（公式/表格/双栏/跨页断句） | 版面层不动：34 页 / 154 段槽位图 + `{vN}` 字形表，公式表格原位保留；`verify_render` 13 项断言 + `post_check` 质检门禁 |
| 语境准确（`successful gamete ≠ 成功的配子`） | 整篇交给能通读全文的引擎（豆包基础对话，免费不限次），术语约束降级为译后校验清单，语境级译法只进文档级台账 |
| 迭代成本（缓存键绑参数，改词 = 重译 39 分钟） | 外部定稿注入缓存：页级→段内占位符重编号 + 文档指纹作用域，改字 = 改字 |

## 架构

```
原文 PDF ──上游converter──▶ 槽位图(页/段 + {vN}字形表) ──侧车latest.jsonl
                                │
                                │ seg_export  编号段落包(#S编号+字形还原+断词修复+跨页合并⋮)
                                ▼
                        [豆包 基础对话]  ◀── 剪贴板（人工两次粘贴，免费零额度）
                                │
                                │ seg_import  编号对账 / ⋮对账 / 高价值字形回锚 / 纯标点按契约丢弃
                                ▼
                        seg_inject   页级→段内{vN}重编号 / 文档指纹作用域 / 前置断言
                                ▼
                        缓存库 ──force──▶ 45s 渲染 ──▶ verify_render (13 断言)
```

可选自动化通道：`doubao_bridge.py` 是零依赖手写的 STDIO MCP 桥（list_inbox / get_payload /
submit_result），注册进豆包「技能·连接器」后可省掉剪贴板（消耗 Agent 模式额度）。

## 增量清单（相对上游 v4.1.7）

| 文件 | 作用 |
|---|---|
| `patches/pdf2zh_translator.py` | 术语表按段指纹进缓存键（改词不全量失效）+ CJK 排版清理（裸花括号脱壳）等 |
| `tools/seg_export.py` | 侧车 → 编号段落包：字形还原、PDF 断词修复、跨页续接合并 |
| `tools/seg_import.py` | 译文校验与回锚：编号/⋮/数字/拉丁名逐一对账 |
| `tools/seg_inject.py` | 缓存注入：重编号 + 文档指纹作用域 + 前置断言 + 自动备份 |
| `tools/doubao_bridge.py` | 豆包本地 MCP 桥（STDIO JSON-RPC，零第三方依赖） |
| `tools/force_rerender.py` | force 重渲染（防漏传 config 静默回落 bing 重译） |
| `tools/verify_render.py` | 渲染验收：新串落页 / 旧串清零，13 项断言 |
| `tools/post_check.py` | 翻译后质检门禁：汉化率 / 引用完整性 / 占位符残留 |
| `tools/seams_report.py` 等 | 接缝台账 / 专项审查 / 对照实验 |

## 部署：拿到仓库后先做三件事

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
| `patches/server_config_venv.json` | `server/config/venv.json` |
| `patches/server_requirements.txt` | `server/requirements.txt` |
| `patches/pdf2zh_converter.py` | 虚拟环境 `site-packages/pdf2zh/converter.py` |
| `patches/pdf2zh_translator.py` | 虚拟环境 `site-packages/pdf2zh/translator.py` |
| `patches/pdf2zh_cache.py` | 虚拟环境 `site-packages/pdf2zh/cache.py` |
| `patches/pdfminer_encodingdb.py` | 虚拟环境 `site-packages/pdfminer/encodingdb.py` |
| `patches/pdfminer_pdffont.py` | 虚拟环境 `site-packages/pdfminer/pdffont.py` |
| `patches/PROTOCOL.md` | **不是覆盖文件**：`{vN}` 占位符协议契约，改动前必读 |

> 为什么要打补丁：上游 v4.1.7 的公式保护参数、中文字体路径与配置写入方式，默认状态下在 Windows 本地环境不能正常工作（逐项原理见 `改动记录.md` 第一节与第二节）。又因为上游升级会覆盖这些改动，本仓库同时关闭了上游的自动更新通道——因此**升级上游前请先读 `patches/PROTOCOL.md` 与 `改动记录.md` 第四节的更新决策流程**。

### 3）配置与目录

```powershell
Copy-Item server/config/config.json.example server/config/config.json
Copy-Item server/config/config.toml.example server/config/config.toml
New-Item -ItemType Directory -Force inbox, out, segflow
```

- 在 `config.json` 中填入**自己的**翻译服务 API Key——本仓库不含任何 Key。
- 将 `NOTO_FONT_PATH` 改成本机中文字体路径；官方默认是 Linux 路径，在 Windows 上会导致中文显示为方块字。
- `server/glossary/terms.csv` 与 `guideline.txt` 是作者所用文献的术语表与翻译指南，请替换为自己的。
- `inbox/`、`out/`、`segflow/` 是交换目录，不在版本库中，需按上面的命令自行创建。

## 快速开始（单篇论文 5 步）

```powershell
# 0) 用上游流程把论文正常翻译一遍（生成缓存与侧车），并归档侧车防覆盖
#    侧车默认在 %USERPROFILE%\.cache\pdf2zh\segflow\latest.jsonl，每翻一篇会被覆盖，所以先归档
Copy-Item "$env:USERPROFILE\.cache\pdf2zh\segflow\latest.jsonl" segflow\<书名>.jsonl

# 1) 导出段落包并放入剪贴板
python tools/seg_export.py --pages 2-4 --name payload_p2_p4 --sidecar segflow/<书名>.jsonl
Set-Clipboard ([IO.File]::ReadAllText('inbox/payload_p2_p4.txt',[Text.Encoding]::UTF8))

# 2) 豆包基础对话：粘贴 → 译完全选复制

# 3) 校验（必须 PASS）
python tools/seg_import.py --manifest inbox/payload_p2_p4.manifest.json --clip --sidecar segflow/<书名>.jsonl

# 4) 注入缓存（指纹自动探测；自动备份）
python tools/seg_inject.py --imported out/payload_p2_p4.imported.json --manifest inbox/payload_p2_p4.manifest.json --sidecar segflow/<书名>.jsonl

# 5) 渲染 + 验收
python tools/force_rerender.py --pdf <原文.pdf> --force
python tools/verify_render.py --expect '<新译关键词>' --forbid '<旧译法>'
```

## 实测数据（M1，2026-09）

| 指标 | 结果 |
|---|---|
| 版面对齐 | 34 页 / 154 段，一一对应 |
| 回锚验收 | 9/9 段 PASS，数字与拉丁名 100% 原位 |
| 渲染验收 | 13/13 断言全过 |
| 改字成本 | 45 秒 / 0 次 LLM（旧管线改一个术语 ≈ 重译 39 分钟） |
| 翻译成本 | 0 元（豆包基础对话免费） |

## 已知限制

- 整页大表格保持英文原样（字形保优先于翻译，表格翻译是后续课题）
- 豆包标点与字形标点并存处可能产生双重标点（吸收算法待改逐字符增量）
- 纯拉丁段落不经过 CJK 清理规则
- `seg_inject` 的文档指纹默认自动探测；若探测不到会回落到作者所用文献的兜底值，换文献使用时请显式传入 `--fp`

## 许可与致谢

- 本仓库基于 [guaguastandup/zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh) v4.1.7 改造，
  依 **AGPL-3.0** 同协议开源；上游改动之外的新文件由仓库作者贡献。
- 底层翻译引擎：[PDFMathTranslate (pdf2zh)](https://github.com/Byaidu/PDFMathTranslate)（AGPL-3.0）。
- 译文引擎推荐豆包桌面版（基础对话免费）；本仓库与字节跳动无隶属关系。
- 仅供学习研究；请勿翻译、传播受版权保护的出版物全文。
