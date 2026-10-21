"""24/7 Full Court Press scanner.

Reads https://fcp.news/?all=1 (and the minutes, role, injury, and health
feeds), prices a side at PrizePicks through The Odds API, and keeps the
alert price next to the closing price in data/clv.csv.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests

from alerts import Alerter
from config import FEED_URLS, MARKET_LABELS, ROOT, load_config
from fcp_session import attach as attach_fcp_session
from history import load_history
from feed import NewsItem, fetch_feed
from model import OUT_RE, UP_RE, Signal, classify, interpret
from names import mentions_player, names_match, norm
from odds_client import Board, Leg, OddsClient, OddsError, choose_main_line
from store import Store

log = logging.getLogger("nba-scanner")
RETRY_UNPRICED = timedelta(hours=6)


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
    alerter = Alerter(config, session)
    beneficiaries = _load_beneficiaries(ROOT / "beneficiaries.json")
    history = load_history(ROOT / "absence_history.json")
    store.export_csv()
    log.info("watching %s", ", ".join(FEED_URLS))
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
    noted: set[str] = set()
    last_beat = time.time()
    while True:
        started = time.time()
        try:
            cycle(config, store, odds, alerter, beneficiaries, history, noted)
            refresh_closes(store, odds)
        except Exception:
            log.exception("scan cycle failed")
        if once:
            log.info("single pass finished")
            store.export_csv()
            return
        if time.time() - last_beat > 600:
            last_beat = time.time()
            log.info(
                "still watching. seen=%s open_bets=%s",
                store.count_seen(),
                len(store.open_bets()),
            )
        time.sleep(max(1.0, config.poll_seconds - (time.time() - started)))


def cycle(config, store, odds, alerter, beneficiaries, history, noted: set[str]) -> None:
    session = odds.session
    items: list[NewsItem] = []
    for url in FEED_URLS:
        try:
            items.extend(fetch_feed(session, url))
        except requests.RequestException as exc:
            log.warning("feed read failed for %s: %s", url, exc)
        time.sleep(0.4)
    seen_ids: set[str] = set()
    now = datetime.now(timezone.utc)
    for item in items:
        if item.id in seen_ids or store.seen(item.id):
            continue
        seen_ids.add(item.id)
        try:
            handle_item(config, store, odds, alerter, beneficiaries, history, noted, item, now)
        except OddsError as exc:
            log.error("odds lookup failed, will retry this card (%s): %s", item.headline, exc)
        except Exception:
            log.exception("could not handle %s", item.headline)


def handle_item(config, store, odds, alerter, beneficiaries, history, noted, item: NewsItem, now: datetime) -> None:
    if item.paywalled or not item.url:
        store.mark_seen(item, alerted=False)
        return
    if item.published and item.published < now - timedelta(hours=config.lookback_hours):
        store.mark_seen(item, alerted=False)
        return

    event = None
    board = None
    if not item.players and item.team and _might_name_a_player(item):
        event = odds.event_for_team(item.team, now, config.max_tip_hours)
        if event is not None:
            board = odds.board(event)
            mentioned = [name for name in board.players if mentions_player(item.text, name, board.players)]
            if 0 < len(mentioned) <= 3:
                item.players = mentioned

    preliminary = interpret(item, roster=item.players, beneficiaries=beneficiaries, history=history)
    if not preliminary:
        store.mark_seen(item, alerted=False)
        return

    if event is None and item.team:
        event = odds.event_for_team(item.team, now, config.max_tip_hours)
    if event is None:
        log.info(
            "no game inside %s hours for %s, so this card is not priced: %s",
            config.max_tip_hours,
            item.team or "?",
            item.headline,
        )
        outs = [signal for signal in preliminary if signal.kind == "out"]
        fresh = [signal for signal in outs if store.take_watch(signal, item)]
        if fresh:
            alerter.send_watch(item, fresh)
        store.mark_seen(item, alerted=False)
        return
    if board is None:
        try:
            board = odds.board(event)
        except OddsError as exc:
            log.warning("could not price %s: %s", item.headline, exc)
            fresh = [signal for signal in preliminary if signal.kind == "out" and store.take_watch(signal, item)]
            if fresh:
                alerter.send_watch(item, fresh)
            if not any(signal.side for signal in preliminary):
                store.mark_seen(item, alerted=bool(fresh))
            return
    roster = list(dict.fromkeys([*item.players, *board.players]))
    signals = interpret(item, roster=roster, beneficiaries=beneficiaries, history=history)
    outs = [signal for signal in signals if signal.kind == "out"]
    priced = [signal for signal in signals if signal.side]

    legs: list[Leg] = []
    if priced:
        legs = _price(priced, board, prefer_retail=False)
        missing = [signal for signal in priced if not _covered(signal, legs)]
        if missing and not odds.quota_exhausted:
            retail = odds.board(event, prefer="retail")
            legs.extend(_price(missing, retail, prefer_retail=True))
        legs = _prefer_prizepicks(legs)
        legs = [
            leg
            for leg in legs
            if not store.has_open(leg.event_id, leg.player, leg.market, leg.side)
        ]

    fresh_outs = [signal for signal in outs if store.take_watch(signal, item)]
    if legs:
        alerter.send_priced(item, legs, outs)
        store.insert_legs(item, legs)
        store.mark_seen(item, alerted=True)
        return
    if fresh_outs:
        alerter.send_watch(item, fresh_outs)
    if not priced:
        store.mark_seen(item, alerted=False)
        return
    if item.id not in noted:
        noted.add(item.id)
        log.info(
            "no line yet for %s (%s). Will keep checking until the card is 6 hours old.",
            ", ".join(signal.player for signal in priced),
            item.headline,
        )
    if item.published is None or item.published < now - RETRY_UNPRICED:
        store.mark_seen(item, alerted=False)


def refresh_closes(store: Store, odds: OddsClient) -> None:
    now = datetime.now(timezone.utc)
    groups: dict[tuple[str, str], list] = {}
    for bet in store.open_bets():
        groups.setdefault((bet["event_id"], bet["book_key"]), []).append(bet)
    changed = False
    for (event_id, book_key), bets in groups.items():
        start = datetime.fromisoformat(bets[0]["game_start"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        ttl = 0 if start - now <= timedelta(minutes=10) else 180
        try:
            board = odds.reprice(
                event_id,
                bets[0]["home"],
                bets[0]["away"],
                start,
                book_key,
                ttl,
            )
        except OddsError as exc:
            log.warning("close refresh failed for %s: %s", bets[0]["game"], exc)
            continue
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


def _price(signals: list[Signal], board: Board, prefer_retail: bool) -> list[Leg]:
    legs: list[Leg] = []
    for signal in signals:
        if not signal.side:
            continue
        for market in signal.markets:
            offer = choose_main_line(board.offers, signal.player, market, signal.side)
            if offer is None:
                continue
            if market == "player_threes" and offer.line < 1.5:
                continue
            legs.append(
                Leg(
                    player=offer.player,
                    team=signal.team,
                    market=market,
                    market_label=MARKET_LABELS.get(market, market),
                    side=signal.side,
                    line=offer.line,
                    price=offer.price,
                    book=offer.book,
                    book_key=offer.book_key,
                    event_id=board.event_id,
                    game=board.game,
                    home=board.home,
                    away=board.away,
                    game_start=board.commence,
                    confidence=signal.confidence,
                    reason=signal.reason,
                )
            )
    if prefer_retail:
        return legs
    return legs


def _covered(signal: Signal, legs: list[Leg]) -> bool:
    return any(names_match(signal.player, leg.player) for leg in legs)


def _prefer_prizepicks(legs: list[Leg]) -> list[Leg]:
    chosen: dict[tuple[str, str, str], Leg] = {}
    for leg in legs:
        key = (norm(leg.player), leg.market, leg.side)
        current = chosen.get(key)
        if current is None or (leg.book_key == "prizepicks" and current.book_key != "prizepicks"):
            chosen[key] = leg
    return list(chosen.values())


def _might_name_a_player(item: NewsItem) -> bool:
    return classify(item.text) is not None or bool(OUT_RE.search(item.text) or UP_RE.search(item.text))


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
