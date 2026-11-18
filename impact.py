"""Who to bet when a starter is ruled out.

This season only. The bet is the teammate market with the biggest jump in
the games that starter was off the court. A starter with no such games, and
a bench injury, has no line.
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

    def bet(self, team: str, player: str) -> dict | None:
        current = self._find("2026-27", team, player)
        if current is None or current.get("absent_role") != "Starter":
            return None
        if current.get("boost_pct") is None or current.get("out_avg") is None:
            return None
        return current

    def lines(self, team: str, player: str, quote: dict | None = None) -> list[str]:
        current = self.bet(team, player)
        if current is None:
            return []
        return [_format(current, quote)]

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


def _format(row: dict, quote: dict | None = None) -> str:
    market = str(row["market"])
    price = ""
    if quote and quote.get("line") is not None and quote.get("price") is not None:
        book = quote.get("book") or "DraftKings"
        price = f" {_num(quote['line'])} ({_american(int(quote['price']))}) {book}"
    usage = ""
    if row.get("usage_pct") is not None:
        usage = (
            f" Usage {float(row['usage_pct']):+.0f}% "
            f"({float(row['usage_in']):.2f} to {float(row['usage_out']):.2f} shots per minute)."
        )
    games = int(row.get("games") or 0)
    return (
        f"Bet {row['beneficiary']} {market} over{price}. "
        f"{market} {float(row['boost_pct']):+.0f}% when {row['absent']} is off the court. "
        f"Average off the court: {_num(row['out_avg'])}. "
        f"Average on the court: {_num(row['baseline'])}."
        f"{usage} "
        f"{games} games this season."
    )


def _american(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


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
