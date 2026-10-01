"""Яндекс Диск (REST API cloud-api.yandex.net/v1/disk)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx

from app.storage.base import RemoteFile, guess_mime, join_path, safe_path

API = "https://cloud-api.yandex.net/v1/disk"


class YandexDiskStorage:
    name = "yandex"

    def __init__(self, access_token: str, root: str = "") -> None:
        self.token = access_token
        self.root = (root or "").strip("/")

    # --- низкий уровень ---
    def _h(self) -> dict[str, str]:
        return {"Authorization": f"OAuth {self.token}", "Accept": "application/json"}

    def _abs(self, rel: str) -> str:
        return join_path(self.root, rel)

    @staticmethod
    def _time(value: str) -> str:
        if not value:
            return ""
        try:
            return (
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                .astimezone(timezone.utc)
                .isoformat()
            )
        except ValueError:
            return value

    async def _resolve(self, rel: str, create: bool = False) -> str:
        parts = [p for p in self._abs(rel).split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            found = await self._stat(current)
            if found is None:
                if not create:
                    return ""
                await self._mkdir(current)
        return current

    async def _stat(self, path: str) -> dict | None:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(
                f"{API}/resources",
                params={"path": path or "/", "limit": 1, "fields": "type,name,path,size,modified,_embedded"},
                headers=self._h(),
            )
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            data = (data.get("_embedded") or {}).get("items", [None])[0]
        if not data:
            return None
        return data

    async def _mkdir(self, path: str) -> None:
        async with httpx.AsyncClient(timeout=60) as c:
            await c.put(f"{API}/resources", params={"path": path, "mkdir": "true"}, headers=self._h())

    async def _children(self, path: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(
                f"{API}/resources",
                params={
                    "path": path or "/",
                    "limit": 1000,
                    "fields": "type,name,path,size,modified,_embedded.items",
                },
                headers=self._h(),
            )
        if r.status_code >= 400:
            r.raise_for_status()
        data = r.json()
        return (data.get("_embedded") or {}).get("items", [])

    # --- StorageBackend ---
    async def list(self, folder: str = "") -> list[RemoteFile]:
        abs_path = self._abs(folder)
        info = await self._stat(abs_path) if abs_path else {"type": "dir", "name": "/", "path": "/"}
        if not info or info.get("type") != "dir":
            return []
        items: list[RemoteFile] = []
        for f in await self._children(abs_path):
            full = f.get("path", "").lstrip("/")
            is_dir = f.get("type") == "dir"
            items.append(
                RemoteFile(
                    path=full,
                    size=0 if is_dir else int(f.get("size") or 0),
                    modified=self._time(f.get("modified", "")),
                    id=f.get("name", ""),
                    mime="inode/directory" if is_dir else guess_mime(f.get("name", "")),
                    checksum="",
                )
            )
        items.sort(key=lambda x: (x.mime != "inode/directory", x.path.lower()))
        return items

    async def walk(self, folder: str = "") -> list[RemoteFile]:
        out: list[RemoteFile] = []
        stack = [self._abs(folder)]
        while stack:
            cur = stack.pop()
            try:
                children = await self._children(cur)
            except httpx.HTTPError:
                continue
            for f in children:
                full = (f.get("path") or "").lstrip("/")
                if f.get("type") == "dir":
                    stack.append(full)
                else:
                    out.append(
                        RemoteFile(
                            path=full,
                            size=int(f.get("size") or 0),
                            modified=self._time(f.get("modified", "")),
                            id=f.get("name", ""),
                            mime=guess_mime(f.get("name", "")),
                        )
                    )
        return out

    async def upload(self, rel: str, data: bytes) -> RemoteFile:
        rel = safe_path(rel)
        full = self._abs(rel)
        existing = await self._stat(full)
        # 1) пробуем загрузить в папку напрямую
        href = await self._upload_href(full.rsplit("/", 1)[0] if "/" in full else "/")
        if href and not existing:
            ok = await self._put(href, data)
            if ok:
                return await self._after(full, rel, data)

        # 2) для больших файлов / когда нужен явный ref
        if existing:
            await self._delete_path(full)
        href = await self._upload_href("__trambot_tmp__")
        if not href:
            raise RuntimeError("cannot_get_upload_href")
        ok = await self._put(href, data)
        if not ok:
            raise RuntimeError("upload_put_failed")
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{API}/resources/upload",
                params={"path": full, "overwrite": "true"},
                headers=self._h(),
                json={"href": href.rstrip("/") + "?r=preserve"},
            )
        if r.status_code >= 400:
            await self._delete_path(full, silent=True)
            r.raise_for_status()
        return await self._after(full, rel, data)

    async def _after(self, full: str, rel: str, data: bytes) -> RemoteFile:
        info = await self._stat(full) or {}
        return RemoteFile(
            path=rel,
            size=int(info.get("size") or len(data)),
            modified=self._time(info.get("modified", "")),
            id=info.get("name", ""),
            mime=guess_mime(rel),
            checksum=hashlib.md5(data).hexdigest(),
        )

    async def _upload_href(self, path: str) -> str:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(f"{API}/resources/upload", params={"path": path}, headers=self._h())
        if r.status_code >= 400:
            return ""
        return r.json().get("href", "")

    async def _put(self, href: str, data: bytes) -> bool:
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.put(href, content=data, headers={"Content-Type": "application/octet-stream"})
            return r.status_code < 300
        except httpx.HTTPError:
            return False

    async def _delete_path(self, path: str, silent: bool = False) -> None:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.delete(
                f"{API}/resources", params={"path": path, "permanent": "true"}, headers=self._h()
            )
        if r.status_code >= 400 and not silent:
            r.raise_for_status()

    async def download(self, rel: str) -> bytes:
        full = self._abs(rel)
        info = await self._stat(full)
        if not info:
            raise FileNotFoundError(rel)
        if info.get("type") == "dir":
            raise IsADirectoryError(rel)
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(f"{API}/resources/download", params={"path": full}, headers=self._h())
        if r.status_code >= 400:
            r.raise_for_status()
        href = r.json().get("href") or r.json().get("data", {}).get("href", "")
        if not href:
            raise RuntimeError("no_download_href")
        async with httpx.AsyncClient(timeout=300) as c:
            d = await c.get(href)
        d.raise_for_status()
        return d.content

    async def delete(self, rel: str) -> None:
        full = self._abs(rel)
        if await self._stat(full):
            await self._delete_path(full)

    async def mkdir(self, rel: str) -> None:
        await self._resolve(rel, create=True)

    async def exists(self, rel: str) -> RemoteFile | None:
        full = self._abs(rel)
        info = await self._stat(full)
        if not info:
            return None
        name = info.get("name", "")
        if info.get("type") == "dir":
            return RemoteFile(path=safe_path(rel), size=0, id=name, mime="inode/directory")
        return RemoteFile(
            path=safe_path(rel),
            size=int(info.get("size") or 0),
            modified=self._time(info.get("modified", "")),
            id=name,
            mime=guess_mime(rel),
        )

