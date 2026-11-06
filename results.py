"""Fill the sheet with the box score after the game.

Hit and loss are graded on the line saved when the news arrived.
A player who does not play is a void. The closing line is a separate column.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from config import ABBR_BY_NAME
from names import names_match

log = logging.getLogger("nba-scanner")

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
EASTERN = ZoneInfo("America/New_York")
LOOK_AFTER = timedelta(hours=2)
GIVE_UP_AFTER = timedelta(hours=8)
RETRY_SECONDS = 15 * 60

_next_try: dict[str, float] = {}


def outcome(side: str, line: float, played: bool, actual: float | None) -> tuple[str, str]:
    """Return the stat to show and hit, loss, push, or void."""
    if not played:
        return "DNP", "void"
    if actual is None:
        return "", ""
    if actual > line:
        hit = "hit" if side == "Over" else "loss"
    elif actual < line:
        hit = "hit" if side == "Under" else "loss"
    else:
        hit = "push"
    number = int(actual) if float(actual).is_integer() else actual
    return str(number), hit


def grade_settled(store, session: requests.Session) -> None:
    """Grade one slate per pass. A final game is saved and not fetched again."""
    pending = []
    now = datetime.now(timezone.utc)
    for bet in store.pending_grades():
        start = _start(bet["game_start"])
        if start is None or now < start + LOOK_AFTER:
            continue
        pending.append(bet)
    if not pending:
        return
    by_day: dict[str, list] = {}
    for bet in pending:
        start = _start(bet["game_start"])
        if start is None:
            continue
        by_day.setdefault(start.astimezone(EASTERN).strftime("%Y%m%d"), []).append(bet)
    clock = time.monotonic()
    for day in sorted(by_day):
        if clock < _next_try.get(day, 0):
            continue
        try:
            changed = _grade_day(store, session, day, by_day[day], now)
        except requests.RequestException as exc:
            log.warning("box score read failed for %s: %s", day, exc)
            _next_try[day] = clock + RETRY_SECONDS
            return
        if changed:
            store.export_csv()
        return


def finals(payload: dict) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for event in payload.get("events") or []:
        status = (event.get("status") or {}).get("type") or {}
        if not status.get("completed"):
            continue
        competition = (event.get("competitions") or [{}])[0]
        abbrs = [
            ((team.get("team") or {}).get("abbreviation") or "")
            for team in competition.get("competitors") or []
        ]
        abbrs = [abbr for abbr in abbrs if abbr]
        if len(abbrs) == 2 and event.get("id"):
            found[tuple(sorted(abbrs))] = str(event["id"])
    return found


def box_lines(payload: dict) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    for team in (payload.get("boxscore") or {}).get("players") or []:
        for block in team.get("statistics") or []:
            for athlete in block.get("athletes") or []:
                name = ((athlete.get("athlete") or {}).get("displayName")) or ""
                if name:
                    rows.append((name, line_from_athlete(athlete)))
    return rows


def line_from_athlete(athlete: dict) -> dict:
    stats = athlete.get("stats") or []
    if athlete.get("didNotPlay") or len(stats) < 7:
        return {"played": False}
    minutes = _number(str(stats[0]).split(":", 1)[0])
    if minutes is None or minutes <= 0:
        return {"played": False}
    points = _number(stats[1])
    threes = _number(stats[3])
    rebounds = _number(stats[5])
    assists = _number(stats[6])
    pra = None if None in (points, rebounds, assists) else points + rebounds + assists
    return {
        "played": True,
        "Points": points,
        "Rebounds": rebounds,
        "Assists": assists,
        "Threes": threes,
        "PRA": pra,
    }


def _grade_day(store, session: requests.Session, day: str, bets: list, now: datetime) -> bool:
    board = session.get(SCOREBOARD, params={"dates": day}, timeout=20, headers=_headers())
    board.raise_for_status()
    done = finals(board.json())
    boxes: dict[str, list[tuple[str, dict]]] = {}
    changed = False
    waiting = False
    for bet in bets:
        key = _pair(bet["home"], bet["away"])
        if key is None:
            store.grade_bet(bet["bet_id"], None, None)
            changed = True
            continue
        event_id = done.get(key)
        if event_id is None:
            start = _start(bet["game_start"])
            if start is not None and now >= start + GIVE_UP_AFTER:
                store.grade_bet(bet["bet_id"], None, None)
                changed = True
            else:
                waiting = True
            continue
        if event_id not in boxes:
            summary = session.get(SUMMARY, params={"event": event_id}, timeout=20, headers=_headers())
            summary.raise_for_status()
            boxes[event_id] = box_lines(summary.json())
        match = _find(boxes[event_id], bet["player"])
        if match is None:
            start = _start(bet["game_start"])
            if start is not None and now >= start + GIVE_UP_AFTER:
                store.grade_bet(bet["bet_id"], None, None)
                changed = True
            else:
                waiting = True
            continue
        actual = match.get(bet["market"]) if match.get("played") else None
        store.grade_bet(bet["bet_id"], 1 if match.get("played") else 0, actual)
        changed = True
        log.info(
            "result %s %s %s %s",
            bet["side"],
            bet["player"],
            bet["market"],
            "DNP" if not match.get("played") else actual,
        )
    _next_try[day] = time.monotonic() + (RETRY_SECONDS if waiting else 0)
    return changed


def _find(rows: list[tuple[str, dict]], player: str) -> dict | None:
    for name, line in rows:
        if names_match(name, player):
            return line
    return None


def _pair(home: str, away: str) -> tuple[str, str] | None:
    home_abbr = ABBR_BY_NAME.get((home or "").casefold())
    away_abbr = ABBR_BY_NAME.get((away or "").casefold())
    if not home_abbr or not away_abbr:
        return None
    return tuple(sorted((home_abbr, away_abbr)))


def _start(value: str) -> datetime | None:
    if not value:
        return None
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _number(value) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"--", "-"}:
        return None
    head = text.split("-", 1)[0].replace("+", "")
    try:
        return float(head)
    except ValueError:
        return None


def _headers() -> dict[str, str]:
    return {"User-Agent": "Mozilla/5.0"}
