"""PrizePicks lines from their partner board.

Used when The Odds API monthly quota is used up. The partner feed publishes
the line, not the American price. A standard two-way PrizePicks line is -137
on both sides, which is the same juice the Odds API was returning.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from config import ABBR_BY_NAME
from odds_client import Offer

log = logging.getLogger("nba-scanner")

PARTNER_URL = "https://partner-api.prizepicks.com/projections"
STANDARD_PRICE = -137
STAT_TO_MARKET = {
    "Points": "player_points",
    "Rebounds": "player_rebounds",
    "Assists": "player_assists",
    "3-PT Made": "player_threes",
    "Pts+Rebs+Asts": "player_points_rebounds_assists",
}
ABBR_FIX = {
    "NY": "NYK",
    "WSH": "WAS",
    "UTAH": "UTA",
    "NO": "NOP",
    "SA": "SAS",
    "GS": "GSW",
    "PHO": "PHX",
}
DEAD_STATUS = {"suspended", "settled", "removed", "closed", "expired", "disabled"}


class PrizePicksError(RuntimeError):
    pass


class PrizePicks:
    def __init__(self, session: requests.Session):
        self.session = session
        self._loaded_at = 0.0
        self._rows: list[dict] = []

    def offers(self, home: str, away: str, commence: datetime) -> list[Offer]:
        self._load()
        sides = {_canon(_abbr(home)), _canon(_abbr(away))}
        sides.discard("")
        offers: list[Offer] = []
        for row in self._rows:
            if row["team"] not in sides:
                continue
            if abs((row["start"] - commence).total_seconds()) > 4 * 3600:
                continue
            for side in row["sides"]:
                offers.append(
                    Offer(
                        book="PrizePicks",
                        book_key="prizepicks",
                        market=row["market"],
                        player=row["player"],
                        side=side,
                        line=row["line"],
                        price=STANDARD_PRICE,
                    )
                )
        return offers

    def _load(self) -> None:
        if self._rows and time.time() - self._loaded_at < 120:
            return
        try:
            response = self.session.get(
                PARTNER_URL,
                params={"league_id": 7, "per_page": 1000},
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                },
                timeout=40,
            )
        except requests.RequestException as exc:
            if self._rows:
                log.warning("prizepicks board could not be refreshed: %s", type(exc).__name__)
                return
            raise PrizePicksError("prizepicks board could not be read") from exc
        if response.status_code == 429 and not self._rows:
            time.sleep(3)
            response = self.session.get(
                PARTNER_URL,
                params={"league_id": 7, "per_page": 1000},
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                timeout=40,
            )
        if response.status_code == 429 and self._rows:
            log.warning("prizepicks rate limited, using the board already loaded")
            return
        if response.status_code >= 400:
            if self._rows:
                log.warning("prizepicks board returned %s, using the board already loaded", response.status_code)
                return
            raise PrizePicksError(f"prizepicks board returned {response.status_code}")
        self._rows = parse_projections(response.json())
        self._loaded_at = time.time()
        log.info("prizepicks board has %s standard lines", len(self._rows))


def parse_projections(payload: dict) -> list[dict]:
    players: dict[str, dict] = {}
    for item in payload.get("included") or []:
        if item.get("type") == "new_player" and item.get("id"):
            players[str(item["id"])] = item.get("attributes") or {}
    rows: list[dict] = []
    for item in payload.get("data") or []:
        attrs = item.get("attributes") or {}
        if str(attrs.get("odds_type") or "").casefold() != "standard":
            continue
        if attrs.get("is_promo"):
            continue
        if str(attrs.get("projection_type") or "Single Stat") not in {"Single Stat", ""}:
            continue
        if str(attrs.get("status") or "").casefold() in DEAD_STATUS:
            continue
        market = STAT_TO_MARKET.get(str(attrs.get("stat_type") or ""))
        if market is None or attrs.get("line_score") is None:
            continue
        rel = (item.get("relationships") or {}).get("new_player") or {}
        player_id = str((rel.get("data") or {}).get("id") or "")
        person = players.get(player_id) or {}
        if person.get("combo"):
            continue
        name = str(person.get("name") or "").strip()
        team = _canon(str(person.get("team") or ""))
        if not name or " + " in name or not team:
            continue
        sides = _sides(attrs.get("allowed_wager_types"))
        start = _parse_time(attrs.get("start_time"))
        if not sides or start is None:
            continue
        rows.append(
            {
                "player": name,
                "team": team,
                "market": market,
                "line": float(attrs["line_score"]),
                "sides": sides,
                "start": start,
            }
        )
    return rows


def _sides(wager) -> list[str]:
    kind = str(wager or "").casefold()
    if kind in {"under_or_over", "over_or_under"}:
        return ["Over", "Under"]
    if kind == "over":
        return ["Over"]
    if kind == "under":
        return ["Under"]
    return []


def _canon(abbr: str) -> str:
    cleaned = abbr.strip().upper()
    return ABBR_FIX.get(cleaned, cleaned)


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
