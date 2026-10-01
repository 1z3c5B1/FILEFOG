"""Проверка подлинности Telegram Mini App (initData).

Схема из документации Telegram:
  secret = HMAC_SHA256(key=b"WebAppData", msg=BOT_TOKEN)
  hash   = HMAC_SHA256(key=secret, msg=data_check_string)

В data_check_string входят все поля initData кроме самой подписи hash,
отсортированные по ключу и склеенные как "k=v&k=v".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

# сколько секунд initData считается живой
MAX_AGE = 86400


@dataclass
class TgInit:
    ok: bool
    user_id: int = 0
    username: str = ""
    first_name: str = ""
    last_name: str = ""
    language: str = ""
    photo: str = ""
    auth_date: int = 0
    start_param: str = ""
    error: str = ""


def _secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def check_hash(bot_token: str, pairs: list[tuple[str, str]]) -> str:
    """Сравнивает подпись. Возвращает "" если всё совпало, иначе описание."""
    data = dict(pairs)
    got = data.pop("hash", "")
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    expect = hmac.new(_secret(bot_token), check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not got:
        return "нет подписи hash"
    if not hmac.compare_digest(expect, got):
        return "подпись не совпадает"
    return ""


def parse_init_data(bot_token: str, init_data: str, max_age: int = MAX_AGE) -> TgInit:
    if not init_data or not bot_token:
        return TgInit(ok=False, error="пустые данные")

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    if not pairs:
        return TgInit(ok=False, error="данные не разобраны")

    err = check_hash(bot_token, pairs)
    if err:
        return TgInit(ok=False, error=err)

    fields = dict(pairs)

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        return TgInit(ok=False, error="плохой auth_date")

    if auth_date <= 0:
        return TgInit(ok=False, error="нет auth_date")
    if max_age and time.time() - auth_date > max_age:
        return TgInit(ok=False, error="данные протухли")

    raw_user = fields.get("user", "{}")
    try:
        u = json.loads(raw_user)
    except json.JSONDecodeError:
        return TgInit(ok=False, error="user не json")

    uid = u.get("id")
    if not isinstance(uid, int) or uid == 0:
        return TgInit(ok=False, error="нет user.id")

    return TgInit(
        ok=True,
        user_id=uid,
        username=str(u.get("username") or ""),
        first_name=str(u.get("first_name") or ""),
        last_name=str(u.get("last_name") or ""),
        language=str(u.get("language_code") or ""),
        photo=str(u.get("photo_url") or ""),
        auth_date=auth_date,
        start_param=str(fields.get("start_param") or ""),
    )
