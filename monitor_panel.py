import argparse
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from cookie_sync import (
    DEFAULT_DEBUG_URL,
    cookie_is_usable,
    fetch_best_cookie_header,
    merge_cookie_headers,
    should_replace_cookie,
    summarize_cookie,
)


ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"

SERVICE_LABEL = "com.xianyu.autoagent"
SERVICE_PLIST = Path.home() / "Library/LaunchAgents/com.xianyu.autoagent.plist"

TASK_PREFIX = "XianyuAutoAgent"
AGENT_TASK_NAME = f"{TASK_PREFIX}-Service"
OLLAMA_TASK_NAME = f"{TASK_PREFIX}-Ollama"
DASHBOARD_TASK_NAME = f"{TASK_PREFIX}-Dashboard"

DB_PATH = ROOT / "data" / "chat_history.db"
TOKEN_PATH = ROOT / "data" / "dashboard_token.txt"
EXTENSION_UPDATE_PATH = "/extensions/xianyu-cookie-sync-update.xml"
EXTENSION_CRX_PATH = "/extensions/xianyu-cookie-sync.crx"

COOKIE_SYNC_STATE = {
    "enabled": False,
    "running": False,
    "last_run": None,
    "last_result": None,
    "debug_url": DEFAULT_DEBUG_URL,
    "interval_seconds": 300,
    "retry_seconds": 60,
}
COOKIE_SYNC_THREAD = None

AGENT_LOG_PATH = ROOT / "logs" / ("agent.err.log" if IS_WINDOWS else "launchd.err.log")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
PROMPT_FILES = {
    "classify_prompt": {"label": "意图分类", "file": "classify_prompt.txt"},
    "default_prompt": {"label": "默认回复", "file": "default_prompt.txt"},
    "tech_prompt": {"label": "技术/使用咨询", "file": "tech_prompt.txt"},
    "price_prompt": {"label": "议价回复", "file": "price_prompt.txt"},
}


def parse_launchctl_status(text):
    state_match = re.search(r"\bstate = ([^\n]+)", text)
    pid_match = re.search(r"\bpid = (\d+)", text)
    runs_match = re.search(r"\bruns = (\d+)", text)
    state = state_match.group(1).strip() if state_match else "unknown"
    pid = int(pid_match.group(1)) if pid_match else None
    runs = int(runs_match.group(1)) if runs_match else 0
    return {
        "state": state,
        "pid": pid,
        "pids": [pid] if pid else [],
        "runs": runs,
        "running": state == "running" and pid is not None,
        "needs_cookie": False,
    }


def read_text_auto(path):
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def is_loopback_host(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return str(host).lower() == "localhost"


def extension_asset_response(path, client_host, root=ROOT):
    if not is_loopback_host(client_host):
        return 403, b"extension assets are only available from loopback clients", "text/plain; charset=utf-8"

    root = Path(root)
    if path == EXTENSION_CRX_PATH:
        asset_path = root / "chrome-cookie-extension.crx"
        content_type = "application/x-chrome-extension"
    elif path == EXTENSION_UPDATE_PATH:
        asset_path = root / "chrome-cookie-extension-update.xml"
        content_type = "text/xml; charset=utf-8"
    else:
        return 404, b"extension asset not found", "text/plain; charset=utf-8"

    if not asset_path.exists():
        return 404, b"extension asset not found", "text/plain; charset=utf-8"
    return 200, asset_path.read_bytes(), content_type


def tail_lines(path, limit=120):
    try:
        lines = read_text_auto(path).splitlines()
    except FileNotFoundError:
        return []
    return lines[-limit:]


def run_command(args, timeout=8):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as exc:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr=str(exc))


