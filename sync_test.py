"""Тест синхронизации на моках Google Drive и Яндекс Диска (сеть не нужна)."""
import asyncio
import hashlib
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="tbsync_")
DB = Path(tempfile.gettempdir()) / "trambot_sync_mock.db"
for suffix in ("", "-journal", "-wal", "-shm"):
    DB.with_name(DB.name + suffix).unlink(missing_ok=True)
os.environ["DB_PATH"] = str(DB)

from app.config import settings  # noqa: E402
from app.core import user_settings as us  # noqa: E402
from app.core.crypto import encrypt  # noqa: E402
from app.core.users import upsert_tg_user  # noqa: E402
from app.db.models import OAuthAccount, OAuthToken  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.storage.base import RemoteFile  # noqa: E402
from app.sync import engine  # noqa: E402
from app.sync.engine import sync_user  # noqa: E402

ok = 0
fail = 0
failures: list[str] = []


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  \u2714 {name}")
    else:
        fail += 1
        print(f"  \u2716 {name} {extra}")


class FakeCloud:
    """Имитация облака: dict путь -> (bytes, modified, id)."""

    def __init__(self, root=""):
        self.root = root.strip("/")
        self.files: dict[str, bytes] = {}
        self.mtimes: dict[str, str] = {}
        self.counter = 0

    def _abs(self, rel):
        rel = (rel or "").strip("/")
        return f"{self.root}/{rel}" if self.root and rel else (rel or "/")

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def _full(self, path):
        root = self.root
        return f"{root}/{path}" if root and path else (path or "/")

    def _rel(self, full):
        full = (full or "").lstrip("/")
        return full[len(self.root) + 1:] if self.root and full.startswith(self.root + "/") else full

    async def walk(self, folder=""):
        prefix = self._abs(folder)
        prefix = "" if prefix == "/" else prefix
        out = []
        for full, data in self.files.items():
            if prefix and not full.startswith(prefix + "/"):
                continue
            out.append(
                RemoteFile(
                    path=self._rel(full),
                    size=len(data),
                    modified=self.mtimes.get(full, self._now()),
                    id=full,
                    mime="text/plain",
                    checksum=hashlib.md5(data).hexdigest(),
                )
            )
        return out

    async def list(self, folder=""):
        return await self.walk(folder)

    async def upload(self, rel, data):
        full = self._full(rel)
        self.files[full] = bytes(data)
        self.mtimes[full] = self._now()
        self.counter += 1
        return RemoteFile(
            path=rel, size=len(data), modified=self.mtimes[full], id=full,
            mime="text/plain", checksum=hashlib.md5(bytes(data)).hexdigest(),
        )

    async def update(self, file_id, rel, data):
        return await self.upload(rel, data)

    async def download(self, rel):
        full = self._full(rel)
        if full not in self.files:
            raise FileNotFoundError(rel)
        return self.files[full]

    async def delete(self, rel):
        full = self._full(rel)
        self.files.pop(full, None)
        self.mtimes.pop(full, None)

    async def mkdir(self, rel):
        pass

    async def exists(self, rel):
        full = self._full(rel)
        if full in self.files:
            return RemoteFile(path=rel, size=len(self.files[full]), id=full, mime="text/plain")
        return None


async def make_user(uid_tg: int):
    async with session_scope() as s:
        u = await upsert_tg_user(s, tg_id=uid_tg, username="u", full_name="U")
        return u.id


async def attach(uid, provider):
    async with session_scope() as s:
        acc = OAuthAccount(user_id=uid, provider=provider, provider_user_id="x1", login="tester")
        s.add(acc)
        await s.flush()
        s.add(OAuthToken(account_id=acc.id, access_token=encrypt("mock-token"), refresh_token=""))
        await s.flush()


async def install_mock(provider, root):
    holder = FakeCloud(root)

    async def _create(session, user_id, prov, cloud_root=""):
        assert prov == provider
        return holder

    from app.sync import engine

    engine.create_cloud_storage = _create
    return holder


