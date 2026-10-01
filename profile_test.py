"""Проверка новой страницы профиля и медиа-галереи."""
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["SECRET_KEY"] = "profile-test-secret"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="tbprof_")
DB = Path(tempfile.gettempdir()) / "trambot_profile_test.db"
for suffix in ("", "-journal", "-wal", "-shm"):
    DB.with_name(DB.name + suffix).unlink(missing_ok=True)
os.environ["DB_PATH"] = str(DB)

from app.config import settings  # noqa: E402
from app.core.crypto import make_token  # noqa: E402
from app.db.models import OAuthAccount, User  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.storage.factory import local_storage  # noqa: E402
from app.web.app import create_app  # noqa: E402
from app.web.deps import COOKIE  # noqa: E402

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


# маленький валидный PNG 1x1
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff0300000600"
    "0557bfabd40000000049454e44ae426082"
)

from starlette.testclient import TestClient  # noqa: E402


async def main():
    await init_db()
    uid = uuid.uuid4().hex
    async with session_scope() as s:
        s.add(
            User(
                id=uid,
                email="vasya@example.com",
                full_name="Вася Пупкин",
                username="vasya",
            )
        )
        await s.flush()
        s.add(
            OAuthAccount(
                user_id=uid,
                provider="google",
                provider_user_id="123",
                login="vasya",
                email="vasya@example.com",
                avatar_url="https://example.com/a.png",
            )
        )
        s.add(
            OAuthAccount(
                user_id=uid,
                provider="github",
                provider_user_id="456",
                login="vasyadev",
                email="vasya@github.dev",
                avatar_url="",
            )
        )

    local = local_storage(uid)
    await local.upload("photo.png", PNG)
    await local.upload("clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
    await local.upload("doc.txt", b"hello")

    app = create_app()
    c = TestClient(app)
    c.cookies.set(COOKIE, make_token({"uid": uid}))

    print("\n=== 1. Страница профиля ===")
    r = c.get("/profile")
    check("профиль открывается", r.status_code == 200, r.status_code)
    h = r.text
    check("имя из OAuth", "Вася Пупкин" in h)
    check("почта из OAuth", "vasya@example.com" in h)
    check("логин", "vasya" in h)
    check("аватар подставлен", "example.com/a.png" in h)
    check("фото в галерее", "data-count=\"1\"" in h)

    print("\n=== 2. API медиа ===")
    r = c.get("/api/media")
    check("api отвечает", r.status_code == 200, r.status_code)
    items = r.json()["items"]
    check("найдено 2 медиа", len(items) == 2, len(items))
    kinds = sorted(i["kind"] for i in items)
    check("фото и видео", kinds == ["photo", "video"], kinds)
    check("txt не попал", all("doc.txt" not in i["path"] for i in items))
    check("есть размер", all(i["size_h"] for i in items))

    print("\n=== 3. Просмотр файлов ===")
    r = c.get("/api/raw", params={"path": "photo.png"})
    check("raw отдаёт фото", r.status_code == 200, r.status_code)
    check("content-type image", r.headers["content-type"].startswith("image/"), r.headers.get("content-type"))
    check("байты совпали", r.content == PNG)

    r = c.get("/api/raw", params={"path": "clip.mp4"})
    check("raw отдаёт видео", r.status_code == 200, r.status_code)

    r = c.get("/api/raw", params={"path": "doc.txt"})
    check("не-медиа отклонено", r.status_code == 415, r.status_code)

    r = c.get("/api/raw", params={"path": "../secret.txt"})
    check("обход пути закрыт", r.status_code in (404, 415, 400), r.status_code)

    print("\n=== 4. Навигация ===")
    r = c.get("/")
    check("в меню есть Профиль", "/profile" in r.text)
    r = c.get("/files")
    check("файлы живут", r.status_code == 200)

    print("\n=== 5. Без авторизации ===")
    anon = TestClient(app)
    r = anon.get("/api/media", follow_redirects=False)
    check("api закрыт без входа", r.status_code in (302, 303, 307, 401, 403), r.status_code)
    r = anon.get("/profile", follow_redirects=False)
    check("профиль закрыт без входа", r.status_code in (302, 303, 307, 401), r.status_code)

    print(f"\n{'=' * 46}\nИТОГО: {ok} успешно, {fail} провалено\n{'=' * 46}")
    return 1 if fail else 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
