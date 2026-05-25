@echo off
setlocal
cd /d "%~dp0\..\.."

schtasks /Run /TN XianyuAutoAgent-CookieChrome
ping -n 21 127.0.0.1 >nul

tasklist /v | findstr /i chrome
echo ====TASKLOG====
powershell.exe -NoProfile -Command "Get-Content .\logs\cookie_chrome_task.log -Tail 40 -ErrorAction SilentlyContinue"
echo ====FILES====
dir .\chrome-cookie-extension.crx .\chrome-cookie-extension.pem .\chrome-cookie-extension-update.xml .\data\cookie_extension_id.txt

