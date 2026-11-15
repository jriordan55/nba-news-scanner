"""Who to bet when a starter is ruled out.

This season only, including a starter who has not missed a game yet. The bet
is the bench teammate who already produces that starter's stat. A bench
injury has no line.
"""

from __future__ import annotations

import json
from pathlib import Path

from names import names_match

TEAM = {
    "NO": "NOP",
    "NOP": "NOP",
    "NY": "NYK",
    "NYK": "NYK",
    "GS": "GSW",
    "GSW": "GSW",
    "SA": "SAS",
    "SAS": "SAS",
    "UTAH": "UTA",
    "UTA": "UTA",
    "WSH": "WAS",
    "WAS": "WAS",
    "PHO": "PHX",
    "PHX": "PHX",
    "BRK": "BKN",
    "BKN": "BKN",
    "CHO": "CHA",
    "CHA": "CHA",
}


class Impacts:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def lines(self, team: str, player: str) -> list[str]:
        current = self._find("2026-27", team, player)
        if current is None or current.get("absent_role") != "Starter":
            return []
        return [_format(current)]

    def _find(self, season: str, team: str, player: str) -> dict | None:
        wanted = _team(team)
        hits = [
            row
            for row in self.rows
            if row.get("season") == season and names_match(row.get("absent", ""), player)
        ]
        if wanted:
            hits = [row for row in hits if _team(row.get("team", "")) == wanted]
        if len(hits) == 1:
            return hits[0]
        return None


def load_impacts(path: Path) -> Impacts:
    if not path.exists():
        return Impacts([])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Impacts([])
    rows = payload.get("impacts") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return Impacts([])
    return Impacts(rows)


def _format(row: dict) -> str:
    games = int(row.get("games") or 0)
    unit = str(row["market"]).lower()
    return (
        f"Bet {row['beneficiary']} {row['market']} over. "
        f"Averaging {_num(row['baseline'])} {unit} and {_num(row['minutes_baseline'])} minutes "
        f"over {games} games this season."
    )


def _team(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return TEAM.get(text.upper(), text.upper())


def _num(value) -> str:
    number = float(value)
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.1f}"
