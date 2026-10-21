import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prizepicks import parse_projections


def projection(player_id, name, team, stat, line, wager="under_or_over", odds_type="standard", promo=False, combo=False):
    return {
        "data": [
            {
                "attributes": {
                    "odds_type": odds_type,
                    "projection_type": "Single Stat",
                    "stat_type": stat,
                    "line_score": line,
                    "allowed_wager_types": wager,
                    "is_promo": promo,
                    "status": "pre_game",
                    "start_time": "2026-10-21T23:00:00Z",
                },
                "relationships": {"new_player": {"data": {"id": player_id}}},
            }
        ],
        "included": [
            {
                "type": "new_player",
                "id": player_id,
                "attributes": {"name": name, "team": team, "combo": combo},
            }
        ],
    }


class PrizePicksTests(unittest.TestCase):
    def test_standard_two_way_line_is_kept(self):
        rows = parse_projections(projection("1", "Ryan Rollins", "MIL", "Points", 17.5))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["player"], "Ryan Rollins")
        self.assertEqual(rows[0]["line"], 17.5)
        self.assertEqual(rows[0]["sides"], ["Over", "Under"])
        self.assertEqual(rows[0]["market"], "player_points")

    def test_goblin_promo_and_combo_are_skipped(self):
        goblin = projection("1", "Ryan Rollins", "MIL", "Points", 22.5, odds_type="goblin")
        promo = projection("1", "Ryan Rollins", "MIL", "Points", 17.5, promo=True)
        combo = projection("1", "Ryan Rollins + Kyle Kuzma", "MIL", "Points", 30.5, combo=True)
        self.assertEqual(parse_projections(goblin), [])
        self.assertEqual(parse_projections(promo), [])
        self.assertEqual(parse_projections(combo), [])


if __name__ == "__main__":
    unittest.main()