def powershell(script):
    executable = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not Path(executable).exists():
        executable = "powershell"
    return run_command([executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=12)


def current_launchctl_output(label=SERVICE_LABEL):
    result = run_command(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
    return result.stdout if result.returncode == 0 else result.stderr


def get_dashboard_token():
    env_token = os.getenv("DASHBOARD_TOKEN", "").strip()
    if env_token:
        return env_token
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.exists():
        token = TOKEN_PATH.read_text(encoding="utf-8", errors="replace").strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    TOKEN_PATH.write_text(token, encoding="utf-8")
    return token


def read_env_pairs(env_path):
    pairs = {}
    try:
        lines = Path(env_path).read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except FileNotFoundError:
        return pairs
    for raw_line in lines:
        line = raw_line.lstrip("\ufeff")
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs[key.strip()] = value
    return pairs


def read_prompt_file(prompt_id):
    meta = PROMPT_FILES.get(prompt_id)
    if not meta:
        raise KeyError(prompt_id)
    path = ROOT / "prompts" / meta["file"]
    try:
        return read_text_auto(path)
    except FileNotFoundError:
        example_path = ROOT / "prompts" / meta["file"].replace(".txt", "_example.txt")
        return read_text_auto(example_path) if example_path.exists() else ""


def prompt_payload():
    prompts = []
    for prompt_id, meta in PROMPT_FILES.items():
        text = read_prompt_file(prompt_id)
        prompts.append({
            "id": prompt_id,
            "label": meta["label"],
            "filename": meta["file"],
            "text": text,
            "length": len(text),
        })
    return {"prompts": prompts}


def update_prompt(prompt_id, text):
    meta = PROMPT_FILES.get(prompt_id)
    if not meta:
        return {"ok": False, "message": "未知提示词类型"}
    text = (text or "").strip()
    if len(text) < 20:
        return {"ok": False, "message": "提示词太短，保存前请补充完整规则"}
    prompt_dir = ROOT / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / meta["file"]).write_text(text.rstrip() + "\n", encoding="utf-8")
    return {"ok": True, "message": "提示词已保存", "prompt_id": prompt_id, "length": len(text)}


def ensure_item_profile_schema(db_path=DB_PATH):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_reply_profiles (
                item_id TEXT PRIMARY KEY,
                enabled INTEGER DEFAULT 1,
                delivery_text TEXT DEFAULT '',
                custom_prompt TEXT DEFAULT '',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _item_title_from_data(data):
    if not data:
        return ""
    try:
        parsed = json.loads(data)
    except (TypeError, json.JSONDecodeError):
        return ""
    return parsed.get("title") or parsed.get("itemTitle") or ""


def item_profile_payload(root=ROOT):
    db_path = Path(root) / "data" / "chat_history.db"
    if not db_path.exists():
        return {"items": []}
    ensure_item_profile_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            WITH all_items AS (
                SELECT item_id FROM items
                UNION
                SELECT item_id FROM messages
            ),
            message_stats AS (
                SELECT item_id, COUNT(*) AS message_count, MAX(timestamp) AS last_message
                FROM messages
                GROUP BY item_id
            )
            SELECT
                all_items.item_id,
                items.data,
                items.price,
                COALESCE(items.description, '') AS description,
                items.last_updated AS item_updated_at,
                COALESCE(message_stats.message_count, 0) AS message_count,
                message_stats.last_message,
                COALESCE(item_reply_profiles.enabled, 1) AS enabled,
                COALESCE(item_reply_profiles.delivery_text, '') AS delivery_text,
                COALESCE(item_reply_profiles.custom_prompt, '') AS custom_prompt,
                item_reply_profiles.updated_at AS profile_updated_at
            FROM all_items
            LEFT JOIN items ON items.item_id = all_items.item_id
            LEFT JOIN message_stats ON message_stats.item_id = all_items.item_id
            LEFT JOIN item_reply_profiles ON item_reply_profiles.item_id = all_items.item_id
            ORDER BY COALESCE(message_stats.last_message, items.last_updated, '') DESC
            """
        ).fetchall()
    except sqlite3.Error:
        return {"items": []}
    finally:
        conn.close()

    items = []
    for row in rows:
        item = dict(row)
        item["title"] = _item_title_from_data(item.pop("data", ""))
        item["enabled"] = bool(item["enabled"])
        item["description_preview"] = (item.get("description") or "").replace("\n", " ")[:160]
        item["configured"] = bool((item.get("delivery_text") or "").strip() or (item.get("custom_prompt") or "").strip())
        items.append(item)
    return {"items": items}


def update_item_profile(item_id, enabled=True, delivery_text="", custom_prompt="", root=ROOT):
    item_id = str(item_id or "").strip()
    if not item_id:
        return {"ok": False, "message": "缺少商品 ID"}

    db_path = Path(root) / "data" / "chat_history.db"
    ensure_item_profile_schema(db_path)
    delivery_text = (delivery_text or "").strip()
    custom_prompt = (custom_prompt or "").strip()
    enabled_value = 1 if enabled else 0
    updated_at = datetime.now().isoformat()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO item_reply_profiles (item_id, enabled, delivery_text, custom_prompt, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_id)
            DO UPDATE SET enabled = ?, delivery_text = ?, custom_prompt = ?, updated_at = ?
            """,
            (
                item_id, enabled_value, delivery_text, custom_prompt, updated_at,
                enabled_value, delivery_text, custom_prompt, updated_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "message": "商品策略已保存",
        "item_id": item_id,
        "enabled": bool(enabled_value),
    }


def read_safe_config(env_path):
    values = {}
    env_path = Path(env_path)
    pairs = read_env_pairs(env_path)
    default_pairs = read_env_pairs(env_path.parent / ".env.windows.example")
    effective_pairs = dict(default_pairs)
    effective_pairs.update({key: value for key, value in pairs.items() if value})
    for key in ["MODEL_NAME", "MODEL_BASE_URL", "LOG_LEVEL", "SIMULATE_HUMAN_TYPING", "TOGGLE_KEYWORDS", "ENABLE_MODEL_SEARCH"]:
        if key in effective_pairs:
            values[key] = effective_pairs[key]
    api_key = effective_pairs.get("API_KEY", "")
    cookie = pairs.get("COOKIES_STR", "")
    cookie_summary = summarize_cookie(cookie) if cookie and cookie != "your_cookies_here" else {}
    values["API_KEY"] = "已设置" if api_key else "未设置"
    values["COOKIES_STR"] = "已设置" if cookie and cookie != "your_cookies_here" else "未设置"
    values["COOKIE_LENGTH"] = len(cookie) if cookie and cookie != "your_cookies_here" else 0
    values["COOKIE_HAS_X5SEC"] = bool(cookie_summary.get("has_x5sec"))
    return values


def default_env_values(env_path):
    defaults = read_env_pairs(Path(env_path).parent / ".env.windows.example")
    return {
        key: value
        for key, value in defaults.items()
        if key != "COOKIES_STR" and value and value != "your_cookies_here"
    }


def set_env_value(env_path, key, value):
    path = Path(env_path)
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except FileNotFoundError:
        lines = []

    replaced = False
    updated_lines = []
    for raw_line in lines:
        line = raw_line.lstrip("\ufeff")
        if line.startswith(f"{key}="):
            updated_lines.append(f"{key}={value}")
            replaced = True
        else:
            updated_lines.append(line)
    if not replaced:
        updated_lines.append(f"{key}={value}")

    existing_keys = {
        line.split("=", 1)[0]
        for line in updated_lines
        if "=" in line
    }
    for default_key, default_value in default_env_values(path).items():
        if default_key not in existing_keys:
            updated_lines.append(f"{default_key}={default_value}")
    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def cookie_sync_config(root=ROOT):
    pairs = read_env_pairs(Path(root) / ".env")
    enabled = truthy(pairs.get("AUTO_COOKIE_SYNC_ENABLED", os.getenv("AUTO_COOKIE_SYNC_ENABLED", "")))
    debug_url = (
        pairs.get("CHROME_DEBUG_URL")
        or pairs.get("COOKIE_SYNC_DEBUG_URL")
        or os.getenv("CHROME_DEBUG_URL")
        or os.getenv("COOKIE_SYNC_DEBUG_URL")
        or DEFAULT_DEBUG_URL
    )
    raw_interval = pairs.get("AUTO_COOKIE_SYNC_INTERVAL_SECONDS", os.getenv("AUTO_COOKIE_SYNC_INTERVAL_SECONDS", "300"))
    raw_retry = pairs.get("AUTO_COOKIE_SYNC_RETRY_SECONDS", os.getenv("AUTO_COOKIE_SYNC_RETRY_SECONDS", "60"))
    try:
        interval_seconds = max(60, int(raw_interval))
    except ValueError:
        interval_seconds = 300
    try:
        retry_seconds = max(30, int(raw_retry))
    except ValueError:
        retry_seconds = 60
    return {
        "enabled": enabled,
        "debug_url": debug_url,
        "interval_seconds": interval_seconds,
        "retry_seconds": retry_seconds,
    }


def sync_cookie_from_browser_once(root=ROOT, fetch_cookie=fetch_best_cookie_header, restart_func=None):
    root = Path(root)
    config = cookie_sync_config(root)
    env_path = root / ".env"
    pairs = read_env_pairs(env_path)
    current_cookie = pairs.get("COOKIES_STR", "")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        candidate_cookie = (fetch_cookie(config["debug_url"]) or "").strip()
    except Exception as exc:
        return {
            "ok": False,
            "changed": False,
            "message": f"读取 Chrome Cookie 失败: {exc}",
            "last_run": now,
            "debug_url": config["debug_url"],
        }

    merged_cookie = merge_cookie_headers(current_cookie, candidate_cookie)
    summary = summarize_cookie(merged_cookie)
    if not cookie_is_usable(merged_cookie):
        return {
            "ok": False,
            "changed": False,
            "message": "Chrome 里读到的 Cookie 不完整，需要包含 unb 和 _m_h5_tk",
            "last_run": now,
            "debug_url": config["debug_url"],
            "cookie": summary,
        }

    if not should_replace_cookie(current_cookie, merged_cookie):
        return {
            "ok": True,
            "changed": False,
            "message": "Cookie 没有变化",
            "last_run": now,
            "debug_url": config["debug_url"],
            "cookie": summary,
        }

    set_env_value(env_path, "COOKIES_STR", merged_cookie)
    restart_result = (restart_func or restart_service)()
    return {
        "ok": bool(restart_result.get("ok")),
        "changed": True,
        "message": "Cookie 已从浏览器同步并重启 Agent",
        "last_run": now,
        "debug_url": config["debug_url"],
        "cookie": summary,
        "restart": restart_result,
    }


def agent_should_restart_for_cookie(root=ROOT):
    if IS_WINDOWS:
        service = windows_service_status(Path(root))
        return service.get("needs_cookie") or not service.get("running")

    service = parse_launchctl_status(current_launchctl_output())
    return not service.get("running")


def agent_requires_validation_cookie(root=ROOT):
    root = Path(root)
    agent_logs = tail_lines(root / "logs" / ("agent.err.log" if IS_WINDOWS else "launchd.err.log"), 180)
    out_logs = tail_lines(root / "logs" / ("agent.out.log" if IS_WINDOWS else "launchd.out.log"), 80)
    joined = "\n".join(agent_logs + out_logs)
    return "RGV587_ERROR" in joined or "FAIL_SYS_USER_VALIDATE" in joined


def accept_browser_cookie_sync(
    cookie,
    root=ROOT,
    restart_func=None,
    should_restart_func=None,
    validation_cookie_required_func=None,
):
    cookie = (cookie or "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    root = Path(root)
    env_path = root / ".env"
    current_cookie = read_env_pairs(env_path).get("COOKIES_STR", "")
    merged_cookie = merge_cookie_headers(current_cookie, cookie)
    summary = summarize_cookie(merged_cookie)
    if not cookie_is_usable(merged_cookie):
        return {
            "ok": False,
            "changed": False,
            "restarted": False,
            "message": "浏览器扩展发来的 Cookie 不完整，需要包含 unb 和 _m_h5_tk",
            "last_run": now,
            "cookie": summary,
        }

    if not should_replace_cookie(current_cookie, merged_cookie):
        return {
            "ok": True,
            "changed": False,
            "restarted": False,
            "message": "浏览器 Cookie 没有变化",
            "last_run": now,
            "cookie": summary,
        }

    set_env_value(env_path, "COOKIES_STR", merged_cookie)
    should_restart = (should_restart_func or (lambda: agent_should_restart_for_cookie(root)))()
    validation_cookie_required = (
        validation_cookie_required_func
        or (lambda: agent_requires_validation_cookie(root))
    )()
    result = {
        "ok": True,
        "changed": True,
        "restarted": False,
        "message": "浏览器 Cookie 已保存",
        "last_run": now,
        "cookie": summary,
    }
    if should_restart and validation_cookie_required and not summary.get("has_x5sec"):
        result["message"] = "浏览器 Cookie 已保存，但仍缺少 x5sec，暂不重启 Agent"
    elif should_restart:
        restart_result = (restart_func or restart_service)()
        result["restart"] = restart_result
        result["restarted"] = bool(restart_result.get("ok"))
        result["ok"] = bool(restart_result.get("ok"))
        result["message"] = "浏览器 Cookie 已保存，并已重启 Agent"
    return result


def start_cookie_sync_worker():
    global COOKIE_SYNC_THREAD
    if COOKIE_SYNC_THREAD and COOKIE_SYNC_THREAD.is_alive():
        return

    def loop():
        while True:
            config = cookie_sync_config()
            COOKIE_SYNC_STATE.update(config)
            if not config["enabled"]:
                COOKIE_SYNC_STATE["running"] = False
                time.sleep(60)
                continue

            COOKIE_SYNC_STATE["running"] = True
            result = sync_cookie_from_browser_once()
            COOKIE_SYNC_STATE["last_run"] = result.get("last_run")
            COOKIE_SYNC_STATE["last_result"] = result
            time.sleep(config["interval_seconds"] if result.get("ok") else config["retry_seconds"])

    COOKIE_SYNC_THREAD = threading.Thread(target=loop, name="cookie-sync", daemon=True)
    COOKIE_SYNC_THREAD.start()


def read_recent_messages(db_path=DB_PATH, limit=30):
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, timestamp, chat_id, user_id, item_id, role, content
            FROM messages
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def get_table_count(db_path, table_name):
    if not Path(db_path).exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def get_db_stats(db_path=DB_PATH):
    return {
        "message_count": get_table_count(db_path, "messages"),
        "item_count": get_table_count(db_path, "items"),
        "bargain_chat_count": get_table_count(db_path, "chat_bargain_counts"),
        "db_size_bytes": Path(db_path).stat().st_size if Path(db_path).exists() else 0,
    }


def format_command_result(result):
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:],
        "stderr": result.stderr[-2000:],
    }


def windows_process_snapshot():
    script = r"""
$items = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('python.exe','ollama.exe') } |
  Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine
$items | ConvertTo-Json -Compress
"""
    result = powershell(script)
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    return data if isinstance(data, list) else []


def detect_needs_cookie(agent_logs, out_logs):
    joined = "\n".join((agent_logs or [])[-120:] + (out_logs or [])[-40:])
    markers = ["FAIL_SYS_USER_VALIDATE", "RGV587_ERROR", "新的Cookie", "Cookie字符串", "Cookie已失效"]
    return any(marker in joined for marker in markers)


def windows_service_status(root=ROOT):
    root_text = str(root).lower()
    processes = windows_process_snapshot()
    agent_processes = []
    ollama_processes = []
    for process in processes:
        command_line = str(process.get("CommandLine") or "")
        executable = str(process.get("ExecutablePath") or "")
        name = str(process.get("Name") or "")
        if name.lower() == "python.exe" and "main.py" in command_line and root_text in (command_line + executable).lower():
            agent_processes.append(process)
        if name.lower() == "ollama.exe" and " serve" in f" {command_line} ":
            ollama_processes.append(process)

    agent_parent_ids = {
        int(process["ParentProcessId"])
        for process in agent_processes
        if process.get("ParentProcessId") is not None
    }
    leaf_agent_processes = [
        process for process in agent_processes
        if process.get("ProcessId") is not None and int(process["ProcessId"]) not in agent_parent_ids
    ]
    if leaf_agent_processes:
        agent_processes = leaf_agent_processes

    agent_logs = tail_lines(root / "logs" / "agent.err.log", 180)
    out_logs = tail_lines(root / "logs" / "agent.out.log", 60)
    pids = [int(process["ProcessId"]) for process in agent_processes if process.get("ProcessId") is not None]
    needs_cookie = detect_needs_cookie(agent_logs, out_logs)
    state = "needs_cookie" if needs_cookie else ("running" if pids else "stopped")
    return {
        "state": state,
        "pid": pids[0] if pids else None,
        "pids": pids,
        "runs": len([line for line in tail_lines(root / "logs" / "agent-launch.log", 500) if "started pid" in line]),
        "running": bool(pids),
        "needs_cookie": needs_cookie,
        "task_name": AGENT_TASK_NAME,
        "ollama_pids": [int(process["ProcessId"]) for process in ollama_processes if process.get("ProcessId") is not None],
    }


def disabled_proxy_opener():
    return build_opener(ProxyHandler({}))


def http_json(url, body=None, timeout=8):
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(url, data=data, headers=headers, method=method)
    with disabled_proxy_opener().open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def ollama_status():
    try:
        version = http_json(f"{OLLAMA_URL}/api/version", timeout=3).get("version", "")
        return {"running": True, "version": version, "url": OLLAMA_URL}
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"running": False, "version": "", "url": OLLAMA_URL, "error": str(exc)}


