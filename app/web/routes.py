"""FastAPI веб-панель: OAuth-роуты, страницы, API."""
from __future__ import annotations

import io
import logging
import mimetypes
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import user_settings as us
from app.core.crypto import make_token, new_state, read_token
from app.core.i18n import tr
from app.core.oauth import COLORS, ICONS, PROVIDERS, TITLES, get_provider
from app.core.users import (
    get_account,
    get_accounts,
    get_by_id,
    link_account,
    unlink_account,
    upsert_tg_user,
)
from app.db.models import StoredFile, User
from app.db.session import get_session
from app.storage.factory import local_storage
from app.storage.local import human_size, md5_of, md5_of_file
from app.sync.engine import last_logs, sync_user
from app.web.deps import current_user, require_user
from app.web.tg_init import parse_init_data

log = logging.getLogger("trambot.web")

router = APIRouter()
templates: Jinja2Templates | None = None  # проставляется в create_app

COOKIE = "tb_session"
STATE_COOKIE = "tb_oauth_state"


def _tmpl() -> Jinja2Templates:
    assert templates is not None, "templates not configured"
    return templates


# приоритет источников для аватара: у кого есть фото
_AVATAR_ORDER = ("google", "discord", "github", "yandex")


def _build_profile(user: User, accounts: list) -> dict:
    """Собирает профиль: имя, почта, фото — из OAuth-аккаунтов."""
    name = user.full_name or ""
    email = user.email or ""
    login = user.username or ""
    avatar = ""

    for provider in _AVATAR_ORDER:
        for acc in accounts:
            if acc.provider == provider and acc.avatar_url:
                avatar = acc.avatar_url
                break
        if avatar:
            break

    # дополняем пустые поля данными аккаунтов
    for acc in accounts:
        if not email and acc.email:
            email = acc.email
        if not login and acc.login:
            login = acc.login

    if not name:
        name = user.display_name

    return {
        "name": name or "Без имени",
        "email": email or "",
        "login": login or "",
        "avatar": avatar,
        "initial": (name or login or "?").strip()[:1].upper(),
        "linked": {a.provider: a for a in accounts},
    }


async def _base_ctx(request: Request, session: AsyncSession, user: User | None, nav: str = "") -> dict:
    ctx: dict = {
        "request": request,
        "providers": PROVIDERS,
        "provider_titles": TITLES,
        "provider_icons": ICONS,
        "provider_colors": COLORS,
        "configured": {p: settings.is_provider_configured(p) for p in PROVIDERS},
        "base_url": settings.public_base_url.rstrip("/"),
        "bot_username": settings.bot_username,
        "app_name": "NEONEX",
        "nav": nav,
        "year": datetime.now().year,
    }
    if user:
        prefs = await us.load_all(session, user.id)
        accounts = await get_accounts(session, user.id)
        by_provider = {a.provider: a for a in accounts}
        ctx["user"] = user
        ctx["prefs"] = prefs
        ctx["accounts"] = by_provider
        ctx["locale"] = prefs["locale"]
        ctx["t"] = lambda key, **kw: tr(key, prefs["locale"], **kw)
        ctx["local"] = local_storage(user.id)
        ctx["usage"] = {
            "used": await ctx["local"].total_size(),
            "total": int(prefs["user_quota_mb"]) * 1024 * 1024,
        }
        # Счётчик файлов считается здесь, а не в шаблоне: walk() ходит в сеть.
        ctx["files_count"] = len(await ctx["local"].walk())
        ctx["profile"] = _build_profile(user, accounts)
    else:
        ctx["locale"] = "ru"
        ctx["t"] = lambda key, **kw: tr(key, "ru", **kw)
    return ctx


# =====================================================================
#  OAuth
# =====================================================================

@router.get("/auth/{provider}/login")
async def oauth_login(provider: str, request: Request, tg_id: int | None = None):
    if provider not in PROVIDERS:
        raise HTTPException(404, "unknown provider")
    if not settings.is_provider_configured(provider):
        return HTMLResponse(
            f"<h3>{TITLES[provider]} не настроен</h3>"
            "<p>Добавь CLIENT_ID / CLIENT_SECRET в .env</p>",
            status_code=503,
        )
    prov = get_provider(provider)
    state = new_state()
    url = prov.auth_url(state)
    resp = RedirectResponse(url)
    payload = {"p": provider}
    if tg_id:
        payload["tg"] = str(tg_id)
    resp.set_cookie(STATE_COOKIE, make_token(payload, ttl=600), max_age=600, httponly=True, samesite="lax")
    return resp


