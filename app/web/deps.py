"""Зависимости FastAPI: текущий пользователь по cookie."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import read_token
from app.core.users import get_by_id
from app.db.models import User
from app.db.session import get_session

COOKIE = "tb_session"


async def current_user(request: Request, session: AsyncSession) -> User | None:
    data = read_token(request.cookies.get(COOKIE))
    if not data:
        return None
    user = await get_by_id(session, data.get("uid", ""))
    if user and not user.active:
        return None
    return user


async def require_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    user = await current_user(request, session)
    if not user:
        raise HTTPException(status_code=401, detail="auth required")
    return user
