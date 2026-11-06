"""Turn a Full Court Press card into a minutes call.

PrizePicks voids a prop when the player does not play, so a confirmed
absence is not an under. It is a search for the teammate who gains the
minutes. Unders are for players who will play on a lighter load.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from feed import NewsItem
from names import mentions_player, names_match, norm

OUT_RE = re.compile(
    r"\b(ruled out|will not play(?!\s+\d)|won'?t play(?!\s+\d)|will miss|going to miss|"
    r"sidelined|inactive|scratched|late scratch|out tonight|out tomorrow|"
    r"out for the(?: season| game| opener| year)?|"
    r"remains? out|sits out|sitting out|done for the (?:night|game))\b"
    r"|\b(?:is|are)(?: already| still)? out\b(?! of (?:the )?rotation)",
    re.IGNORECASE,
)
QUESTIONABLE_RE = re.compile(r"\bquestionable\b", re.IGNORECASE)
DOUBTFUL_RE = re.compile(r"\bdoubtful\b", re.IGNORECASE)
UNDECIDED_RE = re.compile(
    r"undecided|to be determined|\btbd\b|we'?ll sort|whether (?:he|she|they|that)|not sure if",
    re.IGNORECASE,
)
LIMIT_RE = re.compile(
    r"minutes? (?:limit|cap|restriction)|limited to|slightly limited|"
    r"on a (?:minutes? )?limit|lighter minutes|lighter (?:minute )?load|"
    r"fewer minutes|reduced minutes|lower minutes|"
    r"minutes? (?:will|to) (?:be )?(?:cut|reduced|capped)|"
    r"not going to be playing \d+|won'?t be playing \d+|won'?t play \d+|"
    r"smaller role|reduced role|less run|com(?:e|ing) off the bench",
    re.IGNORECASE,
)
HIGH_MINUTES_RE = re.compile(
    r"super high minutes|high minutes|\d{2} minutes",
    re.IGNORECASE,
)
# A cap that is coming off is more minutes, not a lighter load.
LIFTED_RE = re.compile(
    r"without (?:a |the |his |her )?(?:\d+[-\s])?minutes? (?:limit|cap|restriction)|"
    r"no minutes? restrictions?|"
    r"no longer (?:be )?on (?:a |an )?(?:\d+[-\s])?minutes? (?:limit|cap|restriction)|"
    r"(?:will not|won'?t|not) be on (?:a |an )?(?:\d+[-\s])?minutes? (?:limit|cap|restriction)|"
    r"not (?:on|under) (?:a |an )?(?:\d+[-\s])?minutes? (?:limit|cap|restriction)|"
    r"(?:off of|off) (?:a |the |his |her )?(?:\d+[-\s])?minutes? (?:limit|cap|restriction)|"
    r"coming off (?:a |the |his |her )?(?:\d+[-\s])?minutes? (?:limit|cap|restriction)|"
    r"free of (?:a |the |his |her )?(?:\d+[-\s])?minutes? (?:limit|cap|restriction)|"
    r"minutes? (?:limit|cap|restriction) (?:is |was |has been |gets )?(?:lifted|removed|gone|over|done)|"
    r"(?:lifted|removed|dropped) (?:the |his |her )?(?:\d+[-\s])?minutes? (?:limit|cap|restriction)|"
    r"unlimited (?:playing time|minutes|play)",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(
    r"not going to|won'?t|will not|don'?t think|fewer|less|lighter|lower",
    re.IGNORECASE,
)
UP_RE = re.compile(
    r"more minutes|increased minutes|minutes bump|"
    r"minutes will (?:rise|increase|go up)|bigger role|expanded role|larger role|"
    r"increased usage|more usage|usage bump|usage will (?:rise|increase)|"
    r"ball in his hands|ball in their hands|have the ball a lot|"
    r"will start(?! to\b)|getting the start|starting tonight|starting tomorrow|"
    r"moved into the starting|in his place|in her place|start in place|"
    r"draws the start|gets the start|bigger workload|into the starting lineup|"
    r"controlling the offense|"
    r"are (?:all )?back\b(?!\s+to\b)|cleared to play|not on the injury report|"
    r"brought the ball up|brings the ball up|bring the ball up|"
    r"initiat(?:e|es|ed|ing) (?:the |our )?offense|"
    r"chance to run with|run with the .+ role|"
    r"\bthe horse\b|primary option|first option|go-to scorer|featured role",
    re.IGNORECASE,
)
_NAME = r"[A-Z][\w'’.-]+(?:[-\s]+[A-Z][\w'’.-]+){0,3}"
REPLACEMENT_RE = re.compile(
    rf"({_NAME})\s+(?:starts in (?:his|her) place|will start in (?:his|her) place|"
    rf"gets the start|draws the start|takes\b.{{0,80}}?\bstarting spot)",
)
STARTED_OVER_RE = re.compile(rf"({_NAME})\s+started over\s+({_NAME})")
LINEUP_WITH_RE = re.compile(r"lineup with\s+([A-Z][\w'’.-]+)")
ASSIST_RE = re.compile(
    r"assist|facilitat|playmak|ball in his hands|ball in their hands|"
    r"have the ball|controlling the offense",
    re.IGNORECASE,
)
REBOUND_RE = re.compile(r"rebound", re.IGNORECASE)
THREES_RE = re.compile(r"three|3-point|from deep", re.IGNORECASE)
PROPER_NAME_RE = re.compile(r"[A-Z][a-z]{2,}(?:\s+[A-Z][a-zA-Z'’.-]+){0,2}\b")

POINTS = "player_points"
REBOUNDS = "player_rebounds"
ASSISTS = "player_assists"
THREES = "player_threes"
PRA = "player_points_rebounds_assists"


@dataclass
class Signal:
    player: str
    team: str
    kind: str
    side: str | None
    markets: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""


def interpret(
    item: NewsItem,
    roster: list[str] | None = None,
    beneficiaries: dict[str, list[str]] | None = None,
    history=None,
) -> list[Signal]:
    if item.paywalled:
        return []
    roster = list(dict.fromkeys([*item.players, *(roster or []), *_replacement_names(item)]))
    signals: list[Signal] = []
    for player in item.players:
        evidence = evidence_for(player, item, roster)
        kind = classify(evidence)
        if kind is None:
            continue
        signals.append(_signal(player, item.team, kind, evidence))
    signals.extend(_start_swaps(item, roster))
    signals.extend(_preferred_lineup(item, roster))
    out_names = [signal.player for signal in signals if signal.kind == "out"]
    if out_names:
        signals.extend(_gainers(item, out_names, roster, beneficiaries or {}))
    signals = _dedupe(signals)
    if history is not None:
        signals = _apply_history(item, signals, history)
    return signals


def evidence_for(player: str, item: NewsItem, roster: list[str]) -> str:
    single = len(item.players) == 1
    chunks: list[str] = []
    if single or mentions_player(item.headline, player, roster):
        chunks.append(item.headline)
    for sentence in _sentences(item.quote):
        if mentions_player(sentence, player, roster):
            chunks.append(sentence)
        elif single and not _about_someone_else(sentence, player, roster):
            chunks.append(sentence)
    return " ".join(chunks).strip()


def classify(evidence: str) -> str | None:
    if not evidence:
        return None
    if OUT_RE.search(evidence):
        return "out"
    if LIFTED_RE.search(evidence) and not UNDECIDED_RE.search(evidence):
        return "minutes_up"
    if _lighter_minutes(evidence) and not UNDECIDED_RE.search(evidence):
        return "minutes_down"
    if UP_RE.search(evidence) and not UNDECIDED_RE.search(evidence):
        return "minutes_up"
    return None


def select_markets(kind: str, evidence: str) -> list[str]:
    if kind == "out":
        return []
    markets = [POINTS, PRA]
    if ASSIST_RE.search(evidence):
        markets.append(ASSISTS)
    if REBOUND_RE.search(evidence):
        markets.append(REBOUNDS)
    if THREES_RE.search(evidence):
        markets.append(THREES)
    return markets


def _signal(player: str, team: str, kind: str, evidence: str) -> Signal:
    blurb = _short(evidence)
    if kind == "out":
        return Signal(
            player=player,
            team=team,
            kind="out",
            side=None,
            confidence=0.9,
            reason=f"{player} is out. {blurb}",
        )
    if kind == "minutes_down":
        return Signal(
            player=player,
            team=team,
            kind="minutes_down",
            side="Under",
            markets=select_markets(kind, evidence),
            confidence=0.78,
            reason=f"{player} is playing on a lighter load. {blurb}",
        )
    return Signal(
        player=player,
        team=team,
        kind="minutes_up",
        side="Over",
        markets=select_markets(kind, evidence),
        confidence=0.72,
        reason=f"{player} is lined up for more usage. {blurb}",
    )


def _replacement_names(item: NewsItem) -> list[str]:
    text = f"{item.headline} {item.quote}"
    names: list[str] = []
    for match in REPLACEMENT_RE.finditer(text):
        names.append(match.group(1).strip(" ."))
    return names


def _resolve(name: str, roster: list[str]) -> str | None:
    cleaned = name.strip(" .")
    for person in roster:
        if names_match(cleaned, person):
            return person
    last = norm(cleaned).split()[-1] if norm(cleaned) else ""
    if len(last) < 4:
        return None
    hits = [person for person in roster if norm(person).split()[-1] == last]
    unique = {norm(person) for person in hits}
    if len(unique) == 1:
        return hits[0]
    if len(cleaned.split()) >= 2:
        return cleaned
    return None


def _start_swaps(item: NewsItem, roster: list[str]) -> list[Signal]:
    text = f"{item.headline} {item.quote}"
    found: list[Signal] = []
    for match in STARTED_OVER_RE.finditer(text):
        starter = _resolve(match.group(1), roster)
        benched = _resolve(match.group(2), roster)
        if starter:
            found.append(
                _signal(starter, item.team, "minutes_up", f"{starter} started over {match.group(2)}")
            )
        if benched and not (starter and names_match(starter, benched)):
            found.append(
                _signal(
                    benched,
                    item.team,
                    "minutes_down",
                    f"{benched} lost the start to {match.group(1)}",
                )
            )
    return found


def _preferred_lineup(item: NewsItem, roster: list[str]) -> list[Signal]:
    text = f"{item.headline} {item.quote}"
    found: list[Signal] = []
    for match in LINEUP_WITH_RE.finditer(text):
        player = _resolve(match.group(1), roster)
        if not player:
            continue
        found.append(
            _signal(player, item.team, "minutes_up", f"{player} is in the preferred lineup")
        )
    return found


def _gainers(
    item: NewsItem,
    out_names: list[str],
    roster: list[str],
    beneficiaries: dict[str, list[str]],
) -> list[Signal]:
    found: list[Signal] = []
    blocked = {norm(name) for name in out_names}
    for sentence in _sentences(f"{item.headline} {item.quote}"):
        if not UP_RE.search(sentence) or UNDECIDED_RE.search(sentence):
            continue
        for name in roster:
            if norm(name) in blocked or not mentions_player(sentence, name, roster):
                continue
            found.append(
                Signal(
                    player=name,
                    team=item.team,
                    kind="minutes_up",
                    side="Over",
                    markets=select_markets("minutes_up", sentence),
                    confidence=0.74,
                    reason=(
                        f"{name} is the named minute-gainer while "
                        f"{_join(out_names)} sits. {_short(sentence)}"
                    ),
                )
            )
    for out_name in out_names:
        for mapped in _mapped(beneficiaries, out_name):
            if norm(mapped) in blocked:
                continue
            if any(names_match(mapped, signal.player) for signal in found):
                continue
            found.append(
                Signal(
                    player=mapped,
                    team=item.team,
                    kind="minutes_up",
                    side="Over",
                    markets=[POINTS, PRA],
                    confidence=0.64,
                    reason=(
                        f"{out_name} is out. beneficiaries.json lists {mapped} "
                        "as the minute-gainer."
                    ),
                )
            )
    return found


def _mapped(beneficiaries: dict[str, list[str]], player: str) -> list[str]:
    for key, names in beneficiaries.items():
        if names_match(key, player):
            return list(names)
    return []


def _lighter_minutes(evidence: str) -> bool:
    if LIFTED_RE.search(evidence):
        return False
    if LIMIT_RE.search(evidence):
        return True
    return bool(HIGH_MINUTES_RE.search(evidence) and NEGATION_RE.search(evidence))


def _about_someone_else(sentence: str, player: str, roster: list[str]) -> bool:
    if mentions_player(sentence, player, roster):
        return False
    for other in roster:
        if not names_match(other, player) and mentions_player(sentence, other, roster):
            return True
    if OUT_RE.search(sentence) or QUESTIONABLE_RE.search(sentence) or DOUBTFUL_RE.search(sentence):
        return True
    return bool(PROPER_NAME_RE.match(sentence.strip()))


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[\n\r]+|(?<=[.!?])\s+", text or "")
    cleaned = []
    for part in parts:
        sentence = part.strip(" “”\"'")
        if sentence:
            cleaned.append(sentence)
    return cleaned


def _short(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _join(names: list[str]) -> str:
    if not names:
        return "a teammate"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _apply_history(item: NewsItem, signals: list[Signal], history) -> list[Signal]:
    outs = [signal.player for signal in signals if signal.kind == "out"]
    kept: list[Signal] = []
    present = {norm(signal.player) for signal in signals if signal.kind == "minutes_up"}
    for signal in signals:
        if signal.kind == "minutes_down":
            markets = history.under_markets(signal.player)
            if markets is None:
                kept.append(signal)
            elif markets:
                signal.markets = markets
                note = history.lighter_note(signal.player)
                if note:
                    signal.reason = f"{signal.reason} {note}".strip()
                kept.append(signal)
            continue
        if signal.kind == "minutes_up" and outs:
            markets = history.over_markets(outs, signal.player)
            if markets is None:
                kept.append(signal)
            elif markets:
                signal.markets = markets
                _out, note = history.over_note(outs, signal.player)
                if note:
                    signal.reason = f"{signal.reason} {note}".strip()
                kept.append(signal)
            continue
        kept.append(signal)
    for out_name in outs:
        gainer = history.best_gainer(out_name)
        if not gainer or not gainer.get("markets_to_bet"):
            continue
        if norm(gainer["player"]) in present:
            continue
        if any(names_match(gainer["player"], name) for name in outs):
            continue
        present.add(norm(gainer["player"]))
        points = (gainer.get("markets") or {}).get(POINTS) or {}
        hits = int(points.get("overs") or 0)
        games = int(points.get("n") or 0)
        record = f"DraftKings over hit {hits} of {games} last regular season." if games else ""
        kept.append(
            Signal(
                player=gainer["player"],
                team=gainer.get("team") or item.team,
                kind="minutes_up",
                side="Over",
                markets=list(gainer["markets_to_bet"]),
                confidence=0.66,
                reason=(
                    f"{gainer['player']} took the minutes when {gainer.get('out') or out_name} sat. {record}"
                ).strip(),
            )
        )
    return kept


def _dedupe(signals: list[Signal]) -> list[Signal]:
    kept: list[Signal] = []
    seen: set[tuple[str, str]] = set()
    for signal in signals:
        key = (norm(signal.player), signal.kind)
        if key in seen:
            continue
        seen.add(key)
        kept.append(signal)
    return kept
