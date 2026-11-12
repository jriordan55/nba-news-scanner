import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alerts import render_watch
from feed import NewsItem
from impact import load_impacts
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
        self.assertIn("this is not an under", text)
        self.assertNotIn("beneficiaries.json", text)

    def test_last_season_is_used_when_this_season_has_no_sample(self):
        item, outs = _out("Kristaps Porzingis", "ATL")
        text = render_watch(item, outs, self.impacts)
        self.assertIn("Jalen Johnson", text)
        self.assertIn("Threes +148%", text)
        self.assertIn("Hit 11 of 12", text)
        self.assertIn("last season", text)

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
