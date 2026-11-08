"""Passwords and session tokens. Standard library only.

Passwords are hashed with scrypt (random 16-byte salt, stored as `scrypt$n$r$p$salt$hash`) and compared in
constant time. A session token is `user_id.expiry.signature`: HMAC-SHA256 over the first two parts with a key
kept in `data/session.key` (created on first use), so it cannot be forged and carries no secrets. Whether the
user still exists is checked on every request (see `auth_deps.py`), so deleting a user ends their sessions."""
import base64
import hashlib
import hmac
import secrets
import time
import uuid
from pathlib import Path

from app.config import settings

SESSION_COOKIE = "devdocs_session"
SESSION_SECONDS = 7 * 24 * 3600
MIN_PASSWORD_LENGTH = 8

_N, _R, _P = 2 ** 14, 8, 1
_key: bytes | None = None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, _unb64(digest))


# A hash to verify against when the e-mail is unknown, so "no such user" costs the same time as "wrong password".
DUMMY_HASH = hash_password(secrets.token_hex(8))


def _signing_key() -> bytes:
    global _key
    if _key is None:
        path: Path = settings.data_dir / "session.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
        _key = path.read_bytes()
    return _key


def _sign(payload: str) -> str:
    return _b64(hmac.new(_signing_key(), payload.encode(), hashlib.sha256).digest())


def make_token(user_id: uuid.UUID, now: float | None = None) -> str:
    payload = f"{user_id}.{int((now if now is not None else time.time()) + SESSION_SECONDS)}"
    return f"{payload}.{_sign(payload)}"


def read_token(token: str | None, now: float | None = None) -> uuid.UUID | None:
    """The user id inside a valid, unexpired token; None for anything else."""
    if not token:
        return None
    try:
        user_part, expiry, signature = token.split(".")
        if not hmac.compare_digest(_sign(f"{user_part}.{expiry}"), signature):
            return None
        if int(expiry) < (now if now is not None else time.time()):
            return None
        return uuid.UUID(user_part)
    except (ValueError, TypeError):
        return None
