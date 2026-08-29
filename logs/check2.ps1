Get-Process -Name uv -ErrorAction SilentlyContinue | Select-Object Id, CPU, WS, StartTime, @{N='Min';E={[math]::Round((New-TimeSpan -Start $_.StartTime -End (Get-Date)).TotalMinutes, 1)}}
Write-Output '---TEMP-DIR---'
Get-ChildItem $env:APPDATA\uv\python\.temp -ErrorAction SilentlyContinue -Directory | Select-Object Name, LastWriteTime
Write-Output '---TEMP-DIR-SIZE---'
$tempDir = "$env:APPDATA\uv\python\.temp"
if (Test-Path $tempDir) {
  $sizes = Get-ChildItem $tempDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum
  Write-Output ("Total: {0:N2} MB" -f ($sizes.Sum / 1MB))
}
Write-Output '---CACHE-SIZE---'
$cache = "$env:LOCALAPPDATA\uv\cache"
if (Test-Path $cache) {
  $s = (Get-ChildItem $cache -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
  Write-Output ("Cache: {0:N2} GB" -f ($s / 1GB))
  Write-Output ("Files: {0:N0}" -f (Get-ChildItem $cache -Recurse -File -ErrorAction SilentlyContinue).Count)
}
Write-Output '---NET-IO-PROCESS-12072---'
Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq 12072 } | Select-Object RemoteAddress, RemotePort, State
Write-Output '---PORT-8890---'
Get-NetTCPConnection -State Listen -LocalPort 8890 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, State
Write-Output '---ALL-LISTEN-PORTS---'
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -match '127.0.0.1|0.0.0.0' -and $_.LocalPort -lt 65000 } | Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize
