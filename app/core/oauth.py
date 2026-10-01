"""OAuth 2.0 провайдеры: Google, GitHub, Discord, Yandex."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

PROVIDERS = ("google", "github", "discord", "yandex")

TITLES = {
    "google": "Google",
    "github": "GitHub",
    "discord": "Discord",
    "yandex": "Яндекс",
}

ICONS = {
    "google": "\U0001f510",
    "github": "\U0001f4bb",
    "discord": "\U0001f517",
    "yandex": "\U0001f310",
}

# CSS-цвета иконок в веб-панели
COLORS = {
    "google": "#ea4335",
    "github": "#24292f",
    "discord": "#5865f2",
    "yandex": "#fc3f1d",
}


@dataclass
class OAuthUser:
    id: str
    login: str
    email: str
    name: str
    avatar: str


class OAuthProvider:
    name = ""
    authorize_url = ""
    token_url = ""
    scope = ""
    revoke_url = ""
    # доп. параметры authorize-запроса (задаются подклассами)
    extra_auth: dict[str, Any] = {}

    def __init__(self) -> None:
        self.extra_auth = dict(type(self).extra_auth)

    # --- базовые вещи -------------------------------------------------
    @property
    def client_id(self) -> str:
        return getattr(settings, f"{self.name}_client_id", "")

    @property
    def client_secret(self) -> str:
        return getattr(settings, f"{self.name}_client_secret", "")

    @property
    def redirect_uri(self) -> str:
        return settings.callback_url(self.name)

    def auth_url(self, state: str) -> str:
        from urllib.parse import urlencode

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "state": state,
            **self.extra_auth,
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    async def exchange(self, code: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            }
            r = await client.post(
                self.token_url, data=data, headers={"Accept": "application/json"}
            )
            if r.status_code >= 400:
                raise RuntimeError(f"token error {r.status_code}: {r.text[:300]}")
            return r.json()

    async def fetch_user(self, access_token: str) -> OAuthUser:
        raise NotImplementedError

    # --- общие хелперы ------------------------------------------------
    @staticmethod
    def _bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    @staticmethod
    def _expiry(payload: dict[str, Any]) -> int | None:
        if payload.get("expires_in"):
            return int(time.time()) + int(payload["expires_in"])
        if payload.get("expires_at"):
            return int(payload["expires_at"])
        return None

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"refresh error {r.status_code}: {r.text[:200]}")
            return r.json()

    async def revoke(self, token: str) -> None:
        if not self.revoke_url or not token:
            return
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(
                    self.revoke_url,
                    data={"token": token, "client_id": self.client_id, "client_secret": self.client_secret},
                )
        except Exception:
            pass


class GoogleProvider(OAuthProvider):
    name = "google"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    revoke_url = "https://oauth2.googleapis.com/revoke"
    scope = (
        "openid email profile "
        "https://www.googleapis.com/auth/drive.file"
    )
    extra_auth = {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}

    async def fetch_user(self, access_token: str) -> OAuthUser:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers=self._bearer(access_token),
            )
            r.raise_for_status()
            d = r.json()
        return OAuthUser(
            id=str(d.get("sub", "")),
            login=d.get("email", ""),
            email=d.get("email", ""),
            name=d.get("name", ""),
            avatar=d.get("picture", ""),
        )


class GitHubProvider(OAuthProvider):
    name = "github"
    authorize_url = "https://github.com/login/oauth/authorize"
    token_url = "https://github.com/login/oauth/access_token"
    revoke_url = "https://api.github.com/applications/{client_id}/token"
    scope = "read:user user:email"

    async def exchange(self, code: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            return r.json()

    async def fetch_user(self, access_token: str) -> OAuthUser:
        async with httpx.AsyncClient(timeout=30) as client:
            h = self._bearer(access_token)
            r = await client.get("https://api.github.com/user", headers=h)
            r.raise_for_status()
            d = r.json()
            email = d.get("email") or ""
            if not email:
                e = await client.get("https://api.github.com/user/emails", headers=h)
                if e.status_code == 200:
                    for item in e.json():
                        if item.get("primary") and item.get("verified"):
                            email = item.get("email", "")
                            break
        return OAuthUser(
            id=str(d.get("id", "")),
            login=d.get("login", ""),
            email=email,
            name=d.get("name") or d.get("login", ""),
            avatar=d.get("avatar_url", ""),
        )

    async def revoke(self, token: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.delete(
                    f"https://api.github.com/applications/{self.client_id}/token",
                    headers=self._bearer(token),
                    json={"access_token": token},
                )
        except Exception:
            pass


class DiscordProvider(OAuthProvider):
    name = "discord"
    authorize_url = "https://discord.com/oauth2/authorize"
    token_url = "https://discord.com/api/oauth2/token"
    revoke_url = "https://discord.com/api/oauth2/token/revoke"
    scope = "identify email"

    extra_auth = {"prompt": "none"}

    async def exchange(self, code: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"token error {r.status_code}: {r.text[:300]}")
            return r.json()

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"refresh error {r.status_code}: {r.text[:200]}")
            return r.json()

    async def fetch_user(self, access_token: str) -> OAuthUser:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://discord.com/api/v10/users/@me", headers=self._bearer(access_token))
            r.raise_for_status()
            d = r.json()
        uid = d.get("id", "")
        avatar = ""
        if d.get("avatar"):
            ext = "gif" if str(d["avatar"]).startswith("a_") else "png"
            avatar = f"https://cdn.discordapp.com/avatars/{uid}/{d['avatar']}.{ext}?size=256"
        return OAuthUser(
            id=uid,
            login=(d.get("global_name") or d.get("username", "")),
            email=d.get("email", "") or "",
            name=d.get("global_name") or d.get("username", ""),
            avatar=avatar,
        )


class YandexProvider(OAuthProvider):
    name = "yandex"
    authorize_url = "https://oauth.yandex.ru/authorize"
    token_url = "https://oauth.yandex.ru/token"
    revoke_url = "https://oauth.yandex.ru/revoke"
    scope = "login:email login:info read:disk"

    extra_auth = {"force_confirm": "true"}

    async def exchange(self, code: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"token error {r.status_code}: {r.text[:300]}")
            return r.json()

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"refresh error {r.status_code}: {r.text[:200]}")
            return r.json()

    async def fetch_user(self, access_token: str) -> OAuthUser:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                "https://login.yandex.ru/info", params={"format": "json"}, headers=self._bearer(access_token)
            )
            r.raise_for_status()
            d = r.json()
        return OAuthUser(
            id=str(d.get("id", "")),
            login=d.get("display_name") or d.get("login", ""),
            email=d.get("default_email", "") or "",
            name=d.get("display_name") or d.get("real_name") or d.get("login", ""),
            avatar="",
        )


_REGISTRY: dict[str, type[OAuthProvider]] = {
    "google": GoogleProvider,
    "github": GitHubProvider,
    "discord": DiscordProvider,
    "yandex": YandexProvider,
}


def get_provider(name: str) -> OAuthProvider | None:
    cls = _REGISTRY.get(name)
    return cls() if cls else None
