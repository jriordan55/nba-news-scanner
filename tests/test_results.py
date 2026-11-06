import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from results import box_lines, finals, line_from_athlete, outcome


class ResultTests(unittest.TestCase):
    def test_hit_loss_uses_the_line_at_the_news(self):
        self.assertEqual(outcome("Under", 28.5, True, 22), ("22", "hit"))
        self.assertEqual(outcome("Over", 28.5, True, 22), ("22", "loss"))
        self.assertEqual(outcome("Over", 6.5, True, 6.5), ("6.5", "push"))
        self.assertEqual(outcome("Over", 18.5, False, None), ("DNP", "void"))

    def test_box_score_reads_points_threes_and_pra(self):
        athlete = {
            "didNotPlay": False,
            "athlete": {"displayName": "LeBron James"},
            "stats": ["33", "13", "5-10", "1-2", "2-2", "10", "8", "3", "1", "1", "0", "10", "0", "+1"],
        }
        line = line_from_athlete(athlete)
        self.assertEqual(line["Points"], 13)
        self.assertEqual(line["Threes"], 1)
        self.assertEqual(line["PRA"], 31)
        rows = box_lines({"boxscore": {"players": [{"statistics": [{"athletes": [athlete]}]}]}})
        self.assertEqual(rows[0][0], "LeBron James")

    def test_a_final_game_is_kept_by_its_teams(self):
        payload = {
            "events": [
                {
                    "id": "401",
                    "status": {"type": {"completed": True}},
                    "competitions": [
                        {
                            "competitors": [
                                {"team": {"abbreviation": "DET"}},
                                {"team": {"abbreviation": "PHI"}},
                            ]
                        }
                    ],
                }
            ]
        }
        self.assertEqual(finals(payload), {("DET", "PHI"): "401"})

    def test_did_not_play_is_not_a_stat(self):
        self.assertEqual(line_from_athlete({"didNotPlay": True, "stats": []})["played"], False)


if __name__ == "__main__":
    unittest.main()
