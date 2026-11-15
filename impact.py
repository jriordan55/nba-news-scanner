"""Who to bet when a starter is ruled out.

This season only. The injured player has to be a starter. The bet is the
teammate whose usage rose the most while he was out, in the market with the
biggest percentage boost. A bench injury has no line here.
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
    games = int(row["out_games"])
    return (
        f"Bet {row['beneficiary']} {row['market']} over. "
        f"{round(float(row['boost_pct'])):+.0f}% this season "
        f"({_num(row['baseline'])} to {_num(row['out_avg'])}). "
        f"Hit {int(row['hit'])} of {games} above his usual. "
        f"Minutes {_num(row['minutes_baseline'])} to {_num(row['minutes_out'])} "
        f"({float(row['minutes_delta']):+.1f})."
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
