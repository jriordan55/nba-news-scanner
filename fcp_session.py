"""Full Court Press sign-in. The cookie is refreshed before it expires."""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

log = logging.getLogger("nba-scanner")

COOKIE_DOMAIN = "fcp.news"
COOKIE_NAME = "sb-jpdoxlpsqelrcktarnml-auth-token"
SUPABASE_URL = "https://jpdoxlpsqelrcktarnml.supabase.co"
# Public client key shipped in the Full Court Press login page.
SUPABASE_KEY = "sb_publishable_TV20-8EcqU1pfFVhuAHpiw_stXPJpV_"
CHUNK = 3180
REFRESH_EARLY_SECONDS = 10 * 60


def attach(session: requests.Session, data_dir: Path) -> bool:
    path = _path(data_dir)
    if not path.exists():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload = _refresh_if_due(session, payload, path)
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
    return _write(data_dir, {"email": email, "cookies": cookies})


def save_tokens(
    data_dir: Path,
    access_token: str,
    refresh_token: str,
    expires_in: int = 3600,
    email: str = "",
) -> Path:
    expires_at = int(time.time()) + int(expires_in)
    payload = {
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "expires_in": int(expires_in),
        "token_type": "bearer",
    }
    payload["cookies"] = cookies_for_session(payload)
    return _write(data_dir, payload)


def accept_link(data_dir: Path, url: str, http: requests.Session | None = None) -> Path:
    """Turn a one-time email link into a session the scanner can refresh."""
    client = http or requests.Session()
    tokens = _tokens_from_url(client, url.strip())
    return save_tokens(
        data_dir,
        tokens["access_token"],
        tokens["refresh_token"],
        int(tokens.get("expires_in") or 3600),
    )


def cookies_for_session(session: dict) -> dict[str, str]:
    body = {
        "access_token": session["access_token"],
        "refresh_token": session.get("refresh_token") or "",
        "expires_at": session.get("expires_at"),
        "expires_in": session.get("expires_in"),
        "token_type": session.get("token_type") or "bearer",
        "user": session.get("user") or {},
    }
    encoded = "base64-" + _base64url(json.dumps(body, separators=(",", ":")))
    if len(encoded) <= CHUNK:
        return {COOKIE_NAME: encoded}
    cookies: dict[str, str] = {}
    for index, start in enumerate(range(0, len(encoded), CHUNK)):
        cookies[f"{COOKIE_NAME}.{index}"] = encoded[start : start + CHUNK]
    return cookies


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


def _refresh_if_due(http: requests.Session, payload: dict, path: Path) -> dict:
    refresh_token = payload.get("refresh_token") or ""
    if not refresh_token:
        return payload
    expires_at = int(payload.get("expires_at") or 0)
    if payload.get("cookies") and expires_at > time.time() + REFRESH_EARLY_SECONDS:
        return payload
    try:
        response = http.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
            },
            json={"refresh_token": refresh_token},
            timeout=20,
        )
        response.raise_for_status()
        fresh = response.json()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("Full Court Press sign-in refresh failed (%s)", status or type(exc).__name__)
        return payload
    updated = {
        "email": payload.get("email") or "",
        "access_token": fresh["access_token"],
        "refresh_token": fresh.get("refresh_token") or refresh_token,
        "expires_at": int(time.time()) + int(fresh.get("expires_in") or 3600),
        "expires_in": int(fresh.get("expires_in") or 3600),
        "token_type": fresh.get("token_type") or "bearer",
        "user": fresh.get("user") or {},
    }
    updated["cookies"] = cookies_for_session(updated)
    path.write_text(json.dumps(updated), encoding="utf-8")
    log.info("refreshed the Full Court Press sign-in")
    return updated


def _tokens_from_url(http: requests.Session, url: str) -> dict[str, str]:
    found = _parse_token_url(url)
    if found:
        return found
    response = http.get(url, allow_redirects=False, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
    location = response.headers.get("Location") or ""
    found = _parse_token_url(location)
    if found:
        return found
    if location.startswith("http") and response.status_code in {301, 302, 303, 307, 308}:
        return _tokens_from_url(http, location)
    raise ValueError("that link did not include a Full Court Press sign-in")


def _parse_token_url(url: str) -> dict[str, str] | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.fragment or parsed.query)
    access = (query.get("access_token") or [""])[0]
    refresh = (query.get("refresh_token") or [""])[0]
    if not access or not refresh:
        return None
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": (query.get("expires_in") or ["3600"])[0],
    }


def _base64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _path(data_dir: Path) -> Path:
    return data_dir / "fcp_session.json"


def _write(data_dir: Path, payload: dict) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _path(data_dir)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
