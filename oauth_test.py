"""Тест OAuth-провайдеров на моках httpx (сеть не нужна)."""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["SECRET_KEY"] = "oauth-test-secret"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="tboauth_")
DB = Path(tempfile.gettempdir()) / "trambot_oauth_test.db"
for suffix in ("", "-journal", "-wal", "-shm"):
    DB.with_name(DB.name + suffix).unlink(missing_ok=True)
os.environ["DB_PATH"] = str(DB)

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.core import oauth  # noqa: E402
from app.core.crypto import decrypt  # noqa: E402
from app.core.users import (  # noqa: E402
    get_account,
    get_accounts,
    get_by_tg,
    get_valid_access_token,
    link_account,
    unlink_account,
    upsert_tg_user,
)
from app.db.session import init_db, session_scope  # noqa: E402

ok = 0
fail = 0
failures: list[str] = []
calls: list[tuple[str, str, dict]] = []
RULES: list[tuple[str, object]] = []


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail += 1
        failures.append(f"{name} {extra}".strip())
        print(f"  FAIL {name} {extra}")


def reply(match: str, **json_body) -> None:
    """Регистрирует ответ; правила проверяются в порядке добавления."""
    RULES.append((match, httpx.Response(200, json=json_body)))


def reply_raw(match: str, resp: httpx.Response) -> None:
    RULES.append((match, resp))


def reset() -> None:
    RULES.clear()


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    try:
        body = request.content.decode()
    except Exception:
        body = ""
    calls.append((request.method, url, dict(request.headers)))
    calls.append(("BODY", body, {}))
    for match, resp in RULES:
        if match in url:
            return resp
    return httpx.Response(200, json={})


def bodies() -> list[str]:
    return [c[1] for c in calls if c[0] == "BODY"]


