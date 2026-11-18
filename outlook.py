"""Project a questionable tag from historical days missed.

The table is the typical absence on https://hashtagbasketball.com/nba-injury.
A questionable player is treated as an out only when that history says the
absence is long. A short illness or ordinary soreness is not a bet.
"""

from __future__ import annotations

import re

# phrase, typical days out, low end of where most cases fall
_TABLE = (
    ("fractured", 29, 14),
    ("fracture", 29, 14),
    ("surgery", 21, 14),
    ("torn ", 21, 14),
    ("rupture", 21, 14),
    ("strained hamstring", 12, 6),
    ("hamstring strain", 12, 6),
    ("sprained knee", 11, 4),
    ("knee sprain", 11, 4),
    ("strained calf", 11, 4),
    ("calf strain", 11, 4),
    ("concussion", 5, 3),
    ("sprained ankle", 5, 3),
    ("ankle sprain", 5, 3),
    ("sore knee", 4, 2),
    ("illness", 3, 2),
    ("sore ", 3, 2),
)
_HARD = re.compile(
    r"\b(surgery|fractured|fracture|torn|ruptured|rupture|out for the season|"
    r"re-evaluated in \d+ weeks)\b",
    re.IGNORECASE,
)
_PLAYING = re.compile(
    r"\b(probable|available|day-to-day|day to day|will play|is playing)\b",
    re.IGNORECASE,
)
_LISTED = re.compile(r"\b(questionable|doubtful)\b", re.IGNORECASE)


def anticipate_out(text: str) -> bool:
    """True only when the history says this tag should miss the game."""
    if not text or _PLAYING.search(text):
        return False
    if _HARD.search(text):
        return True
    if not _LISTED.search(text):
        return False
    matched = _match(text.casefold())
    if matched is None:
        return False
    typical, low = matched
    if "doubtful" in text.casefold():
        return typical >= 7 and low >= 3
    return typical >= 10 and low >= 6


def _match(folded: str) -> tuple[int, int] | None:
    for phrase, typical, low in _TABLE:
        if phrase in folded:
            return typical, low
    return None
