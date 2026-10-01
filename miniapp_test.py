"""Проверка Telegram Mini App: подпись initData, вход, кнопки в боте."""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TOKEN = "123456:TESTTOKEN"
os.environ["BOT_TOKEN"] = TOKEN
os.environ["SECRET_KEY"] = "miniapp-test-secret"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="tbmg_")
DB = Path(tempfile.gettempdir()) / "trambot_miniapp_test.db"
for suffix in ("", "-journal", "-wal", "-shm"):
    DB.with_name(DB.name + suffix).unlink(missing_ok=True)
os.environ["DB_PATH"] = str(DB)
os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8000"

from starlette.testclient import TestClient  # noqa: E402

from app.bot.keyboards import main_menu, miniapp_button  # noqa: E402
from app.web.app import create_app  # noqa: E402
from app.web.deps import COOKIE  # noqa: E402
from app.web.tg_init import MAX_AGE, parse_init_data  # noqa: E402
from app.core.crypto import make_token  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


def sign(fields: dict, token: str = TOKEN) -> str:
    """Собирает init_data с корректной подписью — как это делает Telegram."""
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check_str = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    h = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**fields, "hash": h})


def user_json(uid: int, **extra) -> str:
    u = {"id": uid, "first_name": "Тест", "last_name": "Юзер", "username": "tester", "language_code": "ru"}
    u.update(extra)
    return json.dumps(u, separators=(",", ":"), ensure_ascii=False)


