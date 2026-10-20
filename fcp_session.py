"""Full Court Press login cookie used by the scanner."""

from __future__ import annotations

import json
from pathlib import Path

import requests

COOKIE_DOMAIN = "fcp.news"


def attach(session: requests.Session, data_dir: Path) -> bool:
    path = data_dir / "fcp_session.json"
    if not path.exists():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    cookies = payload.get("cookies") or {}
    if not cookies:
        return False
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=COOKIE_DOMAIN, path="/")
    return True


def save_cookie_header(data_dir: Path, header: str, email: str = "") -> Path:
    cookies = parse_cookie_header(header)
    if not any(name.startswith("sb-") for name in cookies):
        raise ValueError("that cookie text does not include a Full Court Press sign-in")
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "fcp_session.json"
    path.write_text(
        json.dumps({"email": email, "cookies": cookies}, indent=2),
        encoding="utf-8",
    )
    return path


def parse_cookie_header(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in header.split(";"):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        name, value = piece.split("=", 1)
        name = name.strip()
        if not name or name.lower() in {"path", "expires", "domain", "samesite", "max-age"}:
            continue
        cookies[name] = value.strip()
    return cookies
