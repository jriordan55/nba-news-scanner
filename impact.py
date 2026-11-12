"""Who gains the most, and in which market, when a rotation player is out.

The file is the regular-season study through November 10, 2026. A teammate
counts only if he is still on that team and played at least two extra minutes.
The market is whichever of points, rebounds, assists, and threes rose the
most as a percentage of his nights with the absent player available. The hit
rate is how often that stat beat his usual average on the absence nights.
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
        last = self._find("2025-26", team, player)
        if current is None:
            return [_format(last)] if last else []
        found = [_format(current)]
        if last and current["out_games"] < 4 and last["out_games"] >= 4:
            found.append(_format(last, lead="Last season"))
        return found

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


def _format(row: dict, lead: str = "Impact") -> str:
    spot = "same position" if row.get("same_position") else "different position"
    role = str(row.get("role") or "teammate").lower()
    pos = row.get("beneficiary_pos") or ""
    who = row["beneficiary"]
    if pos:
        who = f"{who} ({pos}, {role}, {spot})"
    season = "this season" if row.get("season") == "2026-27" else "last season"
    games = int(row["out_games"])
    return (
        f"{lead}: {who} {row['market']} {round(float(row['boost_pct'])):+.0f}% "
        f"({_num(row['baseline'])} to {_num(row['out_avg'])}). "
        f"Hit {int(row['hit'])} of {games} above his usual. "
        f"Minutes {_num(row['minutes_baseline'])} to {_num(row['minutes_out'])} "
        f"({float(row['minutes_delta']):+.1f}). "
        f"{games} games {season}."
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
