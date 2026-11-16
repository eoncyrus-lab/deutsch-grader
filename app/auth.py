from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from app import db
from app.settings import settings


PBKDF2_ROUNDS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ROUNDS)
    return "$".join(["pbkdf2_sha256", str(PBKDF2_ROUNDS), salt, digest.hex()])


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(rounds))
    return hmac.compare_digest(digest.hex(), expected)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def create_session(conn, user_id: str) -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    db.insert(conn, "session", {
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "user_id": user_id,
        "csrf": csrf,
        "expires_at": _iso(expires),
        "created_at": db.utcnow(),
    })
    return token, csrf, expires


def session_user(request: Request) -> dict:
    token = request.cookies.get("dg_session", "")
    if not token:
        raise HTTPException(status_code=401, detail="not signed in")
    conn = db.connect()
    try:
        row = db.fetch_one(
            conn,
            "SELECT u.*, s.csrf, s.expires_at FROM session s JOIN app_user u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
            (hashlib.sha256(token.encode()).hexdigest(), db.utcnow()),
        )
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="session expired")
    return row


def require_csrf(request: Request, user: dict) -> None:
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get("dg_csrf", "")
    if not header or not cookie or not hmac.compare_digest(header, cookie) or not hmac.compare_digest(header, user["csrf"]):
        raise HTTPException(status_code=403, detail="csrf check failed")


def require_role(user: dict, *roles: str) -> None:
    if user["role"] not in roles:
        raise HTTPException(status_code=403, detail="insufficient role")
