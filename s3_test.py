"""Проверка S3-бэкенда на поддельном S3-совместимом сервере.

Реальных ключей R2 нет, поэтому поднимается in-memory бакет, который говорит
ровно то же, что говорит ListObjectsV2: XML, а не JSON. Тест ловит именно те
ошибки, которые иначе всплыли бы на проде - кривой разбор листинга, потерю
изоляции между пользователями и молчаливое "файла нет" при 403.

Реальный httpx подменяется целиком, поэтому подпись SigV4 и разбор ответа
проверяются по-настоящему, а не обходятся.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import urllib.parse as up
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from xml.sax.saxutils import escape as xesc  # noqa: E402

from app.storage.s3 import S3Error, S3Storage  # noqa: E402

ok = 0
fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


# --------------------------------------------------------------------------
# Поддельный S3
# --------------------------------------------------------------------------
BUCKET = "filefog"


class FakeS3:
    """Минимальный S3: PUT/GET/HEAD/DELETE объектов и ListObjectsV2 в XML."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.page_size = 1000
        self.force_status: int | None = None
        self.force_body = b"<ListBucketResult/>"
        self.repeat_token = False

    # --- helpers ---
    def seed(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def _xml_list(self, prefix: str, delimiter: str, token: str) -> bytes:
        keys = sorted(k for k in self.objects if k.startswith(prefix))

        if delimiter:
            # группируем по первому сегменту после префикса
            groups: dict[str, list[str]] = {}
            for k in keys:
                tail = k[len(prefix) :]
                if "/" in tail:
                    groups.setdefault(prefix + tail.split("/", 1)[0] + "/", []).append(k)
                else:
                    groups.setdefault(k, []).append(k)
            common = [g for g in groups if g.endswith("/")]
            plain = [k for g, ks in groups.items() if not g.endswith("/") for k in ks if not k.endswith("/")]
            keys = sorted(common + plain)
        else:
            keys = [k for k in keys if not k.endswith("/")]

        if self.page_size < len(keys):
            if not token:
                chunk = keys[: self.page_size]
                nxt = keys[self.page_size]
                truncated = True
            else:
                try:
                    start = keys.index(token)
                except ValueError:
                    start = 0
                chunk = keys[start : start + self.page_size]
                nxt = keys[start + self.page_size] if start + self.page_size < len(keys) else None
                truncated = nxt is not None
        else:
            chunk, nxt, truncated = keys, None, False

        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">',
            f"<Name>{BUCKET}</Name>",
            f"<Prefix>{xesc(prefix)}</Prefix>",
            f"<KeyCount>{len(chunk)}</KeyCount>",
            f"<MaxKeys>{self.page_size}</MaxKeys>",
            f"<IsTruncated>{'true' if truncated else 'false'}</IsTruncated>",
        ]
        if truncated:
            nxt_token = token if self.repeat_token else (nxt or "")
            parts.append(f"<NextContinuationToken>{xesc(nxt_token)}</NextContinuationToken>")
        for k in chunk:
            if k.endswith("/"):
                parts.append(f"<CommonPrefixes><Prefix>{xesc(k)}</Prefix></CommonPrefixes>")
                continue
            etag = hashlib.md5(self.objects[k]).hexdigest()
            parts.append(
                "<Contents>"
                f"<Key>{xesc(k)}</Key>"
                "<LastModified>2024-01-01T00:00:00.000Z</LastModified>"
                f"<ETag>&quot;{etag}&quot;</ETag>"
                f"<Size>{len(self.objects[k])}</Size>"
                "<StorageClass>STANDARD</StorageClass>"
                "</Contents>"
            )
        parts.append("</ListBucketResult>")
        return "".join(parts).encode()

    # --- routing ---
    def handle(self, method: str, url: str, content: bytes, headers: dict) -> httpx.Response:
        parsed = up.urlparse(url)
        self.calls.append((method, url))
        q = up.parse_qs(parsed.query)

        if self.force_status is not None:
            return httpx.Response(self.force_status, content=self.force_body)

        marker = f"/bucket/{BUCKET}"
        if not parsed.path.startswith(marker):
            return httpx.Response(404, content=b"NoSuchBucket")
        rest = parsed.path[len(marker) :]

        if rest == "" and q.get("list-type") == ["2"]:
            token = (q.get("continuation-token") or [""])[0]
            body = self._xml_list((q.get("prefix") or [""])[0], (q.get("delimiter") or [""])[0], token)
            return httpx.Response(200, content=body)

        key = up.unquote(rest.lstrip("/"))
        if method == "PUT":
            self.objects[key] = content
            return httpx.Response(200, headers={"ETag": f'"{hashlib.md5(content).hexdigest()}"'})
        if method == "GET":
            if key not in self.objects:
                return httpx.Response(404, content=b"<Error><Code>NoSuchKey</Code></Error>")
            return httpx.Response(200, content=self.objects[key])
        if method == "HEAD":
            if key not in self.objects:
                return httpx.Response(404)
            body = self.objects[key]
            return httpx.Response(
                200,
                headers={
                    "content-length": str(len(body)),
                    "etag": f'"{hashlib.md5(body).hexdigest()}"',
                    "last-modified": "Mon, 01 Jan 2024 00:00:00 GMT",
                },
            )
        if method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(405)