async def main():
    print("\n=== 1. Конфигурация провайдеров ===")
    for p in oauth.PROVIDERS:
        setattr(settings, f"{p}_client_id", f"{p}-id")
        setattr(settings, f"{p}_client_secret", f"{p}-secret")
    check("все 4 провайдера в реестре", set(oauth.PROVIDERS) == {"google", "github", "discord", "yandex"})
    check("client_id задан", oauth.get_provider("google").client_id == "google-id")
    check("callback из PUBLIC_BASE_URL",
          oauth.get_provider("github").redirect_uri.endswith("/auth/github/callback"),
          oauth.get_provider("github").redirect_uri)
    check("is_provider_configured", settings.is_provider_configured("yandex") is True)
    check("неизвестный провайдер -> None", oauth.get_provider("vk") is None)

    print("\n=== 2. URL авторизации ===")
    for p in oauth.PROVIDERS:
        prov = oauth.get_provider(p)
        url = prov.auth_url("STATE123")
        check(f"{p}: client_id в URL", f"client_id={p}-id" in url)
        check(f"{p}: state в URL", "state=STATE123" in url)
        check(f"{p}: redirect_uri в URL", "redirect_uri=" in url)
        check(f"{p}: scope задан", "scope=" in url, url)
    check("google: access_type=offline", "access_type=offline" in oauth.get_provider("google").auth_url("s"))
    check("google: prompt=consent", "prompt=consent" in oauth.get_provider("google").auth_url("s"))
    check("yandex: force_confirm", "force_confirm=true" in oauth.get_provider("yandex").auth_url("s"))
    check("discord: prompt=none", "prompt=none" in oauth.get_provider("discord").auth_url("s"))
    check("github: без offline-параметров", "access_type" not in oauth.get_provider("github").auth_url("s"))
    check("экземпляры не делят extra_auth",
          oauth.get_provider("google").extra_auth is not oauth.get_provider("google").extra_auth)

    print("\n=== 3. Google: обмен кода и профиль ===")
    reset()
    calls.clear()
    reply("userinfo", sub="google-uid-1", email="user@gmail.com", name="Vadim G", picture="https://pic/g.png")
    reply("token", access_token="at-google-123", refresh_token="rt-google-456",
          expires_in=3599, token_type="Bearer", scope="drive.file")
    orig_client = httpx.AsyncClient

    def mock_client(*a, **kw):
        kw.pop("transport", None)
        return orig_client(*a, transport=httpx.MockTransport(handler), **kw)

    httpx.AsyncClient = mock_client
    prov = oauth.get_provider("google")
    payload = await prov.exchange("CODE")
    check("access_token получен", payload["access_token"] == "at-google-123")
    check("refresh_token получен", bool(payload["refresh_token"]))
    check("scope сохранён", payload.get("scope") == "drive.file")
    check("code отправлен grant_type", "grant_type=authorization_code" in bodies()[0], bodies()[0])
    check("code передаётся", "code=CODE" in bodies()[0], bodies()[0])
    check("redirect_uri в теле", "redirect_uri=" in bodies()[0], bodies()[0])
    info = await prov.fetch_user(payload["access_token"])
    check("профиль получен", info.id == "google-uid-1" and info.email == "user@gmail.com", info)
    check("имя получено", info.name == "Vadim G")
    check("Authorization: Bearer", any("at-google-123" in str(h) for _, _, h in calls))

    print("\n=== 4. Discord: профиль, аватар .png и .gif ===")
    reset()
    reply("/users/@me", id="2222", username="vadim", global_name="Vadim D", avatar="abc123", email="d@x.io")
    d = oauth.get_provider("discord")
    reply_raw("token", httpx.Response(200, json={"access_token": "at-d", "refresh_token": "rt-d", "expires_in": 3600}))
    dinfo = await d.fetch_user("at-d")
    check("discord id", dinfo.id == "2222")
    check("discord имя", dinfo.name == "Vadim D")
    check("email получен", dinfo.email == "d@x.io")
    check("avatar .png", dinfo.avatar.endswith(".png?size=256"), dinfo.avatar)
    check("avatar url cdn", "cdn.discordapp.com/avatars/2222/abc123" in dinfo.avatar, dinfo.avatar)
    reset()
    reply("/users/@me", id="3333", username="gifuser", global_name="Gif", avatar="a_xyz")
    ginfo = await d.fetch_user("at-d")
    check("animated avatar .gif", ginfo.avatar.endswith(".gif?size=256"), ginfo.avatar)
    reset()
    reply("/users/@me", id="4444", username="noav")
    ninfo = await d.fetch_user("at-d")
    check("без аватара — пусто", ninfo.avatar == "", ninfo.avatar)
    check("без global_name берётся username", ninfo.name == "noav", ninfo.name)

    print("\n=== 5. GitHub: primary email и fallback ===")
    reset()
    reply_raw("api.github.com/user/emails", httpx.Response(200, json=[
        {"email": "old@x.io", "primary": False, "verified": True},
        {"email": "main@x.io", "primary": True, "verified": True},
    ]))
    reply("api.github.com/user",
          id=4444, login="vadim-dev", name="Vadim", avatar_url="https://av/gh.png", email=None)
    g = oauth.get_provider("github")
    ginfo = await g.fetch_user("at-g")
    check("github id строкой", ginfo.id == "4444", ginfo.id)
    check("primary email выбран", ginfo.email == "main@x.io", ginfo.email)
    check("login получен", ginfo.login == "vadim-dev")
    check("avatar_url получен", ginfo.avatar == "https://av/gh.png")
    reset()
    reply("api.github.com/user",
          id=5555, login="lone", name=None, avatar_url="", email="direct@x.io")
    g2info = await g.fetch_user("at-g")
    check("email из профиля без запроса /emails", g2info.email == "direct@x.io", g2info.email)
    check("name=None -> login", g2info.name == "lone", g2info.name)

    print("\n=== 6. Яндекс: профиль и refresh_token ===")
    reset()
    reply("login.yandex.ru/info", id="5555", login="ya-user",
          display_name="Яндекс Юзер", default_email="y@y.ru")
    y = oauth.get_provider("yandex")
    yinfo = await y.fetch_user("at-y")
    check("яндекс профиль", yinfo.id == "5555" and yinfo.email == "y@y.ru", yinfo)
    check("имя из display_name", yinfo.name == "Яндекс Юзер", yinfo.name)
    calls.clear()
    reply("token", access_token="at-y-new", refresh_token="rt-y-2")
    refreshed = await y.refresh("rt-y")
    check("access_token обновлён", refreshed["access_token"] == "at-y-new", refreshed)
    check("использован grant_type=refresh_token", "grant_type=refresh_token" in bodies()[-1], bodies()[-1])
    check("refresh передаётся как grant_token", "grant_token=rt-y" in bodies()[-1], bodies()[-1])

    print("\n=== 7. Ошибки обмена и профиля ===")
    reset()
    reply_raw("token", httpx.Response(400, json={"error": "invalid_grant"}))
    try:
        await oauth.get_provider("google").exchange("BAD")
        raised = False
    except RuntimeError as e:
        raised = "token error 400" in str(e)
    check("ошибка обмена обработана", raised)
    reset()
    reply_raw("userinfo", httpx.Response(401, json={"error": "invalid_token"}))
    try:
        await oauth.get_provider("google").fetch_user("bad")
        raised = False
    except httpx.HTTPStatusError:
        raised = True
    check("401 при профиле поднимается", raised)

    print("\n=== 8. Привязка аккаунта к пользователю ===")
    await init_db()
    async with session_scope() as s:
        u = await upsert_tg_user(s, tg_id=777, username="vadim", full_name="Vadim")
        uid = u.id
    async with session_scope() as s:
        u = await get_by_tg(s, 777)
        # у другого юзера уже есть google — проверяем, что null не конфликтует
        await upsert_tg_user(s, tg_id=999, username="other")
        other = await get_by_tg(s, 999)
        await link_account(s, other, "github", {"access_token": "other-gh"}, None)
        acc = await link_account(
            s, u, "google",
            {"access_token": "at-plain", "refresh_token": "rt-plain", "expires_in": 3600, "scope": "drive.file"},
            info,
        )
        check("аккаунт создан", bool(acc.id))
    async with session_scope() as s:
        acc = await get_account(s, uid, "google")
        check("токен зашифрован в БД", acc.token.access_token != "at-plain", acc.token.access_token[:24])
        check("дешифруется обратно", decrypt(acc.token.access_token) == "at-plain")
        check("refresh тоже зашифрован", decrypt(acc.token.refresh_token) == "rt-plain")
        check("provider_user_id сохранён", acc.provider_user_id == "google-uid-1")
        check("login сохранён", acc.login == "user@gmail.com", acc.login)
        check("expires_at вычислен", acc.token.expires_at and acc.token.expires_at > time.time())
        check("email подхватился в юзера", (await get_by_tg(s, 777)).email == "user@gmail.com")
        check("имя из Telegram не затирается", (await get_by_tg(s, 777)).full_name == "Vadim")

    print("\n=== 8b. Несколько аккаунтов без provider_user_id ===")
    async with session_scope() as s:
        accs = await get_accounts(s, (await get_by_tg(s, 999)).id)
    check("второй юзер с пустым uid создан", len(accs) == 1, len(accs))
    check("provider_user_id = NULL", accs[0].provider_user_id is None, accs[0].provider_user_id)
    async with session_scope() as s:
        other = await get_by_tg(s, 999)
        await link_account(s, other, "discord", {"access_token": "x"}, None)
    async with session_scope() as s:
        accs = await get_accounts(s, (await get_by_tg(s, 999)).id)
    check("два null-аккаунта уживаются", len(accs) == 2, len(accs))
    async with session_scope() as s:
        accs = await get_accounts(s, uid)
    check("аккаунты первого юзера не пострадали", len(accs) == 1, [a.provider for a in accs])

    print("\n=== 9. Автообновление протухшего токена ===")
    reset()
    reply("token", access_token="at-refreshed", expires_in=3600)
    async with session_scope() as s:
        acc = await get_account(s, uid, "google")
        acc.token.expires_at = int(time.time()) - 10
    async with session_scope() as s:
        pair = await get_valid_access_token(s, uid, "google")
    check("токен обновлён прозрачно", pair and pair[0] == "at-refreshed", pair)
    async with session_scope() as s:
        acc = await get_account(s, uid, "google")
        check("новый токен сохранён", decrypt(acc.token.access_token) == "at-refreshed")
        check("refresh_token не потерян при обновлении", decrypt(acc.token.refresh_token) == "rt-plain")
        check("новый срок проставлен", acc.token.expires_at > time.time() + 3000)

    print("\n=== 10. Свежий токен не обновляется ===")
    reset()
    calls.clear()
    reply("token", access_token="should-not-be-used", expires_in=3600)
    async with session_scope() as s:
        pair = await get_valid_access_token(s, uid, "google")
    check("токен не трогается", pair and pair[0] == "at-refreshed", pair)
    check("запросов к token-endpoint не было", not any("token" in u for _, u, _ in calls), calls)

    print("\n=== 11. Нет доступа ===")
    async with session_scope() as s:
        await upsert_tg_user(s, tg_id=888, username="guest")
        uid2 = (await get_by_tg(s, 888)).id
    async with session_scope() as s:
        pair = await get_valid_access_token(s, uid2, "google")
    check("без привязки -> None", pair is None)
    async with session_scope() as s:
        accs = await get_accounts(s, uid2)
    check("список аккаунтов пуст", accs == [])

    print("\n=== 12. Несколько провайдеров на одном юзере ===")
    for p, payload in [
        ("github", {"access_token": "gh", "expires_in": None}),
        ("discord", {"access_token": "dc", "expires_in": 3600}),
        ("yandex", {"access_token": "ya", "refresh_token": "rty", "expires_in": 3600}),
    ]:
        async with session_scope() as s:
            uu = await get_by_tg(s, 777)
            await link_account(s, uu, p, payload, None)
    async with session_scope() as s:
        accs = await get_accounts(s, uid)
    providers_now = sorted(a.provider for a in accs)
    check("четыре аккаунта привязано", len(accs) == 4, providers_now)
    check("провайдеры уникальны", len(set(providers_now)) == 4, providers_now)
    async with session_scope() as s:
        check("токен яндекса доступен", (await get_valid_access_token(s, uid, "yandex"))[0] == "ya")
    async with session_scope() as s:
        pair = await get_valid_access_token(s, uid, "github")
    check("токен github доступен", pair and pair[0] == "gh", pair)
    check("github без срока и refresh не падает", pair is not None)

    print("\n=== 13. Повторная привязка того же провайдера ===")
    async with session_scope() as s:
        uu = await get_by_tg(s, 777)
        await link_account(s, uu, "github", {"access_token": "gh2", "refresh_token": "ghr", "expires_in": 3600}, None)
    async with session_scope() as s:
        accs = await get_accounts(s, uid)
    check("дубль не создан", len(accs) == 4, sorted(a.provider for a in accs))
    async with session_scope() as s:
        check("токен обновлён на новый", (await get_valid_access_token(s, uid, "github"))[0] == "gh2")

    print("\n=== 14. Отвязка и revoke ===")
    reset()
    calls.clear()
    reply("oauth2.googleapis.com/revoke", **{})
    async with session_scope() as s:
        removed = await unlink_account(s, uid, "google")
    check("аккаунт отвязан", removed is True)
    async with session_scope() as s:
        accs = await get_accounts(s, uid)
    left = sorted(a.provider for a in accs)
    check("google исчез из списка", left == ["discord", "github", "yandex"], left)
    check("revoke вызван у провайдера", any("revoke" in u for _, u, _ in calls), calls)
    async with session_scope() as s:
        check("повторная отвязка -> False", await unlink_account(s, uid, "google") is False)

    print("\n=== 15. Протухший токен: с refresh и без ===")
    async with session_scope() as s:
        acc = await get_account(s, uid, "github")
        acc.token.expires_at = int(time.time()) - 999
    reset()
    calls.clear()
    reply("token", access_token="gh-refreshed", expires_in=3600)
    async with session_scope() as s:
        pair = await get_valid_access_token(s, uid, "github")
    check("github обновлён через refresh", pair and pair[0] == "gh-refreshed", pair)
    check("refresh действительно вызван",
          len([u for m, u, _ in calls if m == "POST"]) == 1
          and "access_token" in [u for m, u, _ in calls if m == "POST"][0], calls)

    reset()
    calls.clear()
    reply("token", access_token="never", expires_in=3600)
    async with session_scope() as s:
        acc = await get_account(s, uid, "discord")
        acc.token.refresh_token = ""
        acc.token.expires_at = int(time.time()) - 999
    async with session_scope() as s:
        pair = await get_valid_access_token(s, uid, "discord")
    check("без refresh вернулся протухший", pair and pair[0] == "dc", pair)
    check("refresh не вызывался", not any("token" in u for _, u, _ in calls), calls)

    httpx.AsyncClient = orig_client
    print("\n" + "=" * 46)
    for f in failures:
        print("FAILED: " + f)
    print(f"TOTAL: {ok} passed, {fail} failed")
    print("=" * 46)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
