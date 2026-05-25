$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$url = "https://www.goofish.com/"
$extensionDir = Join-Path $root "chrome-cookie-extension"
$tokenPath = Join-Path $root "data\dashboard_token.txt"
$logDir = Join-Path $root "logs"
$taskLog = Join-Path $logDir "cookie_chrome_task.log"
$chromeLog = Join-Path $logDir "chrome-cookie-sync.log"
$crxPath = Join-Path $root "chrome-cookie-extension.crx"
$pemPath = Join-Path $root "chrome-cookie-extension.pem"
$updatePath = Join-Path $root "chrome-cookie-extension-update.xml"
$extensionIdPath = Join-Path $root "data\cookie_extension_id.txt"
$updateUrl = "http://127.0.0.1:8765/extensions/xianyu-cookie-sync-update.xml"
$crxUrl = "http://127.0.0.1:8765/extensions/xianyu-cookie-sync.crx"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-TaskLog {
  param([string]$Message)
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  Add-Content -Path $taskLog -Value $line -Encoding UTF8
}

function Write-Utf8NoBom {
  param(
    [string]$Path,
    [string]$Value
  )
  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

trap {
  Write-TaskLog "ERROR: $($_.Exception.Message)"
  if ($_.ScriptStackTrace) {
    Write-TaskLog $_.ScriptStackTrace
  }
  exit 1
}

$candidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
)

$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
  Write-TaskLog "Chrome not found"
  throw "Chrome not found"
}

if (-not (Test-Path $tokenPath)) {
  Write-TaskLog "Dashboard token not found: $tokenPath"
  throw "Dashboard token not found: $tokenPath"
}

$token = (Get-Content -Path $tokenPath -Raw -Encoding UTF8).Trim()
New-Item -ItemType Directory -Force -Path $extensionDir | Out-Null
Write-TaskLog "Preparing extension in $extensionDir"

$manifest = @'
{
  "manifest_version": 3,
  "name": "Xianyu Cookie Sync",
  "version": "1.0.0",
  "description": "Sync Goofish cookies to local XianyuAutoAgent dashboard.",
  "permissions": ["alarms", "cookies"],
  "host_permissions": [
    "https://goofish.com/*",
    "https://*.goofish.com/*",
    "https://taobao.com/*",
    "https://*.taobao.com/*",
    "https://tmall.com/*",
    "https://*.tmall.com/*",
    "http://127.0.0.1/*",
    "http://localhost/*"
  ],
  "action": {
    "default_title": "Xianyu Cookie Sync"
  },
  "background": {
    "service_worker": "background.js"
  },
  "content_scripts": [
    {
      "matches": [
        "https://goofish.com/*",
        "https://*.goofish.com/*",
        "https://taobao.com/*",
        "https://*.taobao.com/*",
        "https://tmall.com/*",
        "https://*.tmall.com/*"
      ],
      "js": ["content.js"],
      "run_at": "document_idle"
    }
  ]
}
'@
Write-Utf8NoBom -Path (Join-Path $extensionDir "manifest.json") -Value $manifest

$tokenJson = $token | ConvertTo-Json -Compress
$backgroundTemplate = @'
const DASHBOARD_URL = "http://127.0.0.1:8765/api/browser-cookie";
const DASHBOARD_TOKEN = __DASHBOARD_TOKEN_JSON__;
const COOKIE_DOMAINS = ["goofish.com", "taobao.com", "tmall.com"];
const REQUIRED_COOKIE_NAMES = ["unb", "_m_h5_tk"];

function domainMatches(cookieDomain) {
  const normalized = String(cookieDomain || "").replace(/^\\./, "").toLowerCase();
  return COOKIE_DOMAINS.some((domain) => normalized === domain || normalized.endsWith("." + domain));
}

function addCookies(jar, cookies) {
  for (const cookie of cookies || []) {
    if (!cookie.name || !cookie.value || !domainMatches(cookie.domain)) continue;
    jar.set(cookie.name, cookie.value);
  }
}

