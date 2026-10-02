"""Сквозная проверка: вход по initData -> cookie -> доступ к главной.

Если cookie не работает, главная редиректит на /login, страница снова
вызывает tgAuth и уходит в бесконечную перезагрузку ("Подключаемся к
Telegram..." мигает). Этот тест воспроизводит ровно тот цикл.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TOKEN = "123456:TESTTOKEN"
os.environ["BOT_TOKEN"] = TOKEN
os.environ["SECRET_KEY"] = "loop-test-secret"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="tbloop_")
DB = Path(tempfile.gettempdir()) / "trambot_loop_test.db"
for suffix in ("", "-journal", "-wal", "-shm"):
    DB.with_name(DB.name + suffix).unlink(missing_ok=True)
os.environ["DB_PATH"] = str(DB)
os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8000"

from starlette.testclient import TestClient  # noqa: E402

from app.db.session import init_db  # noqa: E402
from app.web.app import create_app  # noqa: E402

ok = 0
fail = 0


def check(name: str, cond: bool, extra: object = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


def sign(fields: dict) -> str:
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check_str = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    h = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**fields, "hash": h})


import asyncio  # noqa: E402

init_data = sign(
    {
        "auth_date": "2000000000",
        "query_id": "AAE-test",
        "user": json.dumps({"id": 555001, "first_name": "Лооп", "username": "looper"}, separators=(",", ":")),
    }
)


async def main() -> int:
    app = create_app()
    await init_db()

    print("=== 1. Вход выдаёт куку ===")
    with TestClient(app) as c:
        r = c.post("/api/tg/auth", json={"init_data": init_data})
        check("вход 200", r.status_code == 200, r.status_code)
        if r.status_code != 200:
            print(f"     тело: {r.text[:300]}")
            return 1
        body = r.json()
        check("ok=true", body.get("ok") is True, body)
        check("редирект на /", body.get("redirect") == "/", body.get("redirect"))

        cookies = r.cookies.get("tb_session") or c.cookies.get("tb_session")
        check("кука tb_session выставлена", bool(cookies), "нет куки")

    print("\n=== 2. Кука открывает главную (иначе будет цикл) ===")
    with TestClient(app) as c:
        c.cookies.set("tb_session", cookies)
        r = c.get("/", follow_redirects=False)
        check("главная не редиректит на /login", r.status_code == 200, r.status_code)
        if r.status_code == 307:
            loc = r.headers.get("location", "")
            check("редирект ведёт на /login (цикл подтверждён)", "/login" in loc, loc)
            print("     >>> ЦИКЛ ПОДТВЕРЖДЁН: главная -> /login -> tgAuth -> / ...")

    print("\n=== 3. Без куки главная отдаёт /login ===")
    with TestClient(app) as c:
        r = c.get("/", follow_redirects=False)
        check("редирект на /login без куки", r.status_code in (302, 307), r.status_code)

    print("\n=== 4. Разметка сообщает браузеру о наличии сессии ===")
    with TestClient(app) as c:
        c.cookies.set("tb_session", cookies)
        r = c.get("/", follow_redirects=False)
        if r.status_code == 200:
            check("data-authed=1 при активной куке", 'data-authed="1"' in r.text, "нет")
    with TestClient(app) as c:
        r = c.get("/login", follow_redirects=False)
        check("data-authed=0 без куки", 'data-authed="0"' in r.text, "нет")

    print("\n=== 5. Страховка от цикла в клиентском коде ===")
    js = (ROOT / "app/web/static/app.js").read_text(encoding="utf-8")
    check("sessionStorage-флаг есть", "tg_auth_tried" in js)
    check("повторная попытка блокируется", "alreadyTried" in js)
    check("редирект не выполняется на месте", "location.pathname + location.search !== to" in js)
    check("tg.ready/expand в try", "try {" in js and "if (tg.expand)" in js)
    check("оформление обёрнуто в try", "tgChrome();" in js)

    print("\n=== 6. Статика версионирована (сброс кеша WebView) ===")
    base = (ROOT / "app/web/templates/base.html").read_text(encoding="utf-8")
    check("app.js с версией", "app.js?v=" in base)
    check("style.css с версией", "style.css?v=" in base)
    from app.web.app import asset_version

    v1 = asset_version()
    check("asset_v непустой", bool(v1), v1)

    print(f"\n{'=' * 46}\nИТОГО: {ok} успешно, {fail} провалено\n{'=' * 46}")
    return 1 if fail else 0


raise SystemExit(asyncio.run(main()))
