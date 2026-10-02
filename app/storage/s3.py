"""S3-совместимое хранилище: Cloudflare R2, Backblaze B2, MinIO.

Подпись запросов делается вручную (AWS Signature V4) на httpx, чтобы не
тянуть в проект botocore. Все имена папок и файлов проходят через
safe_path(), поэтому выход за пределы префикса пользователя невозможен.

В S3 нет настоящих папок: каталог - это ключ, который заканчивается на "/".
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import mimetypes
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.storage.base import RemoteFile, safe_path

ALGO = "AWS4-HMAC-SHA256"
DIR_MIME = "inode/directory"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign_key(secret: str, stamp: str, region: str, service: str) -> bytes:
    # в цепочке HMAC участвует только дата (yyyyMMdd), а не полный
    # таймстамп: 20130524T000000Z -> 20130524
    key = f"AWS4{secret}".encode()
    for part in (stamp[:8], region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def canonical_uri(path: str) -> str:
    """Канонический URI: каждый сегмент экранируется, '/' остаётся разделителем."""
    return quote(path, safe="/-_.~")


def signing_key(secret: str, stamp: str, region: str, service: str = "s3") -> bytes:
    return _sign_key(secret, stamp, region, service)


UNSIGNED_HEADERS = {"authorization", "user-agent", "content-length", "connection"}


def build_authorization(
    *,
    method: str,
    url_path: str,
    query: str,
    headers: dict[str, str],
    payload: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    stamp: str,
    service: str = "s3",
) -> str:
    """Собирает заголовок Authorization для подписи SigV4.

    Подписываются все переданные заголовки, кроме UNSIGNED_HEADERS.
    Набор должен совпадать с тем, что реально уходит в сеть: подпись
    проверяется по полному списку SignedHeaders.
    """
    lowered = {k.lower(): v.strip() for k, v in headers.items()}
    signed = sorted(k for k in lowered if k not in UNSIGNED_HEADERS)
    signed_headers = ";".join(signed)
    canonical_headers = "".join(f"{k}:{lowered[k]}\n" for k in signed)

    canonical_request = "\n".join(
        [
            method,
            canonical_uri(url_path),
            query,
            canonical_headers,
            signed_headers,
            _sha256(payload),
        ]
    )
    scope = f"{stamp[:8]}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [ALGO, stamp, scope, _sha256(canonical_request.encode())]
    )
    signing = _sign_key(secret_key, stamp, region, service)
    signature = hmac.new(signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{ALGO} Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"


def guess_mime(name: str) -> str:
    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


class S3Storage:
    """Хранилище пользователя в одном S3-бакете."""

    name = "s3"

    def __init__(
        self,
        user_id: str,
        *,
        bucket: str,
        endpoint: str,
        access_key: str,
        secret_key: str,
        region: str = "auto",
        prefix: str = "",
        service: str = "s3",
        timeout: float = 60.0,
    ) -> None:
        self.bucket = bucket
        self.endpoint = endpoint.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.service = service
        self.timeout = timeout
        base = safe_path(prefix) if prefix else ""
        parts = [p for p in (base, safe_path(user_id)) if p]
        self.prefix = "/".join(parts)

    # --- ключи ---
    def key_of(self, rel: str) -> str:
        rel = safe_path(rel)
        return f"{self.prefix}/{rel}" if rel else self.prefix

    def dir_key_of(self, rel: str) -> str:
        return self.key_of(rel).rstrip("/") + "/"

    def rel_of(self, key: str) -> str:
        if key.startswith(self.prefix + "/"):
            return key[len(self.prefix) + 1 :]
        return key

    # --- транспорт ---
    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        expect_json: bool = False,
    ) -> httpx.Response:
        clean = {k: v for k, v in (query or {}).items() if v is not None}
        qs = urlencode(sorted(clean.items()), quote_via=quote, safe="-_.~")
        url = f"{self.endpoint}/bucket/{self.bucket}{path}" if path else f"{self.endpoint}/bucket/{self.bucket}"

        now = dt.datetime.now(dt.timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        base_headers = {
            "host": url.split("://", 1)[1],
            "x-amz-date": stamp,
            "x-amz-content-sha256": _sha256(body),
        }
        if headers:
            base_headers.update({k.lower(): v for k, v in headers.items()})

        base_headers["authorization"] = build_authorization(
            method=method,
            url_path=f"/bucket/{self.bucket}{path}",
            query=qs,
            headers=base_headers,
            payload=body,
            access_key=self.access_key,
            secret_key=self.secret_key,
            region=self.region,
            stamp=stamp,
            service=self.service,
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.request(method, url + (f"?{qs}" if qs else ""), content=body, headers=base_headers)
        if r.status_code >= 400:
            raise S3Error(r.status_code, r.text[:400])
        if expect_json:
            return r
        return r

    async def _get(self, path: str, **kw) -> httpx.Response:
        return await self._request("GET", path, **kw)

    async def _put(self, path: str, body: bytes = b"", **kw) -> httpx.Response:
        return await self._request("PUT", path, body=body, **kw)

    async def _delete(self, path: str, **kw) -> httpx.Response:
        return await self._request("DELETE", path, **kw)

    async def _list(self, prefix: str, delimiter: str = "") -> list[dict]:
        out: list[dict] = []
        token = ""
        while True:
            q: dict[str, Any] = {
                "prefix": prefix,
                "max-keys": "1000",
                "encoding-type": "url",
            }
            if delimiter:
                q["delimiter"] = delimiter
            if token:
                q["continuation-token"] = token
            import json

            r = await self._get("", query=q)
            data = json.loads(r.text)
            out.extend(data.get("Contents") or [])
            for cp in data.get("CommonPrefixes") or []:
                out.append({"Key": cp.get("Prefix", ""), "Dir": True})
            if not data.get("IsTruncated"):
                break
            token = data.get("NextContinuationToken") or ""
            if not token:
                break
        return out

    # --- StorageBackend ---
    async def list(self, folder: str = "") -> list[RemoteFile]:
        import json
        from urllib.parse import unquote

        prefix = self.dir_key_of(folder) if folder else self.prefix + "/"
        rows = await self._list(prefix, delimiter="/")
        items: list[RemoteFile] = []
        for row in rows:
            key = unquote(row.get("Key", ""))
            if row.get("Dir"):
                rel = self.rel_of(key).rstrip("/")
                items.append(RemoteFile(path=rel, size=0, id=key, mime=DIR_MIME))
                continue
            if key.endswith("/"):
                continue
            name = key.rsplit("/", 1)[-1]
            items.append(
                RemoteFile(
                    path=self.rel_of(key),
                    size=int(row.get("Size", 0)),
                    modified=str(row.get("LastModified", "")),
                    id=key,
                    mime=guess_mime(name),
                    checksum=str(row.get("ETag", "")).strip('"'),
                )
            )
        items.sort(key=lambda f: (f.mime == DIR_MIME, f.path.lower()))
        return items

    async def upload(self, rel: str, data: bytes) -> RemoteFile:
        import json

        rel = safe_path(rel)
        key = self.key_of(rel)
        r = await self._put(f"/{quote(key)}", body=data)
        etag = r.headers.get("etag", "").strip('"')
        when = dt.datetime.now(dt.timezone.utc).isoformat()
        return RemoteFile(
            path=rel,
            size=len(data),
            modified=when,
            id=key,
            mime=guess_mime(rel.rsplit("/", 1)[-1]),
            checksum=etag,
        )

    async def download(self, rel: str) -> bytes:
        r = await self._get(f"/{quote(self.key_of(rel))}")
        return r.content

    async def delete(self, rel: str) -> None:
        rel = safe_path(rel)
        if not rel:
            return
        await self._delete(f"/{quote(self.key_of(rel))}")

    async def mkdir(self, rel: str) -> None:
        rel = safe_path(rel)
        if not rel:
            return
        await self._put(f"/{quote(self.dir_key_of(rel))}", body=b"")

    async def exists(self, rel: str) -> RemoteFile | None:
        key = self.key_of(rel)
        try:
            r = await self._request("HEAD", f"/{quote(key)}")
        except S3Error as e:
            if e.status in (404, 403):
                return None
            raise
        if r.status_code == 404:
            return None
        size = int(r.headers.get("content-length", 0))
        return RemoteFile(
            path=safe_path(rel),
            size=size,
            modified=r.headers.get("last-modified", ""),
            id=key,
            mime=guess_mime(key.rsplit("/", 1)[-1]),
            checksum=r.headers.get("etag", "").strip('"'),
        )

    # --- операции, которые ждали локальный бэкенд ---
    async def walk(self) -> list[str]:
        rows = await self._list(self.prefix + "/")
        import json  # noqa: F401
        from urllib.parse import unquote

        out = []
        for row in rows:
            key = unquote(row.get("Key", ""))
            if key.endswith("/") or row.get("Dir"):
                continue
            out.append(self.rel_of(key))
        return sorted(out)

    async def total_size(self) -> int:
        rows = await self._list(self.prefix + "/")
        return sum(int(r.get("Size", 0)) for r in rows if not r.get("Dir"))

    async def read(self, rel: str) -> bytes:
        return await self.download(rel)

    async def write(self, rel: str, data: bytes) -> None:
        await self.upload(rel, data)

    async def remove(self, rel: str) -> None:
        await self.delete(rel)

    async def stat(self, rel: str):
        info = await self.exists(rel)
        if info is None:
            raise FileNotFoundError(rel)
        return info


class S3Error(RuntimeError):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"S3 {status}: {text}")
        self.status = status
