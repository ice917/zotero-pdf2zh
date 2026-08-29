Write-Output '=== pip show uv (in anaconda) ==='
D:\Users\97638\anaconda3\python.exe -m pip show uv 2>&1
Write-Output ''
Write-Output '=== uv.exe details ==='
Get-Item 'D:\Users\97638\anaconda3\Scripts\uv.exe' | Select-Object FullName, Length, LastWriteTime, CreationTime
Write-Output ''
Write-Output '=== anaconda3 pip list (filtered) ==='
D:\Users\97638\anaconda3\python.exe -m pip list 2>&1 | Select-String -Pattern 'uv|pip|conda' -SimpleMatch
