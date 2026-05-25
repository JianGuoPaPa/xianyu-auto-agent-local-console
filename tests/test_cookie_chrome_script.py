from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "windows" / "start-cookie-chrome.ps1"


class CookieChromeScriptTests(unittest.TestCase):
    def test_script_writes_background_and_content_cookie_sync(self):
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('"background.js"', text)
        self.assertIn('"content.js"', text)
        self.assertIn('"content_scripts"', text)
        self.assertIn('"https://goofish.com/*"', text)
        self.assertIn('"https://*.goofish.com/*"', text)
        self.assertIn('"http://127.0.0.1/*"', text)
        self.assertIn("chrome.cookies.getAll({})", text)
        self.assertIn("document.cookie", text)
        self.assertIn("source: \"chrome-extension-background\"", text)
        self.assertIn("source: \"chrome-extension-content\"", text)

    def test_script_enables_chrome_launch_logging(self):
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("cookie_chrome_task.log", text)
        self.assertIn("chrome-cookie-sync.log", text)
        self.assertIn("--enable-logging", text)
        self.assertIn("--log-file=", text)

    def test_script_packs_extension_and_registers_chrome_policy(self):
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--pack-extension=", text)
        self.assertIn("chrome-cookie-extension.crx", text)
        self.assertIn("chrome-cookie-extension.pem", text)
        self.assertIn("chrome-cookie-extension-update.xml", text)
        self.assertIn("ExtensionInstallForcelist", text)
        self.assertIn("xianyu-cookie-sync-update.xml", text)

    def test_unpacked_extension_manifest_is_manual_load_friendly(self):
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("Write-Utf8NoBom", text)
        manifest_block = text.split("$manifest = @'", 1)[1].split("'@", 1)[0]
        self.assertNotIn('"update_url"', manifest_block)


if __name__ == "__main__":
    unittest.main()
