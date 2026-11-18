"""24/7 injury scanner.

A starter ruled out, or a questionable tag the injury history says will miss,
sends one alert: the teammate market with the biggest jump this season, the
average when the starter is off the court, and the DraftKings line. Coachspeak
is read only when the words are a clear over or under. Nothing is sent once
that team's game has tipped.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests

from alerts import Alerter
from config import COACHSPEAK_URL, INJURY_URL, MARKET_LABELS, ROOT, load_config
from fcp_session import attach as attach_fcp_session
from history import load_history
from impact import load_impacts
from feed import USER_AGENT, NewsItem, parse_html, parse_injury_page
from injuries import Injury, events as injury_events, fetch_injuries, next_state, same_report
from model import interpret, obvious_side, spoken_market
from odds_client import Leg, OddsClient, OddsError, choose_main_line
from outlook import anticipate_out
from results import grade_settled
from store import Store

log = logging.getLogger("nba-scanner")
RETRY_UNPRICED = timedelta(hours=6)
# Injuries are checked on their own. The news crawl can sit on share images
# and odds calls, and an out cannot wait behind that.
INJURY_POLL_SECONDS = 1
ESPN_POLL_SECONDS = 30
COACH_POLL_SECONDS = 15
PRICE_WAIT = timedelta(minutes=20)
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_ABBR = {
    "GS": "GSW",
    "NY": "NYK",
    "NO": "NOP",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
    "PHO": "PHX",
    "BRK": "BKN",
}
_LIVE_TEAMS: tuple[float, set[str]] = (0.0, set())
LABEL_TO_MARKET = {label: key for key, label in MARKET_LABELS.items()}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    config = load_config()
    _setup_logging(config.data_dir)
    store = Store(config.data_dir)
    if "--status" in args:
        print("\n".join(store.status_lines()))
        return 0
    if not config.odds_api_key:
        log.error("Set ODDS_API_KEY in .env before starting.")
        return 1
    lock = _Lock(config.data_dir / "scanner.lock")
    if not lock.acquire():
        log.error("Another scanner is already running. Close that window before starting a second one.")
        return 2
    try:
        run(config, store, once="--once" in args)
    finally:
        try:
            store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        lock.release()
        store.close()
    return 0


def run(config, store: Store, once: bool) -> None:
    session = requests.Session()
    if attach_fcp_session(session, config.data_dir):
        log.info("signed in to Full Court Press")
    else:
        log.info("Full Court Press session is not saved, so locked quotes stay hidden")
    odds = OddsClient(config.odds_api_key, session)
    impacts = load_impacts(ROOT / "impact.json")
    alerter = Alerter(config, session, impacts)
    beneficiaries = _load_beneficiaries(ROOT / "beneficiaries.json")
    history = load_history(ROOT / "absence_history.json")
    store.export_csv()
    log.info("watching %s", INJURY_URL)
    log.info("clv file: %s", store.csv_path)
    if config.alerts_configured:
        channels = []
        if config.discord_webhook:
            channels.append("Discord")
        if config.telegram_token and config.telegram_chat_id:
            channels.append("Telegram")
        log.info("alerts go to %s", " and ".join(channels))
    else:
        log.info("Discord and Telegram are blank. Alerts will be written to the log and data/clv.csv.")
    log.info("odds api is called only for a player or teammate an alert is about")
    noted: set[str] = set()
    last_beat = time.time()
    raw_limit = os.environ.get("RUN_FOR_SECONDS", "").strip()
    deadline = time.time() + int(raw_limit) if raw_limit else None
    if deadline is not None:
        log.info("this run watches for %s minutes", max(1, int(raw_limit) // 60))
    stop = threading.Event()
    injury_thread = None
    if not once:
        injury_thread = threading.Thread(
            target=_injury_loop,
            args=(config, stop, beneficiaries, history, impacts),
            name="injuries",
            daemon=True,
        )
        injury_thread.start()
        log.info("injury report is checked every %s seconds", INJURY_POLL_SECONDS)
    try:
        while True:
            started = time.time()
            try:
                cycle(config, store, odds, alerter, beneficiaries, history, noted, injuries=once)
                refresh_closes(store, odds)
                grade_settled(store, odds.session)
            except Exception:
                log.exception("scan cycle failed")
            if once or (deadline is not None and time.time() >= deadline):
                log.info("single pass finished" if once else "watch window finished")
                store.export_csv()
                return
            if time.time() - last_beat > 600:
                last_beat = time.time()
                log.info(
                    "still watching. seen=%s open_bets=%s",
                    store.count_seen(),
                    len(store.open_bets()),
                )
            remaining = config.poll_seconds - (time.time() - started)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        stop.set()
        if injury_thread is not None:
            injury_thread.join(timeout=5)


def cycle(config, store, odds, alerter, beneficiaries, history, noted: set[str], injuries: bool = False) -> None:
    """Close out open bets. A one-pass run also reads the injury report."""
    if injuries:
        _scan_injuries(config, store, odds, alerter, beneficiaries, history, noted)


def _injury_loop(config, stop: threading.Event, beneficiaries, history, impacts) -> None:
    """Poll Full Court Press outs without waiting for the news crawl."""
    session = requests.Session()
    attach_fcp_session(session, config.data_dir)
    odds = OddsClient(config.odds_api_key, session)
    alerter = Alerter(config, session, impacts)
    store = Store(config.data_dir)
    noted: set[str] = set()
    last_espn = 0.0
    last_coach = 0.0
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                _scan_fcp_outs(config, store, odds, alerter, beneficiaries, history, noted)
            except Exception:
                log.exception("full court press injury scan failed")
            if time.monotonic() - last_coach >= COACH_POLL_SECONDS:
                last_coach = time.monotonic()
                try:
                    _scan_coachspeak(config, store, odds, alerter, now=datetime.now(timezone.utc))
                except Exception:
                    log.exception("coachspeak scan failed")
            if time.monotonic() - last_espn >= ESPN_POLL_SECONDS:
                last_espn = time.monotonic()
                try:
                    _scan_injuries(config, store, odds, alerter, beneficiaries, history, noted)
                except Exception:
                    log.exception("injury scan failed")
            remaining = INJURY_POLL_SECONDS - (time.monotonic() - started)
            if remaining > 0:
                stop.wait(remaining)
    finally:
        store.close()


def _scan_fcp_outs(config, store, odds, alerter, beneficiaries, history, noted) -> None:
    """Alert the injury page the second a new out, or a projected out, is posted."""
    session = odds.session
    now = datetime.now(timezone.utc)
    try:
        response = session.get(INJURY_URL, timeout=8, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("injury page failed: %s", exc)
        return
    items = parse_injury_page(response.text, now)
    if not store.seen("fcp-injury-baseline"):
        for item in items:
            store.mark_seen(item, alerted=True)
        store.mark_seen(_marker("fcp-injury-baseline"), alerted=True)
        log.info("injury page baseline saved (%s cards). Alerts start on the next update.", len(items))
        return
    for item in items:
        if store.seen(item.id) and store.alerted(item.id):
            continue
        if item.published and item.published < now - timedelta(hours=config.lookback_hours):
            store.mark_seen(item, alerted=True)
            continue
        text = item.text
        outs = [
            signal
            for signal in interpret(item, roster=item.players, beneficiaries=beneficiaries, history=history)
            if signal.kind == "out"
        ]
        if not outs and anticipate_out(text) and item.players:
            item.headline = f"{item.players[0]} is ruled out (projected). {item.headline}"
            outs = [
                signal
                for signal in interpret(item, roster=item.players, beneficiaries=beneficiaries, history=history)
                if signal.kind == "out"
            ]
        if not outs:
            store.mark_seen(item, alerted=True)
            continue
        item.category = "Injury report"
        try:
            handle_item(config, store, odds, alerter, beneficiaries, history, noted, item, now)
        except OddsError as exc:
            log.error("odds lookup failed, will retry this out (%s): %s", item.headline, exc)
        except Exception:
            log.exception("could not handle out %s", item.headline)


def _scan_injuries(config, store, odds, alerter, beneficiaries, history, noted) -> None:
    """Alert when the ESPN injury report changes. The first read is a baseline."""
    try:
        current = fetch_injuries(odds.session)
    except requests.RequestException as exc:
        log.warning("injury report failed: %s", exc)
        return
    if len({injury.team for injury in current}) < 20:
        log.warning("injury report looked partial (%s teams), skipping", len({injury.team for injury in current}))
        return
    previous = {row["athlete_key"]: _stored_injury(row) for row in store.injury_rows()}
    if not previous:
        store.replace_injuries([injury.row() for injury in current])
        log.info("injury baseline saved for %s players. Alerts start on the next change.", len(current))
        return
    if same_report(previous, current):
        return
    resolved: set[str] = set()
    now = datetime.now(timezone.utc)
    for event in injury_events(previous, current):
        try:
            handle_item(config, store, odds, alerter, beneficiaries, history, noted, event.item, now)
        except OddsError as exc:
            log.error("odds lookup failed, will retry this injury (%s): %s", event.item.headline, exc)
            continue
        except Exception:
            log.exception("could not handle injury %s", event.item.headline)
            continue
        if store.seen(event.item.id):
            resolved.add(event.key)
    store.replace_injuries(
        [injury.row() for injury in next_state(previous, current, resolved)]
    )


def _stored_injury(row) -> Injury:
    return Injury(
        key=row["athlete_key"],
        player=row["player"],
        team=row["team"],
        status=row["status"],
        note=row["note"] or "",
        url=row["url"] or "",
        updated=row["updated_at"] or "",
    )


def _usage_bets(alerter, item: NewsItem, outs: list) -> list:
    """Starters whose absence already changed a teammate's usage this season."""
    impacts = getattr(alerter, "impacts", None)
    if impacts is None:
        return []
    return [
        signal
        for signal in outs
        if impacts.lines(signal.team or item.team, signal.player)
    ]


