"""Player-name matching shared by the news model and the odds board."""

from __future__ import annotations

import re
import unicodedata

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv)\b")


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("'", "").replace(".", "")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = _SUFFIX.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def names_match(left: str, right: str) -> bool:
    a, b = norm(left), norm(right)
    if not a or not b:
        return False
    if a == b:
        return True
    a_parts, b_parts = a.split(), b.split()
    if a_parts[-1] != b_parts[-1] or len(a_parts[-1]) < 4:
        return False
    if len(a_parts) == 1 or len(b_parts) == 1:
        return True
    return (
        a_parts[0] == b_parts[0]
        or a_parts[0].startswith(b_parts[0])
        or b_parts[0].startswith(a_parts[0])
    )


def mentions_player(text: str, player: str, roster: list[str]) -> bool:
    """True when `text` refers to `player`.

    A last name counts only when one roster player owns it, so two Greens
    on the same team do not get each other's news.
    """
    haystack = norm(text)
    needle = norm(player)
    if not haystack or not needle:
        return False
    if needle in haystack:
        return True
    parts = needle.split()
    words = set(haystack.split())
    if len(parts) >= 2 and parts[0] in words and parts[-1] in words:
        return True
    last = parts[-1]
    if len(last) < 4 or last not in words:
        return False
    owners = [name for name in roster if norm(name).split()[-1] == last]
    unique_owners = {norm(name) for name in owners}
    return len(unique_owners) == 1 and norm(player) in unique_owners
