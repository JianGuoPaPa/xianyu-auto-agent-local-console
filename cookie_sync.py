import asyncio
import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

import websockets


DEFAULT_DEBUG_URL = "http://127.0.0.1:9222"
DEFAULT_COOKIE_DOMAINS = (
    "goofish.com",
    "taobao.com",
    "tmall.com",
    "alicdn.com",
)
REQUIRED_COOKIE_NAMES = ("unb", "_m_h5_tk")


def _domain_matches(domain, allowed_domains):
    normalized = str(domain or "").lstrip(".").lower()
    return any(normalized == allowed or normalized.endswith(f".{allowed}") for allowed in allowed_domains)


def build_cookie_header(cookies, allowed_domains=DEFAULT_COOKIE_DOMAINS):
    cookie_pairs = {}
    for cookie in cookies or []:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        domain = cookie.get("domain")
        if not name or not value or not _domain_matches(domain, allowed_domains):
            continue
        cookie_pairs[name] = value
    return "; ".join(f"{name}={value}" for name, value in cookie_pairs.items())


def _cookie_names(cookie_header):
    names = set()
    for part in str(cookie_header or "").split(";"):
        if "=" not in part:
            continue
        name, _ = part.split("=", 1)
        if name.strip():
            names.add(name.strip())
    return names


