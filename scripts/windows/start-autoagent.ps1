$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$launchLog = Join-Path $logs "agent-launch.log"
$stdoutLog = Join-Path $logs "agent.out.log"
$stderrLog = Join-Path $logs "agent.err.log"
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
$env:AUTOAGENT_NONINTERACTIVE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$existing = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq "python.exe" -and
  $_.CommandLine -like "*main.py*" -and
  (($_.ExecutablePath -like "$root*") -or ($_.CommandLine -like "*$root*"))
}
if ($existing) {
  "already running pid $($existing.ProcessId -join ',')" | Out-File -FilePath $launchLog -Append -Encoding UTF8
  exit 0
}

foreach ($path in @($stdoutLog, $stderrLog)) {
  if (Test-Path $path) {
    $archive = "$path.$(Get-Date -Format 'yyyyMMdd-HHmmss').bak"
    Move-Item -Path $path -Destination $archive -Force
  }
}

$mainScript = Join-Path $root "main.py"
$process = Start-Process -FilePath $python -ArgumentList "`"$mainScript`"" -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
"started pid $($process.Id)" | Out-File -FilePath $launchLog -Append -Encoding UTF8
