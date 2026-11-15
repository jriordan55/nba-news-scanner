import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alerts import render_watch
from feed import NewsItem
from impact import Impacts, load_impacts
from model import Signal


ROOT = Path(__file__).resolve().parents[1]


def _out(player: str, team: str) -> tuple[NewsItem, list[Signal]]:
    item = NewsItem(
        id="out-1",
        team=team,
        players=[player],
        category="Injury report",
        published=datetime(2026, 11, 11, tzinfo=timezone.utc),
        headline=f"{player} is out.",
        quote=f"{player} will sit.",
        url="https://fcp.news/q/example",
    )
    return item, [Signal(player=player, team=team, kind="out", side=None)]


class ImpactAlertTests(unittest.TestCase):
    def setUp(self):
        self.impacts = load_impacts(ROOT / "impact.json")

    def test_porter_out_bets_brown_points(self):
        item, outs = _out("Michael Porter Jr.", "BKN")
        text = render_watch(item, outs, self.impacts)
        self.assertIn("BKN OUT: Michael Porter Jr.", text)
        self.assertIn("Bet Mikel Brown Jr. Points over.", text)
        self.assertIn("12.9 points", text)
        self.assertIn("23.3 minutes", text)
        self.assertIn("this season", text)
        self.assertNotIn("Last season", text)

    def test_a_bench_injury_does_not_alert(self):
        item, outs = _out("Mikel Brown Jr.", "BKN")
        self.assertEqual(render_watch(item, outs, self.impacts), "")

    def test_last_season_is_never_added(self):
        row = {
            "season": "2026-27",
            "team": "BKN",
            "absent": "Michael Porter Jr.",
            "absent_role": "Starter",
            "beneficiary": "Mikel Brown Jr.",
            "market": "Points",
            "baseline": 12.86,
            "minutes_baseline": 23.29,
            "games": 7,
            "last_season": {"beneficiary": "Mikel Brown Jr.", "market": "Threes"},
        }
        item, outs = _out("Michael Porter Jr.", "BKN")
        text = render_watch(item, outs, Impacts([row]))
        self.assertIn("Bet Mikel Brown Jr. Points over.", text)
        self.assertNotIn("Last season", text)
        self.assertNotIn("Threes", text)

    def test_a_player_without_a_sample_stays_silent(self):
        item, outs = _out("Jock Landale", "ATL")
        self.assertEqual(render_watch(item, outs, self.impacts), "")


if __name__ == "__main__":
    unittest.main()