def test_ollama_model(prompt="Reply with exactly: OK"):
    config = read_env_pairs(ROOT / ".env")
    model_name = config.get("MODEL_NAME", "qwen2.5:3b-instruct")
    base_url = config.get("MODEL_BASE_URL", f"{OLLAMA_URL}/v1").rstrip("/")
    body = {
        "model": model_name,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        result = http_json(f"{base_url}/chat/completions", body=body, timeout=90)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "model": model_name, "content": content}
    except Exception as exc:
        return {"ok": False, "model": model_name, "error": str(exc)}


def collect_logs(root=ROOT):
    log_dir = Path(root) / "logs"
    if IS_WINDOWS:
        return {
            "agent": tail_lines(log_dir / "agent.err.log", 180),
            "agent_output": tail_lines(log_dir / "agent.out.log", 80),
            "agent_launch": tail_lines(log_dir / "agent-launch.log", 80),
            "ollama": tail_lines(log_dir / "ollama.log", 80),
            "dashboard": tail_lines(log_dir / "dashboard.err.log", 80),
        }
    return {
        "agent": tail_lines(log_dir / "launchd.err.log", 180),
        "agent_output": tail_lines(log_dir / "launchd.out.log", 80),
        "agent_launch": tail_lines(log_dir / "monitor-wrapper.log", 80),
        "ollama": [],
        "dashboard": [],
    }


def build_overview(root=ROOT, launchctl_output=None, now=None):
    root = Path(root)
    if IS_WINDOWS:
        service = windows_service_status(root)
    else:
        launchctl_text = launchctl_output if launchctl_output is not None else current_launchctl_output()
        service = parse_launchctl_status(launchctl_text)
    config = read_safe_config(root / ".env")
    return {
        "generated_at": now or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platform": "windows" if IS_WINDOWS else "macos",
        "service": service,
        "ollama": ollama_status(),
        "config": config,
        "cookie_sync": dict(COOKIE_SYNC_STATE),
        "stats": get_db_stats(root / "data" / "chat_history.db"),
        "logs": collect_logs(root),
        "messages": read_recent_messages(root / "data" / "chat_history.db", 40),
    }


