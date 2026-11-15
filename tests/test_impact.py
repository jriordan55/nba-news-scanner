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

    def test_a_starter_out_names_who_to_bet_from_this_season(self):
        item, outs = _out("Jalen Johnson", "ATL")
        text = render_watch(item, outs, self.impacts)
        self.assertIn("ATL OUT: Jalen Johnson.", text)
        self.assertIn("Bet Luguentz Dort Threes over.", text)
        self.assertIn("+215% this season", text)
        self.assertIn("Hit 2 of 2 above his usual", text)
        self.assertIn("Minutes 22.7 to 31.5", text)
        self.assertIn("+8.8", text)
        self.assertNotIn("Last season", text)
        self.assertNotIn("beneficiaries.json", text)

    def test_a_bench_injury_does_not_alert(self):
        item, outs = _out("Jordan Poole", "NOP")
        self.assertEqual(render_watch(item, outs, self.impacts), "")
        item, outs = _out("Terrence Shannon Jr.", "MIN")
        self.assertEqual(render_watch(item, outs, self.impacts), "")

    def test_last_season_is_never_added(self):
        row = {
            "season": "2026-27",
            "team": "ATL",
            "absent": "Jalen Johnson",
            "absent_role": "Starter",
            "beneficiary": "Luguentz Dort",
            "market": "Threes",
            "boost_pct": 215,
            "baseline": 1.11,
            "out_avg": 3.5,
            "hit": 2,
            "out_games": 2,
            "minutes_baseline": 22.67,
            "minutes_out": 31.5,
            "minutes_delta": 8.83,
            "last_season": {
                "season": "2025-26",
                "beneficiary": "Luguentz Dort",
                "market": "Points",
                "boost_pct": 40,
                "baseline": 10,
                "out_avg": 14,
                "hit": 6,
                "out_games": 8,
                "minutes_baseline": 20,
                "minutes_out": 28,
                "minutes_delta": 8,
            },
        }
        item, outs = _out("Jalen Johnson", "ATL")
        text = render_watch(item, outs, Impacts([row]))
        self.assertIn("Bet Luguentz Dort Threes over.", text)
        self.assertNotIn("Last season", text)
        self.assertNotIn("Points", text)

    def test_a_player_without_a_sample_stays_silent(self):
        item, outs = _out("Jock Landale", "ATL")
        self.assertEqual(render_watch(item, outs, self.impacts), "")

    def test_the_same_player_on_a_new_team_uses_that_team(self):
        item, outs = _out("Gui Santos", "GSW")
        text = render_watch(item, outs, self.impacts)
        self.assertIn("Bet Will Richard Threes over.", text)
        self.assertNotIn("Paul Reed", text)


if __name__ == "__main__":
    unittest.main()