@router.get("/auth/{provider}/callback")
async def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
    if provider not in PROVIDERS:
        raise HTTPException(404)
    tmpl = _tmpl()

    if error:
        return tmpl.TemplateResponse(
            request, "message.html", {"request": request, "title": "OAuth error", "text": error}
        )

    st = read_token(request.cookies.get(STATE_COOKIE))
    if not st or st.get("p") != provider:
        return tmpl.TemplateResponse(
            request,
            "message.html",
            {"request": request, "title": "Error", "text": "Сессия авторизации истекла. Попробуй ещё раз."},
            status_code=400,
        )

    prov = get_provider(provider)
    try:
        payload = await prov.exchange(code)
        info = await prov.fetch_user(payload.get("access_token", ""))
    except Exception as e:  # noqa: BLE001
        log.exception("oauth failed")
        return tmpl.TemplateResponse(
            request,
            "message.html",
            {"request": request, "title": "Ошибка OAuth", "text": f"{type(e).__name__}: {e}"[:400]},
            status_code=400,
        )

    from app.db.session import session_scope

    async with session_scope() as session:
        user = None
        if st.get("tg"):
            res = await session.execute(select(User).where(User.tg_id == int(st["tg"])))
            user = res.scalar_one_or_none()
        if user is None:
            res = await session.execute(select(User).where(User.id == st.get("uid", "")))
            user = res.scalar_one_or_none()
        if user is None:
            res = await session.execute(
                select(User).where(User.email != "", User.email == info.email)
            )
            user = res.scalar_one_or_none()
        if user is None:
            user = User(email=info.email, full_name=info.name, username=info.login)
            session.add(user)
            await session.flush()

        await link_account(session, user, provider, payload, info)
        user_id = user.id
        tg_id = user.tg_id

    if tg_id:
        try:
            from app.bot.helpers import send_linked_notification

            await send_linked_notification(tg_id, provider, info.login)
        except Exception:  # noqa: BLE001
            pass

    resp = RedirectResponse("/accounts?ok=1")
    resp.delete_cookie(STATE_COOKIE)
    resp.set_cookie(COOKIE, make_token({"uid": user_id}), max_age=7 * 24 * 3600, httponly=True, samesite="lax")
    return resp


@router.get("/auth/logout")
async def logout(request: Request):
    resp = RedirectResponse("/")
    resp.delete_cookie(COOKIE)
    return resp


