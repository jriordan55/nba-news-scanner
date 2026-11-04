"""Paths, team names, and environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FEED_URLS = (
    "https://fcp.news/?all=1",
    "https://fcp.news/?all=1&cat=availability_status",
    "https://fcp.news/?all=1&cat=minutes_usage",
    "https://fcp.news/?all=1&cat=role_rotation",
    "https://fcp.news/?all=1&cat=health_progress",
    "https://fcp.news/coachspeak",
)

# Full names as The Odds API prints them. Clippers have shown up both ways.
TEAMS: dict[str, tuple[str, ...]] = {
    "ATL": ("Atlanta Hawks",),
    "BKN": ("Brooklyn Nets",),
    "BOS": ("Boston Celtics",),
    "CHA": ("Charlotte Hornets",),
    "CHI": ("Chicago Bulls",),
    "CLE": ("Cleveland Cavaliers",),
    "DAL": ("Dallas Mavericks",),
    "DEN": ("Denver Nuggets",),
    "DET": ("Detroit Pistons",),
    "GSW": ("Golden State Warriors",),
    "HOU": ("Houston Rockets",),
    "IND": ("Indiana Pacers",),
    "LAC": ("Los Angeles Clippers", "LA Clippers"),
    "LAL": ("Los Angeles Lakers",),
    "MEM": ("Memphis Grizzlies",),
    "MIA": ("Miami Heat",),
    "MIL": ("Milwaukee Bucks",),
    "MIN": ("Minnesota Timberwolves",),
    "NOP": ("New Orleans Pelicans",),
    "NYK": ("New York Knicks",),
    "OKC": ("Oklahoma City Thunder",),
    "ORL": ("Orlando Magic",),
    "PHI": ("Philadelphia 76ers",),
    "PHX": ("Phoenix Suns",),
    "POR": ("Portland Trail Blazers",),
    "SAC": ("Sacramento Kings",),
    "SAS": ("San Antonio Spurs",),
    "TOR": ("Toronto Raptors",),
    "UTA": ("Utah Jazz",),
    "WAS": ("Washington Wizards",),
}

ABBR_BY_NAME = {name.casefold(): abbr for abbr, names in TEAMS.items() for name in names}

PROP_MARKETS = (
    "player_points,player_rebounds,player_assists,"
    "player_threes,player_points_rebounds_assists"
)

MARKET_LABELS = {
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_threes": "Threes",
    "player_points_rebounds_assists": "PRA",
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    odds_api_key: str
    discord_webhook: str
    telegram_token: str
    telegram_chat_id: str
    poll_seconds: int
    lookback_hours: int
    max_tip_hours: int
    root: Path
    data_dir: Path

    @property
    def alerts_configured(self) -> bool:
        return bool(self.discord_webhook or (self.telegram_token and self.telegram_chat_id))


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        odds_api_key=os.environ.get("ODDS_API_KEY", "").strip(),
        discord_webhook=os.environ.get("DISCORD_WEBHOOK_URL", "").strip(),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        poll_seconds=_int_env("POLL_SECONDS", 20),
        lookback_hours=_int_env("LOOKBACK_HOURS", 12),
        max_tip_hours=_int_env("MAX_TIP_HOURS", 96),
        root=ROOT,
        data_dir=data_dir,
    )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)
