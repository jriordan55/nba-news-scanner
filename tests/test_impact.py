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

    def test_an_out_names_the_market_with_the_biggest_boost(self):
        item, outs = _out("Jordan Poole", "NOP")
        text = render_watch(item, outs, self.impacts)
        self.assertIn("Jeremiah Fears", text)
        self.assertIn("Points +141%", text)
        self.assertIn("Hit 3 of 3 above his usual", text)
        self.assertIn("Minutes 24.2 to 33", text)
        self.assertIn("+8.8", text)
        self.assertIn("3 games this season", text)
        self.assertIn("starter", text)
        self.assertIn("this is not an under", text)
        self.assertNotIn("beneficiaries.json", text)
        self.assertNotIn("Last season", text)

    def test_last_season_is_not_used_for_a_different_teammate(self):
        item, outs = _out("Kristaps Porzingis", "ATL")
        text = render_watch(item, outs, self.impacts)
        self.assertNotIn("Impact:", text)
        self.assertNotIn("Jalen Johnson", text)
        self.assertIn("No qualified teammate boost", text)

    def test_this_season_uses_the_starter_who_gained_the_minutes(self):
        item, outs = _out("Terrence Shannon Jr.", "MIN")
        text = render_watch(item, outs, self.impacts)
        self.assertIn("Ayo Dosunmu", text)
        self.assertIn("starter", text)
        self.assertIn("this season", text)
        self.assertNotIn("Jonathan Kuminga", text)
        self.assertNotIn("Jaylen Clark", text)
        self.assertNotIn("Last season", text)

    def test_last_season_is_added_only_for_the_same_teammate(self):
        row = {
            "season": "2026-27",
            "team": "NOP",
            "absent": "Jordan Poole",
            "beneficiary": "Jeremiah Fears",
            "beneficiary_pos": "G",
            "same_position": True,
            "role": "Starter",
            "market": "Points",
            "boost_pct": 141,
            "baseline": 9.4,
            "out_avg": 22.67,
            "hit": 3,
            "out_games": 3,
            "minutes_baseline": 24.2,
            "minutes_out": 33,
            "minutes_delta": 8.8,
            "last_season": {
                "season": "2025-26",
                "beneficiary": "Trey Murphy III",
                "beneficiary_pos": "F",
                "same_position": False,
                "role": "Starter",
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
        item, outs = _out("Jordan Poole", "NOP")
        text = render_watch(item, outs, Impacts([row]))
        self.assertNotIn("Last season", text)
        self.assertNotIn("Trey Murphy", text)
        row["last_season"]["beneficiary"] = "Jeremiah Fears"
        text = render_watch(item, outs, Impacts([row]))
        self.assertIn("Last season, same teammate: Jeremiah Fears", text)

    def test_a_player_without_a_sample_stays_a_plain_watch(self):
        item, outs = _out("Jock Landale", "ATL")
        text = render_watch(item, outs, self.impacts)
        self.assertIn("OUT: Jock Landale", text)
        self.assertIn("No qualified teammate boost", text)
        self.assertNotIn("Impact:", text)

    def test_the_same_player_on_a_new_team_uses_that_team(self):
        item, outs = _out("Isaiah Stewart", "MEM")
        text = render_watch(item, outs, self.impacts)
        self.assertIn("GG Jackson", text)
        self.assertNotIn("Paul Reed", text)


if __name__ == "__main__":
    unittest.main()
