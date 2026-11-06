"""Seen-news memory and the CLV spreadsheet."""

from __future__ import annotations

import csv
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from clv import line_clv
from results import outcome
from feed import NewsItem
from model import Signal
from names import names_match, norm
from odds_client import Leg

log = logging.getLogger("nba-scanner")

CSV_COLUMNS = [
    "player",
    "news",
    "category",
    "side",
    "market",
    "book",
    "line at news",
    "odds at news",
    "time of odds",
    "time of news",
    "closing line",
    "closing price",
    "result",
    "hit/loss",
    "CLV",
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
            CREATE TABLE IF NOT EXISTS injuries (
                athlete_key TEXT PRIMARY KEY,
                player TEXT NOT NULL,
                team TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT,
                url TEXT,
                updated_at TEXT NOT NULL
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
        self._ensure_result_columns()
        self.conn.commit()

    def seen(self, news_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM seen WHERE news_id = ?", (news_id,)).fetchone()
        return row is not None

    def alerted(self, news_id: str) -> bool:
        row = self.conn.execute(
            "SELECT alerted FROM seen WHERE news_id = ?", (news_id,)
        ).fetchone()
        return bool(row and row["alerted"])

    def injury_rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM injuries").fetchall())

    def replace_injuries(self, rows: list[tuple]) -> None:
        self.conn.execute("DELETE FROM injuries")
        self.conn.executemany(
            """
            INSERT INTO injuries (athlete_key, player, team, status, note, url, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    def count_seen(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0])

    def mark_seen(self, item: NewsItem, alerted: bool) -> None:
        self.conn.execute(
            """
            INSERT INTO seen (news_id, seen_at, headline, alerted) VALUES (?, ?, ?, ?)
            ON CONFLICT(news_id) DO UPDATE SET
                alerted = MAX(seen.alerted, excluded.alerted),
                headline = excluded.headline
            """,
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
                    reason, headline, news_url, news_id, category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
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
                    item.category,
                ),
            )
        self.conn.commit()
        self.export_csv()

    def pending_grades(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM bets WHERE graded = 0 ORDER BY game_start"
            ).fetchall()
        )

    def grade_bet(self, bet_id: str, played: int | None, actual: float | None) -> None:
        self.conn.execute(
            """
            UPDATE bets
            SET graded = 1, played = ?, actual = ?
            WHERE bet_id = ?
            """,
            (played, actual, bet_id),
        )
        self.conn.commit()

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

    def _ensure_result_columns(self) -> None:
        have = {row[1] for row in self.conn.execute("PRAGMA table_info(bets)")}
        if "graded" not in have:
            self.conn.execute("ALTER TABLE bets ADD COLUMN graded INTEGER NOT NULL DEFAULT 0")
        if "played" not in have:
            self.conn.execute("ALTER TABLE bets ADD COLUMN played INTEGER")
        if "actual" not in have:
            self.conn.execute("ALTER TABLE bets ADD COLUMN actual REAL")
        if "category" not in have:
            self.conn.execute("ALTER TABLE bets ADD COLUMN category TEXT")


def _csv_row(row: sqlite3.Row) -> dict[str, str]:
    if not row["graded"]:
        result, hit = "", ""
    elif row["played"] is None:
        result, hit = "no box", ""
    else:
        result, hit = outcome(row["side"], row["alert_line"], bool(row["played"]), row["actual"])
    return {
        "player": row["player"] or "",
        "news": row["headline"] or "",
        "category": row["category"] or "",
        "side": row["side"] or "",
        "market": row["market"] or "",
        "book": row["book"] or "",
        "line at news": _num(row["alert_line"]),
        "odds at news": _int(row["alert_price"]),
        "time of odds": row["fired_at"] or "",
        "time of news": row["news_time"] or "",
        "closing line": _num(row["close_line"]),
        "closing price": _int(row["close_price"]),
        "result": result,
        "hit/loss": hit,
        "CLV": _clv(row),
    }


def _clv(row: sqlite3.Row) -> str:
    if row["close_line"] is None or row["alert_line"] is None or not row["side"]:
        return ""
    return f"{line_clv(row['side'], row['alert_line'], row['close_line']):+.2f}"


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