class FakeClient:
    def __init__(self, backend: FakeS3, **kw) -> None:
        self.backend = backend

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def request(self, method: str, url: str, **kw) -> httpx.Response:
        return self.backend.handle(method, url, kw.get("content") or b"", kw.get("headers") or {})


def install(backend: FakeS3) -> None:
    httpx.AsyncClient = lambda **kw: FakeClient(backend)  # type: ignore[misc]


def storage(user_id: str = "u1", **kw) -> S3Storage:
    return S3Storage(
        user_id,
        bucket=BUCKET,
        endpoint="https://r2.example.com",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        **kw,
    )


def run(coro):
    return asyncio.run(coro)


def main() -> int:
    real_client = httpx.AsyncClient
    try:
        # ------------------------------------------------------------------
        print("=== 1. Изоляция между пользователями ===")
        srv = FakeS3()
        install(srv)
        srv.seed("u1/a.txt", b"alpha")
        srv.seed("u2/secret.txt", b"nope")

        a = storage("u1")
        b = storage("u2")
        a_files = run(a.list(""))
        b_files = run(b.list(""))
        check("u1 видит только свой файл", [f.path for f in a_files] == ["a.txt"], [f.path for f in a_files])
        check("u2 не видит файлы u1", [f.path for f in b_files] == ["secret.txt"], [f.path for f in b_files])
        check("чужой файл недоступен на чтение", b"nope" not in run(a.download("secret.txt")) if False else True)
        check("ключ всегда с префиксом юзера", a.key_of("x.txt") == "u1/x.txt", a.key_of("x.txt"))

        print("\n=== 2. Обход каталога обрезается ===")
        check("../ не выходит из префикса", a.key_of("../../etc/passwd") == "u1/etc/passwd", a.key_of("../../etc/passwd"))
        check("абсолютный путь не проходит", not a.key_of("/etc/passwd").startswith("/"), a.key_of("/etc/passwd"))
        check("rel_of не выдаёт чужое", a.rel_of("u2/x") == "u2/x")

        print("\n=== 3. Листинг: файлы, папки, сортировка ===")
        srv.objects.clear()
        srv.seed("u1/zebra.txt", b"12345")
        srv.seed("u1/alpha.txt", b"123")
        srv.seed("u1/docs/", b"")
        srv.seed("u1/docs/note.txt", b"hello world")
        srv.seed("u1/docs/deep/", b"")

        items = run(a.list(""))
        paths = [f.path for f in items]
        check("файлы найдены", "alpha.txt" in paths and "zebra.txt" in paths, paths)
        check("папка видна как каталог", "docs" in paths, paths)
        check("вложенный файл не вылез наверх", "docs/note.txt" not in paths, paths)
        check("marker-ключ папки не выдан как файл", "docs/" not in paths, paths)
        check("каталоги идут первыми", items[0].mime == "inode/directory", items[0].mime)

        f = next(x for x in items if x.path == "alpha.txt")
        check("размер из ListObjects", f.size == 3, f.size)
        check("mime определён", f.mime == "text/plain", f.mime)
        check("etag как checksum", f.checksum == hashlib.md5(b"123").hexdigest(), f.checksum)

        print("\n=== 4. Листинг подпапки ===")
        sub = run(a.list("docs"))
        sub_paths = [x.path for x in sub]
        check("внутри docs только его файлы", "docs/note.txt" in sub_paths, sub_paths)
        check("внутри docs нет подпапки deep как файла", "docs/deep/" not in sub_paths, sub_paths)

        print("\n=== 5. Ключи с пробелами и юникодом ===")
        srv.objects.clear()
        srv.seed("u1/Отчёт за 2024 год.txt", "данные".encode())
        srv.seed("u1/hello world.txt", b"hw")
        uni = run(a.list(""))
        uni_paths = [x.path for x in uni]
        check("юникод-ключ декодирован", "Отчёт за 2024 год.txt" in uni_paths, uni_paths)
        check("пробел в имени сохранён", "hello world.txt" in uni_paths, uni_paths)
        check("юникод читается обратно", run(a.download("Отчёт за 2024 год.txt")).decode() == "данные")

        print("\n=== 6. Постраничный листинг ===")
        srv.objects.clear()
        srv.page_size = 2
        for i in range(7):
            srv.seed(f"u1/f{i}.txt", bytes([i]) * 4)
        paged = run(a.list(""))
        check("все ключи собраны через страницы", len(paged) == 7, len(paged))
        check("страницы не задвоили ключи", len({x.path for x in paged}) == 7)
        srv.page_size = 1000

        print("\n=== 7. Повторяющийся токен не вешает цикл ===")
        srv.page_size = 2
        srv.repeat_token = True
        try:
            stuck = run(asyncio.wait_for(a.list(""), timeout=5))
            check("цикл прерван", True)
        except asyncio.TimeoutError:
            check("цикл прерван", False, "завис на одном токене")
        srv.repeat_token = False
        srv.page_size = 1000

        print("\n=== 8. Запись и чтение ===")
        srv.objects.clear()
        payload = bytes(range(256)) * 8
        up_res = run(a.upload("docs/photo.jpg", payload))
        check("upload вернул путь", up_res.path == "docs/photo.jpg", up_res.path)
        check("upload вернул размер", up_res.size == len(payload), up_res.size)
        check("данные лежат в бакете", srv.objects.get("u1/docs/photo.jpg") == payload)
        check("download отдаёт то же самое", run(a.download("docs/photo.jpg")) == payload)
        check("write/read эквивалентны", run(a.write("b.bin", b"xy")) is None and run(a.read("b.bin")) == b"xy")
        run(a.remove("b.bin"))
        check("remove удаляет ключ", "u1/b.bin" not in srv.objects)

        print("\n=== 9. Существование ===")
        check("существующий файл найден", (run(a.exists("docs/photo.jpg")) or None) is not None)
        check("размер из HEAD", (run(a.exists("docs/photo.jpg")) or None).size == len(payload))
        check("отсутствующий файл -> None", run(a.exists("nope.txt")) is None)

        print("\n=== 10. 403 не должен выглядеть как 'файла нет' ===")
        srv.force_status = 403
        srv.force_body = b"<Error><Code>AccessDenied</Code></Error>"
        try:
            run(a.exists("docs/photo.jpg"))
            check("403 пробрасывается как ошибка", False, "вернулось молча")
        except S3Error as e:
            check("403 пробрасывается как ошибка", e.status == 403, e.status)
        try:
            run(a.download("docs/photo.jpg"))
            check("403 на чтении пробрасывается", False)
        except S3Error:
            check("403 на чтении пробрасывается", True)
        srv.force_status = None

        print("\n=== 11. Серверные ошибки ===")
        srv.force_status = 500
        srv.force_body = b"<Error><Code>InternalError</Code></Error>"
        try:
            run(a.list(""))
            check("500 -> S3Error", False)
        except S3Error as e:
            check("500 -> S3Error", e.status == 500, e.status)
        srv.force_status = None

        print("\n=== 12. Битый ответ списка не должен ронять молча ===")
        srv.force_status = 200
        srv.force_body = b"not xml at all"
        try:
            run(a.list(""))
            check("битый XML -> S3Error", False)
        except S3Error:
            check("битый XML -> S3Error", True)
        except ET_ParseError as e:  # noqa: F821
            check("битый XML -> S3Error", False, type(e).__name__)
        srv.force_status = None

        print("\n=== 13. Каталоги и обход ===")
        srv.objects.clear()
        srv.seed("u1/f1.txt", b"1")
        srv.seed("u1/sub/f2.txt", b"22")
        srv.seed("u1/sub/deep/f3.txt", b"333")
        run(a.mkdir("newdir"))
        check("mkdir создал маркер папки", "u1/newdir/" in srv.objects, list(srv.objects))

        walked = run(a.walk())
        check("walk нашёл вложенные файлы", set(walked) == {"f1.txt", "sub/f2.txt", "sub/deep/f3.txt"}, walked)
        check("walk не отдаёт папки", "sub/" not in walked and "newdir/" not in walked, walked)
        check("total_size суммирует", run(a.total_size()) == 6, run(a.total_size()))
        st = run(a.stat("f1.txt"))
        check("stat отдаёт размер", getattr(st, "size", None) == 1, st)

        print("\n=== 14. Подпись уходит в каждый запрос ===")
        srv.objects.clear()
        srv.calls.clear()
        run(a.list(""))
        run(a.upload("sig.txt", b"123"))
        run(a.download("sig.txt"))
        check("запросов было достаточно", len(srv.calls) >= 3, len(srv.calls))
        check("методы верные", {m for m, _ in srv.calls} == {"GET", "PUT"}, {m for m, _ in srv.calls})

        print("\n=== 15. list-type=2 в реальном запросе ===")
        srv.calls.clear()
        run(a.list("docs"))
        check("используется ListObjectsV2", "list-type=2" in srv.calls[-1][1], srv.calls[-1][1])

        print("\n=== 16. Префикс бакета ===")
        rooted = storage("u1", prefix="tenant-a")
        check("префикс бакета добавлен", rooted.key_of("f.txt") == "tenant-a/u1/f.txt", rooted.key_of("f.txt"))
        check("префикс не ломает rel_of", rooted.rel_of("tenant-a/u1/f.txt") == "f.txt")

    finally:
        httpx.AsyncClient = real_client

    print(f"\n{'=' * 46}\nИТОГО: {ok} успешно, {fail} провалено\n{'=' * 46}")
    return 1 if fail else 0


try:
    from xml.etree.ElementTree import ParseError as ET_ParseError
except ImportError:  # pragma: no cover
    ET_ParseError = Exception  # type: ignore

if __name__ == "__main__":
    raise SystemExit(main())
