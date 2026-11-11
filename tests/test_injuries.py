import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feed import NewsItem
from injuries import Injury, events, next_state, parse_injuries, same_report
from model import interpret
from scanner import handle_item
from store import Store


def _injury(key: str, player: str, status: str, team: str = "ATL") -> Injury:
    return Injury(
        key=key,
        player=player,
        team=team,
        status=status,
        note=f"{player} is out tonight.",
        url=f"https://www.espn.com/nba/player/_/id/{key}/{player}",
        updated="2026-11-04T01:00Z",
    )


class InjuryTests(unittest.TestCase):
    def test_parse_maps_the_clippers_and_keeps_the_status(self):
        payload = {
            "injuries": [
                {
                    "displayName": "LA Clippers",
                    "injuries": [
                        {
                            "status": "Out",
                            "date": "2026-11-04T01:00Z",
                            "shortComment": "Beal is out against the Heat.",
                            "longComment": "Bradley Beal will miss a third straight game.",
                            "athlete": {
                                "displayName": "Bradley Beal",
                                "links": [
                                    {"href": "https://www.espn.com/nba/player/_/id/6580/bradley-beal"}
                                ],
                            },
                        },
                        {
                            "status": "Day-To-Day",
                            "date": "2026-11-04T01:00Z",
                            "shortComment": "Harden is day-to-day.",
                            "athlete": {"displayName": "James Harden", "links": []},
                        },
                    ],
                }
            ]
        }
        parsed = {item.player: item for item in parse_injuries(payload)}
        self.assertEqual(parsed["Bradley Beal"].team, "LAC")
        self.assertEqual(parsed["Bradley Beal"].key, "6580")
        self.assertEqual(parsed["Bradley Beal"].status, "Out")
        self.assertEqual(parsed["James Harden"].status, "Day-To-Day")

    def test_an_unchanged_report_is_not_polled_again(self):
        current = [_injury("1", "Jock Landale", "Out"), _injury("2", "Trae Young", "Day-To-Day")]
        previous = {item.key: item for item in current}
        self.assertTrue(same_report(previous, current))
        changed = [_injury("1", "Jock Landale", "Out"), _injury("2", "Trae Young", "Out")]
        self.assertFalse(same_report(previous, changed))

    def test_first_report_is_a_baseline(self):
        current = [_injury("1", "Jock Landale", "Out")]
        self.assertEqual(events({}, current), [])

    def test_new_out_is_a_watch_and_day_to_day_is_not(self):
        previous = {"9": _injury("9", "Trae Young", "Day-To-Day")}
        current = [
            _injury("9", "Trae Young", "Day-To-Day"),
            _injury("1", "Jock Landale", "Out"),
            _injury("2", "Bogdan Bogdanovic", "Day-To-Day"),
        ]
        found = events(previous, current)
        self.assertEqual([event.item.players[0] for event in found], ["Jock Landale"])
        signals = interpret(found[0].item)
        self.assertEqual(signals[0].kind, "out")
        self.assertIsNone(signals[0].side)
        held = {item.key for item in next_state(previous, current, set())}
        self.assertNotIn("1", held)
        self.assertIn("2", held)

    def test_leaving_the_report_after_being_out_is_an_over(self):
        previous = {"1": _injury("1", "Jock Landale", "Out")}
        found = events(previous, [])
        self.assertEqual(found[0].item.headline, "Jock Landale is cleared to play.")
        signals = interpret(found[0].item)
        self.assertEqual(signals[0].side, "Over")
        self.assertEqual(next_state(previous, [], set())[0].key, "1")
        self.assertEqual(next_state(previous, [], {"1"}), [])

    def test_an_injury_out_still_sends_a_watch(self):
        previous = {"9": _injury("9", "Trae Young", "Day-To-Day")}
        current = [_injury("1", "Jock Landale", "Out")]
        item = events(previous, current)[0].item
        sent: list[str] = []

        class Watch:
            def send_watch(self, card, outs):
                sent.append(outs[0].player)

            def send_priced(self, *args):
                raise AssertionError("a confirmed out is a watch, not a bet")

        class Config:
            lookback_hours = 12
            max_tip_hours = 96

        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            try:
                handle_item(
                    Config(),
                    store,
                    None,
                    Watch(),
                    {},
                    None,
                    set(),
                    item,
                    datetime.now(timezone.utc),
                )
                self.assertEqual(sent, ["Jock Landale"])
                self.assertTrue(store.alerted(item.id))
                other = NewsItem(
                    id="news-1",
                    team="BOS",
                    players=["Jayson Tatum"],
                    category="News",
                    published=datetime.now(timezone.utc),
                    headline="Tatum signed an extension.",
                    quote="",
                    url="https://fcp.news/example",
                )
                handle_item(
                    Config(),
                    store,
                    None,
                    Watch(),
                    {},
                    None,
                    set(),
                    other,
                    datetime.now(timezone.utc),
                )
                self.assertEqual(sent, ["Jock Landale"])
                self.assertFalse(store.alerted(other.id))
            finally:
                store.close()

    def test_day_to_day_becoming_out_alerts_once_status_is_saved(self):
        previous = {"1": _injury("1", "Jock Landale", "Day-To-Day")}
        current = [_injury("1", "Jock Landale", "Out")]
        found = events(previous, current)
        self.assertEqual(len(found), 1)
        saved = next_state(previous, current, {found[0].key})
        self.assertEqual(saved[0].status, "Out")
        self.assertEqual(events({item.key: item for item in saved}, current), [])
