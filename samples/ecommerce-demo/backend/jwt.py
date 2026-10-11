import base64
import hashlib
import hmac
import json
import time

SECRET = b"change-me"
TTL_SECONDS = 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def create_token(subject: str) -> str:
    payload = _b64(json.dumps({"sub": subject, "exp": time.time() + TTL_SECONDS}).encode())
    sig = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str) -> dict:
    payload, sig = token.split(".")
    expected = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise PermissionError("bad signature")
    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    if data["exp"] < time.time():
        raise PermissionError("token expired")
    return data
