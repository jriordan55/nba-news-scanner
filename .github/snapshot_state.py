"""Copy the scanner database and write a hash of the rows that decide an alert.

The live process keeps data/scanner.sqlite3 open. Git commits the copy.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "scanner.sqlite3"
SNAP = ROOT / "data" / ".scanner.snapshot.sqlite3"
DIGEST = ROOT / "data" / ".state.hash"


def main() -> None:
    if not LIVE.exists():
        return
    src = sqlite3.connect(LIVE, timeout=30)
    dst = sqlite3.connect(SNAP)
    src.backup(dst)
    dst.close()
    src.close()
    con = sqlite3.connect(SNAP)
    parts: list[str] = []
    for table in ("seen", "watches", "bets"):
        rows = con.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        parts.append(repr(rows))
    con.close()
    DIGEST.write_text(hashlib.sha256("\n".join(parts).encode()).hexdigest(), encoding="utf-8")


if __name__ == "__main__":
    main()
