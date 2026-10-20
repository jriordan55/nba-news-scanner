"""Read the public Full Court Press feed."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("nba-scanner")

USER_AGENT = "nba-news-scanner/1.0 (personal minutes monitor)"


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
    return parse_html(response.text)


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
