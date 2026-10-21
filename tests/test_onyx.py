import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onyx import parse_nba


def payload():
    return {
        "nba": {
            "league": "NBA",
            "ojId": "game-1",
            "homeTeam": "Washington Wizards",
            "awayTeam": "Milwaukee Bucks",
            "startDate": "2026-10-21T23:00:00.000Z",
            "markets": {
                "player_points": {
                    "a": {
                        "selection": "Ryan Rollins",
                        "selectionLine": "over",
                        "betPoints": 16.5,
                        "lastPrice": -113,
                        "isMain": True,
                    },
                    "b": {
                        "selection": "Ryan Rollins",
                        "selectionLine": "under",
                        "betPoints": 16.5,
                        "lastPrice": -107,
                        "isMain": True,
                    },
                    "alt": {
                        "selection": "Ryan Rollins",
                        "selectionLine": "over",
                        "betPoints": 24.5,
                        "lastPrice": 180,
                        "isMain": False,
                    },
                },
                "player_points_+_rebounds_+_assists": {
                    "c": {
                        "selection": "Ryan Rollins",
                        "selectionLine": "over",
                        "betPoints": 26.5,
                        "lastPrice": -115,
                        "isMain": True,
                    }
                },
            },
        },
        "nfl": {"league": "NFL", "ojId": "other", "homeTeam": "A", "awayTeam": "B", "startDate": "2026-10-21T23:00:00Z"},
    }


class OnyxTests(unittest.TestCase):
    def test_nba_main_lines_only(self):
        games = parse_nba(payload())
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["id"], "game-1")
        offers = games[0]["offers"]
        self.assertEqual(len(offers), 3)
        points = [offer for offer in offers if offer.market == "player_points"]
        self.assertEqual({offer.side for offer in points}, {"Over", "Under"})
        self.assertTrue(all(offer.line == 16.5 and offer.book == "Onyx" for offer in points))
        self.assertEqual(offers[-1].market, "player_points_rebounds_assists")


if __name__ == "__main__":
    unittest.main()
