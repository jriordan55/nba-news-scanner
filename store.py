"""Seen-news memory and the CLV spreadsheet."""

from __future__ import annotations

import csv
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from clv import line_clv, price_clv_points
from feed import NewsItem
from model import Signal
from names import names_match, norm
from odds_client import Leg

log = logging.getLogger("nba-scanner")

CSV_COLUMNS = [
    "fired_at_utc",
    "team",
    "player",
    "market",
    "side",
    "book",
    "alert_line",
    "alert_price",
    "latest_line",
    "latest_price",
    "close_line",
    "close_price",
    "close_status",
    "line_clv",
    "price_clv_pct_points",
    "clv_basis",
    "game",
    "game_start_utc",
    "confidence",
    "reason",
    "headline",
    "news_url",
    "news_time_utc",
    "close_at_utc",
    "event_id",
    "bet_id",
]


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "scanner.sqlite3"
        self.csv_path = data_dir / "clv.csv"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (
                news_id TEXT PRIMARY KEY,
                seen_at TEXT NOT NULL,
                headline TEXT,
                alerted INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watches (
                watch_id TEXT PRIMARY KEY,
                seen_at TEXT NOT NULL,
                player TEXT,
                team TEXT
            );
            CREATE TABLE IF NOT EXISTS bets (
                bet_id TEXT PRIMARY KEY,
                fired_at TEXT NOT NULL,
                news_time TEXT,
                team TEXT,
                player TEXT,
                market TEXT,
                side TEXT,
                book TEXT,
                book_key TEXT,
                game TEXT,
                home TEXT,
                away TEXT,
                game_start TEXT,
                event_id TEXT,
                alert_line REAL,
                alert_price INTEGER,
                latest_line REAL,
                latest_price INTEGER,
                latest_at TEXT,
                close_line REAL,
                close_price INTEGER,
                close_at TEXT,
                close_status TEXT,
                confidence REAL,
                reason TEXT,
                headline TEXT,
                news_url TEXT,
                news_id TEXT
            );
            """
        )
        self.conn.commit()

    def seen(self, news_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM seen WHERE news_id = ?", (news_id,)).fetchone()
        return row is not None

    def count_seen(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0])

    def mark_seen(self, item: NewsItem, alerted: bool) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen (news_id, seen_at, headline, alerted) VALUES (?, ?, ?, ?)",
            (item.id, _now(), item.headline, 1 if alerted else 0),
        )
        self.conn.commit()

    def take_watch(self, signal: Signal, item: NewsItem) -> bool:
        day = (item.published or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
        watch_id = f"{norm(signal.player)}|out|{day}"
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO watches (watch_id, seen_at, player, team) VALUES (?, ?, ?, ?)",
            (watch_id, _now(), signal.player, signal.team),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def has_open(
        self,
        event_id: str,
        player: str,
        market: str,
        side: str,
        home: str = "",
        away: str = "",
    ) -> bool:
        rows = self.conn.execute(
            """
            SELECT player, market, side, event_id, home, away FROM bets
            WHERE close_status = 'open'
            """
        ).fetchall()
        for row in rows:
            if not (names_match(row["player"], player) and row["market"] == market and row["side"] == side):
                continue
            if row["event_id"] == event_id:
                return True
            if home and away and names_match(row["home"], home) and names_match(row["away"], away):
                return True
        return False

    def insert_legs(self, item: NewsItem, legs: list[Leg]) -> None:
        fired = _now()
        news_time = item.published.astimezone(timezone.utc).isoformat() if item.published else ""
        for leg in legs:
            bet_id = _bet_id(item.url, leg.player, leg.market, leg.side)
            self.conn.execute(
                """
                INSERT OR IGNORE INTO bets (
                    bet_id, fired_at, news_time, team, player, market, side, book, book_key,
                    game, home, away, game_start, event_id, alert_line, alert_price,
                    latest_line, latest_price, latest_at, close_status, confidence,
                    reason, headline, news_url, news_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
                """,
                (
                    bet_id,
                    fired,
                    news_time,
                    leg.team,
                    leg.player,
                    leg.market_label,
                    leg.side,
                    leg.book,
                    leg.book_key,
                    leg.game,
                    leg.home,
                    leg.away,
                    leg.game_start.astimezone(timezone.utc).isoformat(),
                    leg.event_id,
                    leg.line,
                    leg.price,
                    leg.line,
                    leg.price,
                    fired,
                    leg.confidence,
                    leg.reason,
                    item.headline,
                    item.url,
                    item.id,
                ),
            )
        self.conn.commit()
        self.export_csv()

    def open_bets(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM bets WHERE close_status = 'open' ORDER BY game_start"
            ).fetchall()
        )

    def update_latest(self, bet_id: str, line: float, price: int) -> None:
        self.conn.execute(
            """
            UPDATE bets
            SET latest_line = ?, latest_price = ?, latest_at = ?
            WHERE bet_id = ?
            """,
            (line, price, _now(), bet_id),
        )
        self.conn.commit()

    def close_bet(
        self,
        bet_id: str,
        status: str,
        line: float | None,
        price: int | None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE bets
            SET close_status = ?, close_line = ?, close_price = ?, close_at = ?,
                latest_line = COALESCE(?, latest_line),
                latest_price = COALESCE(?, latest_price),
                latest_at = ?
            WHERE bet_id = ?
            """,
            (status, line, price, _now(), line, price, _now(), bet_id),
        )
        self.conn.commit()

    def export_csv(self) -> None:
        rows = self.conn.execute("SELECT * FROM bets ORDER BY fired_at, player, market").fetchall()
        target = self.csv_path
        temporary = target.with_suffix(".csv.tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow(_csv_row(row))
        try:
            temporary.replace(target)
        except OSError:
            log.warning("could not replace %s. Close it if Excel has the file open.", target)

    def close(self) -> None:
        self.conn.close()

    def status_lines(self) -> list[str]:
        seen = self.count_seen()
        open_count = int(
            self.conn.execute("SELECT COUNT(*) FROM bets WHERE close_status = 'open'").fetchone()[0]
        )
        closed = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM bets WHERE close_status != 'open'"
            ).fetchone()[0]
        )
        lines = [
            f"seen news: {seen}",
            f"open bets: {open_count}",
            f"closed bets: {closed}",
            f"csv: {self.csv_path}",
        ]
        for row in self.open_bets():
            lines.append(
                f"  {row['side']} {row['player']} {row['market']} {row['alert_line']} ({row['alert_price']}) "
                f"latest {row['latest_line']} ({row['latest_price']}) {row['game']}"
            )
        return lines


