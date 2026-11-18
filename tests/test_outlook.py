import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone

from feed import parse_injury_page
from model import obvious_side
from outlook import anticipate_out


class OutlookTests(unittest.TestCase):
    def test_a_long_absence_questionable_is_projected_out(self):
        self.assertTrue(anticipate_out("Jalen Johnson (strained hamstring) is questionable tonight."))

    def test_soreness_and_illness_are_not_projected_outs(self):
        self.assertFalse(anticipate_out("Mikel Brown Jr. (illness) is questionable."))
        self.assertFalse(anticipate_out("Josh Hart (sore knee) is questionable."))

    def test_a_doubtful_calf_strain_is_projected_out(self):
        self.assertTrue(anticipate_out("Walker Kessler (strained calf) is doubtful."))

    def test_a_sprained_knee_questionable_is_not_definite(self):
        self.assertFalse(anticipate_out("Bam Adebayo (sprained knee) is questionable."))

    def test_clear_minutes_language_is_a_bet(self):
        self.assertEqual(obvious_side("He will play more minutes tonight."), "Over")
        self.assertEqual(obvious_side("He is on a minutes restriction."), "Under")

    def test_the_injury_board_reads_outs_and_questionable_rows(self):
        html = """
        <h2>Out 1</h2>
        <details class="board-row"><summary>
          <img alt="Charlotte Hornets">
          <span class="grow"><span class="row-name">Grayson Allen</span>
          <span>Head · concussion-like symptoms</span></span>
          <span class="mono">2h</span>
        </summary><div class="board-detail"><p>Grayson Allen ruled out Tuesday.</p>
        <a href="/teams/CHA?player=1">updates</a></div></details>
        <h2>Questionable 1</h2>
        <details class="board-row"><summary>
          <img alt="Atlanta Hawks">
          <span class="grow"><span class="row-name">Jalen Johnson</span>
          <span>Hamstring · strained hamstring</span></span>
          <span class="mono">20m</span>
        </summary><div class="board-detail"><p>Johnson is listed questionable.</p>
        <a href="/teams/ATL?player=2">updates</a></div></details>
        <h2>Available · last 3 days 1</h2>
        <details class="board-row"><summary>
          <img alt="Brooklyn Nets"><span class="grow"><span class="row-name">Mikel Brown Jr.</span></span>
        </summary><div class="board-detail"><p>Brown is available.</p></div></details>
        """
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        items = parse_injury_page(html, now)
        self.assertEqual([item.players[0] for item in items], ["Grayson Allen", "Jalen Johnson"])
        self.assertEqual(items[0].team, "CHA")
        self.assertIn("is ruled out", items[0].headline)
        self.assertIn("is questionable", items[1].headline)
        self.assertTrue(anticipate_out(items[1].text))
        self.assertFalse(anticipate_out(items[0].text))

    def test_hedged_or_vague_coachspeak_is_not_a_bet(self):
        self.assertIsNone(obvious_side("He could play more minutes if his knee responds."))
        self.assertIsNone(obvious_side("The coach likes his energy."))
        self.assertIsNone(obvious_side("He is questionable with a minutes restriction."))


if __name__ == "__main__":
    unittest.main()
