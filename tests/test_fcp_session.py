import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fcp_session import parse_cookie_header


class SessionTests(unittest.TestCase):
    def test_parses_a_browser_cookie_string(self):
        header = "theme=dark; sb-jpdoxlpsqelrcktarnml-auth-token.0=base64-abc=; sb-jpdoxlpsqelrcktarnml-auth-token.1=def"
        cookies = parse_cookie_header(header)
        self.assertEqual(cookies["theme"], "dark")
        self.assertEqual(cookies["sb-jpdoxlpsqelrcktarnml-auth-token.0"], "base64-abc=")
        self.assertEqual(cookies["sb-jpdoxlpsqelrcktarnml-auth-token.1"], "def")


if __name__ == "__main__":
    unittest.main()
