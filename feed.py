"""Read the public Full Court Press feed."""

from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("nba-scanner")

USER_AGENT = "nba-news-scanner/1.0 (personal minutes monitor)"
_CARD_ID = re.compile(r'\["\$","article","([^"]+)",\{"className":"card"')
_QUOTE = re.compile(r'["“](.+?)["”]', re.DOTALL)
_CAMEL = re.compile(r"([a-z])([A-Z])")
_LETTER_DIGIT = re.compile(r"([A-Za-z])(\d)")
_DIGIT_LETTER = re.compile(r"(\d)([A-Za-z])")
_SPLIT_WORDS = (
    "questionable",
    "doubtful",
    "probable",
    "against",
    "minutes",
    "available",
    "starting",
    "shoulder",
    "surgery",
    "sprain",
    "soreness",
    "accepted",
    "smaller",
    "another",
    "rotation",
)
_SHARE_CACHE: dict[str, tuple[str, str]] = {}
_SHARE_RETRY_AFTER: dict[str, float] = {}
_OCR = None


@dataclass
class NewsItem:
    id: str
    team: str
    players: list[str]
    category: str
    published: datetime | None
    headline: str
    quote: str
    url: str

    @property
    def text(self) -> str:
        return f"{self.headline} {self.quote}".strip()

    @property
    def paywalled(self) -> bool:
        return "sign in to read" in self.headline.lower()


