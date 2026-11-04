import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import FEED_URLS
from feed import card_ids, parse_html, share_text

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

    def test_locked_card_keeps_the_player_names(self):
        html = """
        <article class="card">
          <span class="tbadge">PHX</span>
          <span class="card-name">Jordan Goodwin · Rasheer Fleming</span>
          <span class="cat">Availability</span>
          <p class="card-head">Sign in to read this takeaway and the full quote it came from.</p>
        </article>
        """
        item = parse_html(html)[0]
        self.assertEqual(item.players, ["Jordan Goodwin", "Rasheer Fleming"])
        self.assertTrue(item.paywalled)

    def test_card_ids_follow_the_feed_cards_only(self):
        payload = (
            '["$","article","inj:abc",{"className":"card","children":[]}]'
            '["$","article","2026-11-02:rookie",{"className":"award"}]'
            '["$","article","n:def",{"className":"card","children":[]}]'
        )
        self.assertEqual(card_ids(payload), ["inj:abc", "n:def"])

    def test_share_image_text_keeps_the_quote(self):
        headline, quote = share_text(
            [
                "CLE",
                "Mario Hezonja",
                "Hezonja has asked coaches to let him guard the best and open up more",
                "switching.",
                '"I even tell Mike G and Kenny, like just let me guard the best."',
                "@fcpnba",
                "fcp.news",
            ],
            ["Mario Hezonja"],
        )
        self.assertIn("guard the best", headline)
        self.assertIn("Mike G", quote)
        self.assertNotIn("fcp.news", quote)

    def test_share_image_splits_a_smashed_headline(self):
        headline, quote = share_text(
            [
                "Jordan Goodwin · Rasheer Fleming",
                "PHX",
                "Fleming with a left ankle sprain and Goodwin with right calf soreness are",
                "bothquestionableagainst theBlazers.",
                '"Suns injury report vs. Blazers: QUESTIONABLE: Rasheer Fleming (left ankle sprain)."',
            ],
            ["Jordan Goodwin", "Rasheer Fleming"],
        )
        self.assertIn("questionable", headline)
        self.assertIn("the Blazers", headline)
        self.assertIn("QUESTIONABLE", quote)

    def test_share_image_uses_the_takeaway_when_the_quote_is_garbled(self):
        headline, quote = share_text(
            [
                "NYK",
                "Tyler Nickel",
                "A young guy fans have not seen yet can shoot and has the feel to slide.",
                "Jai^1 'a< uaas t,ua^ey",
                "Nicol, I'm not saying he's going to be a great shooter...",
                "he can shoot the ball and he has the IQ and feel",
                "@fcpnba",
                "Mike Brown·New York Knicks",
            ],
            ["Tyler Nickel"],
        )
        self.assertIn("can shoot", headline)
        self.assertIn("great shooter", quote)
        self.assertNotIn("Jai", quote)

    def test_share_image_drops_a_repeated_name(self):
        headline, _quote = share_text(
            [
                "Karlo Matković",
                "Karlo Matkovic Matkovic plans to run the floor and block shots.",
                '"I am going to run the floor."',
            ],
            ["Karlo Matković"],
        )
        self.assertTrue(headline.startswith("Matkovic plans"))


if __name__ == "__main__":
    unittest.main()
