"""24/7 injury scanner.

The only alert is a starter ruled out. It names who is out and who to bet
from this season's usage. Other news is not read.
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
from config import FCP_OUT_URLS, MARKET_LABELS, ROOT, load_config
from fcp_session import attach as attach_fcp_session
from history import load_history
from impact import load_impacts
from feed import USER_AGENT, NewsItem, parse_html
from injuries import Injury, events as injury_events, fetch_injuries, next_state, same_report
from model import interpret
from odds_client import OddsClient, OddsError, choose_main_line
from results import grade_settled
from store import Store

log = logging.getLogger("nba-scanner")
RETRY_UNPRICED = timedelta(hours=6)
# Injuries are checked on their own. The news crawl can sit on share images
# and odds calls, and an out cannot wait behind that.
INJURY_POLL_SECONDS = 2
ESPN_POLL_SECONDS = 30


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
    log.info("watching starter outs on %s", ", ".join(FCP_OUT_URLS))
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
    odds = OddsClient(config.odds_api_key, session)
    alerter = Alerter(config, session, impacts)
    store = Store(config.data_dir)
    noted: set[str] = set()
    last_espn = 0.0
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                _scan_fcp_outs(config, store, odds, alerter, beneficiaries, history, noted)
            except Exception:
                log.exception("full court press injury scan failed")
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
    """Alert a Full Court Press out as soon as the card is on the page."""
    session = odds.session
    now = datetime.now(timezone.utc)
    seen: set[str] = set()
    for url in FCP_OUT_URLS:
        try:
            response = session.get(url, timeout=8, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
        except requests.RequestException as exc:
            log.warning("full court press out feed failed for %s: %s", url, exc)
            continue
        for item in parse_html(response.text):
            if item.id in seen or item.paywalled:
                continue
            seen.add(item.id)
            if store.seen(item.id) and store.alerted(item.id):
                continue
            outs = [
                signal
                for signal in interpret(item, roster=item.players, beneficiaries=beneficiaries, history=history)
                if signal.kind == "out"
            ]
            if not outs:
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
    fresh = [signal for signal in worth if store.take_watch(signal, item)]
    if fresh:
        alerter.send_watch(item, fresh)
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
