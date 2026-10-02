"""Локальный smoke-тест без Telegram и без облака."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["SECRET_KEY"] = "test-secret-key-1234567890"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="trambot_test_")
DB = Path(tempfile.gettempdir()) / "trambot_smoke.db"
for suffix in ("", "-journal", "-wal", "-shm"):
    DB.with_name(DB.name + suffix).unlink(missing_ok=True)
os.environ["DB_PATH"] = str(DB)

from app.config import settings  # noqa: E402
from app.core import user_settings as us  # noqa: E402
from app.core.crypto import decrypt, encrypt, make_token, read_token  # noqa: E402
from app.core.i18n import tr  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.core.users import upsert_tg_user  # noqa: E402
from app.storage.base import join_path, safe_path, parent_of  # noqa: E402
from app.storage.factory import local_storage  # noqa: E402
from app.storage.local import human_size  # noqa: E402
from app.sync.engine import sync_user  # noqa: E402
from app.web.app import create_app  # noqa: E402

ok = 0
fail = 0
failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  \u2714 {name}")
    else:
        fail += 1
        print(f"  \u2716 {name} {extra}")


async def main() -> int:
    print("\n=== 1. БД и пользователь ===")
    await init_db()
    async with session_scope() as s:
        u = await upsert_tg_user(s, tg_id=42, username="vadim", full_name="Вадим", is_admin=True)
        uid = u.id
    check("пользователь создан", bool(uid))
    check("админ выставлен", u.is_admin)

    print("\n=== 2. Пути и безопасность ===")
    check("обход ../ блокируется", safe_path("../../etc/passwd") == "etc/passwd", safe_path("../../etc/passwd"))
    check("абсолютный путь чистится", safe_path("/var/data/./x.txt") == "var/data/x.txt")
    check("windows-имя нормализуется", safe_path('a<b>:c.txt').replace("?", "") not in ("a<b>:c.txt",))
    check("join_path работает", join_path("Trambot", "a/b.txt") == "Trambot/a/b.txt")
    check("parent_of корректен", parent_of("a/b/c.txt") == "a/b" and parent_of("c.txt") == "")

    print("\n=== 3. Крипто ===")
    ct = encrypt("ya29.super-secret")
    check("токен шифруется", ct != "ya29.super-secret" and decrypt(ct) == "ya29.super-secret")
    tok = make_token({"uid": uid}, ttl=60)
    check("подпись сессии валидна", (read_token(tok) or {}).get("uid") == uid)
    check("подделка токена отвергнута", read_token(tok[:-3] + "xxx") is None)
    check("истёкший токен отвергнут", read_token(make_token({"uid": uid}, ttl=-10)) is None)

    print("\n=== 4. Настройки ===")
    check("всего настроек >= 30", len(us.SETTINGS) >= 30, len(us.SETTINGS))
    check("дефолты загружены", us.DEFAULTS["max_file_mb"] == 100)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    check("дефолт sync_both_ways", prefs["sync_both_ways"] is True)
    async with session_scope() as s:
        ok1, _ = await us.set_value(s, uid, "max_file_mb", 250)
    check("валидное значение принято", ok1)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    check("значение сохранено", prefs["max_file_mb"] == 250)
    async with session_scope() as s:
        bad, info = await us.set_value(s, uid, "max_file_mb", 99999)
    check("лимит отвергнут", not bad, info)
    async with session_scope() as s:
        bad2, _ = await us.set_value(s, uid, "sync_conflicts", "nonsense")
    check("choice отвергнут", not bad2)
    async with session_scope() as s:
        await us.set_value(s, uid, "sync_conflicts", "both")
    async with session_scope() as s:
        check("choice сохранён", (await us.get(s, uid, "sync_conflicts")) == "both")
    check("label_ru для bool", us.BY_KEY["auto_sync"].label_ru(True) == "вкл")
    check("label_ru для choice", us.BY_KEY["locale"].label_ru("en") == "Английский")

    print("\n=== 5. Локальное хранилище ===")
    local = local_storage(uid)
    f1 = await local.upload("Uploads/hello.txt", b"hello trambot")
    check("файл загружен", f1.size == 13, f1.size)
    check("md5 посчитан", len(f1.checksum) == 32)
    f2 = await local.upload("Uploads/sub/data.bin", b"\x00\x01\x02")
    listing = await local.list("Uploads")
    check("в листинге 2 объекта", len(listing) == 2, [i.path for i in listing])
    check("папка помечена как каталог", any(i.mime == "inode/directory" for i in listing))
    check("чтение работает", await local.download("Uploads/hello.txt") == b"hello trambot")
    walked = await local.walk()
    check("walk находит 2 файла", len(walked) == 2, walked)
    check("размер считается", await local.total_size() == 16, await local.total_size())
    snap = await local.snapshot()
    check("snapshot отдаёт размер и время", len(snap) == 2 and all(isinstance(v[0], int) for v in snap.values()), snap)
    await local.mkdir("Uploads/empty")
    check("mkdir создал каталог", local.abspath("Uploads/empty").is_dir())
    await local.delete("Uploads/empty")
    check("delete убрал каталог", not local.abspath("Uploads/empty").exists())
    evil = "../../../Windows/System32/config"
    resolved = local.abspath(evil)
    check(
        "обход остаётся внутри хранилища",
        str(resolved).startswith(str(local.root.resolve())),
        resolved,
    )
    check("обход очищен от ..", ".." not in resolved.as_posix(), resolved.as_posix())
    check("human_size", human_size(1536) == "1.5 KB", human_size(1536))

    print("\n=== 6. i18n ===")
    check("ru ключ", tr("files", "ru") == "\U0001f5c2 Файлы")
    check("en ключ", tr("settings", "en") == "\U0001f6e0 Settings")
    check("форматирование", "100" in tr("file_too_big", "ru", limit=100))

    print("\n=== 7. Синхронизация без облака ===")
    async with session_scope() as s:
        r = await sync_user(s, uid)
    check("ошибка провайдера обработана", r.status == "error", r.message)
    check("сообщение понятное", r.message == "provider_not_selected", r.message)
    async with session_scope() as s:
        r2 = await sync_user(s, uid, "google")
    check("нет токена -> ошибка", r2.status == "error" and "нет доступа" in r2.message, r2.message)
    from app.sync.engine import last_logs
    async with session_scope() as s:
        logs = await last_logs(s, uid, 5)
    check("логи пишутся в БД", len(logs) >= 2, len(logs))

    print("\n=== 8. Веб-панель ===")
    app = create_app()
    routes = sorted({r.path for r in app.routes if hasattr(r, "path")})
    for need in ("/", "/login", "/files", "/settings", "/accounts", "/api/files",
                 "/api/settings", "/api/sync", "/auth/{provider}/login",
                 "/auth/{provider}/callback", "/api/download", "/api/download-zip"):
        check(f"роут {need}", need in routes)

    from fastapi.testclient import TestClient
    from app.core.crypto import make_token as mt

    with TestClient(app) as client:
        r = client.get("/", follow_redirects=False)
        check("гость редиректится на /login", r.status_code == 307, r.status_code)
        r = client.get("/login")
        check("страница входа отдаётся", r.status_code == 200 and "Google" in r.text)
        r = client.get("/static/style.css")
        check("css отдаётся", r.status_code == 200 and "--accent" in r.text)

        client.cookies.set("tb_session", mt({"uid": uid}, ttl=3600))
        r = client.get("/")
        check("главная после входа", r.status_code == 200, r.status_code)
        check("видно имя", "Вадим" in r.text)
        check("видно хранилище", "Uploads" in r.text or "Занято" in r.text)
        r = client.get("/files")
        check("страница файлов (корень)", r.status_code == 200 and "Uploads" in r.text)
        r = client.get("/files?path=Uploads")
        check("вход в папку", r.status_code == 200 and "hello.txt" in r.text)
        r = client.get("/settings")
        check("страница настроек", r.status_code == 200 and "Интервал автосинхронизации" in r.text)
        r = client.get("/accounts")
        check("страница аккаунтов", r.status_code == 200 and "GitHub" in r.text)
        r = client.get("/api/files?path=Uploads")
        check("api списка файлов", r.status_code == 200 and len(r.json()["items"]) == 2, r.text[:200])
        r = client.get("/api/settings")
        check("api настроек", r.status_code == 200 and r.json()["prefs"]["max_file_mb"] == 250)
        r = client.post("/api/settings", json={"prefs": {"max_file_mb": 300}})
        check("api смены настройки", r.status_code == 200 and r.json()["prefs"]["max_file_mb"] == 300, r.text[:200])
        r = client.post("/api/settings", json={"prefs": {"max_file_mb": 99999}})
        check("api валидации", r.status_code == 200 and r.json()["ok"] is False)
        r = client.post("/api/settings", json={"prefs": {"theme": "light"}})
        check("тема сохранилась", r.json()["prefs"]["theme"] == "light")
        r = client.post("/api/files/mkdir", json={"name": "Photos", "path": ""})
        check("создание папки", r.status_code == 200)
        r = client.get("/files?path=Photos")
        check("папка видна", r.status_code == 200)
        r = client.post(
            "/api/files/upload",
            files=[("files", ("web.txt", b"from web panel", "text/plain"))],
            data={"path": "Photos"},
        )
        check("загрузка через панель", r.status_code == 200 and r.json()["saved"] == ["Photos/web.txt"], r.text[:200])
        r = client.get("/api/download?path=Photos/web.txt")
        check("скачивание файла", r.status_code == 200 and r.content == b"from web panel")
        r = client.get("/api/download-zip?path=")
        check("zip скачивается", r.status_code == 200 and r.content[:2] == b"PK", r.content[:8])
        r = client.post("/api/files/rename", json={"from": "Photos/web.txt", "to": "Photos/renamed.txt"})
        check("переименование", r.status_code == 200)
        r = client.get("/files?path=Photos")
        check("новое имя видно", "renamed.txt" in r.text)
        r = client.post("/api/files/delete", json={"paths": ["Photos/renamed.txt"]})
        check("удаление", r.status_code == 200)
        r = client.get("/files?path=Photos")
        check("файл исчез", "renamed.txt" not in r.text)
        r = client.get("/api/sync/logs")
        check("история синхронизации отдаётся", r.status_code == 200 and len(r.json()["items"]) >= 2)
        r = client.post("/api/sync", json={"direction": "both"})
        check("api синхронизации отвечает", r.status_code == 200 and "ok" in r.json())
        r = client.get("/auth/nope/login")
        check("неизвестный провайдер -> 404", r.status_code == 404)
        r = client.get("/auth/google/login", follow_redirects=False)
        check("oauth редиректит", r.status_code in (307, 503), r.status_code)

    print(f"\n{'='*46}\nИТОГО: {ok} успешно, {fail} провалено\n{'='*46}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
