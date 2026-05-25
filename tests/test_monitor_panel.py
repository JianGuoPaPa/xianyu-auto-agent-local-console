import sqlite3
import tempfile
import unittest
import json
from pathlib import Path

import monitor_panel
from monitor_panel import (
    build_overview,
    parse_launchctl_status,
    read_recent_messages,
    restart_service,
    set_env_value,
    tail_lines,
)


class MonitorPanelTests(unittest.TestCase):
    def test_parse_launchctl_status_extracts_running_pid_and_runs(self):
        text = """
gui/501/com.xianyu.autoagent = {
    state = running
    runs = 3
    pid = 51433
}
"""

        status = parse_launchctl_status(text)

        self.assertEqual(status["state"], "running")
        self.assertEqual(status["pid"], 51433)
        self.assertEqual(status["runs"], 3)
        self.assertTrue(status["running"])

    def test_parse_launchctl_status_handles_missing_job(self):
        status = parse_launchctl_status("Bad request. Could not find service")

        self.assertEqual(status["state"], "unknown")
        self.assertIsNone(status["pid"])
        self.assertFalse(status["running"])

    def test_tail_lines_returns_newest_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "service.log"
            log_path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

            self.assertEqual(tail_lines(log_path, 2), ["three", "four"])

    def test_tail_lines_handles_missing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(tail_lines(Path(temp_dir) / "missing.log", 10), [])

    def test_read_recent_messages_returns_descending_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "chat_history.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    chat_id TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO messages (user_id, item_id, role, content, timestamp, chat_id) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("u1", "i1", "user", "你好", "2026-05-03T01:00:00", "c1"),
                    ("seller", "i1", "assistant", "在的", "2026-05-03T01:00:01", "c1"),
                    ("u2", "i2", "user", "能发货吗", "2026-05-03T01:00:02", "c2"),
                ],
            )
            conn.commit()
            conn.close()

            messages = read_recent_messages(db_path, 2)

            self.assertEqual([message["content"] for message in messages], ["能发货吗", "在的"])
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[0]["chat_id"], "c2")

    def test_build_overview_includes_status_logs_messages_and_stats(self):
        old_is_windows = monitor_panel.IS_WINDOWS
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                monitor_panel.IS_WINDOWS = False
                root = Path(temp_dir)
                log_path = root / "logs" / "launchd.err.log"
                db_path = root / "data" / "chat_history.db"
                log_path.parent.mkdir()
                db_path.parent.mkdir()
                log_path.write_text("started\nconnected\n", encoding="utf-8")
                conn = sqlite3.connect(db_path)
                conn.execute(
                    "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, item_id TEXT, role TEXT, content TEXT, timestamp DATETIME, chat_id TEXT)"
                )
                conn.execute(
                    "CREATE TABLE items (item_id TEXT PRIMARY KEY, data TEXT NOT NULL, price REAL, description TEXT, last_updated DATETIME)"
                )
                conn.execute(
                    "INSERT INTO messages (user_id, item_id, role, content, timestamp, chat_id) VALUES ('u1', 'i1', 'user', 'hello', '2026-05-03T01:00:00', 'c1')"
                )
                conn.execute(
                    "INSERT INTO items (item_id, data, price, description, last_updated) VALUES ('i1', '{}', 12.0, 'desc', '2026-05-03T01:00:00')"
                )
                conn.commit()
                conn.close()

                overview = build_overview(
                    root=root,
                    launchctl_output="state = running\nruns = 1\npid = 42\n",
                    now="2026-05-03 01:02:00",
                )

                self.assertTrue(overview["service"]["running"])
                self.assertEqual(overview["service"]["pid"], 42)
                self.assertEqual(overview["logs"]["agent"], ["started", "connected"])
                self.assertEqual(overview["messages"][0]["content"], "hello")
                self.assertEqual(overview["stats"]["message_count"], 1)
                self.assertEqual(overview["stats"]["item_count"], 1)
            finally:
                monitor_panel.IS_WINDOWS = old_is_windows

    def test_set_env_value_updates_bom_prefixed_file_without_bom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            (Path(temp_dir) / ".env.windows.example").write_text(
                "API_KEY=ollama\nMODEL_BASE_URL=http://127.0.0.1:11434/v1\nCOOKIES_STR=your_cookies_here\n",
                encoding="utf-8",
            )
            env_path.write_bytes("\ufeffAPI_KEY=old\nCOOKIES_STR=old-cookie\n".encode("utf-8"))

            set_env_value(env_path, "COOKIES_STR", "new-cookie")

            content = env_path.read_text(encoding="utf-8")
            self.assertFalse(content.startswith("\ufeff"))
            self.assertIn("API_KEY=old", content)
            self.assertIn("COOKIES_STR=new-cookie", content)

    def test_set_env_value_restores_windows_defaults_when_cookie_sync_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            (Path(temp_dir) / ".env.windows.example").write_text(
                "API_KEY=ollama\nMODEL_BASE_URL=http://127.0.0.1:11434/v1\nCOOKIES_STR=your_cookies_here\n",
                encoding="utf-8",
            )
            env_path.write_text("COOKIES_STR=old-cookie\n", encoding="utf-8")

            monitor_panel.set_env_value(env_path, "COOKIES_STR", "new-cookie")

            content = env_path.read_text(encoding="utf-8")
            self.assertIn("COOKIES_STR=new-cookie", content)
            self.assertIn("API_KEY=ollama", content)
            self.assertIn("MODEL_BASE_URL=http://127.0.0.1:11434/v1", content)

    def test_sync_cookie_from_browser_updates_env_and_restarts_when_cookie_changed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            old_cookie = "unb=123; _m_h5_tk=token_old; cookie2=abc"
            new_cookie = "unb=123; _m_h5_tk=token_new; cookie2=abc"
            env_path.write_text(f"COOKIES_STR={old_cookie}\n", encoding="utf-8")
            restarts = []

            result = monitor_panel.sync_cookie_from_browser_once(
                root=root,
                fetch_cookie=lambda debug_url: new_cookie,
                restart_func=lambda: restarts.append(True) or {"ok": True},
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["changed"])
            self.assertEqual(restarts, [True])
            self.assertIn(f"COOKIES_STR={new_cookie}", env_path.read_text(encoding="utf-8"))

    def test_sync_cookie_from_browser_skips_restart_when_cookie_same(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            cookie = "unb=123; _m_h5_tk=token_old; cookie2=abc"
            env_path.write_text(f"COOKIES_STR={cookie}\n", encoding="utf-8")
            restarts = []

            result = monitor_panel.sync_cookie_from_browser_once(
                root=root,
                fetch_cookie=lambda debug_url: cookie,
                restart_func=lambda: restarts.append(True) or {"ok": True},
            )

            self.assertTrue(result["ok"])
            self.assertFalse(result["changed"])
            self.assertEqual(restarts, [])

    def test_accept_browser_cookie_sync_saves_without_restart_when_agent_running(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            env_path.write_text("COOKIES_STR=unb=123; _m_h5_tk=old; cookie2=abc\n", encoding="utf-8")
            restarts = []

            result = monitor_panel.accept_browser_cookie_sync(
                "unb=123; _m_h5_tk=new; cookie2=abc",
                root=root,
                restart_func=lambda: restarts.append(True) or {"ok": True},
                should_restart_func=lambda: False,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["changed"])
            self.assertFalse(result["restarted"])
            self.assertEqual(restarts, [])
            self.assertIn("_m_h5_tk=new", env_path.read_text(encoding="utf-8"))

    def test_accept_browser_cookie_sync_restarts_when_agent_needs_cookie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            env_path.write_text("COOKIES_STR=unb=123; _m_h5_tk=old; cookie2=abc\n", encoding="utf-8")
            restarts = []

            result = monitor_panel.accept_browser_cookie_sync(
                "unb=123; _m_h5_tk=new; cookie2=abc",
                root=root,
                restart_func=lambda: restarts.append(True) or {"ok": True},
                should_restart_func=lambda: True,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["restarted"])
            self.assertEqual(restarts, [True])

    def test_accept_browser_cookie_sync_merges_short_cookie_with_existing_cookie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            env_path.write_text(
                "COOKIES_STR=unb=123; _m_h5_tk=old; cookie2=abc; x5sec=validated\n",
                encoding="utf-8",
            )

            result = monitor_panel.accept_browser_cookie_sync(
                "unb=123; _m_h5_tk=new",
                root=root,
                should_restart_func=lambda: False,
            )

            self.assertTrue(result["ok"])
            content = env_path.read_text(encoding="utf-8")
            self.assertIn("_m_h5_tk=new", content)
            self.assertIn("cookie2=abc", content)
            self.assertIn("x5sec=validated", content)

    def test_accept_browser_cookie_sync_waits_for_x5sec_after_validation_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            env_path.write_text("COOKIES_STR=unb=123; _m_h5_tk=old; cookie2=abc\n", encoding="utf-8")
            restarts = []

            result = monitor_panel.accept_browser_cookie_sync(
                "unb=123; _m_h5_tk=new; cookie2=abc",
                root=root,
                restart_func=lambda: restarts.append(True) or {"ok": True},
                should_restart_func=lambda: True,
                validation_cookie_required_func=lambda: True,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["changed"])
            self.assertFalse(result["restarted"])
            self.assertEqual(restarts, [])
            self.assertIn("x5sec", result["message"])

    def test_extension_assets_are_served_only_to_loopback_clients(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "chrome-cookie-extension.crx").write_bytes(b"crx-data")
            (root / "chrome-cookie-extension-update.xml").write_text("<gupdate />", encoding="utf-8")

            status, data, content_type = monitor_panel.extension_asset_response(
                "/extensions/xianyu-cookie-sync.crx",
                "127.0.0.1",
                root=root,
            )
            self.assertEqual(status, 200)
            self.assertEqual(data, b"crx-data")
            self.assertEqual(content_type, "application/x-chrome-extension")

            status, data, content_type = monitor_panel.extension_asset_response(
                "/extensions/xianyu-cookie-sync-update.xml",
                "::1",
                root=root,
            )
            self.assertEqual(status, 200)
            self.assertEqual(data, b"<gupdate />")
            self.assertEqual(content_type, "text/xml; charset=utf-8")

            status, data, _ = monitor_panel.extension_asset_response(
                "/extensions/xianyu-cookie-sync.crx",
                "192.168.31.10",
                root=root,
            )
            self.assertEqual(status, 403)
            self.assertIn(b"loopback", data)

    def test_prompt_payload_and_update_prompt_use_prompt_files(self):
        old_root = monitor_panel.ROOT
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / "prompts").mkdir()
                monitor_panel.ROOT = root

                result = monitor_panel.update_prompt("default_prompt", "这是测试提示词，长度足够用于保存，并且包含完整的回复规则。")

                self.assertTrue(result["ok"])
                payload = monitor_panel.prompt_payload()
                default_prompt = next(prompt for prompt in payload["prompts"] if prompt["id"] == "default_prompt")
                self.assertIn("这是测试提示词", default_prompt["text"])
        finally:
            monitor_panel.ROOT = old_root

    def test_item_profile_payload_and_update_item_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "data" / "chat_history.db"
            db_path.parent.mkdir()
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, item_id TEXT, role TEXT, content TEXT, timestamp DATETIME, chat_id TEXT)"
            )
            conn.execute(
                "CREATE TABLE items (item_id TEXT PRIMARY KEY, data TEXT NOT NULL, price REAL, description TEXT, last_updated DATETIME)"
            )
            conn.execute(
                "INSERT INTO items (item_id, data, price, description, last_updated) VALUES (?, ?, ?, ?, ?)",
                ("i1", json.dumps({"title": "OpenClaw资料"}, ensure_ascii=False), 9.9, "资料包说明", "2026-05-20T10:00:00"),
            )
            conn.execute(
                "INSERT INTO messages (user_id, item_id, role, content, timestamp, chat_id) VALUES ('u1', 'i1', 'user', '怎么发货', '2026-05-20T10:01:00', 'c1')"
            )
            conn.commit()
            conn.close()

            result = monitor_panel.update_item_profile(
                "i1",
                enabled=True,
                delivery_text="拍下后自动发送网盘链接",
                custom_prompt="必须围绕 OpenClaw 资料回答",
                root=root,
            )

            self.assertTrue(result["ok"])
            payload = monitor_panel.item_profile_payload(root=root)
            self.assertEqual(payload["items"][0]["title"], "OpenClaw资料")
            self.assertEqual(payload["items"][0]["delivery_text"], "拍下后自动发送网盘链接")
            self.assertEqual(payload["items"][0]["custom_prompt"], "必须围绕 OpenClaw 资料回答")

    def test_restart_service_bootstraps_when_kickstart_fails(self):
        old_is_windows = monitor_panel.IS_WINDOWS
        calls = []

        def fake_runner(args, timeout=8):
            calls.append(args)
            if args[:2] == ["launchctl", "kickstart"] and len([call for call in calls if call[:2] == ["launchctl", "kickstart"]]) == 1:
                return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "missing service"})()
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        try:
            monitor_panel.IS_WINDOWS = False
            result = restart_service(fake_runner, user_id=501)

            self.assertTrue(result["ok"])
            self.assertEqual(calls[0][:3], ["launchctl", "kickstart", "-k"])
            self.assertEqual(calls[1][:3], ["launchctl", "bootstrap", "gui/501"])
            self.assertEqual(calls[2][:3], ["launchctl", "kickstart", "-k"])
        finally:
            monitor_panel.IS_WINDOWS = old_is_windows


if __name__ == "__main__":
    unittest.main()
