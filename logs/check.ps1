Get-ChildItem $env:APPDATA\uv\python -Force | Select-Object Name, LastWriteTime
Write-Output '---'
Get-ChildItem $env:APPDATA\uv\python\.temp -ErrorAction SilentlyContinue | Select-Object Name, LastWriteTime
Write-Output '---PROCS---'
Get-Process -Name uv,python -ErrorAction SilentlyContinue | Select-Object Name, Id, CPU, StartTime
Write-Output '---PORT-8890---'
Test-NetConnection -ComputerName 127.0.0.1 -Port 8890 -InformationLevel Quiet -WarningAction SilentlyContinue