@router.post("/api/tg/auth")
async def api_tg_auth(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Вход в панель по данным Telegram Mini App."""
    if not settings.webapp_enabled:
        return JSONResponse({"ok": False, "error": "mini app выключен"}, status_code=404)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "плохой json"}, status_code=400)

    info = parse_init_data(settings.bot_token, str(body.get("init_data") or ""))
    if not info.ok:
        log.info("mini app auth rejected: %s", info.error)
        return JSONResponse({"ok": False, "error": info.error}, status_code=401)

    full = " ".join(x for x in (info.first_name, info.last_name) if x)
    user = await upsert_tg_user(
        session,
        tg_id=info.user_id,
        username=info.username,
        full_name=full,
        is_admin=info.user_id in settings.admin_ids,
    )
    await session.commit()
    user_id = user.id

    resp = JSONResponse({"ok": True, "redirect": "/"})
    resp.set_cookie(COOKIE, make_token({"uid": user_id}), max_age=7 * 24 * 3600, httponly=True, samesite="lax")
    return resp


# =====================================================================
#  Страницы
# =====================================================================

@router.get("/", response_class=HTMLResponse)
async def page_index(request: Request, session: AsyncSession = Depends(get_session)):
    user = await current_user(request, session)
    if not user:
        return RedirectResponse("/login")
    ctx = await _base_ctx(request, session, user, "home")
    ctx["logs"] = await last_logs(session, user.id, 5)
    ctx["files"] = (await ctx["local"].list(""))[:12]
    return _tmpl().TemplateResponse(request, "index.html", ctx)


@router.get("/login", response_class=HTMLResponse)
async def page_login(request: Request, session: AsyncSession = Depends(get_session)):
    user = await current_user(request, session)
    if user:
        return RedirectResponse("/")
    ctx = await _base_ctx(request, session, None)
    return _tmpl().TemplateResponse(request, "login.html", ctx)


@router.get("/files", response_class=HTMLResponse)
async def page_files(
    request: Request,
    path: str = Query(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
):
    ctx = await _base_ctx(request, session, user, "files")
    local = ctx["local"]
    from app.storage.base import parent_of

    ctx["path"] = path.strip("/")
    ctx["parent"] = parent_of(ctx["path"])
    ctx["items"] = await local.list(ctx["path"])
    return _tmpl().TemplateResponse(request, "files.html", ctx)


@router.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    ctx = await _base_ctx(request, session, user, "settings")
    ctx["sections"] = us.SECTIONS
    ctx["defs"] = {s.key: s for s in us.SETTINGS}
    ctx["by_section"] = {
        sec: [s for s in us.SETTINGS if s.section == sec] for sec, _ in us.SECTIONS
    }
    return _tmpl().TemplateResponse(request, "settings.html", ctx)


@router.get("/accounts", response_class=HTMLResponse)
async def page_accounts(request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    ctx = await _base_ctx(request, session, user, "accounts")
    ctx["all_accounts"] = await get_accounts(session, user.id)
    return _tmpl().TemplateResponse(request, "accounts.html", ctx)


@router.get("/profile", response_class=HTMLResponse)
async def page_profile(request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    ctx = await _base_ctx(request, session, user, "profile")
    photos, videos = 0, 0
    for rel in await ctx["local"].walk():
        ext = Path(rel).suffix.lower()
        if ext in IMAGE_EXT:
            photos += 1
        elif ext in VIDEO_EXT:
            videos += 1
    ctx["photos"] = [0] * photos
    ctx["videos"] = [0] * videos
    return _tmpl().TemplateResponse(request, "profile.html", ctx)


# =====================================================================
#  API: файлы
# =====================================================================

@router.get("/api/files")
async def api_files(path: str = "", session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    local = local_storage(user.id)
    items = await local.list(path)
    return {
        "path": path,
        "items": [
            {
                "path": i.path,
                "name": i.path.rsplit("/", 1)[-1],
                "size": i.size,
                "size_h": human_size(i.size),
                "is_dir": i.mime == "inode/directory",
                "modified": i.modified,
            }
            for i in items
        ],
    }


@router.post("/api/files/upload")
async def api_upload(
    files: list[UploadFile],
    path: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
):
    prefs = await us.load_all(session, user.id)
    local = local_storage(user.id)
    max_bytes = int(prefs["max_file_mb"]) * 1024 * 1024
    quota = int(prefs["user_quota_mb"]) * 1024 * 1024
    used = await local.total_size()
    saved: list[str] = []
    for up in files:
        data = await up.read()
        if len(data) > max_bytes:
            return JSONResponse({"error": f"{up.filename}: too big"}, status_code=413)
        if quota and used + len(data) > quota:
            return JSONResponse({"error": "quota"}, status_code=413)
        rel = f"{path.strip('/')}/{up.filename}".strip("/") if path.strip("/") else up.filename
        await local.upload(rel, data)
        used += len(data)
        saved.append(rel)
    return {"ok": True, "saved": saved}


async def _send_stored(local, path: str, *, download: bool) -> Response:
    """Отдать файл из хранилища.

    Раньше здесь был FileResponse(путь на диске), но при R2 файла на диске
    нет вовсе, поэтому содержимое читается из бэкенда и уходит из памяти.
    """
    found = await local.exists(path)
    if found is None or found.mime == "inode/directory":
        raise HTTPException(404, "not found")
    name = path.rsplit("/", 1)[-1]
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not download:
        if not mime.startswith(("image/", "video/", "audio/")):
            raise HTTPException(415, "not media")
        return Response(
            content=await local.download(path),
            media_type=mime,
            headers={"Cache-Control": "private, max-age=300"},
        )
    quoted = urllib.parse.quote(name)
    return Response(
        content=await local.download(path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"},
    )


@router.get("/api/download")
async def api_download(path: str, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    return await _send_stored(local_storage(user.id), path, download=True)


@router.get("/api/raw")
async def api_raw(path: str, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    """Файл без заголовка attachment — для <img>/<video> в галерее."""
    return await _send_stored(local_storage(user.id), path, download=False)


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".heic"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi", ".ogv"}


@router.get("/api/media")
async def api_media(
    session: AsyncSession = Depends(get_session), user: User = Depends(require_user)
):
    """Список фото и видео пользователя для галереи."""
    local = local_storage(user.id)
    items: list[dict] = []
    for rel in await local.walk():
        ext = Path(rel).suffix.lower()
        if ext in IMAGE_EXT:
            kind = "photo"
        elif ext in VIDEO_EXT:
            kind = "video"
        else:
            continue
        st = await local.exists(rel)
        if st is None or st.mime == "inode/directory":
            continue
        items.append(
            {
                "path": rel,
                "name": rel.rsplit("/", 1)[-1],
                "kind": kind,
                "size": st.size,
                "size_h": human_size(st.size),
                "mime": mimetypes.guess_type(rel)[0] or "",
                "modified": st.modified or "",            }
        )
    items.sort(key=lambda i: i["modified"], reverse=True)
    return {"items": items}


@router.get("/api/download-zip")
async def api_download_zip(
    path: str = "", session: AsyncSession = Depends(get_session), user: User = Depends(require_user)
):
    local = local_storage(user.id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        files = await local.walk()
        for rel in files:
            if path and not (rel == path or rel.startswith(path.rstrip("/") + "/")):
                continue
            z.writestr(rel, await local.read(rel))
    name = (path.rsplit("/", 1)[-1] or "storage") + ".zip"
    data = buf.getvalue()
    quoted = urllib.parse.quote(name)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


@router.post("/api/files/delete")
async def api_delete(
    payload: dict, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)
):
    prefs = await us.load_all(session, user.id)
    local = local_storage(user.id)
    targets: list[str] = payload.get("paths") or ([payload["path"]] if payload.get("path") else [])
    if not targets:
        return JSONResponse({"error": "no paths"}, status_code=400)
    trash = bool(prefs["trash_enabled"])
    for rel in targets:
        try:
            if trash:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                dest = f".trash/{stamp}_{rel.replace('/', '_')}"
                await local.upload(dest, await local.download(rel))
            await local.delete(rel)
            await session.execute(
                delete(StoredFile).where(StoredFile.user_id == user.id, StoredFile.rel_path == rel)
            )
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": f"{rel}: {e}"}, status_code=400)
    await session.commit()
    return {"ok": True}


@router.post("/api/files/mkdir")
async def api_mkdir(
    payload: dict, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)
):
    name = (payload.get("name") or "").strip()
    path = f"{payload.get('path', '').strip('/')}/{name}".strip("/")
    if not name:
        return JSONResponse({"error": "empty name"}, status_code=400)
    await local_storage(user.id).mkdir(path)
    return {"ok": True, "path": path}


@router.post("/api/files/rename")
async def api_rename(
    payload: dict, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)
):
    local = local_storage(user.id)
    src = payload.get("from", "")
    dst = payload.get("to", "")
    if not src or not dst or await local.exists(src) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = await local.download(src)
    await local.upload(dst, data)
    await local.delete(src)
    return {"ok": True}


# =====================================================================
#  API: настройки
# =====================================================================

@router.get("/api/settings")
async def api_settings_get(session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    prefs = await us.load_all(session, user.id)
    return {
        "prefs": prefs,
        "meta": {
            s.key: {
                "type": s.type,
                "title": s.title,
                "hint": s.hint,
                "section": s.section,
                "choices": [{"value": c[0], "title": c[1]} for c in (s.choices or [])],
                "min": s.min,
                "max": s.max,
            }
            for s in us.SETTINGS
        },
    }


@router.post("/api/settings")
async def api_settings_set(
    payload: dict, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)
):
    changed = dict(payload.get("prefs") or payload)
    results = await us.set_many(session, user.id, changed)
    if changed.get("locale"):
        user.locale = str(changed["locale"])[:8]
    await session.commit()
    bad = [(k, info) for k, ok, info in results if not ok]
    return {"ok": not bad, "errors": bad, "prefs": await us.load_all(session, user.id)}


# =====================================================================
#  API: аккаунты и синхронизация
# =====================================================================

@router.post("/api/accounts/{provider}/unlink")
async def api_unlink(provider: str, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    ok = await unlink_account(session, user.id, provider)
    await session.commit()
    return {"ok": ok}


@router.post("/api/sync")
async def api_sync(
    payload: dict, session: AsyncSession = Depends(get_session), user: User = Depends(require_user)
):
    r = await sync_user(
        session, user.id, payload.get("provider"), payload.get("direction")
    )
    return {
        "ok": r.status == "ok",
        "provider": r.provider,
        "status": r.status,
        "summary": r.summary(await us.get(session, user.id, "locale") or "ru"),
        "uploaded": r.uploaded,
        "downloaded": r.downloaded,
        "deleted": r.deleted,
        "errors": r.errors,
    }


@router.get("/api/sync/logs")
async def api_sync_logs(session: AsyncSession = Depends(get_session), user: User = Depends(require_user)):
    logs = await last_logs(session, user.id, 25)
    return {
        "items": [
            {
                "id": l.id,
                "provider": l.provider,
                "direction": l.direction,
                "status": l.status,
                "up": l.uploaded,
                "down": l.downloaded,
                "del": l.deleted,
                "message": l.message,
                "created_at": l.created_at.isoformat() if l.created_at else "",
            }
            for l in logs
        ]
    }
