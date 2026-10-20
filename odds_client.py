"""The Odds API client. PrizePicks props live on the us_dfs region."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from config import ABBR_BY_NAME, PROP_MARKETS, TEAMS
from names import names_match

log = logging.getLogger("nba-scanner")

ODDS_ROOT = "https://api.the-odds-api.com/v4"
_KEY_RE = re.compile(r"apiKey=[^&\s]+", re.IGNORECASE)


class OddsError(RuntimeError):
    pass


@dataclass
class Offer:
    book: str
    book_key: str
    market: str
    player: str
    side: str
    line: float
    price: int


@dataclass
class Board:
    event_id: str
    home: str
    away: str
    commence: datetime
    offers: list[Offer]

    @property
    def players(self) -> list[str]:
        seen: list[str] = []
        for offer in self.offers:
            if offer.player not in seen:
                seen.append(offer.player)
        return seen

    @property
    def game(self) -> str:
        return f"{_abbr(self.away)} @ {_abbr(self.home)}"


@dataclass
class Leg:
    player: str
    team: str
    market: str
    market_label: str
    side: str
    line: float
    price: int
    book: str
    book_key: str
    event_id: str
    game: str
    home: str
    away: str
    game_start: datetime
    confidence: float
    reason: str


class OddsClient:
    def __init__(self, api_key: str, session: requests.Session):
        self.api_key = api_key
        self.session = session
        self._events: list[dict] = []
        self._events_at = 0.0
        self._boards: dict[tuple[str, str], tuple[float, Board]] = {}
        self.disabled_until = 0.0

    def event_for_team(self, abbr: str, now: datetime, max_hours: int) -> dict | None:
        names = {name.casefold() for name in TEAMS.get(abbr, ())}
        if not names:
            return None
        chosen = None
        for event in self._upcoming():
            home = str(event.get("home_team", "")).casefold()
            away = str(event.get("away_team", "")).casefold()
            if home not in names and away not in names:
                continue
            commence = _parse_time(event.get("commence_time"))
            if commence is None:
                continue
            if commence <= now + timedelta(minutes=2):
                continue
            if commence > now + timedelta(hours=max_hours):
                continue
            if chosen is None or event["commence_time"] < chosen["commence_time"]:
                chosen = event
        return chosen

    def board(self, event: dict, prefer: str = "prizepicks", ttl: float = 120) -> Board:
        if time.time() < self.disabled_until:
            raise OddsError("odds api paused after an auth failure")
        event_id = event["id"]
        cached = self._boards.get((event_id, prefer))
        if cached and time.time() - cached[0] < ttl:
            return cached[1]
        if prefer == "prizepicks":
            payload = self._props(event_id, region="us_dfs", books="prizepicks")
        else:
            payload = self._props(event_id, region="us", books="draftkings,fanduel")
        board = Board(
            event_id=event_id,
            home=event.get("home_team", ""),
            away=event.get("away_team", ""),
            commence=_parse_time(event.get("commence_time")) or datetime.now(timezone.utc),
            offers=parse_offers(payload),
        )
        self._boards[(event_id, prefer)] = (time.time(), board)
        return board

    def reprice(self, event_id: str, home: str, away: str, commence: datetime, book_key: str, ttl: float) -> Board:
        event = {
            "id": event_id,
            "home_team": home,
            "away_team": away,
            "commence_time": commence.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        prefer = "prizepicks" if book_key == "prizepicks" else "retail"
        return self.board(event, prefer=prefer, ttl=ttl)

    def _upcoming(self) -> list[dict]:
        if time.time() - self._events_at > 600 or not self._events:
            payload = self._get(f"{ODDS_ROOT}/sports/basketball_nba/events")
            if not isinstance(payload, list):
                raise OddsError("events response was not a list")
            self._events = payload
            self._events_at = time.time()
            log.info("nba slate has %s events", len(self._events))
        return self._events

    def _props(self, event_id: str, region: str, books: str) -> dict:
        return self._get(
            f"{ODDS_ROOT}/sports/basketball_nba/events/{event_id}/odds",
            {
                "regions": region,
                "markets": PROP_MARKETS,
                "oddsFormat": "american",
                "bookmakers": books,
            },
        )

    def _get(self, url: str, params: dict | None = None) -> dict | list:
        query = {"apiKey": self.api_key, **(params or {})}
        try:
            response = self.session.get(url, params=query, timeout=30)
        except requests.RequestException as exc:
            raise OddsError(_safe(str(exc))) from exc
        remaining = response.headers.get("x-requests-remaining")
        spent = response.headers.get("x-requests-last")
        log.debug("odds %s status=%s spent=%s remaining=%s", url, response.status_code, spent, remaining)
        if response.status_code in (401, 403):
            self.disabled_until = time.time() + 1800
            raise OddsError(f"odds api returned {response.status_code}. Check ODDS_API_KEY. Pausing odds calls for 30 minutes.")
        if response.status_code == 429:
            raise OddsError("odds api rate limited")
        if response.status_code >= 400:
            raise OddsError(f"odds api {response.status_code}: {_safe(response.text[:240])}")
        if remaining and int(float(remaining)) < 1000:
            log.warning("odds api credits almost gone: %s remaining", remaining)
        return response.json()


def parse_offers(payload: dict) -> list[Offer]:
    offers: list[Offer] = []
    for book in payload.get("bookmakers") or []:
        for market in book.get("markets") or []:
            for outcome in market.get("outcomes") or []:
                if outcome.get("name") not in ("Over", "Under"):
                    continue
                if outcome.get("point") is None or outcome.get("price") is None:
                    continue
                player = str(outcome.get("description") or "").strip()
                if not player:
                    continue
                offers.append(
                    Offer(
                        book=str(book.get("title") or book.get("key")),
                        book_key=str(book.get("key")),
                        market=str(market.get("key")),
                        player=player,
                        side=str(outcome["name"]),
                        line=float(outcome["point"]),
                        price=int(outcome["price"]),
                    )
                )
    return offers


def choose_main_line(offers: list[Offer], player: str, market: str, side: str) -> Offer | None:
    """Prefer the two-way line closest to standard PrizePicks juice.

    Goblin and demon ladders show up as extra points with lopsided prices.
    The main line is the point where Over and Under are both posted and the
    two prices sit nearest each other, then nearest -137.
    """
    grouped: dict[float, dict[str, Offer]] = {}
    for offer in offers:
        if offer.market != market or not names_match(offer.player, player):
            continue
        grouped.setdefault(offer.line, {})[offer.side] = offer
    best: tuple[tuple, Offer] | None = None
    for line, sides in grouped.items():
        if side not in sides:
            continue
        if "Over" in sides and "Under" in sides:
            gap = abs(sides["Over"].price - sides["Under"].price)
            juice = abs(sides["Over"].price + 137) + abs(sides["Under"].price + 137)
            rank = (0, gap, juice, line)
        else:
            rank = (1, abs(sides[side].price + 137), 0, line)
        if best is None or rank < best[0]:
            best = (rank, sides[side])
    return None if best is None else best[1]


def _abbr(full_name: str) -> str:
    return ABBR_BY_NAME.get(full_name.casefold(), full_name)


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe(text: str) -> str:
    return _KEY_RE.sub("apiKey=REDACTED", text)
