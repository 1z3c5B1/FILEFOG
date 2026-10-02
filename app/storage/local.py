"""Локальное файловое хранилище (по папке на пользователя)."""
from __future__ import annotations

import hashlib
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.storage.base import RemoteFile, safe_path


def md5_of(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def guess_mime(name: str) -> str:
    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


class LocalStorage:
    name = "local"

    def __init__(self, user_id: str) -> None:
        self.root = settings.storage_path / user_id
        self.root.mkdir(parents=True, exist_ok=True)

    # --- утилиты ---
    def abspath(self, rel: str) -> Path:
        rel = safe_path(rel)
        target = (self.root / rel).resolve() if rel else self.root.resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError("path outside of storage")
        return target

    async def walk(self) -> list[str]:
        out: list[str] = []
        for p in self.root.rglob("*"):
            if p.is_file():
                out.append(p.relative_to(self.root).as_posix())
        return sorted(out)

    async def total_size(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    async def snapshot(self) -> dict[str, tuple[int, float, str]]:
        """rel -> (size, mtime, md5) одним обходом папки."""
        out: dict[str, tuple[int, float, str]] = {}
        for p in self.root.rglob("*"):
            if not p.is_file():
                continue
            st = p.stat()
            out[p.relative_to(self.root).as_posix()] = (st.st_size, st.st_mtime, md5_of_file(p))
        return out

    # --- StorageBackend ---
    async def list(self, folder: str = "") -> list[RemoteFile]:
        base = self.abspath(folder)
        if not base.exists() or not base.is_dir():
            return []
        items: list[RemoteFile] = []
        for p in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            rel = p.relative_to(self.root).as_posix()
            if p.is_dir():
                items.append(RemoteFile(path=rel, size=0, id=p.name, mime="inode/directory"))
            else:
                st = p.stat()
                items.append(
                    RemoteFile(
                        path=rel,
                        size=st.st_size,
                        modified=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                        id=p.name,
                        mime=guess_mime(p.name),
                        checksum=md5_of_file(p),
                    )
                )
        return items

    async def upload(self, rel: str, data: bytes) -> RemoteFile:
        path = self.abspath(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return RemoteFile(
            path=safe_path(rel),
            size=len(data),
            modified=datetime.now(timezone.utc).isoformat(),
            mime=guess_mime(path.name),
            checksum=md5_of(data),
        )

    async def download(self, rel: str) -> bytes:
        path = self.abspath(rel)
        if not path.is_file():
            raise FileNotFoundError(rel)
        return path.read_bytes()

    async def delete(self, rel: str) -> None:
        path = self.abspath(rel)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    async def mkdir(self, rel: str) -> None:
        self.abspath(rel).mkdir(parents=True, exist_ok=True)

    async def exists(self, rel: str) -> RemoteFile | None:
        path = self.abspath(rel)
        if not path.exists():
            return None
        if path.is_dir():
            return RemoteFile(path=safe_path(rel), size=0, id=path.name, mime="inode/directory")
        st = path.stat()
        return RemoteFile(
            path=safe_path(rel),
            size=st.st_size,
            modified=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            mime=guess_mime(path.name),
            checksum=md5_of_file(path),
        )

    # --- пакетные операции ---
    # Асинхронные, чтобы сигнатуры совпадали с S3Storage: иначе при
    # переключении на R2 пришлось бы переписывать всех вызывающих.
    async def read(self, rel: str) -> bytes:
        return self.abspath(rel).read_bytes()

    async def write(self, rel: str, data: bytes) -> None:
        path = self.abspath(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def stat(self, rel: str) -> RemoteFile:
        info = await self.exists(rel)
        if info is None:
            raise FileNotFoundError(rel)
        return info

    async def remove(self, rel: str) -> None:
        path = self.abspath(rel)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()
