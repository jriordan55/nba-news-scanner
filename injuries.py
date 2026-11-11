"""NBA injury report from the same ESPN feed sports-leader reads.

https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries

Only a new Out, or a player leaving the report after being Out, becomes a card.
Day-to-day stays on file and is not a bet.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from config import ABBR_BY_NAME
from feed import NewsItem
from names import norm

INJURY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
REPORT_URL = "https://www.espn.com/nba/injuries"


@dataclass(frozen=True)
class Injury:
    key: str
    player: str
    team: str
    status: str
    note: str
    url: str
    updated: str

    def row(self) -> tuple[str, str, str, str, str, str, str]:
        return (self.key, self.player, self.team, self.status, self.note, self.url, self.updated)


@dataclass(frozen=True)
class InjuryEvent:
    item: NewsItem
    key: str


def fetch_injuries(session: requests.Session) -> list[Injury]:
    response = session.get(
        INJURY_URL,
        timeout=12,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    return parse_injuries(response.json())


def parse_injuries(payload: dict) -> list[Injury]:
    found: list[Injury] = []
    for team in payload.get("injuries") or []:
        abbr = ABBR_BY_NAME.get(str(team.get("displayName") or "").casefold(), "")
        if not abbr:
            continue
        for row in team.get("injuries") or []:
            athlete = row.get("athlete") or {}
            name = str(athlete.get("displayName") or "").strip()
            if not name:
                continue
            status = str(row.get("status") or "").strip()
            note = " ".join(
                part.strip()
                for part in (row.get("shortComment") or "", row.get("longComment") or "")
                if str(part).strip()
            )
            found.append(
                Injury(
                    key=_athlete_key(athlete, name),
                    player=name,
                    team=abbr,
                    status=status,
                    note=note,
                    url=_player_url(athlete),
                    updated=str(row.get("date") or ""),
                )
            )
    return found


def same_report(previous: dict[str, Injury], current: list[Injury]) -> bool:
    """True when every player's status is unchanged. Notes alone are not news."""
    current_by = {item.key: item for item in current}
    if set(previous) != set(current_by):
        return False
    return all(previous[key].status == item.status for key, item in current_by.items())


def events(previous: dict[str, Injury], current: list[Injury]) -> list[InjuryEvent]:
    """Cards for changes. An empty previous report is a baseline, not news."""
    if not previous:
        return []
    now = datetime.now(timezone.utc)
    current_by = {item.key: item for item in current}
    found: list[InjuryEvent] = []
    for injury in current:
        old = previous.get(injury.key)
        if not _is_out(injury.status):
            continue
        if old and _is_out(old.status):
            continue
        found.append(InjuryEvent(_out_item(injury, now), injury.key))
    for key, old in previous.items():
        if not _is_out(old.status):
            continue
        still = current_by.get(key)
        if still is not None:
            continue
        found.append(InjuryEvent(_return_item(old, now), key))
    return found


def next_state(
    previous: dict[str, Injury],
    current: list[Injury],
    resolved: set[str],
) -> list[Injury]:
    """Keep a change that has not alerted yet so the next pass can retry it."""
    if not previous:
        return list(current)
    current_by = {item.key: item for item in current}
    kept: dict[str, Injury] = {}
    for injury in current:
        old = previous.get(injury.key)
        became_out = _is_out(injury.status) and not (old and _is_out(old.status))
        if became_out and injury.key not in resolved:
            if old:
                kept[injury.key] = old
            continue
        kept[injury.key] = injury
    for key, old in previous.items():
        if key in current_by or not _is_out(old.status):
            continue
        if key not in resolved:
            kept[key] = old
    return list(kept.values())


def _is_out(status: str) -> bool:
    return status.casefold() == "out"


def _out_item(injury: Injury, now: datetime) -> NewsItem:
    headline = injury.note.split(".")[0].strip() if injury.note else ""
    if not headline or injury.player.split()[-1].casefold() not in headline.casefold():
        headline = f"{injury.player} is out."
    return _item(
        injury,
        now,
        headline=headline,
        quote=injury.note,
        kind=f"out|{injury.updated}",
    )


def _return_item(injury: Injury, now: datetime) -> NewsItem:
    return _item(
        injury,
        now,
        headline=f"{injury.player} is cleared to play.",
        quote=f"{injury.player} came off the injury report after being listed out.",
        kind="cleared",
    )


def _item(injury: Injury, now: datetime, headline: str, quote: str, kind: str) -> NewsItem:
    identity = f"injury|{injury.key}|{kind}"
    return NewsItem(
        id=hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16],
        team=injury.team,
        players=[injury.player],
        category="Injury report",
        published=now,
        headline=headline,
        quote=quote,
        url=injury.url or REPORT_URL,
    )


def _athlete_key(athlete: dict, name: str) -> str:
    for link in athlete.get("links") or []:
        href = str(link.get("href") or "")
        marker = "/id/"
        if marker not in href:
            continue
        ident = href.split(marker, 1)[1].split("/", 1)[0]
        if ident.isdigit():
            return ident
    return norm(name)


def _player_url(athlete: dict) -> str:
    for link in athlete.get("links") or []:
        href = str(link.get("href") or "")
        if href.startswith("http") and "/id/" in href:
            return href
    return REPORT_URL
