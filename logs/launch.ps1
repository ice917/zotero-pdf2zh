$ErrorActionPreference = 'Continue'
$logOut = 'D:\zotero-pdf2zh\logs\server_out.log'
$logErr = 'D:\zotero-pdf2zh\logs\server_err.log'
$logRun = 'D:\zotero-pdf2zh\logs\server_run.log'

'' | Set-Content -Path $logOut
'' | Set-Content -Path $logErr
'' | Set-Content -Path $logRun

$uv = 'D:\Users\97638\anaconda3\Scripts\uv.exe'
$workDir = 'D:\zotero-pdf2zh\server'

Add-Content -Path $logRun -Value "Start at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

$p = Start-Process -FilePath $uv `
  -ArgumentList @('run','--python','3.12','--with-requirements','requirements.txt','server.py','--check_update','false') `
  -WorkingDirectory $workDir `
  -RedirectStandardOutput $logOut `
  -RedirectStandardError $logErr `
  -PassThru `
  -WindowStyle Hidden

Add-Content -Path $logRun -Value "PID: $($p.Id)"
Write-Output "Launched PID: $($p.Id)"
Write-Output "Logs: D:\zotero-pdf2zh\logs\"
