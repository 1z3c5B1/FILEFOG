"""Хелперы работы с пользователями и привязкой аккаунтов."""
from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.core.oauth import OAuthUser, get_provider
from app.db.models import OAuthAccount, OAuthToken, User


async def get_by_tg(session: AsyncSession, tg_id: int) -> User | None:
    res = await session.execute(select(User).where(User.tg_id == tg_id))
    return res.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def upsert_tg_user(
    session: AsyncSession,
    tg_id: int,
    username: str = "",
    full_name: str = "",
    is_admin: bool = False,
) -> User:
    user = await get_by_tg(session, tg_id)
    if user is None:
        user = User(tg_id=tg_id, is_admin=is_admin)
        session.add(user)
    if username:
        user.username = username
    if full_name:
        user.full_name = full_name
    if is_admin:
        user.is_admin = True
    await session.flush()
    return user


async def get_accounts(session: AsyncSession, user_id: str) -> list[OAuthAccount]:
    res = await session.execute(
        select(OAuthAccount).where(OAuthAccount.user_id == user_id).order_by(OAuthAccount.provider)
    )
    return list(res.scalars())


async def get_account(session: AsyncSession, user_id: str, provider: str) -> OAuthAccount | None:
    res = await session.execute(
        select(OAuthAccount).where(OAuthAccount.user_id == user_id, OAuthAccount.provider == provider)
    )
    return res.scalar_one_or_none()


async def link_account(
    session: AsyncSession,
    user: User,
    provider: str,
    payload: dict,
    info: OAuthUser | None = None,
) -> OAuthAccount:
    acc = await get_account(session, user.id, provider)
    if acc is None:
        acc = OAuthAccount(user_id=user.id, provider=provider)
        session.add(acc)
    if info:
        acc.provider_user_id = info.id
        acc.login = info.login
        acc.email = info.email
        acc.avatar_url = info.avatar
        if info.email and not user.email:
            user.email = info.email
        if info.name and not user.full_name:
            user.full_name = info.name

    tok = acc.token
    if tok is None:
        tok = OAuthToken(account_id=acc.id)
        session.add(tok)
        acc.token = tok
    tok.access_token = encrypt(payload.get("access_token", ""))
    refresh = payload.get("refresh_token")
    if refresh:
        tok.refresh_token = encrypt(refresh)
    expires = payload.get("expires_in") or payload.get("expires_at")
    if expires:
        tok.expires_at = int(time.time()) + int(expires) if payload.get("expires_in") else int(expires)
    tok.scope = str(payload.get("scope", ""))[:500]
    await session.flush()
    return acc


async def unlink_account(session: AsyncSession, user_id: str, provider: str) -> bool:
    acc = await get_account(session, user_id, provider)
    if not acc:
        return False
    if acc.token:
        p = get_provider(provider)
        plain = decrypt(acc.token.access_token)
        if p:
            await p.revoke(plain)
    await session.delete(acc)
    await session.flush()
    return True


async def get_valid_access_token(
    session: AsyncSession, user_id: str, provider: str
) -> tuple[str, str] | None:
    """Возвращает (access_token, provider) с авто-обновлением refresh-токена."""
    acc = await get_account(session, user_id, provider)
    if not acc or not acc.token:
        return None
    tok = acc.token
    prov = get_provider(provider)
    if not prov:
        return None

    access = decrypt(tok.access_token)
    expired = tok.expires_at is not None and tok.expires_at - 60 < int(time.time())
    if expired and tok.refresh_token:
        try:
            data = await prov.refresh(decrypt(tok.refresh_token))
            access = data.get("access_token", "")
            tok.access_token = encrypt(access)
            if data.get("refresh_token"):
                tok.refresh_token = encrypt(data["refresh_token"])
            if data.get("expires_in"):
                tok.expires_at = int(time.time()) + int(data["expires_in"])
            await session.flush()
        except Exception:
            if not access:
                return None
    if not access:
        return None
    return access, provider
