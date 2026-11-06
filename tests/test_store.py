import csv
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feed import NewsItem
from odds_client import Leg
from store import Store


class StoreTests(unittest.TestCase):
    def test_csv_keeps_the_alert_price_and_fills_the_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            try:
                self._write_and_check(store)
            finally:
                store.close()

    def _write_and_check(self, store: Store) -> None:
        start = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)
        item = NewsItem(
            id="abc",
            team="POR",
            players=["Damian Lillard"],
            category="Minutes & Usage",
            published=start,
            headline="Lillard expects a lighter minutes load.",
            quote="",
            url="https://x.com/example/status/9",
        )
        leg = Leg(
            player="Damian Lillard",
            team="POR",
            market="player_points",
            market_label="Points",
            side="Under",
            line=28.5,
            price=-137,
            book="PrizePicks",
            book_key="prizepicks",
            event_id="evt",
            game="POR @ LAL",
            home="Los Angeles Lakers",
            away="Portland Trail Blazers",
            game_start=start,
            confidence=0.78,
            reason="lighter minutes",
        )
        store.insert_legs(item, [leg])
        bet_id = store.open_bets()[0]["bet_id"]
        store.update_latest(bet_id, 27.5, -150)
        store.close_bet(bet_id, "closed", 27.5, -150)
        store.export_csv()
        with store.csv_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["player"], "Damian Lillard")
        self.assertEqual(row["news"], "Lillard expects a lighter minutes load.")
        self.assertEqual(row["category"], "Minutes & Usage")
        self.assertEqual(row["side"], "Under")
        self.assertEqual(row["market"], "Points")
        self.assertEqual(row["line at news"], "28.5")
        self.assertEqual(row["odds at news"], "-137")
        self.assertEqual(row["closing line"], "27.5")
        self.assertEqual(row["closing price"], "-150")
        self.assertEqual(row["CLV"], "+1.00")
        self.assertEqual(row["result"], "")
        store.grade_bet(bet_id, 1, 22)
        store.export_csv()
        with store.csv_path.open(encoding="utf-8-sig", newline="") as handle:
            graded = list(csv.DictReader(handle))[0]
        self.assertEqual(graded["result"], "22")
        self.assertEqual(graded["hit/loss"], "hit")


if __name__ == "__main__":
    unittest.main()