def _scan_coachspeak(config, store, odds, alerter, now) -> None:
    """One alert when a coach's words are a clear over or under."""
    session = odds.session
    try:
        response = session.get(COACHSPEAK_URL, timeout=8, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("coachspeak failed: %s", exc)
        return
    items = [item for item in parse_html(response.text) if item.url and not item.paywalled]
    if not store.seen("coachspeak-baseline"):
        for item in items:
            store.mark_seen(item, alerted=True)
        store.mark_seen(_marker("coachspeak-baseline"), alerted=True)
        log.info("coachspeak baseline saved (%s cards). Alerts start on the next update.", len(items))
        return
    for item in items:
        if store.alerted(item.id):
            continue
        if item.published and item.published < now - timedelta(hours=config.lookback_hours):
            store.mark_seen(item, alerted=True)
            continue
        side = obvious_side(item.text)
        if side is None or not item.players:
            store.mark_seen(item, alerted=True)
            continue
        if _game_underway(odds, item.team, now):
            log.info("no alert during the game for %s", item.team or item.headline)
            store.mark_seen(item, alerted=True)
            continue
        player = item.players[0]
        market = spoken_market(item.text)
        quote = None
        if odds is not None:
            ready, quote = _price_player(odds, config, item, player, market, side, now)
            if not ready:
                continue
        book = ""
        if quote:
            book = f" {_num_line(quote['line'])} ({_american(quote['price'])}) {quote['book']}"
        text = f"{item.team} {player} {side} {market}{book}.\n{item.headline}"
        if item.url:
            text += f"\n{item.url}"
        alerter.deliver(text)
        if quote:
            _save_leg(store, item, player, item.team, market, side, quote, item.headline)
        store.mark_seen(item, alerted=True)


def _attach_prices(odds, config, impacts, item, outs, now) -> tuple[bool, dict]:
    """DraftKings first. Wait up to 20 minutes if the number is not posted yet."""
    quotes: dict = {}
    waiting = False
    for signal in outs:
        bet = impacts.bet(signal.team or item.team, signal.player) if hasattr(impacts, "bet") else None
        if bet is None:
            continue
        event = odds.event_for_team(bet.get("team") or item.team or "", now, config.max_tip_hours)
        if event is None:
            continue
        quote = _lookup(odds, event, bet["beneficiary"], bet["market"], "Over")
        if quote is None:
            if not _aged(item, now):
                waiting = True
            continue
        quote["reason"] = impacts.lines(signal.team or item.team, signal.player, quote)[0]
        quotes[signal.player] = quote
    if waiting:
        return False, {}
    return True, quotes


def _price_player(odds, config, item, player, market, side, now) -> tuple[bool, dict | None]:
    event = odds.event_for_team(item.team or "", now, config.max_tip_hours)
    if event is None:
        return True, None
    quote = _lookup(odds, event, player, market, side)
    if quote is None and not _aged(item, now):
        return False, None
    return True, quote


def _lookup(odds, event, player, market, side) -> dict | None:
    key = LABEL_TO_MARKET.get(market)
    if not key:
        return None
    offer = None
    board = None
    for prefer in ("retail", "pick6"):
        board = odds.board(event, [key], prefer=prefer, ttl=15)
        offer = choose_main_line(board.offers, player, key, side)
        if offer is not None:
            break
    if offer is None or board is None:
        return None
    book = "DraftKings" if offer.book_key == "draftkings" else offer.book
    return {
        "line": offer.line,
        "price": offer.price,
        "book": book,
        "book_key": offer.book_key,
        "event_id": board.event_id,
        "game": board.game,
        "home": board.home,
        "away": board.away,
        "game_start": board.commence,
    }


def _save_quotes(store, item, impacts, outs, quotes) -> None:
    for signal in outs:
        quote = quotes.get(signal.player)
        if not quote:
            continue
        bet = impacts.bet(signal.team or item.team, signal.player)
        if bet is None:
            continue
        _save_leg(
            store,
            item,
            bet["beneficiary"],
            bet.get("team") or item.team,
            bet["market"],
            "Over",
            quote,
            quote.get("reason") or item.headline,
        )


def _save_leg(store, item, player, team, market, side, quote, reason) -> None:
    leg = Leg(
        player=player,
        team=team or item.team,
        market=market,
        market_label=market,
        side=side,
        line=float(quote["line"]),
        price=int(quote["price"]),
        book=quote["book"],
        book_key=quote["book_key"],
        event_id=quote["event_id"],
        game=quote["game"],
        home=quote["home"],
        away=quote["away"],
        game_start=quote["game_start"],
        confidence=1.0,
        reason=reason,
    )
    if store.has_open(leg.event_id, leg.player, leg.market, leg.side, leg.home, leg.away):
        return
    store.insert_legs(item, [leg])


def _game_underway(odds, team: str, now: datetime) -> bool:
    """A tipped game is not an alert. Pregame news still sends."""
    if odds is None or not team:
        return False
    try:
        if odds.in_progress(team, now):
            return True
    except OddsError as exc:
        log.warning("could not check the odds slate (%s)", exc)
    session = getattr(odds, "session", None)
    if session is None:
        return False
    return team in _live_teams(session)


def _live_teams(session) -> set[str]:
    """ESPN abbreviations of games that are in progress. Cached for 20 seconds."""
    global _LIVE_TEAMS
    fetched, teams = _LIVE_TEAMS
    if time.monotonic() - fetched < 20:
        return teams
    try:
        response = session.get(SCOREBOARD_URL, timeout=8, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        teams = _scoreboard_live(response.json())
    except (requests.RequestException, ValueError) as exc:
        log.warning("scoreboard check failed: %s", exc)
        return teams
    _LIVE_TEAMS = (time.monotonic(), teams)
    return teams


def _scoreboard_live(payload: dict) -> set[str]:
    live: set[str] = set()
    for event in payload.get("events") or []:
        state = ((event.get("status") or {}).get("type") or {}).get("state")
        if state != "in":
            continue
        for competition in event.get("competitions") or []:
            for side in competition.get("competitors") or []:
                abbr = str((side.get("team") or {}).get("abbreviation") or "").upper()
                abbr = ESPN_ABBR.get(abbr, abbr)
                if abbr:
                    live.add(abbr)
    return live


def _aged(item: NewsItem, now: datetime) -> bool:
    if item.published is None:
        return True
    published = item.published
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return now - published >= PRICE_WAIT


def _marker(news_id: str) -> NewsItem:
    return NewsItem(
        id=news_id,
        team="",
        players=[],
        category="",
        published=None,
        headline=news_id,
        quote="",
        url="",
    )


def _num_line(value) -> str:
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text


def _american(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def handle_item(config, store, odds, alerter, beneficiaries, history, noted, item: NewsItem, now: datetime) -> None:
    """Send the one starter-out alert. Every other card is closed with no message."""
    if item.paywalled:
        return
    if not item.url:
        store.mark_seen(item, alerted=False)
        return
    if item.published and item.published < now - timedelta(hours=config.lookback_hours):
        store.mark_seen(item, alerted=False)
        return
    if item.category != "Injury report":
        store.mark_seen(item, alerted=True)
        return

    signals = interpret(item, roster=item.players, beneficiaries=beneficiaries, history=history)
    outs = [signal for signal in signals if signal.kind == "out"]
    worth = _usage_bets(alerter, item, outs)
    if not worth:
        store.mark_seen(item, alerted=True)
        return
    teams = {item.team, *(signal.team for signal in worth)}
    if any(_game_underway(odds, team, now) for team in teams if team):
        log.info("no alert during the game for %s", item.team or ", ".join(signal.player for signal in worth))
        store.mark_seen(item, alerted=True)
        return
    quotes: dict = {}
    if odds is not None:
        ready, quotes = _attach_prices(odds, config, alerter.impacts, item, worth, now)
        if not ready:
            return
    fresh = [signal for signal in worth if store.take_watch(signal, item)]
    if fresh:
        alerter.quotes = quotes
        alerter.send_watch(item, fresh)
        _save_quotes(store, item, alerter.impacts, fresh, quotes)
    store.mark_seen(item, alerted=True)


def refresh_closes(store: Store, odds: OddsClient) -> None:
    """Update a close only from a board already fetched for an alert.

    A separate poll of every open bet would call the API when nobody new
    is being alerted.
    """
    now = datetime.now(timezone.utc)
    groups: dict[str, list] = {}
    for bet in store.open_bets():
        groups.setdefault(bet["event_id"], []).append(bet)
    changed = False
    for event_id, bets in groups.items():
        board = odds.cached_board(event_id)
        if board is None:
            continue
        start = datetime.fromisoformat(bets[0]["game_start"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        for bet in bets:
            offer = choose_main_line(board.offers, bet["player"], _market_key(bet["market"]), bet["side"])
            tip = now >= start
            if offer is not None:
                moved = offer.line != bet["latest_line"] or offer.price != bet["latest_price"]
                if moved:
                    log.info(
                        "line move %s %s %s %s (%s) -> %s (%s)",
                        bet["side"],
                        bet["player"],
                        bet["market"],
                        bet["latest_line"],
                        bet["latest_price"],
                        offer.line,
                        offer.price,
                    )
                if tip:
                    store.close_bet(bet["bet_id"], "closed", offer.line, offer.price)
                    changed = True
                elif moved:
                    store.update_latest(bet["bet_id"], offer.line, offer.price)
                    changed = True
            elif tip:
                status = "pulled"
                store.close_bet(bet["bet_id"], status, bet["latest_line"], bet["latest_price"])
                log.info(
                    "line pulled at tip for %s %s %s; close is the last price seen",
                    bet["side"],
                    bet["player"],
                    bet["market"],
                )
                changed = True
    if changed:
        store.export_csv()


def _market_key(label: str) -> str:
    for key, name in MARKET_LABELS.items():
        if name == label:
            return key
    return label


def _load_beneficiaries(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    players = payload.get("players") or {}
    return {str(key): [str(name) for name in names] for key, names in players.items()}


def _setup_logging(data_dir: Path) -> None:
    logger = logging.getLogger("nba-scanner")
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(
        data_dir / "scanner.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream)


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.handle.close()
        self.handle = None


if __name__ == "__main__":
    raise SystemExit(main())
