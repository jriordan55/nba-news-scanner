import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feed import NewsItem
from history import History
from model import interpret
from names import names_match


def card(**kwargs) -> NewsItem:
    base = dict(
        id="n1",
        team="POR",
        players=["Damian Lillard"],
        category="Minutes & Usage",
        published=datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc),
        headline="",
        quote="",
        url="https://x.com/example/status/1",
    )
    base.update(kwargs)
    return NewsItem(**base)


class ModelTests(unittest.TestCase):
    def test_wont_play_a_minutes_number_is_an_under(self):
        item = card(
            headline="Lillard expects a lighter minutes load because Portland's guard depth means he won't play 38 a night.",
            quote="I'm not going to be playing 38 minutes per night.",
        )
        signals = interpret(item)
        self.assertEqual([signal.kind for signal in signals], ["minutes_down"])
        self.assertEqual(signals[0].side, "Under")

    def test_coming_off_the_bench_is_an_under(self):
        item = card(
            team="POR",
            players=["Ja Morant"],
            headline="Morant would come off the bench in the season opener.",
            quote="he'd be coming off the bench in the season opener",
        )
        signals = interpret(item)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, "Under")
        self.assertIn("player_points", signals[0].markets)
        self.assertNotIn("player_assists", signals[0].markets)

    def test_a_lifted_minutes_cap_is_an_over(self):
        item = card(
            team="DAL",
            players=["Morez Johnson Jr."],
            headline="Morez Johnson Jr. plays without the 17 minute cap for the first time tonight.",
            quote="tonight is the first night Morez Johnson will not be on a 17 minutes restriction",
        )
        signals = interpret(item)
        self.assertEqual([signal.side for signal in signals], ["Over"])
        self.assertNotIn("lighter load", signals[0].reason)

    def test_no_minutes_restriction_is_an_over(self):
        item = card(
            team="HOU",
            players=["Fred VanVleet", "Steven Adams"],
            headline="VanVleet and Adams face no minutes restriction and Houston will be smart on back-to-backs.",
            quote="Fred VanVleet and Steven Adams have no minutes restrictions.",
        )
        signals = interpret(item)
        self.assertEqual({signal.player: signal.side for signal in signals}, {
            "Fred VanVleet": "Over",
            "Steven Adams": "Over",
        })

    def test_no_longer_on_a_limit_is_an_over(self):
        item = card(
            team="WAS",
            players=["AJ Dybantsa"],
            headline="Dybantsa is free of his minutes restriction and can play as much as the game needs.",
            quote="AJ Dybantsa will no longer be on a minutes limit.",
        )
        signals = interpret(item)
        self.assertEqual([signal.side for signal in signals], ["Over"])

    def test_still_on_a_minutes_restriction_is_an_under(self):
        item = card(
            team="DAL",
            players=["Morez Johnson Jr."],
            headline="Johnson is still on a minutes restriction.",
            quote="Morez Johnson Jr. has made big plays while on a minutes restriction.",
        )
        signals = interpret(item)
        self.assertEqual([signal.side for signal in signals], ["Under"])

    def test_lighter_minutes_is_an_under(self):
        item = card(
            headline="Lillard expects a lighter minutes load because of Portland's guard depth.",
            quote="I'm not going to be playing 38 minutes per night.",
        )
        signals = interpret(item)
        self.assertEqual([signal.side for signal in signals], ["Under"])
        self.assertEqual(signals[0].player, "Damian Lillard")
        self.assertIn("player_points", signals[0].markets)
        self.assertIn("player_points_rebounds_assists", signals[0].markets)

    def test_a_start_and_a_heavy_minute_load_are_overs(self):
        start = card(
            team="POR",
            players=["Donovan Clingan"],
            headline="Clingan is almost certain to start while Williams plays a lot with the smaller lineups.",
            quote="Donovan Clingan is almost certain to start.",
        )
        heavy = card(
            team="IND",
            players=["Tyrese Haliburton"],
            headline="Haliburton is set for a good chunk of minutes in this game.",
            quote="Tyrese Haliburton is set for a good chunk of minutes.",
        )
        self.assertEqual([signal.side for signal in interpret(start)], ["Over"])
        self.assertEqual([signal.side for signal in interpret(heavy)], ["Over"])

    def test_not_starting_or_fewer_touches_is_an_under(self):
        bench = card(
            team="POR",
            players=["Carlson"],
            headline="Carlson and Potter are not starting and can play backup four or five.",
            quote="Carlson is not starting.",
        )
        touches = card(
            team="OKC",
            players=["Jalen Williams"],
            headline="Williams is getting fewer touches than before in his current role but is doing it well.",
            quote="Jalen Williams is getting fewer touches.",
        )
        self.assertEqual([signal.side for signal in interpret(bench)], ["Under"])
        self.assertEqual([signal.side for signal in interpret(touches)], ["Under"])

    def test_practice_notes_are_not_a_bet(self):
        item = card(
            team="NOP",
            players=["Yves Missi"],
            headline="Missi was special in practice today.",
            quote="Yves Missi was special in practice today.",
        )
        self.assertEqual(interpret(item), [])

    def test_bigger_role_is_an_over_and_picks_up_playmaking(self):
        item = card(
            team="MIL",
            players=["Ryan Rollins"],
            headline="Rollins has a much bigger role controlling the offense.",
            quote="It's a lot more responsibility. Leadership of controlling the offense.",
        )
        signals = interpret(item)
        self.assertEqual(signals[0].side, "Over")
        self.assertIn("player_assists", signals[0].markets)

    def test_ball_in_hands_adds_assists(self):
        item = card(
            team="PHX",
            players=["Jalen Green"],
            headline="Jalen Green will have the ball a lot with chances to score and facilitate.",
            quote="He's going to have the ball in his hands a lot so he can score and facilitate.",
        )
        signals = interpret(item)
        self.assertEqual(signals[0].side, "Over")
        self.assertIn("player_assists", signals[0].markets)

    def test_already_out_is_not_an_under(self):
        item = card(
            team="DAL",
            players=["Dereck Lively II", "Santi Aldama"],
            headline="Santi Aldama and Dereck Lively II are already out for Wednesday's opener at Houston.",
            quote="Aldama and Lively are already out.",
        )
        signals = interpret(item)
        self.assertEqual(sorted(signal.player for signal in signals), ["Dereck Lively II", "Santi Aldama"])
        self.assertTrue(all(signal.kind == "out" and signal.side is None for signal in signals))

    def test_a_full_court_press_out_card_is_a_watch(self):
        powell = card(
            team="CHI",
            players=["Norman Powell"],
            category="",
            headline="Powell out for second consecutive game.",
            quote="Norman Powell will sit out a second straight game.",
        )
        signals = interpret(powell)
        self.assertEqual([signal.kind for signal in signals], ["out"])
        self.assertIsNone(signals[0].side)
        playing = card(
            team="NYK",
            players=["Karl-Anthony Towns"],
            category="",
            headline="KAT cleared to play with calf.",
            quote="Mike Brown said KAT (calf) will play.",
        )
        self.assertTrue(all(signal.kind != "out" for signal in interpret(playing)))

    def test_confirmed_out_is_not_an_under(self):
        item = card(
            team="OKC",
            players=["Nikola Topic"],
            headline="Topic is out for the season opener against San Antonio.",
            quote="Nikola Topic (back surgery) is out.",
        )
        signals = interpret(item)
        self.assertEqual([signal.kind for signal in signals], ["out"])
        self.assertIsNone(signals[0].side)

    def test_return_to_the_lineup_is_an_over(self):
        item = card(
            team="LAC",
            players=["Rui Hachimura", "Derrick Jones Jr.", "Max Strus"],
            headline="Hachimura and Jones Jr. are back against the Heat and Strus is cleared too.",
            quote=(
                "Rui Hachimura and Derrick Jones Jr. are all back tomorrow vs. the Heat. "
                "Max Strus is also not on the injury report and is cleared to play."
            ),
        )
        signals = interpret(item)
        overs = {signal.player for signal in signals if signal.side == "Over"}
        self.assertEqual(overs, {"Rui Hachimura", "Derrick Jones Jr.", "Max Strus"})

    def test_started_over_splits_the_two_players(self):
        item = card(
            team="BOS",
            players=["Jayson Tatum", "Mitchell Robinson", "Neemias Queta"],
            headline="Queta started over Robinson for the first time while Tatum's minutes with each have been split.",
            quote="Queta, who started over Mitch for the first time last night.",
        )
        signals = interpret(item)
        by_player = {signal.player: signal.side for signal in signals}
        self.assertEqual(by_player.get("Neemias Queta"), "Over")
        self.assertEqual(by_player.get("Mitchell Robinson"), "Under")
        self.assertNotIn("Jayson Tatum", by_player)

    def test_ball_handler_is_an_assists_over(self):
        item = card(
            team="NOP",
            players=["Trendon Watford"],
            headline="Watford brought the ball up and initiated offense.",
            quote="He was able to bring the ball up, initiate our offense, be a very good playmaker.",
        )
        signals = interpret(item)
        self.assertEqual(signals[0].side, "Over")
        self.assertIn("player_assists", signals[0].markets)

    def test_unnamed_on_the_card_still_gets_the_start(self):
        item = card(
            team="TOR",
            players=["Jakob Poeltl"],
            headline="Poeltl is a late scratch against the Lakers.",
            quote="Jakob Poeltl is a late scratch. Trayce Jackson-Davis starts in his place.",
        )
        signals = interpret(item)
        overs = [signal for signal in signals if signal.side == "Over"]
        self.assertEqual(overs[0].player, "Trayce Jackson-Davis")
        self.assertTrue(any(signal.player == "Jakob Poeltl" and signal.side is None for signal in signals))

    def test_named_replacement_becomes_the_over(self):
        item = card(
            team="OKC",
            players=["Nikola Topic"],
            headline="Topic is out for the opener.",
            quote="Nikola Topic is out. Isaiah Hartenstein will start in his place.",
        )
        signals = interpret(item, roster=["Nikola Topic", "Isaiah Hartenstein"])
        overs = [signal for signal in signals if signal.side == "Over"]
        self.assertEqual(len(overs), 1)
        self.assertEqual(overs[0].player, "Isaiah Hartenstein")
        self.assertFalse(any(signal.side == "Under" for signal in signals))

    def test_rotation_map_is_used_when_the_post_names_nobody(self):
        item = card(
            team="OKC",
            players=["Chet Holmgren"],
            headline="Holmgren is out tonight.",
            quote="Chet Holmgren (ankle) is out.",
        )
        signals = interpret(
            item,
            roster=["Chet Holmgren"],
            beneficiaries={"Chet Holmgren": ["Isaiah Hartenstein"]},
        )
        overs = [signal for signal in signals if signal.side == "Over"]
        self.assertEqual(overs[0].player, "Isaiah Hartenstein")

    def test_available_star_does_not_inherit_a_teammates_out_line(self):
        item = card(
            team="OKC",
            players=["Chet Holmgren"],
            headline="Holmgren is available for the season opener after a quad contusion kept him out of preseason.",
            quote="Chet Holmgren is available to play tomorrow. Otega Oweh (torn Achilles) is out.",
        )
        signals = interpret(item, roster=["Chet Holmgren", "Otega Oweh"])
        self.assertEqual(signals, [])

    def test_questionable_and_undecided_and_extension_are_not_bets(self):
        questionable = card(
            team="PHI",
            players=["Anfernee Simons", "Dean Wade"],
            headline="Simons and Wade are questionable at New York.",
            quote="Anfernee Simons – hamstring – QUESTIONABLE. Dean Wade – ankle – QUESTIONABLE.",
        )
        undecided = card(
            team="MIL",
            players=["Kyle Kuzma"],
            headline="Kuzma's starting bench or closing role is undecided.",
            quote="Whether that's starting, coming off the bench, finishing games, we'll sort through that.",
        )
        extension = card(
            team="BKN",
            players=["Noah Clowney"],
            headline="Clowney got no rookie extension and now plays out a prove-it season in a new role.",
            quote="Nets don't offer Noah Clowney a rookie extension.",
        )
        self.assertEqual(interpret(questionable), [])
        self.assertEqual(interpret(undecided), [])
        self.assertEqual(interpret(extension), [])

    def test_paywall_is_ignored(self):
        item = card(headline="Sign in to read this takeaway and the full quote it came from.", players=[])
        self.assertEqual(interpret(item), [])

    def test_history_drops_a_light_load_that_cleared_the_over(self):
        history = History(
            {
                "lighter": {
                    "damian lillard": {
                        "player": "Damian Lillard",
                        "markets": {
                            "player_points": {"n": 10, "overs": 7, "unders": 3},
                            "player_points_rebounds_assists": {"n": 10, "overs": 6, "unders": 4},
                        },
                    }
                }
            }
        )
        item = card(headline="Lillard expects a lighter minutes load.", quote="")
        self.assertEqual(interpret(item, history=history), [])

    def test_history_adds_the_teammate_who_cleared_draftkings(self):
        history = History(
            {
                "when_out": {
                    "chet holmgren": {
                        "player": "Chet Holmgren",
                        "team": "OKC",
                        "gainers": [
                            {
                                "player": "Isaiah Hartenstein",
                                "markets": {
                                    "player_points": {"n": 12, "overs": 8, "unders": 4},
                                    "player_rebounds": {"n": 12, "overs": 9, "unders": 3},
                                    "player_assists": {"n": 12, "overs": 4, "unders": 8},
                                },
                            }
                        ],
                    }
                }
            }
        )
        item = card(
            team="OKC",
            players=["Chet Holmgren"],
            headline="Chet Holmgren is out tonight.",
            quote="",
        )
        overs = [signal for signal in interpret(item, history=history) if signal.side == "Over"]
        self.assertEqual([signal.player for signal in overs], ["Isaiah Hartenstein"])
        self.assertIn("player_points", overs[0].markets)
        self.assertIn("player_rebounds", overs[0].markets)
        self.assertNotIn("player_assists", overs[0].markets)

    def test_history_drops_a_light_load_that_cleared_the_over(self):
        history = History(
            {
                "lighter": {
                    "damian lillard": {
                        "player": "Damian Lillard",
                        "markets": {
                            "player_points": {"n": 10, "overs": 7, "unders": 3},
                            "player_points_rebounds_assists": {"n": 10, "overs": 6, "unders": 4},
                        },
                    }
                }
            }
        )
        item = card(headline="Lillard expects a lighter minutes load.", quote="")
        self.assertEqual(interpret(item, history=history), [])

    def test_history_adds_the_teammate_who_cleared_draftkings(self):
        history = History(
            {
                "when_out": {
                    "chet holmgren": {
                        "player": "Chet Holmgren",
                        "team": "OKC",
                        "gainers": [
                            {
                                "player": "Isaiah Hartenstein",
                                "markets": {
                                    "player_points": {"n": 12, "overs": 8, "unders": 4},
                                    "player_rebounds": {"n": 12, "overs": 9, "unders": 3},
                                    "player_assists": {"n": 12, "overs": 4, "unders": 8},
                                },
                            }
                        ],
                    }
                }
            }
        )
        item = card(
            team="OKC",
            players=["Chet Holmgren"],
            headline="Chet Holmgren is out tonight.",
            quote="",
        )
        overs = [signal for signal in interpret(item, history=history) if signal.side == "Over"]
        self.assertEqual([signal.player for signal in overs], ["Isaiah Hartenstein"])
        self.assertIn("player_points", overs[0].markets)
        self.assertIn("player_rebounds", overs[0].markets)
        self.assertNotIn("player_assists", overs[0].markets)

    def test_accent_and_suffix_names_match(self):
        self.assertTrue(names_match("Nikola Topić", "Nikola Topic"))
        self.assertTrue(names_match("Dereck Lively II", "Dereck Lively"))


if __name__ == "__main__":
    unittest.main()