def fetch_feed(session: requests.Session, url: str) -> list[NewsItem]:
    response = session.get(url, timeout=25, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    items = parse_html(response.text)
    if any(item.paywalled for item in items):
        _unlock_paywalled(session, url, items)
    return items


def card_ids(payload: str) -> list[str]:
    return _CARD_ID.findall(payload)


def share_text(lines: list[str], players: list[str]) -> tuple[str, str] | None:
    """Pull the takeaway and the quote out of a share-image transcript."""
    kept = [line for line in (_clean(line) for line in lines) if _keep_line(line, players)]
    blob = "\n".join(kept)
    found = _QUOTE.search(blob)
    if found is not None:
        quote_text = _tidy(found.group(1)).strip(" \"")
        quote_text = re.split(r"\s@\w+|\sfcp\.news|\sFCP\b", quote_text)[0].strip()
        if _readable(quote_text):
            headline = _repair_spacing(" ".join(blob[: found.start()].splitlines()))
            if not headline:
                headline = quote_text.split(".")[0].strip()
            if headline and quote_text:
                return _drop_echoed_name(headline, players), quote_text
    if not kept:
        return None
    headline_parts: list[str] = []
    rest: list[str] = []
    started_quote = False
    for line in kept:
        if not started_quote:
            headline_parts.append(line.strip(" \""))
            if line.endswith("."):
                started_quote = True
        else:
            rest.append(line.strip(" \""))
    headline = _repair_spacing(" ".join(headline_parts))
    quote_text = _tidy(" ".join(rest)) or headline
    if not headline or not _readable(quote_text):
        return None
    return _drop_echoed_name(headline, players), quote_text


def _keep_line(line: str, players: list[str]) -> bool:
    if not line or not _readable(line):
        return False
    folded = _fold(line)
    names = {_fold(player) for player in players}
    names.add(_fold(" · ".join(players)))
    if folded in names:
        return False
    if re.fullmatch(r"[A-Z]{2,3}", line):
        return False
    if folded in {"fcp", "fcp.news"} or folded.startswith("@") or folded.startswith("via @"):
        return False
    if "·" in line or "•" in line:
        return False
    return True


def _readable(text: str) -> bool:
    if not text:
        return False
    weird = sum(
        1
        for char in text
        if not (char.isascii() and (char.isalnum() or char.isspace() or char in ".,;:'\"?!-()#/&%+"))
    )
    return weird / len(text) < 0.08


def _fold(value: str) -> str:
    stripped = unicodedata.normalize("NFKD", value)
    return "".join(char for char in stripped if not unicodedata.combining(char)).lower()


def _drop_echoed_name(text: str, players: list[str]) -> str:
    """OCR sometimes prints the name on its own and again at the start of the takeaway."""
    for player in players:
        parts = player.split()
        if len(parts) < 2:
            continue
        full = _fold(player)
        last = _fold(parts[-1])
        folded = _fold(text)
        if folded.startswith(full) and folded[len(full) :].lstrip().startswith(last):
            return _cut_folded_prefix(text, full)
    return text


def _cut_folded_prefix(text: str, folded_prefix: str) -> str:
    count = 0
    index = 0
    while index < len(text) and count < len(folded_prefix):
        count += len(_fold(text[index]))
        index += 1
    return text[index:].lstrip(" .:-")


def _tidy(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())


def _repair_spacing(text: str) -> str:
    text = _CAMEL.sub(r"\1 \2", text)
    text = _LETTER_DIGIT.sub(r"\1 \2", text)
    text = _DIGIT_LETTER.sub(r"\1 \2", text)

    def split_token(token: str) -> str:
        if len(token) < 16:
            return token
        lowered = token.lower()
        pieces: list[str] = []
        index = 0
        while index < len(token):
            word = next((item for item in _SPLIT_WORDS if lowered.startswith(item, index)), "")
            if word:
                pieces.append(token[index : index + len(word)])
                index += len(word)
                continue
            nxt = index + 1
            while nxt < len(token) and not any(lowered.startswith(item, nxt) for item in _SPLIT_WORDS):
                nxt += 1
            pieces.append(token[index:nxt])
            index = nxt
        return " ".join(pieces)

    return " ".join(split_token(part) for part in text.split())


def parse_html(html: str) -> list[NewsItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[NewsItem] = []
    seen: set[str] = set()
    for article in soup.select("article.card"):
        item = _parse_article(article)
        if item is None or item.id in seen:
            continue
        seen.add(item.id)
        items.append(item)
    return items


def _parse_article(article) -> NewsItem | None:
    headline_node = article.select_one(".card-head")
    headline = _clean(headline_node.get_text(" ", strip=True) if headline_node else "")
    if not headline:
        return None
    quote_node = article.select_one(".card-quote")
    quote = _clean(quote_node.get_text(" ", strip=True) if quote_node else "")
    team_node = article.select_one(".tbadge")
    team = team_node.get_text(strip=True) if team_node else ""
    players = []
    for link in article.select(".card-name a"):
        name = _clean(link.get_text(" ", strip=True))
        if name and name not in players:
            players.append(name)
    if not players:
        name_node = article.select_one(".card-name")
        raw_name = _clean(name_node.get_text(" ", strip=True) if name_node else "")
        for name in re.split(r"\s*[·•]\s*", raw_name):
            if name and name not in players:
                players.append(name)
    category_node = article.select_one(".cat")
    category = _clean(category_node.get_text(" ", strip=True) if category_node else "")
    link = article.select_one(".card-foot a.btn")
    url = (link.get("href") or "").strip() if link else ""
    published = _published(article)
    stamp = published.isoformat() if published else ""
    identity = f"{url}|{headline}|{','.join(players)}|{stamp}"
    news_id = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    return NewsItem(
        id=news_id,
        team=team,
        players=players,
        category=category,
        published=published,
        headline=headline,
        quote=quote,
        url=url,
    )


def _published(article) -> datetime | None:
    stamp = article.select_one("time")
    if stamp is None or not stamp.get("datetime"):
        return None
    raw = stamp["datetime"].strip()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        log.debug("unparsed time %s", raw)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _unlock_paywalled(session: requests.Session, url: str, items: list[NewsItem]) -> None:
    try:
        response = session.get(
            url,
            timeout=25,
            headers={"User-Agent": USER_AGENT, "RSC": "1", "Accept": "text/x-component"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("could not read card ids for %s: %s", url, exc)
        return
    ids = card_ids(response.text)
    if len(ids) != len(items):
        log.warning("card ids (%s) did not match cards (%s) on %s", len(ids), len(items), url)
        return
    for item, quote_id in zip(items, ids):
        if not item.paywalled:
            continue
        item.id = hashlib.sha1(f"fcp-locked|{quote_id}".encode()).hexdigest()[:16]
        unlocked = _cached_share(session, quote_id, item.players)
        if unlocked is None:
            continue
        item.headline, item.quote = unlocked
        item.url = "https://fcp.news/q/" + quote(quote_id, safe="") + "/opengraph-image"
        log.info("read free card %s: %s", quote_id, item.headline[:90])


def _cached_share(
    session: requests.Session, quote_id: str, players: list[str]
) -> tuple[str, str] | None:
    cached = _SHARE_CACHE.get(quote_id)
    if cached is not None:
        return cached
    if _SHARE_RETRY_AFTER.get(quote_id, 0) > time.time():
        return None
    unlocked = _read_share(session, quote_id, players)
    if unlocked is None:
        _SHARE_RETRY_AFTER[quote_id] = time.time() + 600
        return None
    _SHARE_CACHE[quote_id] = unlocked
    return unlocked


def _read_share(
    session: requests.Session, quote_id: str, players: list[str]
) -> tuple[str, str] | None:
    image_url = "https://fcp.news/q/" + quote(quote_id, safe="") + "/opengraph-image"
    try:
        response = session.get(image_url, timeout=30, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("share image failed for %s: %s", quote_id, exc)
        return None
    if not response.content.startswith(b"\x89PNG"):
        log.warning("share image for %s was not a png", quote_id)
        return None
    try:
        result, _elapsed = _ocr()(response.content)
    except Exception:
        log.exception("could not read the share image for %s", quote_id)
        return None
    lines = [item[1] for item in (result or []) if len(item) > 1 and item[1]]
    unlocked = share_text(lines, players)
    if unlocked is None:
        log.warning("share image for %s had no quote", quote_id)
    return unlocked


def _ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR = RapidOCR()
    return _OCR