def restart_service(runner=run_command, user_id=None):
    if IS_WINDOWS:
        stop_service()
        result = runner(["schtasks", "/Run", "/TN", AGENT_TASK_NAME], timeout=15)
        return format_command_result(result)

    uid = os.getuid() if user_id is None else user_id
    target = f"gui/{uid}/{SERVICE_LABEL}"
    result = runner(["launchctl", "kickstart", "-k", target], timeout=15)
    if result.returncode == 0:
        return format_command_result(result)

    bootstrap_result = runner(["launchctl", "bootstrap", f"gui/{uid}", str(SERVICE_PLIST)], timeout=15)
    if bootstrap_result.returncode != 0 and "already bootstrapped" not in bootstrap_result.stderr:
        return format_command_result(bootstrap_result)

    return format_command_result(runner(["launchctl", "kickstart", "-k", target], timeout=15))


def stop_windows_agent_processes():
    script = rf"""
$root = {json.dumps(str(ROOT))}
Get-CimInstance Win32_Process |
  Where-Object {{
    $_.Name -eq 'python.exe' -and
    $_.CommandLine -like '*main.py*' -and
    (
      ($_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith($root.ToLower())) -or
      ($_.CommandLine -and $_.CommandLine.ToLower().Contains($root.ToLower()))
    )
  }} |
  ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}
"""
    return powershell(script)


def stop_service(runner=run_command, user_id=None):
    if IS_WINDOWS:
        runner(["schtasks", "/End", "/TN", AGENT_TASK_NAME], timeout=10)
        return format_command_result(stop_windows_agent_processes())

    uid = os.getuid() if user_id is None else user_id
    result = runner(["launchctl", "bootout", f"gui/{uid}", str(SERVICE_PLIST)], timeout=15)
    return format_command_result(result)


def restart_ollama():
    if not IS_WINDOWS:
        return {"ok": False, "message": "当前只支持 Windows 上重启 Ollama"}
    run_command(["schtasks", "/End", "/TN", OLLAMA_TASK_NAME], timeout=10)
    result = run_command(["schtasks", "/Run", "/TN", OLLAMA_TASK_NAME], timeout=15)
    return format_command_result(result)


def update_cookie(cookie):
    cookie = (cookie or "").strip()
    if len(cookie) < 80 or "=" not in cookie:
        return {"ok": False, "message": "Cookie 看起来不完整，请复制浏览器请求里的完整 Cookie 字符串"}
    set_env_value(ROOT / ".env", "COOKIES_STR", cookie)
    return {"ok": True, "message": "Cookie 已保存"}


def perform_service_action(action):
    if action == "restart":
        return restart_service()
    if action == "stop":
        return stop_service()
    if action == "restart_ollama":
        return restart_ollama()
    return {"ok": False, "message": "未知操作"}


def html_page():
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>闲鱼自动发货看板</title>
  <style>{CSS}</style>
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">闲</div>
        <div>
          <strong>闲鱼托管</strong>
          <span>AutoAgent Console</span>
        </div>
      </div>
      <nav class="side-nav" aria-label="主导航">
        <button class="nav-item active" data-view="overview" type="button">总览</button>
        <button class="nav-item" data-view="items" type="button">商品策略</button>
        <button class="nav-item" data-view="prompts" type="button">全局提示词</button>
        <button class="nav-item" data-view="messages" type="button">最近对话</button>
        <button class="nav-item" data-view="logs" type="button">运行日志</button>
        <button class="nav-item" data-view="settings" type="button">设置</button>
      </nav>
      <div class="sidebar-foot">
        <span>本地模型</span>
        <strong id="sidebarModelName">-</strong>
      </div>
    </aside>

    <div class="main-area">
      <header class="topbar">
        <div>
          <h1 id="pageTitle">总览</h1>
          <p id="pageSubtitle">运行状态、消息量和托管服务概况</p>
        </div>
        <div class="top-status">
          <span id="statusDot" class="dot"></span>
          <strong id="statusText">读取中</strong>
          <span id="updatedAt"></span>
          <code id="pidText">PID -</code>
        </div>
        <div class="actions">
          <button id="refreshBtn" type="button">刷新</button>
          <button id="testModelBtn" type="button">测试模型</button>
          <button id="restartAgentBtn" type="button">重启 Agent</button>
          <button id="restartOllamaBtn" type="button">重启 Ollama</button>
          <button id="stopAgentBtn" type="button" class="danger">停止 Agent</button>
        </div>
      </header>

      <main class="content">
        <section class="page-view active" data-page="overview">
          <section class="metrics" id="metrics"></section>
          <section class="overview-grid">
            <article class="panel">
              <div class="panel-head">
                <h2>服务状态</h2>
                <span id="overviewPlatform">Windows 托管</span>
              </div>
              <div class="panel-body status-list">
                <div><span>Agent</span><strong id="overviewAgent">读取中</strong></div>
                <div><span>Cookie</span><strong id="overviewCookie">读取中</strong></div>
                <div><span>Ollama</span><strong id="overviewOllama">读取中</strong></div>
                <div><span>数据库</span><strong id="overviewDb">-</strong></div>
              </div>
            </article>
            <article class="panel">
              <div class="panel-head">
                <h2>模型状态</h2>
                <span id="modelName">-</span>
              </div>
              <div class="panel-body model-box">
                <div><span>接口</span><code id="modelBase">-</code></div>
                <div><span>Ollama</span><strong id="ollamaStatus">读取中</strong></div>
                <pre id="modelResult">点击“测试模型”确认本地模型回复。</pre>
              </div>
            </article>
          </section>
        </section>

        <section class="page-view" data-page="items">
          <section class="workspace two-column">
            <aside class="panel item-list-panel">
              <div class="panel-head">
                <h2>商品列表</h2>
                <button id="reloadItemsBtn" type="button">刷新商品</button>
              </div>
              <div class="list-toolbar">
                <input id="itemSearch" type="search" placeholder="搜索商品标题或ID">
                <select id="itemSelect" class="hidden-select" aria-hidden="true" tabindex="-1"></select>
                <span id="itemCountText">0 个商品</span>
              </div>
              <div id="itemList" class="item-list"></div>
            </aside>

            <article class="panel item-detail-panel">
              <div class="panel-head">
                <div>
                  <h2 id="itemDetailTitle">商品专属策略</h2>
                  <span id="itemDetailSub">选择一个商品后编辑它的自动发货内容和咨询口径</span>
                </div>
                <div class="prompt-actions">
                  <button id="saveItemBtn" type="button">保存当前商品策略</button>
                </div>
              </div>
              <div class="panel-body">
                <div class="item-summary" id="itemSummary">读取中...</div>
                <div class="strategy-banner">
                  <strong>付款后自动发货只绑定到这个商品 ID</strong>
                  <span id="itemScopeText">-</span>
                </div>
                <label class="check-row">
                  <input id="itemEnabled" type="checkbox" checked>
                    <span>启用该商品自动发货和专属回复</span>
                </label>
                <div class="item-edit-grid">
                  <label>
                    <span>付款后自动发货内容</span>
                    <textarea id="itemDeliveryEditor" class="product-editor" spellcheck="false" placeholder="买家付款后，检测到“等待卖家发货”时会直接发送这里的内容。可以填写百度网盘链接、提取码、查看说明和售后提示。"></textarea>
                  </label>
                  <label>
                    <span>咨询回复提示词</span>
                    <textarea id="itemPromptEditor" class="product-editor" spellcheck="false" placeholder="例如：只回答当前商品相关问题；不要承诺实物快递；未付款前不要泄露网盘链接；用户问发货时说明付款后系统发送。"></textarea>
                  </label>
                </div>
                <div class="form-actions">
                  <span id="itemMeta"></span>
                  <span id="itemResult"></span>
                </div>
              </div>
            </article>
          </section>
        </section>

        <section class="page-view" data-page="prompts">
          <article class="panel prompt-panel">
            <div class="panel-head">
              <div>
                <h2>全局回复策略 / 提示词</h2>
                <span>这里控制所有商品的默认话术边界</span>
              </div>
              <div class="prompt-actions">
                <select id="promptSelect"></select>
                <button id="reloadPromptBtn" type="button">重新读取</button>
                <button id="savePromptBtn" type="button">保存并重启 Agent</button>
              </div>
            </div>
            <div class="panel-body">
              <p class="hint">商品专属策略优先级更高；这里适合放通用禁说规则、分类规则和默认客服口径。</p>
              <textarea id="promptEditor" class="prompt-editor" spellcheck="false" placeholder="读取中..."></textarea>
              <div class="form-actions">
                <span id="promptMeta"></span>
                <span id="promptResult"></span>
              </div>
            </div>
          </article>
        </section>

        <section class="page-view" data-page="messages">
          <article class="panel">
            <div class="panel-head">
              <h2>最近对话</h2>
              <span id="messageCount">0 条</span>
            </div>
            <div id="messages" class="messages"></div>
          </article>
        </section>

        <section class="page-view" data-page="logs">
          <article class="panel logs-panel">
            <div class="panel-head">
              <h2>运行日志</h2>
              <select id="logSelect">
                <option value="agent">Agent 错误/状态</option>
                <option value="agent_output">Agent 输出</option>
                <option value="agent_launch">Agent 启动</option>
                <option value="ollama">Ollama</option>
                <option value="dashboard">看板</option>
              </select>
            </div>
            <pre id="logs">加载中...</pre>
          </article>
        </section>

        <section class="page-view" data-page="settings">
          <section class="overview-grid">
            <article class="panel">
              <div class="panel-head">
                <h2>Cookie 更新</h2>
                <span id="cookieState">读取中</span>
              </div>
              <div class="panel-body">
                <textarea id="cookieInput" spellcheck="false" placeholder="粘贴闲鱼网页版请求里的完整 Cookie 字符串"></textarea>
                <div class="form-actions">
                  <button id="saveCookieBtn" type="button">保存 Cookie 并重启 Agent</button>
                  <button id="syncCookieBtn" type="button">从浏览器同步 Cookie</button>
                  <span id="cookieResult"></span>
                </div>
                <p class="hint" id="cookieSyncState">浏览器自动同步：读取中</p>
              </div>
            </article>
            <article class="panel">
              <div class="panel-head">
                <h2>运行设置</h2>
                <span>本地托管</span>
              </div>
              <div class="panel-body status-list">
                <div><span>模型</span><strong id="settingsModel">-</strong></div>
                <div><span>接口</span><code id="settingsModelBase">-</code></div>
                <div><span>Cookie</span><strong id="settingsCookie">-</strong></div>
              </div>
            </article>
          </section>
        </section>
      </main>
    </div>
  </div>
  <script>{JS}</script>
