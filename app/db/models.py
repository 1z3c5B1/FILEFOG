"""SQLAlchemy 2.0 модели."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tg_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    full_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    locale: Mapped[str] = mapped_column(String(8), default="ru")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    accounts: Mapped[list["OAuthAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    files: Mapped[list["StoredFile"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        return self.full_name or (f"@{self.username}" if self.username else str(self.tg_id))


class OAuthAccount(Base):
    """Привязанный аккаунт внешнего сервиса."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id", name="uq_provider_uid"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)  # google|github|discord|yandex
    # NULL, пока профиль не получен: в SQLite несколько NULL не конфликтуют,
    # в отличие от пустой строки
    provider_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    login: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    avatar_url: Mapped[str] = mapped_column(String(512), default="")
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="accounts")
    token: Mapped["OAuthToken | None"] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )


class OAuthToken(Base):
    """Токены хранятся только в зашифрованном виде (Fernet)."""

    __tablename__ = "oauth_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_accounts.id", ondelete="CASCADE"), index=True
    )
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)  # unix ts
    scope: Mapped[str] = mapped_column(String(512), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    account: Mapped["OAuthAccount"] = relationship(back_populates="token")


class UserSettings(Base):
    """Произвольные ключи настроек пользователя (key -> json value)."""

    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StoredFile(Base):
    """Файл в локальном хранилище + привязка к облаку."""

    __tablename__ = "stored_files"
    __table_args__ = (UniqueConstraint("user_id", "rel_path", name="uq_user_path"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rel_path: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    checksum: Mapped[str] = mapped_column(String(64), default="")
    provider: Mapped[str] = mapped_column(String(32), default="")  # "" | google | yandex
    remote_id: Mapped[str] = mapped_column(String(256), default="")
    remote_path: Mapped[str] = mapped_column(String(512), default="")
    remote_modified: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="files")


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    direction: Mapped[str] = mapped_column(String(16), default="both")  # pull|push|both
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error|running
    uploaded: Mapped[int] = mapped_column(Integer, default=0)
    downloaded: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