function hasRequiredCookies(jar) {
  return REQUIRED_COOKIE_NAMES.every((name) => jar.has(name));
}

function formatCookieHeader(jar) {
  return Array.from(jar.entries()).map(([name, value]) => `${name}=${value}`).join("; ");
}

async function collectCookies() {
  const jar = new Map();
  addCookies(jar, await chrome.cookies.getAll({}));

  for (const domain of COOKIE_DOMAINS) {
    if (hasRequiredCookies(jar)) break;
    addCookies(jar, await chrome.cookies.getAll({ domain }));
  }

  return formatCookieHeader(jar);
}

async function syncCookies() {
  try {
    const cookie = await collectCookies();
    await fetch(DASHBOARD_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Dashboard-Token": DASHBOARD_TOKEN
      },
      body: JSON.stringify({ cookie, source: "chrome-extension-background", timestamp: new Date().toISOString() })
    });
  } catch (error) {
    console.warn("Xianyu cookie sync failed", error);
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("sync-xianyu-cookie", { periodInMinutes: 1 });
  syncCookies();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("sync-xianyu-cookie", { periodInMinutes: 1 });
  syncCookies();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "sync-xianyu-cookie") syncCookies();
});

chrome.alarms.create("sync-xianyu-cookie", { periodInMinutes: 1 });
syncCookies();
'@
$background = $backgroundTemplate.Replace("__DASHBOARD_TOKEN_JSON__", $tokenJson)
Write-Utf8NoBom -Path (Join-Path $extensionDir "background.js") -Value $background

$contentTemplate = @'
const DASHBOARD_URL = "http://127.0.0.1:8765/api/browser-cookie";
const DASHBOARD_TOKEN = __DASHBOARD_TOKEN_JSON__;

async function syncDocumentCookie() {
  try {
    const cookie = document.cookie || "";
    await fetch(DASHBOARD_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Dashboard-Token": DASHBOARD_TOKEN
      },
      body: JSON.stringify({ cookie, source: "chrome-extension-content", href: location.href, timestamp: new Date().toISOString() })
    });
  } catch (error) {
    console.warn("Xianyu content cookie sync failed", error);
  }
}

syncDocumentCookie();
setInterval(syncDocumentCookie, 60000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) syncDocumentCookie();
});
'@
$content = $contentTemplate.Replace("__DASHBOARD_TOKEN_JSON__", $tokenJson)
Write-Utf8NoBom -Path (Join-Path $extensionDir "content.js") -Value $content
Write-TaskLog "Extension files written"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}

$crxIdScript = @'
import sys

data = open(sys.argv[1], "rb").read()
if data[:4] != b"Cr24":
    raise SystemExit("not a CRX file")

header_size = int.from_bytes(data[8:12], "little")
header = data[12:12 + header_size]

