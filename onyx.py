"""NBA prices from Onyx.

https://app.onyxodds.com/leagues/NBA is a slice of the public board at
https://api.onyxodds.com/api/public/odds. The league filter on that URL
does not work, so the full board is downloaded once and NBA is kept here.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from odds_client import Offer

log = logging.getLogger("nba-scanner")

ONYX_URL = "https://api.onyxodds.com/api/public/odds"
MARKET_MAP = {
    "player_points": "player_points",
    "player_rebounds": "player_rebounds",
    "player_assists": "player_assists",
    "player_made_threes": "player_threes",
    "player_points_+_rebounds_+_assists": "player_points_rebounds_assists",
}


class OnyxError(RuntimeError):
    pass


class Onyx:
    def __init__(self, session: requests.Session):
        self.session = session
        self._loaded_at = 0.0
        self._games: list[dict] = []

    def games(self) -> list[dict]:
        self._load()
        return self._games

    def _load(self) -> None:
        if self._games and time.time() - self._loaded_at < 120:
            return
        try:
            response = self.session.get(
                ONYX_URL,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                    "Origin": "https://app.onyxodds.com",
                    "Referer": "https://app.onyxodds.com/",
                },
                timeout=45,
            )
        except requests.RequestException as exc:
            if self._games:
                log.warning("onyx board could not be refreshed: %s", type(exc).__name__)
                return
            raise OnyxError("onyx board could not be read") from exc
        if response.status_code >= 400:
            if self._games:
                log.warning("onyx board returned %s, using the board already loaded", response.status_code)
                return
            raise OnyxError(f"onyx board returned {response.status_code}")
        self._games = parse_nba(response.json())
        self._loaded_at = time.time()
        log.info("onyx nba slate has %s games", len(self._games))


def parse_nba(payload) -> list[dict]:
    raw_games = payload.values() if isinstance(payload, dict) else payload or []
    games: list[dict] = []
    for game in raw_games:
        if not isinstance(game, dict) or str(game.get("league") or "") != "NBA":
            continue
        start = _parse_time(game.get("startDate") or game.get("commence_time"))
        if start is None:
            continue
        games.append(
            {
                "id": str(game.get("ojId") or game.get("id") or ""),
                "home_team": str(game.get("homeTeam") or game.get("home_team") or ""),
                "away_team": str(game.get("awayTeam") or game.get("away_team") or ""),
                "commence_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "offers": _offers(game),
            }
        )
    return games


def _offers(game: dict) -> list[Offer]:
    offers: list[Offer] = []
    for market_key, selections in (game.get("markets") or {}).items():
        mapped = MARKET_MAP.get(str(market_key))
        if mapped is None or not isinstance(selections, dict):
            continue
        for sel in selections.values():
            if not isinstance(sel, dict) or sel.get("isMain") is not True:
                continue
            side = str(sel.get("selectionLine") or "").strip().title()
            if side not in {"Over", "Under"}:
                continue
            player = str(sel.get("selection") or "").strip()
            point = sel.get("betPoints")
            price = sel.get("lastPrice")
            if price is None:
                price = sel.get("price")
            if not player or point is None or price is None:
                continue
            offers.append(
                Offer(
                    book="Onyx",
                    book_key="onyx",
                    market=mapped,
                    player=player,
                    side=side,
                    line=float(point),
                    price=int(float(price)),
                )
            )
    return offers


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
