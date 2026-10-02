"""Двусторонняя синхронизация локального хранилища с Google Drive / Яндекс Диском."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import user_settings as us
from app.db.models import StoredFile, SyncLog
from app.storage.base import RemoteFile, parent_of
from app.storage.factory import LocalStorage, StorageUnavailable, create_cloud_storage
from app.storage.local import guess_mime, md5_of

log = logging.getLogger("trambot.sync")

LOCKS: dict[str, bool] = {}


@dataclass
class SyncResult:
    provider: str
    direction: str = "both"
    status: str = "ok"
    uploaded: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    message: str = ""

    def summary(self, locale: str = "ru") -> str:
        ru = locale == "ru"
        if self.status == "error":
            return f"❌ {'Ошибка' if ru else 'Error'}: {self.message}"
        parts = []
        if self.downloaded:
            parts.append(f"⬇️ {len(self.downloaded)}")
        if self.uploaded:
            parts.append(f"⬆️ {len(self.uploaded)}")
        if self.deleted:
            parts.append(f"\U0001f5d1 {len(self.deleted)}")
        if self.skipped:
            parts.append(f"⏭ {self.skipped}")
        head = "✅ " + ("Синхронизация завершена" if ru else "Sync finished")
        tail = " · ".join(parts) if parts else ("ничего не менялось" if ru else "nothing changed")
        text = f"{head}: {tail}"
        if self.errors:
            text += f"\n⚠️ {'; '.join(self.errors[:3])}"
        return text


def _ts(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _in_scope(rel: str, root: str) -> bool:
    """Фильтр по подпапке (пустой root = вся папка пользователя)."""
    root = (root or "").strip("/")
    if not root:
        return True
    return rel == root or rel.startswith(root + "/")


async def _sync_row(session: AsyncSession, user_id: str) -> dict[str, StoredFile]:
    rows = await session.execute(select(StoredFile).where(StoredFile.user_id == user_id))
    return {r.rel_path: r for r in rows.scalars()}


async def _local_snapshot(local: LocalStorage) -> dict[str, tuple[int, float, str]]:
    """rel -> (size, mtime, md5). Собирается одним обходом бэкенда."""
    return await local.snapshot()


async def _remember(
    session: AsyncSession,
    user_id: str,
    rel: str,
    provider: str,
    remote: RemoteFile | None = None,
    checksum: str = "",
    size: int = 0,
) -> None:
    res = await session.execute(
        select(StoredFile).where(StoredFile.user_id == user_id, StoredFile.rel_path == rel)
    )
    row = res.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = StoredFile(user_id=user_id, rel_path=rel)
        session.add(row)
    row.size = size or (remote.size if remote else 0)
    row.mime = guess_mime(rel)
    row.checksum = checksum or (remote.checksum if remote else "")
    row.provider = provider
    if remote:
        row.remote_id = remote.id
        row.remote_path = remote.path
        row.remote_modified = remote.modified
    row.updated_at = now
    row.synced_at = now
    await session.flush()


async def _forget(session: AsyncSession, user_id: str, rel: str) -> None:
    res = await session.execute(
        select(StoredFile).where(StoredFile.user_id == user_id, StoredFile.rel_path == rel)
    )
    row = res.scalar_one_or_none()
    if row:
        await session.delete(row)
    await session.flush()


async def sync_user(
    session: AsyncSession,
    user_id: str,
    provider: str | None = None,
    direction: str | None = None,
    local_root: str = "",
) -> SyncResult:
    """Основная точка входа. direction: pull | push | both"""
    prefs = await us.load_all(session, user_id)
    provider = provider or prefs["default_provider"]
    result = SyncResult(provider=provider or "local")

    if not provider:
        result.status = "error"
        result.message = "provider_not_selected"
        await _log(session, user_id, result)
        return result

    direction = direction or ("both" if prefs["sync_both_ways"] else "push")
    result.direction = direction

    if LOCKS.get(f"{user_id}:{provider}"):
        result.status = "error"
        result.message = "already_running"
        await _log(session, user_id, result)
        return result
    LOCKS[f"{user_id}:{provider}"] = True

    cloud_root = (prefs["sync_folder"] or "").strip("/")
    local = LocalStorage(user_id)
    try:
        cloud = await create_cloud_storage(session, user_id, provider, cloud_root)
    except StorageUnavailable as e:
        result.status = "error"
        result.message = str(e)
        LOCKS.pop(f"{user_id}:{provider}", None)
        await _log(session, user_id, result)
        return result
    except Exception as e:  # noqa: BLE001
        log.exception("cloud init failed")
        result.status = "error"
        result.message = f"{type(e).__name__}: {e}"[:300]
        LOCKS.pop(f"{user_id}:{provider}", None)
        await _log(session, user_id, result)
        return result

    try:
        max_bytes = int(prefs["sync_max_file_mb"] or 0) * 1024 * 1024
        verify = bool(prefs["checksum_verify"])
        policy = prefs["sync_conflicts"]

        local_map = await _local_snapshot(local)
        rel_to_local = {rel: meta for rel, meta in local_map.items()}
        try:
            remote_items = await cloud.walk(local_root)
        except Exception as e:  # noqa: BLE001
            result.status = "error"
            result.message = f"list failed: {e}"[:300]
            return result
        remote_map = {r.path: r for r in remote_items}
        known = await _sync_row(session, user_id)

        # ---------- PULL: облако -> локально ----------
        if direction in ("pull", "both"):
            for rpath, r in remote_map.items():
                if r.size and max_bytes and r.size > max_bytes:
                    result.skipped += 1
                    continue
                lmeta = rel_to_local.get(rpath)
                if lmeta is None:
                    if await _pull_one(cloud, local, rpath, r, provider, session, user_id, result, prefs):
                        result.downloaded.append(rpath)
                    continue

                size_differs = lmeta[0] != r.size
                remote_newer = _ts(r.modified) > lmeta[1] + 1
                if not (size_differs or remote_newer):
                    result.skipped += 1
                    continue

                row = known.get(rpath)
                remote_changed = bool(row and (row.remote_modified != r.modified or row.checksum != (r.checksum or row.checksum)))
                local_changed = bool(row and row.synced_at and lmeta[1] > row.synced_at.timestamp() + 1)

                if size_differs and remote_changed and local_changed and policy != "newer":
                    if policy == "local":
                        result.skipped += 1
                        continue
                    if policy == "both":
                        rpath = await _keep_both(local, rpath)
                    elif policy == "remote":
                        pass
                    elif policy == "local":
                        result.skipped += 1
                        continue

                try:
                    data = await cloud.download(r.path)
                    await _write_local(local, rpath, data)
                    result.downloaded.append(rpath)
                    await _remember(session, user_id, rpath, provider, remote=r, size=len(data))
                except Exception as e:  # noqa: BLE001
                    result.errors.append(f"{rpath}: {type(e).__name__}")

        # ---------- PUSH: локально -> облако ----------
        if direction in ("push", "both"):
            for lpath, (size, mtime, md5) in sorted(rel_to_local.items()):
                if not _in_scope(lpath, local_root):
                    continue
                r = remote_map.get(lpath)
                if r is not None:
                    if r.size == size and (not verify or (r.checksum and r.checksum == md5)):
                        result.skipped += 1
                        continue
                if max_bytes and size > max_bytes:
                    result.skipped += 1
                    continue
                data = await local.read(lpath)
                try:
                    if r is not None and r.id:
                        if provider == "google":
                            new = await cloud.update(r.id, lpath, data)  # type: ignore[attr-defined]
                        else:
                            await cloud.delete(lpath)
                            new = await cloud.upload(lpath, data)
                    else:
                        new = await cloud.upload(lpath, data)
                    result.uploaded.append(lpath)
                    await _remember(session, user_id, lpath, provider, remote=new, checksum=md5, size=size)
                except Exception as e:  # noqa: BLE001
                    result.errors.append(f"{lpath}: {type(e).__name__}")

        # ---------- DELETIONS ----------
        propagate = bool(prefs["sync_delete_propagate"])
        for rpath in list(known):
            if rpath.startswith(".") or not _in_scope(rpath, local_root):
                continue
            in_local = rpath in local_map
            in_remote = rpath in remote_map
            if in_local and in_remote:
                continue
            if in_local and not in_remote:
                # файл есть локально, но пропал из облака — просто забываем запись,
                # push выше его уже перезальёт
                if not propagate:
                    await _forget(session, user_id, rpath)
                continue
            # файла нет локально
            if propagate and direction in ("push", "both"):
                try:
                    await cloud.delete(rpath)
                    await _forget(session, user_id, rpath)
                    result.deleted.append(rpath)
                except Exception as e:  # noqa: BLE001
                    result.errors.append(f"-{rpath}: {type(e).__name__}")
            elif not in_remote:
                await _forget(session, user_id, rpath)
    finally:
        LOCKS.pop(f"{user_id}:{provider}", None)
        await _log(session, user_id, result)

    return result


async def _write_local(local: LocalStorage, rel: str, data: bytes) -> None:
    await local.write(rel, data)


async def _pull_one(
    cloud, local: LocalStorage, rel: str, remote: RemoteFile, provider: str,
    session: AsyncSession, user_id: str, result: SyncResult, prefs: dict,
) -> bool:
    try:
        if remote.mime == "inode/directory":
            await local.mkdir(rel)
            return True
        data = await cloud.download(remote.path)
        await _write_local(local, rel, data)
        await _remember(
            session, user_id, rel, provider, remote=remote,
            checksum=md5_of(data), size=len(data),
        )
        return True
    except Exception as e:  # noqa: BLE001
        result.errors.append(f"{rel}: {type(e).__name__}")
        return False


async def _keep_both(local: LocalStorage, rel: str) -> str:
    stem, dot, ext = rel.rpartition(".")
    if not dot:
        stem, ext = rel, ""
    else:
        ext = f".{ext}"
    new = f"{stem} (cloud {datetime.now().strftime('%Y-%m-%d %H-%M')}){ext}"
    parent = parent_of(rel)
    target = f"{parent}/{new}" if parent else new
    n = 1
    while await local.exists(target) is not None:
        target = f"{parent}/{stem} (cloud {n}){ext}" if parent else f"{stem} ({n}){ext}"
        n += 1
    return target


async def _log(session: AsyncSession, user_id: str, result: SyncResult) -> None:
    try:
        session.add(
            SyncLog(
                user_id=user_id,
                provider=result.provider,
                direction=result.direction,
                status=result.status,
                uploaded=len(result.uploaded),
                downloaded=len(result.downloaded),
                deleted=len(result.deleted),
                message=(result.message or "; ".join(result.errors[:5]))[:500],
            )
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()


async def last_logs(session: AsyncSession, user_id: str, limit: int = 10) -> list[SyncLog]:
    res = await session.execute(
        select(SyncLog).where(SyncLog.user_id == user_id).order_by(SyncLog.created_at.desc()).limit(limit)
    )
    return list(res.scalars())
