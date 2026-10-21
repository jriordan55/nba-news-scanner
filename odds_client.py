"""Live NBA prop boards. Prices come from Onyx."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from config import ABBR_BY_NAME, TEAMS
from names import names_match

log = logging.getLogger("nba-scanner")


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
        self._onyx = None

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

    def board(self, event: dict, prefer: str = "onyx", ttl: float = 120) -> Board:
        event_id = str(event.get("id") or "")
        cached = self._boards.get((event_id, "onyx"))
        if cached and time.time() - cached[0] < ttl:
            return cached[1]
        match = self._onyx_game(event_id, event.get("home_team", ""), event.get("away_team", ""), _parse_time(event.get("commence_time")))
        if match is None:
            raise OddsError("onyx has no board for this game")
        board = Board(
            event_id=match["id"],
            home=match["home_team"],
            away=match["away_team"],
            commence=_parse_time(match["commence_time"]) or datetime.now(timezone.utc),
            offers=list(match["offers"]),
        )
        self._boards[(event_id, "onyx")] = (time.time(), board)
        self._boards[(match["id"], "onyx")] = (time.time(), board)
        log.info("priced %s from Onyx (%s lines)", board.game, len(board.offers))
        return board

    def reprice(self, event_id: str, home: str, away: str, commence: datetime, book_key: str, ttl: float) -> Board:
        event = {
            "id": event_id,
            "home_team": home,
            "away_team": away,
            "commence_time": commence.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return self.board(event, ttl=ttl)

    def _slate(self):
        from onyx import Onyx, OnyxError

        if self._onyx is None:
            self._onyx = Onyx(self.session)
        try:
            return self._onyx.games()
        except OnyxError as exc:
            raise OddsError(str(exc)) from exc

    def _onyx_game(self, event_id: str, home: str, away: str, commence: datetime | None) -> dict | None:
        games = self._slate()
        for game in games:
            if event_id and game["id"] == event_id:
                return game
        if not home or not away or commence is None:
            return None
        best = None
        best_gap = None
        for game in games:
            if game["home_team"].casefold() != home.casefold() or game["away_team"].casefold() != away.casefold():
                continue
            start = _parse_time(game["commence_time"])
            if start is None:
                continue
            gap = abs((start - commence).total_seconds())
            if gap > 4 * 3600:
                continue
            if best_gap is None or gap < best_gap:
                best = game
                best_gap = gap
        return best

    def _upcoming(self) -> list[dict]:
        if time.time() - self._events_at > 120 or not self._events:
            self._events = self._slate()
            self._events_at = time.time()
        return self._events


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
