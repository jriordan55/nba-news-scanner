import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alerts import render_priced
from feed import NewsItem
from matchup import parse_matchup
from odds_client import Leg

KUZMA = """
["dvp-full","children":"Kyle Kuzma"]
["title":"Celtics allow 25.4 pts to PFs (league 19.9, +5.5), 3rd softest of 30"]
["title":"Celtics allow 11.8 reb to PFs (league 8.6, +3.2), 1st softest of 30"]
["title":"Celtics allow 6.0 ast to PFs (league 4.0, +2.0), 1st softest of 30"]
["title":"Celtics allow 3.2 fg3m to PFs (league 2.1, +1.1), 1st softest of 30"]
["dvp-full","children":"Myles Turner"]
["title":"Celtics allow 20.0 pts to Cs (league 20.4, -0.4), 15th softest of 30"]
["title":"Celtics allow 13.8 reb to Cs (league 13.3, +0.5), 14th softest of 30"]
["title":"Celtics allow 3.8 ast to Cs (league 3.9, -0.1), 18th softest of 30"]
["title":"Celtics allow 1.7 fg3m to Cs (league 1.3, +0.4), 5th softest of 30"]
"""


class MatchupTests(unittest.TestCase):
    def test_points_note_uses_that_position(self):
        matchup = parse_matchup(KUZMA)
        self.assertIsNotNone(matchup)
        note = matchup.note("Kyle Kuzma", "player_points")
        self.assertIn("Last 15", note)
        self.assertIn("25.4 points", note)
        self.assertIn("PFs", note)
        self.assertIn("3rd softest", note)
        self.assertNotIn("20.0", note)

    def test_pra_lists_points_rebounds_and_assists(self):
        note = parse_matchup(KUZMA).note("Kyle Kuzma", "player_points_rebounds_assists")
        self.assertIn("25.4 pts (3rd)", note)
        self.assertIn("11.8 reb (1st)", note)
        self.assertIn("6.0 ast (1st)", note)
        self.assertNotIn("3.2", note)

    def test_threes_and_a_missing_player(self):
        matchup = parse_matchup(KUZMA)
        self.assertIn("1.7 threes", matchup.note("Myles Turner", "player_threes"))
        self.assertEqual(matchup.note("Giannis Antetokounmpo", "player_points"), "")

    def test_a_short_table_is_ignored(self):
        self.assertIsNone(parse_matchup('["dvp-full","children":"Kyle Kuzma"]'))

    def test_alert_text_includes_the_defense_line(self):
        start = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
        item = NewsItem(
            id="1",
            team="MIL",
            players=["Kyle Kuzma"],
            category="Role",
            published=start,
            headline="Kuzma is in the starting lineup.",
            quote="",
            url="https://fcp.news/q/example",
        )
        leg = Leg(
            player="Kyle Kuzma",
            team="MIL",
            market="player_points",
            market_label="Points",
            side="Over",
            line=16.5,
            price=-120,
            book="PrizePicks",
            book_key="prizepicks",
            event_id="evt",
            game="MIL @ BOS",
            home="Boston Celtics",
            away="Milwaukee Bucks",
            game_start=start,
            confidence=0.72,
            reason="Kuzma is lined up for more usage.",
            defense="Last 15: Celtics allow 25.4 points to PFs, 3rd softest of 30.",
        )
        text = render_priced(item, [leg], [])
        self.assertIn("25.4 points", text)
        self.assertIn("Kuzma is lined up for more usage.", text)


if __name__ == "__main__":
    unittest.main()
