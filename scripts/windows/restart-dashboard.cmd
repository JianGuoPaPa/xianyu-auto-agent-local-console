@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*monitor_panel.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
ping -n 2 127.0.0.1 >nul
schtasks /Run /TN XianyuAutoAgent-Dashboard
ping -n 6 127.0.0.1 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*monitor_panel.py*' } | Select-Object ProcessId,CommandLine | Format-Table -AutoSize"
