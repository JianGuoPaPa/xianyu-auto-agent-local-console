import unittest

from cookie_sync import (
    build_cookie_header,
    cookie_is_usable,
    extract_chrome_profile_from_command_line,
    fetch_best_cookie_header,
    merge_cookie_headers,
    should_replace_cookie,
)


class CookieSyncTests(unittest.TestCase):
    def test_build_cookie_header_filters_domains_and_keeps_latest_duplicate(self):
        cookies = [
            {"name": "unb", "value": "old", "domain": ".goofish.com"},
            {"name": "_m_h5_tk", "value": "token_123", "domain": ".goofish.com"},
            {"name": "cookie2", "value": "abc", "domain": ".taobao.com"},
            {"name": "unb", "value": "new", "domain": ".goofish.com"},
            {"name": "sid", "value": "ignore", "domain": ".example.com"},
        ]

        header = build_cookie_header(cookies)

        self.assertIn("unb=new", header)
        self.assertNotIn("unb=old", header)
        self.assertIn("_m_h5_tk=token_123", header)
        self.assertIn("cookie2=abc", header)
        self.assertNotIn("sid=ignore", header)

    def test_cookie_is_usable_requires_unb_and_h5_token(self):
        self.assertFalse(cookie_is_usable("unb=123; cookie2=abc"))
        self.assertFalse(cookie_is_usable("_m_h5_tk=token; cookie2=abc"))
        self.assertTrue(cookie_is_usable("unb=123; _m_h5_tk=token; cookie2=abc"))

    def test_merge_cookie_headers_updates_candidate_and_keeps_existing_names(self):
        merged = merge_cookie_headers(
            "unb=123; _m_h5_tk=old; cookie2=abc; x5sec=validated",
            "unb=123; _m_h5_tk=new",
        )

        self.assertIn("_m_h5_tk=new", merged)
        self.assertIn("cookie2=abc", merged)
        self.assertIn("x5sec=validated", merged)

    def test_should_replace_cookie_only_when_usable_and_changed(self):
        old_cookie = "unb=123; _m_h5_tk=token_old; cookie2=abc"
        self.assertFalse(should_replace_cookie(old_cookie, old_cookie))
        self.assertFalse(should_replace_cookie(old_cookie, "unb=123; cookie2=abc"))
        self.assertTrue(
            should_replace_cookie(
                old_cookie,
                "unb=123; _m_h5_tk=token_new; cookie2=abc",
            )
        )

    def test_should_not_replace_x5sec_cookie_with_document_cookie(self):
        full_cookie = "unb=123; _m_h5_tk=token_old; x5sec=validated; cookie2=abc"
        document_cookie = "unb=123; _m_h5_tk=token_new; cookie2=abc"

        self.assertFalse(should_replace_cookie(full_cookie, document_cookie))
        self.assertTrue(should_replace_cookie(document_cookie, full_cookie))

    def test_extracts_user_data_and_profile_from_chrome_command_line(self):
        info = extract_chrome_profile_from_command_line(
            '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
            '--user-data-dir="C:\\Users\\me\\Chrome Data" --profile-directory="Profile 1"'
        )

        self.assertEqual(info["user_data_dir"], "C:\\Users\\me\\Chrome Data")
        self.assertEqual(info["profile_directory"], "Profile 1")

    def test_fetch_best_cookie_header_prefers_database_cookie_when_usable(self):
        result = fetch_best_cookie_header(
            db_fetcher=lambda: "unb=123; _m_h5_tk=db_token; cookie2=abc",
            cdp_fetcher=lambda debug_url: "unb=123; _m_h5_tk=cdp_token; cookie2=abc",
        )

        self.assertIn("_m_h5_tk=db_token", result)

    def test_fetch_best_cookie_header_falls_back_to_debugger_cookie(self):
        result = fetch_best_cookie_header(
            db_fetcher=lambda: "_m_h5_tk=only_token",
            cdp_fetcher=lambda debug_url: "unb=123; _m_h5_tk=cdp_token; cookie2=abc",
        )

        self.assertIn("_m_h5_tk=cdp_token", result)


if __name__ == "__main__":
    unittest.main()
