# 镜像同步脚本: venv/server 运行侧 → patches/ (单向)
# 用法: powershell -ExecutionPolicy Bypass -File tools\sync_patches.ps1
# 行为: 复制 11 个补丁文件到 patches/, 复制后逐文件 MD5 断言, 任一失败退出码 1
# 配套: 同步后跑 tools\tests\run_all.py 回归(test_mirrors_sync 会再次核对)
#
# venv 侧 site-packages 从哪来(不焊死本机路径):
#   1. 环境变量 PDF2ZH_VENV_SITE 直接指定
#   2. 否则由 PDF2ZH_PYTHON(或已激活/在 PATH 上的 python)推出同级 Lib\site-packages
#      —— 与 tools/tests/*.py 的 PDF2ZH_VENV_SITE 约定同一套

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$venvSite = $env:PDF2ZH_VENV_SITE
if (-not $venvSite) {
    $py = if ($env:PDF2ZH_PYTHON) { $env:PDF2ZH_PYTHON } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
    if (-not $py) {
        Write-Output 'FAIL 找不到 pdf2zh 解释器: 设 $env:PDF2ZH_PYTHON 或先激活 venv 再跑'
        exit 1
    }
    $venvSite = Join-Path (Split-Path -Parent $py) 'Lib\site-packages'
}
Write-Output "venv site-packages = $venvSite"

$pairs = @(
    @{ src = Join-Path $venvSite 'pdf2zh\cache.py';                    dst = 'patches\pdf2zh_cache.py' },
    @{ src = Join-Path $venvSite 'pdf2zh\converter.py';                dst = 'patches\pdf2zh_converter.py' },
    @{ src = Join-Path $venvSite 'pdf2zh\translator.py';               dst = 'patches\pdf2zh_translator.py' },
    @{ src = Join-Path $venvSite 'pdfminer\encodingdb.py';             dst = 'patches\pdfminer_encodingdb.py' },
    @{ src = Join-Path $venvSite 'pdfminer\pdffont.py';                dst = 'patches\pdfminer_pdffont.py' },
    @{ src = Join-Path $venvSite 'pdfminer\pdfinterp.py';              dst = 'patches\pdfminer_pdfinterp.py' },
    @{ src = Join-Path $repo 'server\server.py';                       dst = 'patches\server_server.py' },
    @{ src = Join-Path $repo 'server\utils\config.py';                 dst = 'patches\server_utils_config.py' },
    @{ src = Join-Path $repo 'server\utils\environment_lifecycle.py';  dst = 'patches\server_utils_environment_lifecycle.py' },
    @{ src = Join-Path $repo 'server\utils\execute.py';                dst = 'patches\server_utils_execute.py' },
    @{ src = Join-Path $repo 'server\utils\task_manager.py';           dst = 'patches\server_utils_task_manager.py' }
)

$failed = 0
foreach ($p in $pairs) {
    if (-not (Test-Path $p.src)) {
        Write-Output "FAIL 源缺失: $($p.src)"
        $failed++; continue
    }
    Copy-Item $p.src (Join-Path $repo $p.dst) -Force
    $h1 = (Get-FileHash $p.src -Algorithm MD5).Hash
    $h2 = (Get-FileHash (Join-Path $repo $p.dst) -Algorithm MD5).Hash
    if ($h1 -eq $h2) {
        Write-Output ("PASS {0} ({1})" -f $p.dst, $h1.Substring(0, 8))
    } else {
        Write-Output "FAIL MD5 不一致: $($p.dst)"
        $failed++
    }
}

if ($failed -gt 0) {
    Write-Output "`n同步失败 $failed 项"
    exit 1
}
Write-Output "`n全部同步完成。请运行回归: venv python tools\tests\run_all.py"
exit 0
