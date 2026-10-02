"""Абстракция хранилища."""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Protocol

_UNSAFE = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass
class RemoteFile:
    path: str
    size: int
    modified: str = ""
    id: str = ""
    mime: str = ""
    checksum: str = ""


def safe_component(name: str) -> str:
    name = _UNSAFE.sub("_", (name or "").strip())
    name = name.rstrip(". ")
    if not name:
        return "unnamed"
    if name.split(".")[0].upper() in _WIN_RESERVED:
        name = "_" + name
    return name[:180]


def safe_path(path: str) -> str:
    """Нормализует относительный путь: без .., без абсолютных путей, / вместо \\."""
    raw = (path or "").replace("\\", "/").strip()
    parts: list[str] = []
    for chunk in raw.split("/"):
        chunk = chunk.strip()
        if not chunk or chunk in (".", ".."):
            continue
        parts.append(safe_component(chunk))
    return posixpath.join(*parts) if parts else ""


def join_path(root: str, rel: str) -> str:
    return posixpath.join(root.strip("/"), safe_path(rel)) if root.strip("/") else safe_path(rel)


def parent_of(rel: str) -> str:
    parent = posixpath.dirname(rel)
    return "" if parent in (".", "/") else parent


def guess_mime(name: str) -> str:
    import mimetypes

    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


class StorageBackend(Protocol):
    name: str

    async def list(self, folder: str = "") -> list[RemoteFile]: ...
    async def upload(self, rel: str, data: bytes) -> RemoteFile: ...
    async def download(self, rel: str) -> bytes: ...
    async def delete(self, rel: str) -> None: ...
    async def mkdir(self, rel: str) -> None: ...
    async def exists(self, rel: str) -> RemoteFile | None: ...
    async def snapshot(self) -> dict[str, tuple[int, float, str]]: ...