</body>
</html>"""


CSS = """
:root {
  color-scheme: light;
  --bg: #f7f6f0;
  --surface: #ffffff;
  --surface-2: #f1f5f4;
  --text: #202523;
  --muted: #6b706c;
  --border: #ddd9c8;
  --accent: #f6c343;
  --accent-2: #147d64;
  --danger: #b42318;
  --warn: #b76e00;
  --shadow: 0 18px 42px rgba(36, 34, 21, 0.08);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: linear-gradient(180deg, #fff8d7 0, var(--bg) 220px);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
}
.shell { width: min(1240px, calc(100vw - 32px)); margin: 0 auto; padding: 24px 0 34px; }
.topbar, .status-band, .panel, .metric, .auth-box {
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
  border-radius: 8px;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 20px;
}
h1, h2, p { margin: 0; }
h1 { font-size: 24px; line-height: 1.2; font-weight: 750; letter-spacing: 0; }
h2 { font-size: 15px; line-height: 1.2; font-weight: 750; letter-spacing: 0; }
p, .panel-head span, .metric span, #updatedAt, #cookieResult { color: var(--muted); font-size: 13px; line-height: 1.45; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
button, select, input {
  height: 36px;
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text);
  border-radius: 6px;
  padding: 0 12px;
  font-size: 13px;
  font-weight: 650;
}
button { cursor: pointer; }
button:hover { border-color: #d6a700; color: #7a5600; }
button.danger { color: var(--danger); }
.hidden { display: none; }
.auth-box { margin-top: 14px; padding: 16px; }
.inline-form { display: flex; gap: 8px; margin-top: 10px; }
.inline-form input { min-width: 320px; }
.status-band {
  margin-top: 14px;
  padding: 15px 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.status-band > div { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.dot {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: var(--warn);
  box-shadow: 0 0 0 4px rgba(183, 110, 0, 0.14);
}
.dot.running { background: var(--accent-2); box-shadow: 0 0 0 4px rgba(20, 125, 100, 0.14); }
.dot.blocked { background: var(--danger); box-shadow: 0 0 0 4px rgba(180, 35, 24, 0.12); }
code {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 5px 7px;
  color: #29413d;
  font-size: 12px;
  word-break: break-all;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.metric { padding: 15px; min-height: 86px; }
.metric strong { display: block; font-size: 23px; line-height: 1.15; margin-top: 8px; letter-spacing: 0; }
.config-grid, .grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 0.82fr);
  gap: 14px;
  margin-top: 14px;
}
.panel { overflow: hidden; min-width: 0; }
.panel-head {
  min-height: 50px;
  padding: 14px 15px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.panel-body { padding: 15px; }
textarea {
  width: 100%;
  min-height: 122px;
  resize: vertical;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 11px;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.form-actions { display: flex; align-items: center; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.hint { margin-bottom: 10px; }
.prompt-panel { margin-top: 14px; }
.prompt-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
.prompt-editor {
  min-height: 260px;
  font-size: 13px;
  line-height: 1.55;
}
#promptMeta, #promptResult { color: var(--muted); font-size: 13px; line-height: 1.45; }
.item-panel { margin-top: 14px; }
.item-summary {
  padding: 10px 11px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface-2);
  font-size: 13px;
  line-height: 1.55;
  color: var(--text);
  word-break: break-word;
}
.check-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  color: var(--muted);
  font-size: 13px;
}
.check-row input { width: 16px; height: 16px; }
.item-edit-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.86fr) minmax(0, 1.14fr);
  gap: 12px;
  margin-top: 12px;
}
.item-edit-grid label span {
  display: block;
  margin-bottom: 7px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.35;
}
.product-editor { min-height: 170px; }
#itemMeta, #itemResult { color: var(--muted); font-size: 13px; line-height: 1.45; }
.model-box { display: grid; gap: 10px; }
.model-box > div { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
pre {
  margin: 0;
  min-height: 540px;
  max-height: 66vh;
  overflow: auto;
  padding: 15px;
  background: #17211f;
  color: #d8ebe7;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}
.model-box pre { min-height: 86px; max-height: 160px; background: #22251f; }
.messages { max-height: 66vh; overflow: auto; padding: 8px 0; }
.message { padding: 12px 15px; border-bottom: 1px solid var(--border); }
.message:last-child { border-bottom: 0; }
.meta { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; color: var(--muted); font-size: 12px; line-height: 1.45; }
.role { color: #fff; background: #5d625b; border-radius: 5px; padding: 2px 6px; font-weight: 700; }
.role.assistant { background: var(--accent-2); }
.content { margin-top: 7px; font-size: 14px; line-height: 1.55; word-break: break-word; }
.empty { padding: 24px 15px; color: var(--muted); font-size: 14px; }
@media (max-width: 920px) {
  .shell { width: min(100vw - 20px, 1240px); padding-top: 10px; }
  .topbar, .status-band { align-items: flex-start; flex-direction: column; }
  .actions { justify-content: flex-start; }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .config-grid, .grid { grid-template-columns: 1fr; }
  .item-edit-grid { grid-template-columns: 1fr; }
  pre { min-height: 360px; }
  .inline-form { flex-direction: column; }
  .inline-form input { min-width: 0; width: 100%; }
}

body {
  background: #f3f5f7;
  color: #111827;
}
.app-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 236px minmax(0, 1fr);
}
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 18px 14px;
  background: #151923;
  color: #f9fafb;
  border-right: 1px solid #0f121a;
}
.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 6px 8px 16px;
  border-bottom: 1px solid rgba(255,255,255,0.09);
}
.brand-mark {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: #ffd84d;
  color: #171717;
  font-weight: 850;
}
.brand strong { display: block; font-size: 15px; line-height: 1.2; }
.brand span { display: block; margin-top: 3px; color: #9ca3af; font-size: 12px; line-height: 1.2; }
.side-nav { display: grid; gap: 4px; }
.nav-item {
  width: 100%;
  height: 40px;
  padding: 0 11px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #cfd4dc;
  text-align: left;
  font-size: 13px;
  font-weight: 700;
}
.nav-item:hover { background: rgba(255,255,255,0.07); color: #ffffff; }
.nav-item.active { background: #ffffff; color: #111827; }
.sidebar-foot {
  margin-top: auto;
  padding: 12px;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 8px;
  background: rgba(255,255,255,0.05);
}
.sidebar-foot span { display: block; color: #9ca3af; font-size: 12px; }
.sidebar-foot strong {
  display: block;
  margin-top: 5px;
  color: #f9fafb;
  font-size: 13px;
  word-break: break-all;
}
.main-area { min-width: 0; }
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: grid;
  grid-template-columns: minmax(240px, 1fr) auto auto;
  align-items: center;
  gap: 16px;
  padding: 14px 22px;
  border: 0;
  border-bottom: 1px solid #dfe3e8;
  border-radius: 0;
  box-shadow: none;
  background: rgba(255,255,255,0.95);
  backdrop-filter: blur(10px);
}
h1 { font-size: 22px; font-weight: 780; color: #111827; }
h2 { font-size: 15px; color: #111827; }
.topbar p { margin-top: 4px; color: #6b7280; font-size: 13px; }
.top-status {
  min-height: 36px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f9fafb;
  white-space: nowrap;
}
.top-status strong { font-size: 13px; }
.top-status span { color: #6b7280; font-size: 12px; }
.actions { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: nowrap; }
button, select, input {
  border-color: #d6dbe1;
  border-radius: 7px;
  color: #111827;
  background: #ffffff;
}
button:hover { border-color: #111827; color: #111827; }
button.danger { color: #b42318; border-color: #f0c4bf; background: #fffafa; }
.content {
  width: min(100%, 1440px);
  margin: 0 auto;
  padding: 20px 22px 34px;
}
.page-view { display: none; }
.page-view.active { display: block; }
.panel, .metric, .auth-box {
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  box-shadow: 0 12px 30px rgba(17, 24, 39, 0.05);
  background: #ffffff;
}
.panel-head {
  padding: 14px 16px;
  border-bottom: 1px solid #e8ebef;
}
.panel-head span { color: #6b7280; font-size: 12px; }
.panel-body { padding: 16px; }
.metrics {
  margin-top: 0;
  grid-template-columns: repeat(5, minmax(130px, 1fr));
}
.metric { min-height: 92px; padding: 16px; }
.metric span { color: #6b7280; font-size: 12px; }
.metric strong { color: #111827; font-size: 24px; }
.overview-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(420px, 1.1fr);
  gap: 14px;
  margin-top: 14px;
}
.status-list { display: grid; gap: 10px; }
.status-list > div {
  min-height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid #edf0f3;
}
.status-list > div:last-child { border-bottom: 0; padding-bottom: 0; }
.status-list span { color: #6b7280; font-size: 13px; }
.status-list strong { font-size: 13px; }
.workspace.two-column {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}
.item-list-panel { max-height: calc(100vh - 116px); display: flex; flex-direction: column; }
.list-toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 12px;
  border-bottom: 1px solid #e8ebef;
}
.list-toolbar input { width: 100%; }
.list-toolbar span { color: #6b7280; font-size: 12px; white-space: nowrap; }
.hidden-select {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
.item-list {
  overflow: auto;
  padding: 8px;
  display: grid;
  gap: 8px;
}
.item-card {
  width: 100%;
  min-height: 94px;
  padding: 11px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #ffffff;
  text-align: left;
  cursor: pointer;
}
.item-card:hover { border-color: #c3cad3; background: #fafafa; }
.item-card.active { border-color: #111827; background: #f7f8fa; }
.item-card-title {
  color: #111827;
  font-size: 13px;
  line-height: 1.35;
  font-weight: 750;
  word-break: break-word;
}
.item-card-meta {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 8px;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.3;
}
.tag {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 7px;
  border-radius: 999px;
  background: #eef2f7;
  color: #4b5563;
  font-size: 12px;
  font-weight: 700;
}
.tag.configured { background: #e7f7ee; color: #0f7a45; }
.tag.empty { background: #fff7d6; color: #8a6100; }
.item-detail-panel { min-height: calc(100vh - 116px); }
.item-summary, .strategy-banner {
  border-color: #e2e7ee;
  background: #f8fafc;
}
.strategy-banner {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  padding: 10px 11px;
  border: 1px solid #e2e7ee;
  border-radius: 7px;
}
.strategy-banner strong { font-size: 13px; color: #111827; }
.strategy-banner span { color: #6b7280; font-size: 12px; word-break: break-all; }
.item-edit-grid { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
textarea {
  border-color: #d6dbe1;
  border-radius: 7px;
  background: #ffffff;
  color: #111827;
  font-size: 12px;
}
textarea:focus, input:focus, select:focus {
  outline: 2px solid rgba(255,216,77,0.45);
  border-color: #c99b00;
}
.prompt-panel { margin-top: 0; }
.prompt-editor { min-height: calc(100vh - 260px); }
pre {
  min-height: calc(100vh - 180px);
  max-height: calc(100vh - 180px);
  background: #111827;
  color: #d1fae5;
  border-radius: 0;
}
.model-box pre { min-height: 96px; max-height: 160px; border-radius: 7px; }
.messages {
  max-height: calc(100vh - 142px);
  padding: 0;
}
.message { padding: 14px 16px; }
.auth-box {
  position: fixed;
  z-index: 50;
  top: 72px;
  right: 22px;
  width: min(460px, calc(100vw - 44px));
  padding: 16px;
}
@media (max-width: 1100px) {
  .app-shell { grid-template-columns: 72px minmax(0, 1fr); }
  .brand div:not(.brand-mark), .sidebar-foot, .nav-item { font-size: 0; }
  .nav-item::first-letter { font-size: 13px; }
  .topbar { grid-template-columns: 1fr; align-items: stretch; }
  .actions, .top-status { justify-content: flex-start; flex-wrap: wrap; }
  .workspace.two-column, .overview-grid { grid-template-columns: 1fr; }
  .item-list-panel, .item-detail-panel { min-height: 0; max-height: none; }
}
@media (max-width: 760px) {
  .app-shell { display: block; }
  .sidebar {
    position: static;
    height: auto;
    flex-direction: row;
    overflow-x: auto;
    padding: 10px;
  }
  .brand, .sidebar-foot { display: none; }
  .side-nav { display: flex; gap: 6px; }
  .nav-item { width: auto; min-width: 78px; text-align: center; font-size: 13px; }
  .content { padding: 12px; }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .item-edit-grid { grid-template-columns: 1fr; }
}
"""


JS = """
const $ = (id) => document.getElementById(id);
let lastData = null;
let prompts = [];
let activePromptId = "";
let items = [];
let activeItemId = "";
let activeView = "overview";

const pageMeta = {
  overview: ["总览", "运行状态、消息量和托管服务概况"],
  items: ["商品策略", "每个商品独立设置发货内容和专属提示词"],
  prompts: ["全局提示词", "所有商品共用的默认客服规则"],
  messages: ["最近对话", "查看近期买家消息和自动回复"],
  logs: ["运行日志", "排查 Cookie、模型和 Agent 运行状态"],
  settings: ["设置", "Cookie、模型接口和托管配置"]
};

function fmtBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function cookieStateLabel(config) {
  const state = config.COOKIES_STR || "未设置";
  if (!config.COOKIE_LENGTH) return state;
  const x5sec = config.COOKIE_HAS_X5SEC ? "已含 x5sec" : "缺 x5sec";
  return `${state} · ${config.COOKIE_LENGTH} 字符 · ${x5sec}`;
}

function switchView(view) {
  activeView = view;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".page-view").forEach((page) => {
    page.classList.toggle("active", page.dataset.page === view);
  });
  const meta = pageMeta[view] || pageMeta.overview;
  setText("pageTitle", meta[0]);
  setText("pageSubtitle", meta[1]);
}

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(path, Object.assign({}, options, { headers }));
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function statusLabel(service) {
  if (service.needs_cookie) return "需要更新 Cookie / 过风控";
  if (service.running) return "Agent 运行中";
  return "Agent 未运行";
}

function renderLogs() {
  if (!lastData) return;
  const selected = $("logSelect").value;
  $("logs").textContent = (lastData.logs[selected] || []).join("\\n") || "暂无日志";
}

function renderPromptEditor() {
  const current = prompts.find((prompt) => prompt.id === activePromptId) || prompts[0];
  if (!current) return;
  activePromptId = current.id;
  $("promptSelect").value = current.id;
  $("promptEditor").value = current.text || "";
  $("promptMeta").textContent = `${current.filename} · ${current.length || 0} 字符`;
}

async function loadPrompts() {
  const data = await api("/api/prompts");
  prompts = data.prompts || [];
  $("promptSelect").innerHTML = prompts.map((prompt) => `<option value="${prompt.id}">${prompt.label}</option>`).join("");
  if (!activePromptId && prompts[0]) activePromptId = prompts[0].id;
  renderPromptEditor();
}

function filteredItems() {
  const keyword = ($("itemSearch")?.value || "").trim().toLowerCase();
  if (!keyword) return items;
  return items.filter((item) => {
    return `${item.item_id || ""} ${item.title || ""} ${item.description || ""}`.toLowerCase().includes(keyword);
  });
}

function renderItemList() {
  const visibleItems = filteredItems();
  setText("itemCountText", `${visibleItems.length} 个商品`);
  if (!visibleItems.length) {
    $("itemList").innerHTML = `<div class="empty">没有匹配的商品。系统会在买家咨询后自动缓存商品，也可以后续扩展主动同步。</div>`;
    return;
  }
  $("itemList").innerHTML = visibleItems.map((item) => {
    const title = item.title || `商品 ${item.item_id}`;
    const price = item.price === null || item.price === undefined ? "价格未知" : `¥${item.price}`;
    const configuredClass = item.configured ? "configured" : "empty";
    const configuredText = item.configured ? "已配置" : "未配置";
    const activeClass = item.item_id === activeItemId ? "active" : "";
    return `
      <button class="item-card ${activeClass}" type="button" data-item-id="${escapeHtml(item.item_id)}">
        <div class="item-card-title">${escapeHtml(title)}</div>
        <div class="item-card-meta">
          <span>${escapeHtml(price)}</span>
          <span>ID ${escapeHtml(item.item_id)}</span>
          <span>咨询 ${item.message_count || 0}</span>
          <span class="tag ${configuredClass}">${configuredText}</span>
        </div>
      </button>
    `;
  }).join("");
}

function renderItemEditor() {
  const current = items.find((item) => item.item_id === activeItemId) || items[0];
  if (!current) {
    $("itemSelect").innerHTML = `<option value="">暂无商品</option>`;
    $("itemSummary").textContent = "还没有读取到商品。系统会在买家咨询某个商品后，自动读取并缓存该商品信息。";
    $("itemDeliveryEditor").value = "";
    $("itemPromptEditor").value = "";
    $("itemEnabled").checked = true;
    $("itemMeta").textContent = "";
    setText("itemDetailTitle", "商品专属策略");
    setText("itemDetailSub", "选择一个商品后编辑它自己的提示词");
    setText("itemScopeText", "-");
    renderItemList();
    return;
  }
  activeItemId = current.item_id;
  $("itemSelect").value = current.item_id;
  $("itemEnabled").checked = Boolean(current.enabled);
  $("itemDeliveryEditor").value = current.delivery_text || "";
  $("itemPromptEditor").value = current.custom_prompt || "";
  const title = current.title || "未读取到标题";
  const price = current.price === null || current.price === undefined ? "价格未知" : `¥${current.price}`;
  const messageCount = current.message_count || 0;
  const preview = current.description_preview ? ` · ${current.description_preview}` : "";
  $("itemSummary").textContent = `${title} · ${price} · 商品ID ${current.item_id} · 咨询 ${messageCount} 条${preview}`;
  $("itemDetailTitle").textContent = title;
  $("itemDetailSub").textContent = `${price} · 商品 ID ${current.item_id}`;
  $("itemScopeText").textContent = `只保存到 ${current.item_id}`;
  $("itemMeta").textContent = `商品更新 ${current.item_updated_at || "-"} · 策略更新 ${current.profile_updated_at || "-"}`;
  renderItemList();
}

async function loadItems() {
  const data = await api("/api/items");
  items = data.items || [];
  $("itemSelect").innerHTML = items.length
    ? items.map((item) => `<option value="${item.item_id}">${item.title || item.item_id}</option>`).join("")
    : `<option value="">暂无商品</option>`;
  if (!activeItemId && items[0]) activeItemId = items[0].item_id;
  if (activeItemId && !items.some((item) => item.item_id === activeItemId) && items[0]) activeItemId = items[0].item_id;
  renderItemEditor();
}

function render(data) {
  lastData = data;
  const service = data.service || {};
  const running = service.running && !service.needs_cookie;
  $("statusDot").className = `dot ${running ? "running" : ""} ${service.needs_cookie ? "blocked" : ""}`;
  $("statusText").textContent = statusLabel(service);
  $("updatedAt").textContent = `更新于 ${data.generated_at}`;
  $("pidText").textContent = `PID ${(service.pids || []).join(", ") || "-"}`;
  $("cookieState").textContent = cookieStateLabel(data.config);
  $("modelName").textContent = data.config.MODEL_NAME || "-";
  setText("sidebarModelName", data.config.MODEL_NAME || "-");
  $("modelBase").textContent = data.config.MODEL_BASE_URL || "-";
  $("ollamaStatus").textContent = data.ollama.running ? `运行中 · ${data.ollama.version}` : `未运行`;
  setText("overviewAgent", statusLabel(service));
  setText("overviewCookie", cookieStateLabel(data.config));
  setText("overviewOllama", data.ollama.running ? `运行中 · ${data.ollama.version}` : "未运行");
  setText("overviewDb", fmtBytes(data.stats.db_size_bytes ?? 0));
  setText("settingsModel", data.config.MODEL_NAME || "-");
  setText("settingsModelBase", data.config.MODEL_BASE_URL || "-");
  setText("settingsCookie", cookieStateLabel(data.config));
  const cookieSync = data.cookie_sync || {};
  const syncResult = cookieSync.last_result || {};
  const syncText = cookieSync.enabled
    ? `已开启 · 每 ${Math.round((cookieSync.interval_seconds || 1800) / 60)} 分钟 · ${syncResult.message || "等待首次同步"}`
    : "未开启";
  setText("cookieSyncState", `浏览器自动同步：${syncText}`);
  $("metrics").innerHTML = [
    ["Agent", service.needs_cookie ? "需处理" : (service.running ? "运行" : "停止")],
    ["Ollama", data.ollama.running ? "运行" : "停止"],
    ["聊天记录", data.stats.message_count ?? 0],
    ["商品缓存", data.stats.item_count ?? 0],
    ["数据库", fmtBytes(data.stats.db_size_bytes ?? 0)]
  ].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join("");
  renderLogs();
  $("messageCount").textContent = `${(data.messages || []).length} 条`;
  $("messages").innerHTML = (data.messages || []).length ? data.messages.map((message) => `
    <div class="message">
      <div class="meta">
        <span class="role ${message.role === "assistant" ? "assistant" : ""}">${message.role || "-"}</span>
        <span>${message.timestamp || ""}</span>
        <span>会话 ${message.chat_id || "-"}</span>
        <span>商品 ${message.item_id || "-"}</span>
      </div>
      <div class="content"></div>
    </div>
  `).join("") : `<div class="empty">还没有聊天记录</div>`;
  Array.from(document.querySelectorAll(".message .content")).forEach((node, index) => {
    node.textContent = data.messages[index].content || "";
  });
}

async function refresh() {
  try {
    render(await api("/api/status"));
  } catch (error) {
    $("statusText").textContent = `读取失败：${error.message}`;
  }
}

async function postAction(path, payload) {
  const result = await api(path, {
    method: "POST",
    body: payload ? JSON.stringify(payload) : undefined
  });
  await new Promise((resolve) => setTimeout(resolve, 900));
  await refresh();
  if (!result.ok) alert(result.message || result.stderr || result.stdout || "操作失败");
  return result;
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view || "overview"));
});
$("refreshBtn").addEventListener("click", refresh);
$("logSelect").addEventListener("change", renderLogs);
$("promptSelect").addEventListener("change", () => {
  activePromptId = $("promptSelect").value;
  renderPromptEditor();
});
$("itemSelect").addEventListener("change", () => {
  activeItemId = $("itemSelect").value;
  renderItemEditor();
});
$("itemSearch").addEventListener("input", renderItemList);
$("itemList").addEventListener("click", (event) => {
  const card = event.target.closest(".item-card");
  if (!card) return;
  activeItemId = card.dataset.itemId;
  renderItemEditor();
});
$("reloadPromptBtn").addEventListener("click", async () => {
  $("promptResult").textContent = "读取中...";
  try {
    await loadPrompts();
    $("promptResult").textContent = "已重新读取";
  } catch (error) {
    $("promptResult").textContent = error.message;
  }
});
$("reloadItemsBtn").addEventListener("click", async () => {
  $("itemResult").textContent = "读取中...";
  try {
    await loadItems();
    $("itemResult").textContent = "已刷新商品";
  } catch (error) {
    $("itemResult").textContent = error.message;
  }
});
$("savePromptBtn").addEventListener("click", async () => {
  $("promptResult").textContent = "保存中...";
  try {
    const result = await postAction("/api/prompts", {
      id: activePromptId,
      text: $("promptEditor").value
    });
    $("promptResult").textContent = result.ok ? "已保存，并已重启 Agent" : (result.message || "保存失败");
    await loadPrompts();
  } catch (error) {
    $("promptResult").textContent = error.message;
  }
});
$("saveItemBtn").addEventListener("click", async () => {
  if (!activeItemId) {
    $("itemResult").textContent = "暂无可保存的商品";
    return;
  }
  $("itemResult").textContent = "保存中...";
  try {
    const result = await postAction("/api/items", {
      item_id: activeItemId,
      enabled: $("itemEnabled").checked,
      delivery_text: $("itemDeliveryEditor").value,
      custom_prompt: $("itemPromptEditor").value
    });
    $("itemResult").textContent = result.ok ? "已保存，下一次回复立即生效" : (result.message || "保存失败");
    await loadItems();
  } catch (error) {
    $("itemResult").textContent = error.message;
  }
});
$("restartAgentBtn").addEventListener("click", () => postAction("/api/restart-agent"));
$("restartOllamaBtn").addEventListener("click", () => postAction("/api/restart-ollama"));
$("stopAgentBtn").addEventListener("click", () => {
  if (confirm("停止后自动回复会暂停。确认停止 Agent？")) postAction("/api/stop-agent");
});
$("testModelBtn").addEventListener("click", async () => {
  $("modelResult").textContent = "测试中...";
  try {
    const result = await postAction("/api/test-model", { prompt: "Reply with exactly: OK" });
    $("modelResult").textContent = result.ok ? result.content : (result.error || "测试失败");
  } catch (error) {
    $("modelResult").textContent = error.message;
  }
});
$("saveCookieBtn").addEventListener("click", async () => {
  $("cookieResult").textContent = "保存中...";
  try {
    const cookie = $("cookieInput").value.trim();
    const result = await postAction("/api/cookie", { cookie });
    $("cookieResult").textContent = result.ok ? "已保存，并已重启 Agent" : (result.message || "保存失败");
    if (result.ok) $("cookieInput").value = "";
  } catch (error) {
    $("cookieResult").textContent = error.message;
  }
});
$("syncCookieBtn").addEventListener("click", async () => {
  $("cookieResult").textContent = "正在从浏览器读取...";
  try {
    const result = await postAction("/api/sync-cookie-from-browser");
    $("cookieResult").textContent = result.ok ? (result.message || "已同步") : (result.message || "同步失败");
  } catch (error) {
    $("cookieResult").textContent = error.message;
  }
});

switchView(activeView);
refresh();
loadPrompts().catch((error) => {
  $("promptResult").textContent = error.message;
});
loadItems().catch((error) => {
  $("itemResult").textContent = error.message;
});
setInterval(refresh, 5000);
"""


class MonitorHandler(BaseHTTPRequestHandler):
    server_version = "XianyuDashboard/2.0"

    def do_OPTIONS(self):
        self.write_response(204, b"", "text/plain; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.write_response(200, html_page().encode("utf-8"), "text/html; charset=utf-8")
        elif path.startswith("/extensions/"):
            status, data, content_type = extension_asset_response(path, self.client_address[0])
            self.write_response(status, data, content_type)
        elif path == "/api/status":
            if not self.authorized():
                self.write_json({"error": "unauthorized"}, status=401)
                return
            self.write_json(build_overview())
        elif path == "/api/prompts":
            if not self.authorized():
                self.write_json({"error": "unauthorized"}, status=401)
                return
            self.write_json(prompt_payload())
        elif path == "/api/items":
            if not self.authorized():
                self.write_json({"error": "unauthorized"}, status=401)
                return
            self.write_json(item_profile_payload())
        else:
            self.write_json({"error": "not found"}, status=404)

    def do_POST(self):
        if not self.authorized():
            self.write_json({"error": "unauthorized"}, status=401)
            return
        path = urlparse(self.path).path
        if path == "/api/restart-agent":
            self.write_json(perform_service_action("restart"))
        elif path == "/api/stop-agent":
            self.write_json(perform_service_action("stop"))
        elif path == "/api/restart-ollama":
            self.write_json(perform_service_action("restart_ollama"))
        elif path == "/api/test-model":
            payload = self.read_json_body()
            self.write_json(test_ollama_model(payload.get("prompt") or "Reply with exactly: OK"))
        elif path == "/api/cookie":
            result = update_cookie(self.read_json_body().get("cookie"))
            if result.get("ok"):
                restart_result = restart_service()
                result["restart"] = restart_result
                result["ok"] = restart_result.get("ok", False)
            self.write_json(result)
        elif path == "/api/sync-cookie-from-browser":
            result = sync_cookie_from_browser_once()
            COOKIE_SYNC_STATE["last_run"] = result.get("last_run")
            COOKIE_SYNC_STATE["last_result"] = result
            self.write_json(result)
        elif path == "/api/browser-cookie":
            payload = self.read_json_body()
            result = accept_browser_cookie_sync(payload.get("cookie", ""))
            COOKIE_SYNC_STATE["last_run"] = result.get("last_run")
            COOKIE_SYNC_STATE["last_result"] = result
            self.write_json(result)
        elif path == "/api/prompts":
            payload = self.read_json_body()
            result = update_prompt(payload.get("id"), payload.get("text"))
            if result.get("ok"):
                restart_result = restart_service()
                result["restart"] = restart_result
                result["ok"] = restart_result.get("ok", False)
            self.write_json(result)
        elif path == "/api/items":
            payload = self.read_json_body()
            self.write_json(update_item_profile(
                payload.get("item_id"),
                enabled=payload.get("enabled", True),
                delivery_text=payload.get("delivery_text", ""),
                custom_prompt=payload.get("custom_prompt", ""),
            ))
        else:
            self.write_json({"error": "not found"}, status=404)

    def authorized(self):
        return True

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def log_message(self, format, *args):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{timestamp} {self.client_address[0]} {format % args}", flush=True)

    def write_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.write_response(status, data, "application/json; charset=utf-8")

    def write_response(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-Dashboard-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description="XianyuAutoAgent web dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), MonitorHandler)
    start_cookie_sync_worker()
    print(f"Xianyu dashboard listening on http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