async def main():
    from app.sync import engine

    sync_user = engine.sync_user

    print("\n=== 1. Push: локально -> облако ===")
    await init_db()
    uid = await make_user(101)
    async with session_scope() as s:
        await us.set_value(s, uid, "default_provider", "google")
        await us.set_value(s, uid, "sync_folder", "Trambot")
    await attach(uid, "google")

    from app.storage.factory import local_storage

    local = local_storage(uid)
    await local.upload("a.txt", b"alpha")
    await local.upload("docs/b.txt", b"bravo")
    await local.upload("docs/c.bin", b"charlie-data")

    cloud = await install_mock("google", "Trambot")
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="push")
    check("push успешен", r.status == "ok", r.message)
    check("3 файла загружено", len(r.uploaded) == 3, r.uploaded)
    check("в облаке 3 файла", len(cloud.files) == 3, list(cloud.files))
    check("пути с префиксом sync_folder", "Trambot/docs/b.txt" in cloud.files, list(cloud.files))

    print("\n=== 2. Повторный push: ничего не меняется ===")
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="push")
    check("повторов нет", len(r.uploaded) == 0, r.uploaded)
    check("всё пропущено", r.skipped == 3, r.skipped)

    print("\n=== 3. Pull: облако -> локально ===")
    await local.delete("docs/b.txt")
    cloud.files["Trambot/docs/d.txt"] = b"delta"
    cloud.mtimes["Trambot/docs/d.txt"] = datetime.now(timezone.utc).isoformat()
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="pull")
    check("pull успешен", r.status == "ok", r.message)
    check("2 файла скачано", sorted(r.downloaded) == ["docs/b.txt", "docs/d.txt"], r.downloaded)
    check("b.txt восстановлен", await local.download("docs/b.txt") == b"bravo")
    check("d.txt скачан", await local.download("docs/d.txt") == b"delta")

    print("\n=== 4. Изменение файла в облаке (remote newer) ===")
    cloud.files["Trambot/a.txt"] = b"alpha-updated"
    cloud.mtimes["Trambot/a.txt"] = datetime.now(timezone.utc).isoformat()
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="pull")
    check("файл обновлён", "a.txt" in r.downloaded, r.downloaded)
    check("новое содержимое", await local.download("a.txt") == b"alpha-updated")

    print("\n=== 5. Изменение файла локально (local newer) ===")
    await local.upload("docs/c.bin", b"charlie-LOCAL-EDIT")
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="push")
    check("изменение ушло в облако", "docs/c.bin" in r.uploaded, r.uploaded)
    check("в облаке новое", cloud.files["Trambot/docs/c.bin"] == b"charlie-LOCAL-EDIT")

    print("\n=== 6. Удаление: по умолчанию не распространяется ===")
    cloud.files.pop("Trambot/docs/d.txt", None)
    await local.delete("a.txt")
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="both")
    check("удаление не тронуло облако", "Trambot/a.txt" in cloud.files, list(cloud.files))
    check("удаление не попало в результат", "a.txt" not in r.deleted, r.deleted)
    check("файл восстановлен из облака", await local.download("a.txt") == b"alpha-updated")
    check("в отчёте есть скачивание", "a.txt" in r.downloaded, r.downloaded)

    print("\n=== 7. Удаление с propagate ===")
    async with session_scope() as s:
        await us.set_value(s, uid, "sync_delete_propagate", True)
    await local.delete("a.txt")
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="both")
    check("удалено из облака", "Trambot/a.txt" not in cloud.files, list(cloud.files))
    check("a.txt в deleted", "a.txt" in r.deleted, r.deleted)

    print("\n=== 8. Конфликт: обе стороны изменились ===")
    await local.upload("conf.txt", b"local-version")
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="push")
    check("первый пуш", "conf.txt" in r.uploaded, r.uploaded)
    cloud.files["Trambot/conf.txt"] = b"cloud-version"
    cloud.mtimes["Trambot/conf.txt"] = datetime.now(timezone.utc).isoformat()
    await local.write("conf.txt", b"local-edited-later")
    import os as _os

    future = _os.path.getmtime(local.abspath("conf.txt")) + 10
    _os.utime(local.abspath("conf.txt"), (future, future))
    async with session_scope() as s:
        await us.set_value(s, uid, "sync_conflicts", "both")
        r = await sync_user(s, uid, direction="both")
    check("конфликт разрешён", r.status == "ok", r.message)
    check("локальная версия сохранена", await local.download("conf.txt") == b"local-edited-later")
    check("появилась копия из облака", any("(cloud" in p for p in r.downloaded), r.downloaded)

    print("\n=== 9. Политика local / remote ===")
    async with session_scope() as s:
        await us.set_value(s, uid, "sync_conflicts", "local")
    cloud.files["Trambot/conf.txt"] = b"cloud-wins-test"
    cloud.mtimes["Trambot/conf.txt"] = datetime.now(timezone.utc).isoformat()
    _os.utime(local.abspath("conf.txt"), (future + 50, future + 50))
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="both")
    check("локальная версия не перезаписана", await local.download("conf.txt") == b"local-edited-later")

    print("\n=== 10. Лимит размера при синхронизации ===")
    async with session_scope() as s:
        await us.set_value(s, uid, "sync_max_file_mb", 1)
    await local.upload("small.txt", b"x" * 100)
    big = b"y" * (2 * 1024 * 1024)
    await local.upload("big.bin", big)
    async with session_scope() as s:
        r = await sync_user(s, uid, direction="push")
    check("мелкий файл загружен", "small.txt" in r.uploaded, r.uploaded)
    check("большой файл пропущен", "big.bin" not in r.uploaded, r.uploaded)
    check("большой не попал в облако", "Trambot/big.bin" not in cloud.files, list(cloud.files))

    print("\n=== 11. Яндекс Диск ===")
    uid2 = await make_user(202)
    async with session_scope() as s:
        await us.set_value(s, uid2, "default_provider", "yandex")
        await us.set_value(s, uid2, "sync_folder", "")
        await us.set_value(s, uid2, "sync_max_file_mb", 0)
    await attach(uid2, "yandex")
    local2 = local_storage(uid2)
    await local2.upload("ydoc.txt", b"yandex-content")
    await local2.upload("sub/deep.txt", b"deep-content")
    ycloud = await install_mock("yandex", "")
    async with session_scope() as s:
        r = await sync_user(s, uid2, direction="both")
    check("яндекс: push работает", len(r.uploaded) == 2, r.uploaded)
    check("яндекс: пути без префикса", "ydoc.txt" in ycloud.files, list(ycloud.files))
    ycloud.files["from-cloud.txt"] = b"pulled"
    ycloud.mtimes["from-cloud.txt"] = datetime.now(timezone.utc).isoformat()
    async with session_scope() as s:
        r = await sync_user(s, uid2, direction="both")
    check("яндекс: pull работает", "from-cloud.txt" in r.downloaded, r.downloaded)
    check("яндекс: файл на диске", await local2.download("from-cloud.txt") == b"pulled")

    print("\n=== 12. Защита от параллельного запуска ===")
    from app.sync.engine import LOCKS

    LOCKS[f"{uid2}:yandex"] = True
    async with session_scope() as s:
        r = await sync_user(s, uid2, "yandex")
    check("второй запуск отклонён", r.message == "already_running", r.message)
    LOCKS.clear()

    print("\n=== 13. Санитизация имён ===")
    from app.storage.base import safe_path

    check("путь-обход срезан", ".." not in safe_path("a/../../b"), safe_path("a/../../b"))
    check("спецсимволы заменены", "?" not in safe_path('bad?name.txt'), safe_path('bad?name.txt'))
    check("вложенный путь сохранён", safe_path("docs/2024/report.pdf") == "docs/2024/report.pdf")
    check("CON не проходит как есть", safe_path("CON.txt").startswith("_"), safe_path("CON.txt"))

    print(f"\n{'='*46}\nИТОГО: {ok} успешно, {fail} провалено\n{'='*46}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
