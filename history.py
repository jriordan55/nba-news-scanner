"""Last season's DraftKings results when a teammate was out.

Built from the 2025-26 regular season. Players who changed teams before
this season are left out of the file, so they keep the normal news rules.
"""

from __future__ import annotations

import json
from pathlib import Path

from names import names_match

MIN_GAMES = 8
KEEP = 0.56
ADD_GAINER = 0.58
POINTS = "player_points"
REBOUNDS = "player_rebounds"
ASSISTS = "player_assists"
THREES = "player_threes"
PRA = "player_points_rebounds_assists"
ORDER = (POINTS, PRA, REBOUNDS, ASSISTS, THREES)


class History:
    def __init__(self, payload: dict):
        self.when_out = payload.get("when_out") or {}
        self.lighter = payload.get("lighter") or {}

    def under_markets(self, player: str) -> list[str] | None:
        record = self._lighter(player)
        if record is None or not record.get("markets"):
            return None
        return _clearing(record["markets"], "unders")

    def over_markets(self, outs: list[str], player: str) -> list[str] | None:
        pair = self.pair(outs, player)
        if pair is None or not pair.get("markets"):
            return None
        return _clearing(pair["markets"], "overs")

    def lighter_note(self, player: str) -> str:
        record = self._lighter(player)
        if record is None:
            return ""
        return _hit((record.get("markets") or {}).get(POINTS), "unders")

    def over_note(self, outs: list[str], player: str) -> tuple[str, str]:
        pair = self.pair(outs, player)
        if pair is None:
            return "", ""
        markets = pair.get("markets") or {}
        points = markets.get(POINTS) or next(iter(markets.values()), None)
        return pair.get("out") or "", _hit(points, "overs")

    def best_gainer(self, out_player: str) -> dict | None:
        entry = self._out(out_player)
        if entry is None:
            return None
        best = None
        best_rate = 0.0
        for gainer in entry.get("gainers") or []:
            rate = _rate((gainer.get("markets") or {}).get(POINTS), "overs")
            if rate is None or rate < ADD_GAINER or rate <= best_rate:
                continue
            best = gainer
            best_rate = rate
        if best is None:
            return None
        chosen = dict(best)
        chosen["team"] = entry.get("team") or ""
        chosen["out"] = entry.get("player") or out_player
        chosen["markets_to_bet"] = _clearing(chosen.get("markets") or {}, "overs")
        return chosen

    def pair(self, outs: list[str], gainer: str) -> dict | None:
        for out_name in outs:
            entry = self._out(out_name)
            if entry is None:
                continue
            for row in entry.get("gainers") or []:
                if names_match(row.get("player", ""), gainer):
                    found = dict(row)
                    found["out"] = entry.get("player") or out_name
                    return found
        return None

    def _lighter(self, player: str) -> dict | None:
        for key, record in self.lighter.items():
            if names_match(key, player) or names_match(record.get("player", ""), player):
                return record
        return None

    def _out(self, player: str) -> dict | None:
        for key, record in self.when_out.items():
            if names_match(key, player) or names_match(record.get("player", ""), player):
                return record
        return None


def load_history(path: Path) -> History | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return History(payload)


def _rate(record: dict | None, side: str) -> float | None:
    if not record:
        return None
    games = int(record.get("n") or 0)
    if games < MIN_GAMES:
        return None
    return int(record.get(side) or 0) / games


def _clearing(markets: dict, side: str) -> list[str]:
    chosen = []
    for market in ORDER:
        rate = _rate(markets.get(market), side)
        if rate is not None and rate >= KEEP:
            chosen.append(market)
    return chosen


def _hit(record: dict | None, side: str) -> str:
    if not record or int(record.get("n") or 0) < MIN_GAMES:
        return ""
    hits = int(record.get(side) or 0)
    games = int(record["n"])
    word = "over" if side == "overs" else "under"
    return f"DraftKings {word} hit {hits} of {games} last regular season."
