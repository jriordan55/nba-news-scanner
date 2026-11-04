import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fcp_session import cookies_for_session, parse_cookie_header, _parse_token_url


class SessionTests(unittest.TestCase):
    def test_parses_a_browser_cookie_string(self):
        header = "theme=dark; sb-jpdoxlpsqelrcktarnml-auth-token.0=base64-abc=; sb-jpdoxlpsqelrcktarnml-auth-token.1=def"
        cookies = parse_cookie_header(header)
        self.assertEqual(cookies["theme"], "dark")
        self.assertEqual(cookies["sb-jpdoxlpsqelrcktarnml-auth-token.0"], "base64-abc=")
        self.assertEqual(cookies["sb-jpdoxlpsqelrcktarnml-auth-token.1"], "def")

    def test_session_cookie_carries_the_access_token(self):
        cookies = cookies_for_session(
            {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600}
        )
        value = cookies["sb-jpdoxlpsqelrcktarnml-auth-token"]
        self.assertTrue(value.startswith("base64-"))
        self.assertNotIn("refresh-1", value[:12])

    def test_long_session_is_split_across_cookies(self):
        cookies = cookies_for_session(
            {"access_token": "a" * 5000, "refresh_token": "refresh-1", "expires_in": 3600}
        )
        self.assertIn("sb-jpdoxlpsqelrcktarnml-auth-token.0", cookies)
        self.assertIn("sb-jpdoxlpsqelrcktarnml-auth-token.1", cookies)

    def test_email_link_hash_has_both_tokens(self):
        found = _parse_token_url(
            "https://fcp.news/auth/email#access_token=aaa&refresh_token=bbb&expires_in=3600"
        )
        self.assertEqual(found["access_token"], "aaa")
        self.assertEqual(found["refresh_token"], "bbb")


if __name__ == "__main__":
    unittest.main()