def _cookie_pairs(cookie_header):
    pairs = {}
    for part in str(cookie_header or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and value:
            pairs[name] = value
    return pairs


def merge_cookie_headers(current_cookie, candidate_cookie):
    pairs = _cookie_pairs(current_cookie)
    pairs.update(_cookie_pairs(candidate_cookie))
    return "; ".join(f"{name}={value}" for name, value in pairs.items())


def cookie_is_usable(cookie_header, required_names=REQUIRED_COOKIE_NAMES):
    names = _cookie_names(cookie_header)
    return all(name in names for name in required_names)


def should_replace_cookie(current_cookie, candidate_cookie):
    candidate = (candidate_cookie or "").strip()
    if not cookie_is_usable(candidate):
        return False
    current = (current_cookie or "").strip()
    if candidate == current:
        return False

    current_names = _cookie_names(current)
    candidate_names = _cookie_names(candidate)
    if "x5sec" in current_names and "x5sec" not in candidate_names:
        return False

    return True


def _extract_chrome_flag(command_line, flag_name):
    pattern = rf"--{re.escape(flag_name)}(?:=|\s+)(?:\"([^\"]+)\"|([^\s]+))"
    match = re.search(pattern, command_line or "")
    if not match:
        return None
    return match.group(1) or match.group(2)


def extract_chrome_profile_from_command_line(command_line):
    return {
        "user_data_dir": _extract_chrome_flag(command_line, "user-data-dir"),
        "profile_directory": _extract_chrome_flag(command_line, "profile-directory"),
    }


def _default_chrome_user_data_dir():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return Path(local_app_data) / "Google" / "Chrome" / "User Data"


def _profile_cookie_db(profile_path):
    profile_path = Path(profile_path)
    candidates = (
        profile_path / "Network" / "Cookies",
        profile_path / "Cookies",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def discover_chrome_profile_paths(user_data_dirs=None):
    paths = []
    env_paths = os.environ.get("CHROME_COOKIE_PROFILE_PATHS")
    if env_paths:
        paths.extend(Path(path.strip()) for path in env_paths.split(";") if path.strip())

    base_dirs = []
    if user_data_dirs:
        base_dirs.extend(Path(path) for path in user_data_dirs)
    env_user_data = os.environ.get("CHROME_USER_DATA_DIR")
    if env_user_data:
        base_dirs.append(Path(env_user_data))
    default_dir = _default_chrome_user_data_dir()
    if default_dir:
        base_dirs.append(default_dir)

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue
        for child in base_dir.iterdir():
            if not child.is_dir():
                continue
            if child.name == "Default" or child.name.startswith("Profile "):
                paths.append(child)

    unique = []
    seen = set()
    for path in paths:
        resolved = str(path)
        if resolved in seen or not _profile_cookie_db(path):
            continue
        seen.add(resolved)
        unique.append(Path(path))

    return sorted(unique, key=lambda path: (_profile_cookie_db(path).stat().st_mtime, str(path).lower()))


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _crypt_unprotect_data(data):
    if os.name != "nt":
        raise RuntimeError("DPAPI 只能在 Windows 上解密 Chrome Cookie")

    in_buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    blob_out = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise ctypes.WinError()

    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _chrome_master_key(user_data_dir):
    local_state_path = Path(user_data_dir) / "Local State"
    state = json.loads(local_state_path.read_text(encoding="utf-8", errors="replace"))
    encrypted_key = base64.b64decode(state["os_crypt"]["encrypted_key"])
    if encrypted_key.startswith(b"DPAPI"):
        encrypted_key = encrypted_key[5:]
    return _crypt_unprotect_data(encrypted_key)


class BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("dwInfoVersion", ctypes.c_ulong),
        ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbNonce", ctypes.c_ulong),
        ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbAuthData", ctypes.c_ulong),
        ("pbTag", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbTag", ctypes.c_ulong),
        ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbMacContext", ctypes.c_ulong),
        ("cbAAD", ctypes.c_ulong),
        ("cbData", ctypes.c_ulonglong),
        ("dwFlags", ctypes.c_ulong),
    ]


def _nt_success(status):
    return status >= 0


def _bcrypt_property_ulong(handle, prop_name):
    bcrypt = ctypes.windll.bcrypt
    output = ctypes.c_ulong()
    result_size = ctypes.c_ulong()
    status = bcrypt.BCryptGetProperty(
        handle,
        ctypes.c_wchar_p(prop_name),
        ctypes.byref(output),
        ctypes.sizeof(output),
        ctypes.byref(result_size),
        0,
    )
    if not _nt_success(status):
        raise OSError(f"BCryptGetProperty failed: 0x{status & 0xffffffff:08x}")
    return output.value


def _aes_gcm_decrypt(key, nonce, ciphertext, tag):
    if os.name != "nt":
        raise RuntimeError("AES-GCM Cookie 解密只能在 Windows 上执行")

    bcrypt = ctypes.windll.bcrypt
    algorithm = ctypes.c_void_p()
    key_handle = ctypes.c_void_p()
    object_buffer = None
    try:
        status = bcrypt.BCryptOpenAlgorithmProvider(
            ctypes.byref(algorithm),
            ctypes.c_wchar_p("AES"),
            None,
            0,
        )
        if not _nt_success(status):
            raise OSError(f"BCryptOpenAlgorithmProvider failed: 0x{status & 0xffffffff:08x}")

        mode = ctypes.create_unicode_buffer("ChainingModeGCM")
        status = bcrypt.BCryptSetProperty(
            algorithm,
            ctypes.c_wchar_p("ChainingMode"),
            ctypes.cast(mode, ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.sizeof(mode),
            0,
        )
        if not _nt_success(status):
            raise OSError(f"BCryptSetProperty failed: 0x{status & 0xffffffff:08x}")

        object_length = _bcrypt_property_ulong(algorithm, "ObjectLength")
        object_buffer = ctypes.create_string_buffer(object_length)
        key_buffer = ctypes.create_string_buffer(key, len(key))
        status = bcrypt.BCryptGenerateSymmetricKey(
            algorithm,
            ctypes.byref(key_handle),
            ctypes.cast(object_buffer, ctypes.POINTER(ctypes.c_ubyte)),
            object_length,
            ctypes.cast(key_buffer, ctypes.POINTER(ctypes.c_ubyte)),
            len(key),
            0,
        )
        if not _nt_success(status):
            raise OSError(f"BCryptGenerateSymmetricKey failed: 0x{status & 0xffffffff:08x}")

        nonce_buffer = ctypes.create_string_buffer(nonce, len(nonce))
        tag_buffer = ctypes.create_string_buffer(tag, len(tag))
        input_buffer = ctypes.create_string_buffer(ciphertext, len(ciphertext))
        output_buffer = ctypes.create_string_buffer(len(ciphertext))
        result_size = ctypes.c_ulong()
        auth_info = BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO()
        auth_info.cbSize = ctypes.sizeof(BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO)
        auth_info.dwInfoVersion = 1
        auth_info.pbNonce = ctypes.cast(nonce_buffer, ctypes.POINTER(ctypes.c_ubyte))
        auth_info.cbNonce = len(nonce)
        auth_info.pbTag = ctypes.cast(tag_buffer, ctypes.POINTER(ctypes.c_ubyte))
        auth_info.cbTag = len(tag)

        status = bcrypt.BCryptDecrypt(
            key_handle,
            ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
            len(ciphertext),
            ctypes.byref(auth_info),
            None,
            0,
            ctypes.cast(output_buffer, ctypes.POINTER(ctypes.c_ubyte)),
            len(ciphertext),
            ctypes.byref(result_size),
            0,
        )
        if not _nt_success(status):
            raise OSError(f"BCryptDecrypt failed: 0x{status & 0xffffffff:08x}")
        return output_buffer.raw[: result_size.value]
    finally:
        if key_handle:
            bcrypt.BCryptDestroyKey(key_handle)
        if algorithm:
            bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)


def decrypt_chrome_cookie_value(value, encrypted_value, host_key="", master_key=None):
    if value:
        return value
    encrypted_value = bytes(encrypted_value or b"")
    if not encrypted_value:
        return ""

    if encrypted_value.startswith((b"v10", b"v11")):
        if not master_key:
            raise RuntimeError("缺少 Chrome AES-GCM 主密钥")
        nonce = encrypted_value[3:15]
        payload = encrypted_value[15:]
        ciphertext, tag = payload[:-16], payload[-16:]
        decrypted = _aes_gcm_decrypt(master_key, nonce, ciphertext, tag)
        host_digest = hashlib.sha256(str(host_key or "").encode("utf-8")).digest()
        if decrypted.startswith(host_digest):
            decrypted = decrypted[32:]
        return decrypted.decode("utf-8", errors="replace")

    if encrypted_value.startswith(b"v20"):
        raise RuntimeError("Chrome Cookie 使用 v20 App-Bound 加密，当前进程不能直接解密")

    return _crypt_unprotect_data(encrypted_value).decode("utf-8", errors="replace")


def _copy_cookie_db(cookie_db_path):
    fd, temp_path = tempfile.mkstemp(prefix="xianyu_chrome_cookies_", suffix=".sqlite")
    os.close(fd)
    shutil.copy2(cookie_db_path, temp_path)
    return temp_path


def _open_cookie_db(cookie_db_path):
    try:
        temp_db = _copy_cookie_db(cookie_db_path)
        return sqlite3.connect(temp_db), temp_db
    except OSError:
        uri_path = quote(Path(cookie_db_path).resolve().as_posix(), safe="/:")
        return sqlite3.connect(f"file:{uri_path}?mode=ro&immutable=1", uri=True), None


def fetch_chrome_cookie_header_from_db(profile_paths=None, user_data_dir=None):
    if os.name != "nt":
        raise RuntimeError("直接读取 Chrome Cookie 数据库只支持 Windows")

    if profile_paths is None:
        user_data_dirs = [user_data_dir] if user_data_dir else None
        profile_paths = discover_chrome_profile_paths(user_data_dirs=user_data_dirs)
    if not profile_paths:
        raise RuntimeError("没有找到 Chrome profile Cookie 数据库")

    cookies = []
    errors = []
    for profile_path in profile_paths:
        profile_path = Path(profile_path)
        cookie_db = _profile_cookie_db(profile_path)
        if not cookie_db:
            continue
        try:
            master_key = _chrome_master_key(profile_path.parent)
            conn = None
            temp_db = None
            try:
                conn, temp_db = _open_cookie_db(cookie_db)
                rows = conn.execute(
                    """
                    SELECT host_key, name, value, encrypted_value
                    FROM cookies
                    WHERE host_key LIKE '%goofish.com'
                       OR host_key LIKE '%.goofish.com'
                       OR host_key LIKE '%taobao.com'
                       OR host_key LIKE '%.taobao.com'
                       OR host_key LIKE '%tmall.com'
                       OR host_key LIKE '%.tmall.com'
                    ORDER BY last_access_utc ASC, creation_utc ASC
                    """
                ).fetchall()
            finally:
                if conn:
                    conn.close()
                try:
                    if temp_db:
                        os.unlink(temp_db)
                except OSError:
                    pass

            for host_key, name, value, encrypted_value in rows:
                try:
                    cookie_value = decrypt_chrome_cookie_value(value, encrypted_value, host_key, master_key)
                except Exception as exc:
                    errors.append(f"{profile_path.name}:{name}:{exc}")
                    continue
                if cookie_value:
                    cookies.append({"domain": host_key, "name": name, "value": cookie_value})
        except Exception as exc:
            errors.append(f"{profile_path}:{exc}")

    cookie_header = build_cookie_header(cookies)
    if not cookie_header and errors:
        raise RuntimeError("; ".join(errors[-5:]))
    return cookie_header


def fetch_best_cookie_header(debug_url=DEFAULT_DEBUG_URL, db_fetcher=fetch_chrome_cookie_header_from_db, cdp_fetcher=None):
    errors = []
    try:
        cookie_header = db_fetcher()
        if cookie_is_usable(cookie_header):
            return cookie_header
        errors.append(f"Chrome Cookie 数据库不完整: {summarize_cookie(cookie_header)}")
    except Exception as exc:
        errors.append(f"Chrome Cookie 数据库读取失败: {exc}")

    cdp_fetcher = cdp_fetcher or fetch_chrome_cookie_header
    try:
        cookie_header = cdp_fetcher(debug_url)
        if cookie_is_usable(cookie_header):
            return cookie_header
        errors.append(f"Chrome 调试端口 Cookie 不完整: {summarize_cookie(cookie_header)}")
    except Exception as exc:
        errors.append(f"Chrome 调试端口读取失败: {exc}")

    raise RuntimeError("; ".join(errors))


def _json_get(url, timeout=5):
    opener = build_opener(ProxyHandler({}))
    req = Request(url, headers={"Accept": "application/json"})
    with opener.open(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _target_sort_key(target):
    url = str(target.get("url") or "").lower()
    title = str(target.get("title") or "").lower()
    haystack = f"{url} {title}"
    if "goofish.com" in haystack:
        return 0
    if "xianyu" in haystack:
        return 1
    if target.get("type") == "page":
        return 2
    return 3


def find_debugger_websocket(debug_url=DEFAULT_DEBUG_URL):
    targets = _json_get(f"{debug_url.rstrip('/')}/json")
    if not isinstance(targets, list):
        raise RuntimeError("Chrome 调试端口返回的目标列表格式不正确")

    for target in sorted(targets, key=_target_sort_key):
        ws_url = target.get("webSocketDebuggerUrl")
        if ws_url:
            return ws_url
    raise RuntimeError("Chrome 调试端口没有可连接的页面目标")


async def _fetch_cookies_from_websocket(ws_url):
    websocket = await websockets.connect(ws_url, open_timeout=5, close_timeout=2)
    try:
        for request_id, method in enumerate(("Network.getAllCookies", "Storage.getCookies"), start=1):
            await websocket.send(json.dumps({"id": request_id, "method": method}))
            for _ in range(8):
                response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=5))
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    break
                cookies = response.get("result", {}).get("cookies", [])
                if cookies:
                    return cookies
        return []
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


async def fetch_chrome_cookie_header_async(debug_url=DEFAULT_DEBUG_URL):
    ws_url = find_debugger_websocket(debug_url)
    cookies = await _fetch_cookies_from_websocket(ws_url)
    return build_cookie_header(cookies)


def fetch_chrome_cookie_header(debug_url=DEFAULT_DEBUG_URL):
    try:
        return asyncio.run(fetch_chrome_cookie_header_async(debug_url))
    except URLError as exc:
        raise RuntimeError(f"无法连接 Chrome 调试端口 {debug_url}: {exc}") from exc


def summarize_cookie(cookie_header):
    names = _cookie_names(cookie_header)
    return {
        "length": len(cookie_header or ""),
        "has_unb": "unb" in names,
        "has_h5_token": "_m_h5_tk" in names,
        "has_x5sec": "x5sec" in names,
    }
