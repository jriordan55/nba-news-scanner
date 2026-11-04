import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import FEED_URLS
from feed import parse_html

HTML = """
<article class="card">
  <div class="card-top">
    <span class="tbadge">POR</span>
    <span class="card-name"><a href="/teams/POR?player=1">Damian Lillard</a></span>
    <span class="cat">Minutes &amp; Usage</span>
    <time datetime="2026-10-19T23:07:44+00:00">1h</time>
  </div>
  <p class="card-head">Lillard expects a lighter minutes load.</p>
  <blockquote class="card-quote">I'm not going to be playing 38 minutes per night.</blockquote>
  <div class="card-foot"><a class="btn" href="https://x.com/example/status/9">View post</a></div>
</article>
<article class="card">
  <div class="card-top"><span class="tbadge">MIL</span><span class="cat">Role</span></div>
  <p class="card-head">Sign in to read this takeaway and the full quote it came from.</p>
  <blockquote class="card-quote">Free accounts get the full feed.</blockquote>
</article>
"""


class FeedTests(unittest.TestCase):
    def test_parses_public_cards_and_keeps_paywalled_ones_marked(self):
        items = parse_html(HTML)
        self.assertEqual(len(items), 2)
        public = items[0]
        self.assertEqual(public.team, "POR")
        self.assertEqual(public.players, ["Damian Lillard"])
        self.assertEqual(public.category, "Minutes & Usage")
        self.assertEqual(public.url, "https://x.com/example/status/9")
        self.assertIn("38 minutes", public.quote)
        self.assertIsNotNone(public.published)
        self.assertTrue(items[1].paywalled)

    def test_coachspeak_is_on_the_scan(self):
        self.assertIn("https://fcp.news/coachspeak", FEED_URLS)


if __name__ == "__main__":
    unittest.main()
