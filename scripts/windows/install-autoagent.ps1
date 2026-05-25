param(
  [string]$ModelName = "qwen2.5:3b-instruct",
  [string]$TaskPrefix = "XianyuAutoAgent",
  [string]$PythonExe = "",
  [string]$OllamaModelsDir = "",
  [string]$PipCacheDir = "",
  [string]$PipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

if (-not $OllamaModelsDir) {
  $OllamaModelsDir = Join-Path $env:LOCALAPPDATA "Ollama\models"
}
if (-not $PipCacheDir) {
  $PipCacheDir = Join-Path $env:LOCALAPPDATA "pip-cache"
}

if (-not (Test-Path ".env")) {
  if (Test-Path ".env.windows.example") {
    Copy-Item ".env.windows.example" ".env"
  } elseif (Test-Path ".env.example") {
    Copy-Item ".env.example" ".env"
  } else {
    throw ".env not found and no example env file is available."
  }
}

New-Item -ItemType Directory -Force -Path $PipCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $OllamaModelsDir | Out-Null

[Environment]::SetEnvironmentVariable("PIP_CACHE_DIR", $PipCacheDir, "User")
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $OllamaModelsDir, "User")
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "127.0.0.1:11434", "User")

$env:PIP_CACHE_DIR = $PipCacheDir
$env:OLLAMA_MODELS = $OllamaModelsDir
$env:OLLAMA_HOST = "127.0.0.1:11434"
$env:NO_PROXY = "*"
$env:no_proxy = "*"

if ($PythonExe -and (Test-Path $PythonExe)) {
  & $PythonExe -m venv .venv
} else {
  $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pythonLauncher) {
    & py -3 -m venv .venv
  } else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python -or $python.Source -like "*\Microsoft\WindowsApps\python.exe") {
      throw "Python 3 is required. Install Python 3, or pass -PythonExe with an absolute path."
    }
    & python -m venv .venv
  }
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
& $venvPython -m pip install --disable-pip-version-check --no-input --index-url $PipIndexUrl --trusted-host "pypi.tuna.tsinghua.edu.cn" --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE" }

& $venvPython -m pip install --disable-pip-version-check --no-input --index-url $PipIndexUrl --trusted-host "pypi.tuna.tsinghua.edu.cn" -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "dependency install failed with exit code $LASTEXITCODE" }

$envPath = Join-Path $root ".env"
$envText = Get-Content $envPath -Raw
$pairs = @{
  "API_KEY" = "ollama"
  "MODEL_BASE_URL" = "http://127.0.0.1:11434/v1"
  "MODEL_NAME" = $ModelName
  "ENABLE_MODEL_SEARCH" = "False"
}

foreach ($key in $pairs.Keys) {
  $value = $pairs[$key]
  if ($envText -match "(?m)^$key=") {
    $envText = [regex]::Replace($envText, "(?m)^$key=.*$", "$key=$value")
  } else {
    $envText += "`r`n$key=$value"
  }
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($envPath, $envText, $utf8NoBom)

$powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$ollamaScript = Join-Path $root "scripts\windows\start-ollama.ps1"
$agentScript = Join-Path $root "scripts\windows\start-autoagent.ps1"
$dashboardScript = Join-Path $root "scripts\windows\start-dashboard.ps1"

$ollamaAction = "`"$powerShell`" -NoProfile -ExecutionPolicy Bypass -File `"$ollamaScript`""
$agentAction = "`"$powerShell`" -NoProfile -ExecutionPolicy Bypass -File `"$agentScript`""
$dashboardAction = "`"$powerShell`" -NoProfile -ExecutionPolicy Bypass -File `"$dashboardScript`""

schtasks /Create /TN "$TaskPrefix-Ollama" /SC ONLOGON /TR $ollamaAction /F | Out-Host
schtasks /Create /TN "$TaskPrefix-Service" /SC ONLOGON /TR $agentAction /F | Out-Host
schtasks /Create /TN "$TaskPrefix-Dashboard" /SC ONLOGON /TR $dashboardAction /F | Out-Host

try {
  New-NetFirewallRule -DisplayName "$TaskPrefix Dashboard 8765" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private,Domain -ErrorAction Stop | Out-Null
} catch {
  Write-Warning "Could not create firewall rule for dashboard port 8765: $($_.Exception.Message)"
}

Write-Output "Installed. Run these to start now:"
Write-Output "  schtasks /Run /TN `"$TaskPrefix-Ollama`""
Write-Output "  schtasks /Run /TN `"$TaskPrefix-Service`""
Write-Output "  schtasks /Run /TN `"$TaskPrefix-Dashboard`""
