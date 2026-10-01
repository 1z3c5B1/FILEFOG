"""Шифрование токенов (AES-128-GCM через Fernet) и подпись сессий."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        return _fernet().decrypt(cipher.encode()).decode()
    except InvalidToken:
        return ""


# ---------- подписанные cookie ----------

def _sign(payload: bytes) -> str:
    mac = hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def make_token(data: dict, ttl: int = 60 * 60 * 24 * 30) -> str:
    body = dict(data)
    body["exp"] = int(time.time()) + ttl
    raw = json.dumps(body, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode() + "." + _sign(raw)


def read_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body_b64, sig = token.rsplit(".", 1)
    try:
        raw = base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4))
    except Exception:
        return None
    if not hmac.compare_digest(_sign(raw), sig):
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    return data


def new_state() -> str:
    return secrets.token_urlsafe(24)
