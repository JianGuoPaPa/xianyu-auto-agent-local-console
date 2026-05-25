import os
import tempfile
import unittest

import requests

from XianyuApis import XianyuApis


class XianyuApisCookieTests(unittest.TestCase):
    def test_sync_cookies_from_response_merges_set_cookie_and_updates_env(self):
        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                os.chdir(temp_dir)
                with open(".env", "w", encoding="utf-8") as env_file:
                    env_file.write(
                        "API_KEY=ollama\n"
                        "MODEL_BASE_URL=http://127.0.0.1:11434/v1\n"
                        "COOKIES_STR=unb=123; _m_h5_tk=old; cookie2=abc; x5sec=validated\n"
                    )

                api = XianyuApis()
                api.session.cookies.set("unb", "123", domain=".goofish.com")
                api.session.cookies.set("_m_h5_tk", "old", domain=".goofish.com")
                api.session.cookies.set("cookie2", "abc", domain=".goofish.com")

                response = requests.Response()
                response.headers["Set-Cookie"] = "_m_h5_tk=new; Domain=.goofish.com; Path=/"
                response.cookies.set("_m_h5_tk", "new", domain=".goofish.com", path="/")

                self.assertTrue(api.sync_cookies_from_response(response))

                with open(".env", encoding="utf-8") as env_file:
                    env_content = env_file.read()
                self.assertIn("_m_h5_tk=new", env_content)
                self.assertIn("unb=123", env_content)
                self.assertIn("cookie2=abc", env_content)
                self.assertIn("x5sec=validated", env_content)
                self.assertIn("API_KEY=ollama", env_content)
                self.assertIn("MODEL_BASE_URL=http://127.0.0.1:11434/v1", env_content)
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
