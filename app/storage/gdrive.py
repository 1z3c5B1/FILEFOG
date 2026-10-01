"""Google Drive (files.create/files.list v3 + download через alt=media)."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import httpx

from app.storage.base import RemoteFile, guess_mime, join_path, safe_path

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"


class GoogleDriveStorage:
    name = "google"

    def __init__(self, access_token: str, root: str = "") -> None:
        self.token = access_token
        self.root = (root or "").strip("/")

    # --- низкий уровень ---
    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

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

    def _abs(self, rel: str) -> str:
        return join_path(self.root, rel)

    async def _resolve(self, rel: str, create: bool = False) -> tuple[str, str]:
        """Возвращает (id, mime) узла по относительному пути."""
        parts = [p for p in self._abs(rel).split("/") if p]
        parent_id = "root"
        node_id, node_mime = "root", FOLDER_MIME
        for i, part in enumerate(parts):
            found = await self._child(parent_id, part)
            if found is None:
                if not create:
                    return "", ""
                is_last = i == len(parts) - 1
                node_id = await self._create_folder(part, parent_id)
                node_mime = FOLDER_MIME
            else:
                node_id, node_mime = found
            parent_id = node_id
        return node_id, node_mime

    async def _child(self, parent_id: str, name: str) -> tuple[str, str] | None:
        esc = name.replace("'", r"\'")
        q = f"name = '{esc}' and '{parent_id}' in parents and trashed = false"
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(
                f"{API}/files",
                params={
                    "q": q,
                    "fields": "files(id,name,mimeType,size,modifiedTime,md5Checksum)",
                    "spaces": "drive",
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                    "pageSize": 1,
                },
                headers=self._h(),
            )
        r.raise_for_status()
        files = r.json().get("files") or []
        if not files:
            return None
        f = files[0]
        return f["id"], f.get("mimeType", "")

    async def _create_folder(self, name: str, parent_id: str = "root") -> str:
        meta = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id != "root":
            meta["parents"] = [parent_id]
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{API}/files",
                params={"supportsAllDrives": "true", "fields": "id"},
                headers={**self._h(), "Content-Type": "application/json"},
                content=json.dumps(meta),
            )
        r.raise_for_status()
        return r.json()["id"]

    async def _children(self, folder_id: str) -> list[dict]:
        out: list[dict] = []
        token = None
        async with httpx.AsyncClient(timeout=60) as c:
            while True:
                params = {
                    "q": f"'{folder_id}' in parents and trashed = false",
                    "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,md5Checksum)",
                    "pageSize": 200,
                    "spaces": "drive",
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                }
                if token:
                    params["pageToken"] = token
                r = await c.get(f"{API}/files", params=params, headers=self._h())
                r.raise_for_status()
                data = r.json()
                out.extend(data.get("files") or [])
                token = data.get("nextPageToken")
                if not token:
                    break
        return out

    # --- StorageBackend ---
    async def list(self, folder: str = "") -> list[RemoteFile]:
        fid, _ = await self._resolve(folder)
        if not fid:
            return []
        base = safe_path(self._abs(folder))
        items: list[RemoteFile] = []
        for f in await self._children(fid):
            rel = f"{base}/{f['name']}".strip("/") if base else f["name"]
            is_dir = f.get("mimeType") == FOLDER_MIME
            items.append(
                RemoteFile(
                    path=rel,
                    size=0 if is_dir else int(f.get("size") or 0),
                    modified=self._time(f.get("modifiedTime", "")),
                    id=f["id"],
                    mime="inode/directory" if is_dir else (f.get("mimeType") or guess_mime(f["name"])),
                    checksum=f.get("md5Checksum") or "",
                )
            )
        items.sort(key=lambda x: (x.mime != "inode/directory", x.path.lower()))
        return items

    async def walk(self, folder: str = "") -> list[RemoteFile]:
        """Рекурсивный обход — используется синхронизацией."""
        out: list[RemoteFile] = []
        stack = [folder]
        while stack:
            cur = stack.pop()
            for item in await self.list(cur):
                if item.mime == "inode/directory":
                    stack.append(item.path)
                else:
                    out.append(item)
        return out

    async def upload(self, rel: str, data: bytes) -> RemoteFile:
        name = safe_path(rel)
        parent = name.rsplit("/", 1)[0] if "/" in name else ""
        parent_id, _ = await self._resolve(parent, create=True)
        meta = {"name": name.rsplit("/", 1)[-1]}
        if parent_id != "root":
            meta["parents"] = [parent_id]
        boundary = "trambotboundary"
        body = self._multipart(boundary, meta, data)
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(
                f"{UPLOAD}/files",
                params={"uploadType": "multipart", "fields": "id,name,size,modifiedTime,md5Checksum,mimeType", "supportsAllDrives": "true"},
                headers={**self._h(), "Content-Type": f"multipart/related; boundary={boundary}"},
                content=body,
            )
        r.raise_for_status()
        d = r.json()
        return RemoteFile(
            path=name,
            size=int(d.get("size") or len(data)),
            modified=self._time(d.get("modifiedTime", "")),
            id=d["id"],
            mime=d.get("mimeType") or guess_mime(name),
            checksum=d.get("md5Checksum") or "",
        )

    @staticmethod
    def _multipart(boundary: str, meta: dict, data: bytes) -> bytes:
        buf = io.BytesIO()
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(b"Content-Type: application/json; charset=UTF-8\r\n\r\n")
        buf.write(json.dumps(meta).encode())
        buf.write(f"\r\n--{boundary}\r\n".encode())
        buf.write(b"Content-Type: application/octet-stream\r\n\r\n")
        buf.write(data)
        buf.write(f"\r\n--{boundary}--".encode())
        return buf.getvalue()

    async def update(self, file_id: str, rel: str, data: bytes) -> RemoteFile:
        name = safe_path(rel)
        meta = {"name": name.rsplit("/", 1)[-1]}
        boundary = "trambotupdate"
        body = self._multipart(boundary, meta, data)
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.patch(
                f"{UPLOAD}/files/{file_id}",
                params={"uploadType": "multipart", "fields": "id,name,size,modifiedTime,md5Checksum,mimeType", "supportsAllDrives": "true"},
                headers={**self._h(), "Content-Type": f"multipart/related; boundary={boundary}"},
                content=body,
            )
        r.raise_for_status()
        d = r.json()
        return RemoteFile(
            path=name,
            size=int(d.get("size") or len(data)),
            modified=self._time(d.get("modifiedTime", "")),
            id=d["id"],
            mime=d.get("mimeType") or guess_mime(name),
            checksum=d.get("md5Checksum") or "",
        )

    async def download(self, rel: str) -> bytes:
        fid, mime = await self._resolve(rel)
        if not fid:
            raise FileNotFoundError(rel)
        if mime == FOLDER_MIME:
            raise IsADirectoryError(rel)
        if mime.startswith("application/vnd.google-apps"):
            raise RuntimeError("google_apps_file_not_downloadable")
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.get(
                f"{API}/files/{fid}",
                params={"alt": "media", "supportsAllDrives": "true"},
                headers=self._h(),
            )
        r.raise_for_status()
        return r.content

    async def delete(self, rel: str) -> None:
        fid, _ = await self._resolve(rel)
        if not fid:
            return
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.delete(f"{API}/files/{fid}", params={"supportsAllDrives": "true"}, headers=self._h())
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    async def mkdir(self, rel: str) -> None:
        await self._resolve(rel, create=True)

    async def exists(self, rel: str) -> RemoteFile | None:
        fid, mime = await self._resolve(rel)
        if not fid:
            return None
        if mime == FOLDER_MIME:
            return RemoteFile(path=safe_path(rel), size=0, id=fid, mime="inode/directory")
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(
                f"{API}/files/{fid}",
                params={"fields": "id,name,size,modifiedTime,md5Checksum,mimeType", "supportsAllDrives": "true"},
                headers=self._h(),
            )
        if r.status_code != 200:
            return None
        d = r.json()
        return RemoteFile(
            path=safe_path(rel),
            size=int(d.get("size") or 0),
            modified=self._time(d.get("modifiedTime", "")),
            id=d["id"],
            mime=d.get("mimeType") or guess_mime(rel),
            checksum=d.get("md5Checksum") or "",
        )
