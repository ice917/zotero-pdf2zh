# 上游升级预案（9 月重构版专用）

> 背景：server 启动日志公告——"项目将全面重构并预计 9 月发布新版本……可能随时需要向本仓库提交补丁"。
> 本地补丁体系（`patches/` 8 文件 + venv 内自研代码）将面临上游合并的真正考验。
> **升级前必读本文件，按步骤执行。**

## 〇、升级前 48 小时内必做

```powershell
# 1. 冻结快照（任何升级操作前）
cd D:\zotero-pdf2zh
git add -A
git commit -m "pre-upstream-merge: final state before upgrade"
git tag pre-upstream-merge

# 2. 导出当前 venv 实际包版本（用于对照上游新 requirements）
& "D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\python.exe" -m pip freeze > logs\pip_freeze_pre_upgrade.txt

# 3. 备份 venv 内的自研补丁文件（三处，最关键）
Copy-Item "D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages\pdf2zh\translator.py" "patches\pdf2zh_translator.py" -Force
Copy-Item "D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages\pdfminer\encodingdb.py" "patches\pdfminer_encodingdb.py" -Force
Copy-Item "D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages\pdfminer\pdffont.py" "patches\pdfminer_pdffont.py" -Force
```

## 一、升级时逐项核对（patches/ 8 文件 → 新上游）

| 本地补丁 | 上游风险 | 核对方法 |
|---|---|---|
| `server_config_venv.json` | 上游可能改 venv.json 结构 | 升级后 diff 新 example 与本文件 |
| `server_requirements.txt` | 上游必改（依赖升级） | **不要直接覆盖**——用 `pip freeze` 对照，只钉自研依赖的版本 |
| `server_server.py` | `--skip-subset-fonts`/`-f/-c`/`prepare_path` 三处自研点 | 在新 server.py 里搜这三处是否被保留 |
| `server_utils_config.py` | 空值保护 + POLISH 键保护（4 处 append） | 新代码里搜 `translator_keys.append`，数一下应有 7+ 处 |
| `server_utils_environment_lifecycle.py` | 环境更新冻结 return | 新代码搜 `maybe_prompt_existing_user_update`，确认 freeze 注释还在 |
| `pdf2zh_translator.py` | **最大风险**：润色管线全部自研 | 新包安装后此文件必然被覆盖，必须重放。重放后跑 `python -c "import ast; ast.parse(open(r'...translator.py',encoding='utf-8').read())"` |
| `pdfminer_encodingdb.py` / `pdfminer_pdffont.py` | 字体解码修正 | 若上游 pdfminer.six 版本变更，需在**新版本源码上重新套补丁**（不能直接覆盖二进制不匹配的旧补丁） |

## 二、升级后冒烟测试（缺一不可）

```powershell
# 1. 语法层面：三个自研 py 文件可解析
& "D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\python.exe" -X utf8 -c "
import ast, io
for p in [r'D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\Lib\site-packages\pdf2zh\translator.py',
          r'D:\zotero-pdf2zh\server\server.py',
          r'D:\zotero-pdf2zh\server\utils\config.py']:
    ast.parse(io.open(p, encoding='utf-8').read()); print('OK', p.split('\\')[-1])
"

# 2. 服务层面：启动 + 健康
D:\zotero-pdf2zh\logs\launch.ps1
Start-Sleep 30
Invoke-RestMethod http://127.0.0.1:8890/health

# 3. 功能层面：翻一篇短 PDF，检查
#    a) 启动日志有"润色钩子自检"行
#    b) 产物有审校报告 + 存疑清单（review 目录）
#    c) 字体正确（霞鹜新致宋）
#    d) config.json 翻译后 POLISH_* 键仍在（空值保护生效）

# 4. 查证器仍可用
python -X utf8 tools\term_verify.py --context "test" --limit 1
```

## 三、回退预案

```powershell
# 任何一步失败，立即回滚
git checkout pre-upstream-merge -- .
# 若 venv 被升级污染，用 pip freeze 对照降级：
& "D:\Users\97638\anaconda3\envs\zotero-pdf2zh-venv\python.exe" -m pip install -r patches\server_requirements.txt --force-reinstall
```

## 四、长期建议（9 月后新版本稳定时）

1. **评估上游是否已吸收自研功能**：重构版可能原生支持公式保护、上下文压缩等——若已支持，逐步下线对应补丁，减少维护面。
2. **润色管线考虑外置**：translator.py 内嵌补丁每次升级都要重放。可改为独立模块（通过 env 指向），只在 `translate()` 钩子处留一行调用——上游覆盖面从 400 行缩到 3 行。
3. **密钥轮换**（详见下节）：升级日一并做最省事。

## 五、密钥轮换清单（本仓库 git 历史含历史密钥，纯本地无泄露，但建议择机轮换）

| 密钥 | 位置 | 历史状态 | 轮换方式 |
|---|---|---|---|
| 硅基流动 `sk-` | `server/config/config.json`（v1 起入库） | 7 个提交含 | silicon 平台重新生成 → 更新 config.json → 旧 Key 失效即安全 |
| Tavily `tvly-dev-` | AI Butler `local_mcp_server/.env.example`（eb72c01 入库） | 已从工作区移除 | tavily.com 重新生成 → 更新 `.env`（.env 不入库，安全） |
| DASHSCOPE | `local_mcp_server\.env` | 从未入库 | 无需动 |
| QWEN_PROXY_TOKEN | `local_mcp_server\.env` | 从未入库 | 无需动 |

> 轮换后，git 历史中的旧 Key 自然失效，无需改写历史（`git filter-branch`/BFG 风险大，纯本地仓库不值得）。
