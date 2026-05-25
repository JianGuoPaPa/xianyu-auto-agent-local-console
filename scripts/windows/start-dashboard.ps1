$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$launchLog = Join-Path $logs "dashboard-launch.log"
$stdoutLog = Join-Path $logs "dashboard.out.log"
$stderrLog = Join-Path $logs "dashboard.err.log"
"==== start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====" | Out-File -FilePath $launchLog -Append -Encoding UTF8

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Python venv not found. Run scripts\windows\install-autoagent.ps1 first."
}

$env:NO_PROXY = "*"
$env:no_proxy = "*"
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""
$env:http_proxy = ""
$env:https_proxy = ""
$env:all_proxy = ""
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$existing = Get-CimInstance Win32_Process | Where-Object {
  $_.ExecutablePath -eq $python -and $_.CommandLine -like "*monitor_panel.py*"
}
if ($existing) {
  "already running pid $($existing.ProcessId -join ',')" | Out-File -FilePath $launchLog -Append -Encoding UTF8
  exit 0
}

$args = @("monitor_panel.py", "--host", "0.0.0.0", "--port", "8765")
$process = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
"started pid $($process.Id)" | Out-File -FilePath $launchLog -Append -Encoding UTF8
