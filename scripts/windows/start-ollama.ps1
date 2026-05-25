$ErrorActionPreference = "Stop"

$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollama)) {
  throw "Ollama executable not found: $ollama"
}

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$logFile = Join-Path $logs "ollama.log"
"==== start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====" | Out-File -FilePath $logFile -Append -Encoding UTF8

$env:OLLAMA_HOST = "127.0.0.1:11434"
if (-not $env:OLLAMA_MODELS) {
  $env:OLLAMA_MODELS = Join-Path $env:LOCALAPPDATA "Ollama\models"
}
New-Item -ItemType Directory -Force -Path $env:OLLAMA_MODELS | Out-Null

try {
  $version = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 2
  "already running: $($version.version)" | Out-File -FilePath $logFile -Append -Encoding UTF8
  exit 0
} catch {
  "starting ollama serve" | Out-File -FilePath $logFile -Append -Encoding UTF8
}

$process = Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden -PassThru
"started pid $($process.Id)" | Out-File -FilePath $logFile -Append -Encoding UTF8
