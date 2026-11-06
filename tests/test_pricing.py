import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clv import line_clv, price_clv_points
from odds_client import Offer, book_source, choose_main_line


def offer(side, line, price, market="player_points", player="Damian Lillard"):
    return Offer("PrizePicks", "prizepicks", market, player, side, line, price)


class PricingTests(unittest.TestCase):
    def test_main_line_beats_a_goblin_ladder(self):
        offers = [
            offer("Over", 25.5, -137),
            offer("Under", 25.5, -137),
            offer("Over", 32.5, -450),
            offer("Under", 32.5, 280),
        ]
        chosen = choose_main_line(offers, "Damian Lillard", "player_points", "Under")
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.line, 25.5)
        self.assertEqual(chosen.price, -137)

    def test_pick6_is_the_book(self):
        self.assertEqual(book_source("pick6"), ("us_dfs", "pick6"))
        self.assertEqual(book_source("retail"), ("us", "draftkings"))

    def test_line_clv_direction(self):
        self.assertEqual(line_clv("Over", 24.5, 26.5), 2.0)
        self.assertEqual(line_clv("Under", 24.5, 22.5), 2.0)
        self.assertLess(line_clv("Under", 24.5, 26.5), 0)

    def test_price_clv_is_positive_when_the_side_shortens(self):
        self.assertGreater(price_clv_points(-137, -160), 0)
        self.assertLess(price_clv_points(-137, -110), 0)


if __name__ == "__main__":
    unittest.main()
