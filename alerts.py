"""Discord and Telegram delivery. Both are optional."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from config import Config
from feed import NewsItem
from model import Signal
from odds_client import Leg

log = logging.getLogger("nba-scanner")
ET = ZoneInfo("America/New_York")


class Alerter:
    def __init__(self, config: Config, session: requests.Session):
        self.config = config
        self.session = session

    def send_priced(self, item: NewsItem, legs: list[Leg], outs: list[Signal]) -> None:
        text = render_priced(item, legs, outs)
        log.info("ALERT\n%s", text)
        self._deliver(text, embeds=discord_embeds(item, legs, outs))

    def send_watch(self, item: NewsItem, outs: list[Signal]) -> None:
        text = render_watch(item, outs)
        log.info("WATCH\n%s", text)
        self._deliver(text, embeds=None)

    def _deliver(self, text: str, embeds: list[dict] | None) -> None:
        if self.config.discord_webhook:
            self._discord(text, embeds)
        if self.config.telegram_token and self.config.telegram_chat_id:
            self._telegram(text)
        if not self.config.alerts_configured:
            log.info("no Discord webhook or Telegram chat configured; alert kept in the log and CSV only")

    def _discord(self, text: str, embeds: list[dict] | None) -> None:
        payload: dict = {"content": text[:1900]}
        if embeds:
            payload["content"] = _context(text)
            payload["embeds"] = embeds[:10]
            if not payload["content"]:
                payload.pop("content")
        try:
            response = self.session.post(self.config.discord_webhook, json=payload, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            log.error("discord webhook failed (status %s)", status or type(exc).__name__)

    def _telegram(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.config.telegram_token}/sendMessage"
        for chunk in _chunks(text, 3800):
            try:
                response = self.session.post(
                    url,
                    json={"chat_id": self.config.telegram_chat_id, "text": chunk, "disable_web_page_preview": False},
                    timeout=20,
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                log.error("telegram send failed (status %s)", status or type(exc).__name__)


def render_priced(item: NewsItem, legs: list[Leg], outs: list[Signal]) -> str:
    lines = []
    if outs:
        lines.append("Out: " + ", ".join(signal.player for signal in outs))
    for leg in legs:
        lines.append(
            f"{leg.side.upper()} {leg.team} {leg.player} {leg.market_label} "
            f"{_line(leg.line)} ({_price(leg.price)}) {leg.book}"
        )
        lines.append(f"Game: {leg.game} {format_et(leg.game_start)}")
        lines.append(leg.reason)
    lines.append("Alert price saved in data/clv.csv. The close fills in at tip-off.")
    lines.append(item.headline)
    if item.url:
        lines.append(item.url)
    return "\n".join(lines)


def render_watch(item: NewsItem, outs: list[Signal]) -> str:
    names = ", ".join(signal.player for signal in outs)
    team = outs[0].team or item.team
    lines = [
        f"{team} OUT: {names}.",
        "PrizePicks voids a player who does not play, so this is not an under.",
        "The post does not name who gains the minutes. Add that teammate under players in beneficiaries.json to price the over automatically.",
        item.headline,
    ]
    if item.url:
        lines.append(item.url)
    return "\n".join(lines)


def discord_embeds(item: NewsItem, legs: list[Leg], outs: list[Signal]) -> list[dict]:
    embeds = []
    context = ""
    if outs:
        context = "While " + ", ".join(signal.player for signal in outs) + " is out. "
    for leg in legs[:10]:
        embeds.append(
            {
                "title": f"{leg.side.upper()} {leg.player} {leg.market_label} {_line(leg.line)}",
                "url": item.url or None,
                "color": 0x3DDC97 if leg.side == "Over" else 0xE24B4B,
                "description": (context + leg.reason)[:4000],
                "fields": [
                    {"name": leg.book, "value": f"{_line(leg.line)} ({_price(leg.price)})", "inline": True},
                    {"name": "Game", "value": f"{leg.game} · {format_et(leg.game_start)}", "inline": True},
                    {"name": "News", "value": item.headline[:250], "inline": False},
                ],
                "footer": {"text": "Alert price saved to data/clv.csv. Close fills in at tip-off."},
            }
        )
    return embeds


def format_et(moment: datetime) -> str:
    local = moment.astimezone(ET)
    hour = str(int(local.strftime("%I")))
    return f"{local.strftime('%a')} {hour}:{local.strftime('%M %p')} ET"


def _line(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text


def _price(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def _context(text: str) -> str:
    first = text.splitlines()[0] if text else ""
    if first.startswith("Out:"):
        return first[:1800]
    return ""


def _chunks(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    blocks = []
    current = ""
    for line in text.splitlines():
        piece = line if not current else current + "\n" + line
        if len(piece) > size and current:
            blocks.append(current)
            current = line
        else:
            current = piece
    if current:
        blocks.append(current)
    return blocks
