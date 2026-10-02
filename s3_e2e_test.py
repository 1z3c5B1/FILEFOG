"""Сквозной тест веб-приложения, когда хранилище - S3/R2, а не папка.

Проверяет ровно то, что ломается при переключении на R2: страницы, которые
раньше читали файлы с диска, и endpoints, отдававшие FileResponse по пути.
Здесь поднимается поддельный S3-сервер, так что ключи не нужны.

Главная проверка - что все маршруты доходят до бэкета и не падают.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["SECRET_KEY"] = "test-secret-key-1234567890"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="trambot_s3e2e_")
DB = Path(tempfile.gettempdir()) / "trambot_s3e2e.db"
for suffix in ("", "-journal", "-wal", "-shm"):
    DB.with_name(DB.name + suffix).unlink(missing_ok=True)
os.environ["DB_PATH"] = str(DB)

# Включаем S3 до импорта конфига: settings читается один раз при импорте.
os.environ["S3_ENDPOINT"] = "https://r2.example.com"
os.environ["S3_BUCKET"] = "filefog"
os.environ["S3_ACCESS_KEY"] = "AKIAIOSFODNN7EXAMPLE"
os.environ["S3_SECRET_KEY"] = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
os.environ["S3_REGION"] = "auto"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from s3_test import BUCKET, FakeClient, FakeS3  # noqa: E402

server = FakeS3()
httpx.AsyncClient = lambda **kw: FakeClient(server)  # type: ignore[misc]

from app.config import settings  # noqa: E402
from app.core.crypto import make_token  # noqa: E402
from app.core.users import upsert_tg_user  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.storage.factory import backend_name, local_storage  # noqa: E402
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
        failures.append(name)
        print(f"  \u2716 {name} {extra}")


async def prepare() -> str:
    from app.db.session import session_scope

    await init_db()
    async with session_scope() as s:
        user = await upsert_tg_user(s, 100500, username="e2e", full_name="E2E")
        return user.id


def main() -> int:
    print("=== 1. S3 действительно включён ===")
    check("settings видят S3", settings.s3_ready)
    check("бэкенд выбран - s3", backend_name() == "s3", backend_name())
    check("частичной настройки нет", not settings.s3_configured_partially)

    uid = asyncio.run(prepare())
    check("пользователь создан", bool(uid), uid)

    store = local_storage(uid)
    check("фабрика вернула S3Storage", store.name == "s3", store.name)
    check("префикс пользователя", store.key_of("a.txt") == f"{uid}/a.txt", store.key_of("a.txt"))

    app = create_app()
    cookie = {"tb_session": make_token({"uid": uid}, ttl=3600)}

    with TestClient(app, cookies=cookie) as client:
        print("\n=== 2. Страницы с S3-хранилищем ===")
        for path in ("/", "/files", "/files?path=Uploads", "/profile", "/settings", "/accounts"):
            r = client.get(path)
            check(f"{path} -> 200", r.status_code == 200, r.status_code)
            check(f"{path} без traceback", "Traceback" not in r.text)

        print("\n=== 3. Загрузка через веб ===")
        r = client.post(
            "/api/files/upload",
            files=[("files", ("hello.txt", b"hello from r2", "text/plain"))],
            data={"path": "Uploads"},
        )
        check("upload -> ok", r.status_code == 200 and r.json().get("ok"), r.text[:120])
        saved = r.json().get("saved") or []
        check("путь сохранён", saved == ["Uploads/hello.txt"], saved)
        check("объект лежит в бакете", f"{uid}/Uploads/hello.txt" in server.objects, list(server.objects)[:5])

        print("\n=== 4. Список файлов ===")
        r = client.get("/api/files?path=Uploads")
        check("листинг -> 200", r.status_code == 200, r.status_code)
        names = [i["path"] for i in r.json().get("items", [])]
        check("файл в листинге", "Uploads/hello.txt" in names, names)
        r = client.get("/api/files")
        check("листинг корня -> 200", r.status_code == 200, r.status_code)
        roots = [i["path"] for i in r.json().get("items", [])]
        check("папка Uploads видна", "Uploads" in roots, roots)

        print("\n=== 5. Скачивание ===")
        r = client.get("/api/download", params={"path": "Uploads/hello.txt"})
        check("download -> 200", r.status_code == 200, r.status_code)
        check("содержимое целое", r.content == b"hello from r2", r.content[:40])
        check("это вложение", "attachment" in r.headers.get("content-disposition", ""), r.headers.get("content-disposition"))

        r = client.get("/api/raw", params={"path": "Uploads/hello.txt"})
        check("raw отдаёт текст", r.status_code == 415, r.status_code)

        print("\n=== 6. Бинарные файлы и медиа ===")
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
            b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
            b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        r = client.post("/api/files/upload", files=[("files", ("pic.png", png, "image/png"))], data={"path": "Uploads"})
        check("png загружен", r.status_code == 200 and r.json().get("ok"), r.text[:120])
        r = client.get("/api/raw", params={"path": "Uploads/pic.png"})
        check("raw отдаёт картинку", r.status_code == 200, r.status_code)
        check("png не побился", r.content == png, len(r.content))
        check("mime image/png", r.headers.get("content-type", "").startswith("image/png"), r.headers.get("content-type"))

        r = client.get("/api/media")
        check("медиалист -> 200", r.status_code == 200, r.status_code)
        media = [i["path"] for i in r.json().get("items", [])]
        check("картинка попала в медиа", "Uploads/pic.png" in media, media)
        item = next((i for i in r.json()["items"] if i["path"] == "Uploads/pic.png"), {})
        check("размер из листинга", item.get("size") == len(png), item.get("size"))
        check("размер_человеческий", item.get("size_h"), item.get("size_h"))

        print("\n=== 7. Папки ===")
        r = client.post("/api/files/mkdir", json={"path": "Uploads", "name": "sub"})
        check("mkdir -> ok", r.status_code == 200 and r.json().get("ok"), r.text[:120])
        check("маркер папки создан", f"{uid}/Uploads/sub/" in server.objects, list(server.objects))
        r = client.get("/api/files", params={"path": "Uploads"})
        subs = [i["path"] for i in r.json().get("items", [])]
        check("папка видна в листинге", "Uploads/sub" in subs, subs)

        print("\n=== 8. Переименование ===")
        r = client.post("/api/files/rename", json={"from": "Uploads/hello.txt", "to": "Uploads/renamed.txt"})
        check("rename -> ok", r.status_code == 200 and r.json().get("ok"), r.text[:120])
        check("новое имя в бакете", f"{uid}/Uploads/renamed.txt" in server.objects, list(server.objects))
        check("старое имя удалено", f"{uid}/Uploads/hello.txt" not in server.objects)

        print("\n=== 9. Архив ===")
        r = client.get("/api/download-zip", params={"path": "Uploads"})
        check("zip -> 200", r.status_code == 200, r.status_code)
        check("zip не пустой", r.content[:2] == b"PK", r.content[:8])
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = z.namelist()
        check("в архиве есть файл", "Uploads/renamed.txt" in names, names)

        print("\n=== 10. Удаление ===")
        r = client.post("/api/files/delete", json={"paths": ["Uploads/renamed.txt"]})
        check("delete -> ok", r.status_code == 200 and r.json().get("ok"), r.text[:120])
        check("объект удалён из бакета", f"{uid}/Uploads/renamed.txt" not in server.objects)
        r = client.get("/api/download", params={"path": "Uploads/renamed.txt"})
        check("удалённый файл -> 404", r.status_code == 404, r.status_code)

        print("\n=== 11. Чужой файл недоступен ===")
        server.seed("someone-else/secret.txt", b"private")
        r = client.get("/api/download", params={"path": "../someone-else/secret.txt"})
        check("обход префикса заблокирован", r.status_code in (404, 400), r.status_code)
        check("чужой файл не отдан", b"private" not in r.content)

        print("\n=== 12. Квота считается по S3 ===")
        r = client.get("/api/settings")
        check("settings -> 200", r.status_code == 200, r.status_code)
        r = client.get("/api/files?path=Uploads")
        check("после удалений листинг жив", r.status_code == 200, r.status_code)

    print(f"\n{'=' * 50}\nИТОГО: {ok} успешно, {fail} провалено\n{'=' * 50}")
    if failures:
        print("провалы: " + "; ".join(failures))
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