async def main():
    print("\n=== 1. Подпись initData ===")
    good = sign({"auth_date": str(int(time.time())), "query_id": "AAA", "user": user_json(42)})
    r = parse_init_data(TOKEN, good)
    check("валидные данные приняты", r.ok, r.error)
    check("user_id извлечён", r.user_id == 42, r.user_id)
    check("username извлечён", r.username == "tester", r.username)
    check("имя собрано", r.first_name == "Тест" and r.last_name == "Юзер")
    check("язык извлечён", r.language == "ru", r.language)

    print("\n=== 2. Подделка ===")
    r = parse_init_data(TOKEN, sign({"auth_date": str(int(time.time())), "user": user_json(42)}, token="999:WRONG"))
    check("чужой токен отклонён", not r.ok)
    check("причина понятна", "подпись" in r.error, r.error)

    tampered = good.replace("tester", "hacker")
    r = parse_init_data(TOKEN, tampered)
    check("изменённое поле отклонено", not r.ok)
    check("подмена видна", "подпись" in r.error, r.error)

    r = parse_init_data(TOKEN, "auth_date=1&user=%7B%22id%22%3A1%7D")
    check("без подписи отклонено", not r.ok)
    check("сказано про hash", "hash" in r.error, r.error)

    old = sign({"auth_date": "1", "user": user_json(42)})
    r = parse_init_data(TOKEN, old)
    check("протухшее отклонено", not r.ok)
    check("сказано про срок", "протухли" in r.error, r.error)

    r = parse_init_data(TOKEN, sign({"auth_date": str(int(time.time())), "user": "не-json"}))
    check("битый user отклонён", not r.ok)

    r = parse_init_data(TOKEN, "")
    check("пустые данные отклонены", not r.ok)
    r = parse_init_data(TOKEN, good, max_age=MAX_AGE * 3650)
    check("свежие проходят с большим max_age", r.ok, r.error)

    print("\n=== 3. Вход через /api/tg/auth ===")
    await init_db()
    app = create_app()
    c = TestClient(app)

    init_777 = sign({"auth_date": str(int(time.time())), "user": user_json(777)})
    r = c.post("/api/tg/auth", json={"init_data": init_777})
    check("вход успешен", r.status_code == 200 and r.json()["ok"], f"{r.status_code} {r.text[:80]}")
    check("cookie выставлена", COOKIE in r.cookies or bool(c.cookies.get(COOKIE)), c.cookies.get(COOKIE))

    r = c.get("/api/media")
    check("сессия работает после входа", r.status_code == 200, r.status_code)

    r = c.post("/api/tg/auth", json={"init_data": "мусор"})
    check("мусор отклонён", r.status_code == 401, r.status_code)

    r = c.post("/api/tg/auth", json={})
    check("пустое init_data отклонено", r.status_code == 401, r.status_code)

    r = c.post("/api/tg/auth", json={"init_data": init_777.replace("777", "888")})
    check("правка id отклонена", r.status_code == 401, r.status_code)
    r = c.post("/api/tg/auth", json={"init_data": init_777.replace("tester", "hacker")})
    check("правка username отклонена", r.status_code == 401, r.status_code)

    print("\n=== 4. Пользователь создан в БД ===")
    async with session_scope() as s:
        from sqlalchemy import select

        found = list((await s.execute(select(User).where(User.tg_id == 777))).scalars())
    check("юзер записан", len(found) == 1, len(found))
    if found:
        check("имя записано", found[0].full_name == "Тест Юзер", found[0].full_name)
        check("username записан", found[0].username == "tester", found[0].username)

    print("\n=== 5. Повторный вход ===")
    c2 = TestClient(app)
    r = c2.post("/api/tg/auth", json={"init_data": init_777})
    check("повторный вход ок", r.status_code == 200, r.status_code)
    async with session_scope() as s:
        from sqlalchemy import select

        n = len(list((await s.execute(select(User).where(User.tg_id == 777))).scalars()))
    check("дублей нет", n == 1, n)

    print("\n=== 6. Кнопки в боте ===")
    b = miniapp_button()
    check("кнопка есть", b is not None)
    check("это web_app", b is not None and b.web_app is not None)
    check("url из конфига", b is not None and b.web_app.url == "http://127.0.0.1:8000", b.web_app.url if b else "")
    kb = main_menu("ru")
    check("кнопка в меню", any(btn.web_app for row in kb.inline_keyboard for btn in row))
    check("кнопка первая", bool(kb.inline_keyboard[0][0].web_app))
    check("остальные кнопки на месте", any(bb.callback_data == "menu:files" for row in kb.inline_keyboard for bb in row))
    check("callback-кнопки без web_app", all(
        bb.web_app is None for row in kb.inline_keyboard for bb in row if bb.callback_data
    ))

    print("\n=== 7. base.html подключает SDK ===")
    html = (Path(__file__).resolve().parent / "app/web/templates/base.html").read_text(encoding="utf-8")
    check("SDK подключён", "telegram-web-app.js" in html)
    check("есть загрузчик", "tgLoader" in html)
    check("есть класс in-telegram", "in-telegram" in html or "in-telegram" in Path(
        Path(__file__).resolve().parent / "app/web/static/style.css").read_text(encoding="utf-8"))

    print("\n=== 8. Нет зацикливания перезагрузки (data-authed) ===")
    # tgAuth() вызывается на каждой странице. Если он безусловно делает
    # location.replace('/'), то на главной страница заменяет саму себя
    # бесконечно: лоадер мигает, кнопка назад моргает.
    js = (Path(__file__).resolve().parent / "app/web/static/app.js").read_text(encoding="utf-8")
    check("base.html передаёт data-authed", 'data-authed=' in html)
    check("JS читает data-authed", "dataset.authed" in js)
    check("JS проверяет alreadyAuthed перед входом", "alreadyAuthed()" in js)
    check("редирект не выполняется на месте", "location.pathname + location.search !== to" in js)
    body = html.split("<body", 1)[1].split(">", 1)[0]
    check("data-authed внутри body", "data-authed" in body, body[:120])
    check("кнопка назад не дублируется", js.count("back.id = 'tgBack'") == 1, js.count("back.id = 'tgBack'"))

    print(f"\n{'=' * 46}\nИТОГО: {ok} успешно, {fail} провалено\n{'=' * 46}")
    return 1 if fail else 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