def read_varint(buf, index):
    result = 0
    shift = 0
    while True:
        byte = buf[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, index
        shift += 7

def read_field(buf, target):
    index = 0
    while index < len(buf):
        key, index = read_varint(buf, index)
        field = key >> 3
        wire_type = key & 7
        if wire_type == 0:
            _, index = read_varint(buf, index)
            continue
        if wire_type == 1:
            value = buf[index:index + 8]
            index += 8
        elif wire_type == 2:
            length, index = read_varint(buf, index)
            value = buf[index:index + length]
            index += length
        elif wire_type == 5:
            value = buf[index:index + 4]
            index += 4
        else:
            raise SystemExit(f"unsupported protobuf wire type: {wire_type}")
        if field == target:
            return value
    return None

signed_header = read_field(header, 10000)
if not signed_header:
    raise SystemExit("CRX signed header not found")
crx_id = read_field(signed_header, 1)
if not crx_id:
    raise SystemExit("CRX id not found")

alphabet = "abcdefghijklmnop"
print("".join(alphabet[byte >> 4] + alphabet[byte & 15] for byte in crx_id))
'@
$crxIdScriptPath = Join-Path $extensionDir "read_crx_id.py"
Write-Utf8NoBom -Path $crxIdScriptPath -Value $crxIdScript

Remove-Item -Path $crxPath -Force -ErrorAction SilentlyContinue
$packArgs = @("--pack-extension=`"$extensionDir`"")
if (Test-Path $pemPath) {
  $packArgs += "--pack-extension-key=`"$pemPath`""
}
$packProcess = Start-Process -FilePath $chrome -ArgumentList ($packArgs -join " ") -Wait -PassThru -WindowStyle Hidden
if ($packProcess.ExitCode -ne 0 -or -not (Test-Path $crxPath)) {
  Write-TaskLog "Chrome extension pack failed with exit code $($packProcess.ExitCode)"
  throw "Chrome extension pack failed"
}

$extensionId = (& $python $crxIdScriptPath $crxPath).Trim()
if (-not $extensionId) {
  Write-TaskLog "Failed to read extension id from $crxPath"
  throw "Failed to read extension id"
}
Set-Content -Path $extensionIdPath -Value $extensionId -Encoding UTF8

$updateXml = @"
<?xml version="1.0" encoding="UTF-8"?>
<gupdate xmlns="http://www.google.com/update2/response" protocol="2.0">
  <app appid="$extensionId">
    <updatecheck codebase="$crxUrl" version="1.0.0" />
  </app>
</gupdate>
"@
Set-Content -Path $updatePath -Value $updateXml -Encoding UTF8
Write-TaskLog "Packed extension $extensionId to $crxPath"

$targetPolicy = "$extensionId;$updateUrl"

foreach ($policyBase in @(
  "HKLM:\Software\Policies\Google\Chrome",
  "HKCU:\Software\Policies\Google\Chrome"
)) {
  try {
    New-Item -Path $policyBase -Force | Out-Null
    $extensionSettings = @{
      $extensionId = @{
        installation_mode = "force_installed"
        update_url = $updateUrl
        override_update_url = $true
      }
    } | ConvertTo-Json -Depth 5 -Compress
    New-ItemProperty -Path $policyBase -Name "ExtensionSettings" -Value $extensionSettings -PropertyType String -Force | Out-Null

    $forceListKey = Join-Path $policyBase "ExtensionInstallForcelist"
    if (-not (Test-Path $forceListKey)) {
      New-Item -Path $forceListKey -Force | Out-Null
    }
    $properties = Get-ItemProperty -Path $forceListKey
    $policyName = $null
    foreach ($property in $properties.PSObject.Properties) {
      if ($property.Name -match "^\d+$" -and [string]$property.Value -like "$extensionId;*") {
        $policyName = $property.Name
        break
      }
    }
    if (-not $policyName) {
      $used = @($properties.PSObject.Properties | Where-Object { $_.Name -match "^\d+$" } | ForEach-Object { [int]$_.Name })
      $next = 1
      while ($used -contains $next) {
        $next += 1
      }
      $policyName = [string]$next
    }
    New-ItemProperty -Path $forceListKey -Name $policyName -Value $targetPolicy -PropertyType String -Force | Out-Null
    Write-TaskLog "Registered Chrome extension policy at $policyBase"
    break
  } catch {
    Write-TaskLog "Policy registration skipped for ${policyBase}: $($_.Exception.Message)"
  }
}

$args = @(
  '--profile-directory="Profile 1"',
  "--restore-last-session",
  "--no-first-run",
  "--enable-extensions",
  "--disable-background-timer-throttling",
  "--enable-logging",
  "--v=1",
  "--log-file=`"$chromeLog`"",
  "--disable-features=ChromeWhatsNewUI",
  "`"$url`""
)

Start-Process -FilePath $chrome -ArgumentList ($args -join " ") -WindowStyle Normal
Write-TaskLog "Started Chrome from $chrome with args: $($args -join ' ')"
Write-Host "Started Chrome Profile 1 with Xianyu cookie sync extension."
