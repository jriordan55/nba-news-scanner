"""Opponent defense vs position, from the Full Court Press matchup page.

Rank 1 allows the most. The numbers supplement an alert. They do not pick a side.
"""

from __future__ import annotations

import logging
import re
import time

import requests

from config import ABBR_BY_NAME
from feed import USER_AGENT
from names import names_match

log = logging.getLogger("nba-scanner")

_NAME = re.compile(r'"dvp-full","children":"([^"]+)"')
_TITLE = re.compile(r'"title":"([^"]* allow [^"]*)"')
_CELL = re.compile(
    r"^(?P<defense>.+?) allow (?P<value>\d+(?:\.\d+)?) (?P<stat>pts|reb|ast|fg3m) "
    r"to (?P<pos>PG|SG|SF|PF|C)s? "
    r"\(league (?P<league>\d+(?:\.\d+)?), (?P<delta>[+\-]?\d+(?:\.\d+)?)\), "
    r"(?P<rank>\d+)(?:st|nd|rd|th) softest of (?P<teams>\d+)$"
)
_STAT_FOR_MARKET = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
_LABEL = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes"}
_POS = {"PG": "PGs", "SG": "SGs", "SF": "SFs", "PF": "PFs", "C": "Cs"}
_CACHE: dict[str, tuple[float, "Matchup | None"]] = {}
_TTL = 6 * 60 * 60


class Cell:
    def __init__(self, defense: str, value: str, stat: str, position: str, league: str, delta: str, rank: int, teams: int):
        self.defense = defense
        self.value = value
        self.stat = stat
        self.position = position
        self.league = league
        self.delta = delta
        self.rank = rank
        self.teams = teams

    def sentence(self) -> str:
        return (
            f"Last 15: {self.defense} allow {self.value} {_LABEL[self.stat]} to {_POS[self.position]}, "
            f"{_ordinal(self.rank)} softest of {self.teams} (league {self.league}, {_signed(self.delta)})."
        )


class Spot:
    def __init__(self, player: str, cells: dict[str, Cell]):
        self.player = player
        self.cells = cells


class Matchup:
    def __init__(self, spots: list[Spot]):
        self.spots = spots

    def note(self, player: str, market: str) -> str:
        spot = next((item for item in self.spots if names_match(item.player, player)), None)
        if spot is None:
            return ""
        if market == "player_points_rebounds_assists":
            return _pra(spot)
        stat = _STAT_FOR_MARKET.get(market, "")
        cell = spot.cells.get(stat)
        return cell.sentence() if cell else ""


def matchup_for(session: requests.Session, away: str, home: str) -> Matchup | None:
    away_abbr = _abbr(away)
    home_abbr = _abbr(home)
    if not away_abbr or not home_abbr or away_abbr == home_abbr:
        return None
    key = "-".join(sorted((away_abbr, home_abbr)))
    cached = _CACHE.get(key)
    if cached is not None and time.time() - cached[0] < _TTL:
        return cached[1]
    matchup = _fetch(session, away_abbr, home_abbr) or _fetch(session, home_abbr, away_abbr)
    if matchup is not None:
        _CACHE[key] = (time.time(), matchup)
    return matchup


def parse_matchup(payload: str) -> Matchup | None:
    names = _NAME.findall(payload)
    titles = _TITLE.findall(payload)
    if not names or len(titles) != len(names) * 4:
        return None
    spots: list[Spot] = []
    for index, name in enumerate(names):
        cells: dict[str, Cell] = {}
        for title in titles[index * 4 : (index + 1) * 4]:
            cell = _parse_cell(title)
            if cell is None:
                return None
            cells[cell.stat] = cell
        if set(cells) != set(_LABEL):
            return None
        spots.append(Spot(name, cells))
    return Matchup(spots)


def _fetch(session: requests.Session, left: str, right: str) -> Matchup | None:
    url = f"https://fcp.news/matchup/{left}-{right}?dvp=last15"
    try:
        response = session.get(
            url,
            timeout=25,
            headers={"User-Agent": USER_AGENT, "RSC": "1", "Accept": "text/x-component"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("matchup read failed for %s-%s: %s", left, right, exc)
        return None
    matchup = parse_matchup(response.text)
    if matchup is None:
        log.warning("matchup page %s-%s did not include a defense table", left, right)
    else:
        log.info("matchup %s-%s last 15, %s starters", left, right, len(matchup.spots))
    return matchup


def _parse_cell(title: str) -> Cell | None:
    found = _CELL.match(title)
    if found is None:
        return None
    return Cell(
        defense=found.group("defense"),
        value=found.group("value"),
        stat=found.group("stat"),
        position=found.group("pos"),
        league=found.group("league"),
        delta=found.group("delta"),
        rank=int(found.group("rank")),
        teams=int(found.group("teams")),
    )


def _pra(spot: Spot) -> str:
    cell = spot.cells.get("pts")
    if cell is None:
        return ""
    bits = []
    for stat in ("pts", "reb", "ast"):
        item = spot.cells[stat]
        bits.append(f"{item.value} {stat} ({_ordinal(item.rank)})")
    return f"Last 15, {cell.defense} vs {_POS[cell.position]}: {', '.join(bits)}."


def _abbr(name: str) -> str:
    text = (name or "").strip()
    if text in ABBR_BY_NAME.values():
        return text
    return ABBR_BY_NAME.get(text.casefold(), "")


def _ordinal(rank: int) -> str:
    if 10 <= rank % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suffix}"


def _signed(raw: str) -> str:
    value = float(raw)
    if value > 0:
        return f"+{value:.1f}"
    return f"{value:.1f}"