def _csv_row(row: sqlite3.Row) -> dict[str, str]:
    basis, line_value, price_value = _clv(row)
    return {
        "fired_at_utc": row["fired_at"] or "",
        "team": row["team"] or "",
        "player": row["player"] or "",
        "market": row["market"] or "",
        "side": row["side"] or "",
        "book": row["book"] or "",
        "alert_line": _num(row["alert_line"]),
        "alert_price": _int(row["alert_price"]),
        "latest_line": _num(row["latest_line"]),
        "latest_price": _int(row["latest_price"]),
        "close_line": _num(row["close_line"]),
        "close_price": _int(row["close_price"]),
        "close_status": row["close_status"] or "",
        "line_clv": line_value,
        "price_clv_pct_points": price_value,
        "clv_basis": basis,
        "game": row["game"] or "",
        "game_start_utc": row["game_start"] or "",
        "confidence": _num(row["confidence"]),
        "reason": row["reason"] or "",
        "headline": row["headline"] or "",
        "news_url": row["news_url"] or "",
        "news_time_utc": row["news_time"] or "",
        "close_at_utc": row["close_at"] or "",
        "event_id": row["event_id"] or "",
        "bet_id": row["bet_id"] or "",
    }


def _clv(row: sqlite3.Row) -> tuple[str, str, str]:
    status = row["close_status"]
    if status in ("closed", "pulled") and row["close_line"] is not None and row["close_price"] is not None:
        other_line, other_price, basis = row["close_line"], row["close_price"], "close"
    elif status == "open" and (
        row["latest_line"] != row["alert_line"] or row["latest_price"] != row["alert_price"]
    ):
        other_line, other_price, basis = row["latest_line"], row["latest_price"], "latest"
    else:
        return ("", "", "") if status == "open" else ("close", "", "")
    if other_line is None or other_price is None:
        return basis, "", ""
    return (
        basis,
        f"{line_clv(row['side'], row['alert_line'], other_line):.2f}",
        f"{price_clv_points(row['alert_price'], other_price):.2f}",
    )


def _bet_id(url: str, player: str, market: str, side: str) -> str:
    import hashlib

    raw = f"{url}|{norm(player)}|{market}|{side}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(value) -> str:
    if value is None:
        return ""
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def _int(value) -> str:
    if value is None:
        return ""
    return str(int(value))
