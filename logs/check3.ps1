Get-Process -Name uv -ErrorAction SilentlyContinue | Select-Object Id, CPU, WS, StartTime, @{N='Min';E={[math]::Round((New-TimeSpan -Start $_.StartTime -End (Get-Date)).TotalMinutes, 1)}}
Write-Output '---TEMP-FILES-LATEST-10---'
Get-ChildItem $env:APPDATA\uv\python\.temp -Recurse -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 10 | Format-Table FullName, Length, LastWriteTime -AutoSize
Write-Output '---TEMP-DIR-SIZE---'
$tempDir = "$env:APPDATA\uv\python\.temp"
if (Test-Path $tempDir) {
  $sizes = Get-ChildItem $tempDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum
  Write-Output ("Total: {0:N2} MB in {1} files" -f ($sizes.Sum / 1MB), (Get-ChildItem $tempDir -Recurse -File -ErrorAction SilentlyContinue).Count)
}
Write-Output '---NET-CONNECTIONS---'
Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq 12072 } | Select-Object RemoteAddress, RemotePort, State
Write-Output '---PORT-8890---'
Get-NetTCPConnection -State Listen -LocalPort 8890 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, State
Write-Output '---LOGS-DIR---'
Get-ChildItem D:\zotero-pdf2zh\logs -Force | Select-Object Name, Length, LastWriteTime
