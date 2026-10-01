"""Проверка сборки бота: роутеры, хендлеры, клавиатуры, i18n, апдейты."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["SECRET_KEY"] = "bot-test-secret"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="tbbot_")
DB = Path(tempfile.gettempdir()) / "trambot_bot_test.db"
for suffix in ("", "-journal", "-wal", "-shm"):
    DB.with_name(DB.name + suffix).unlink(missing_ok=True)
os.environ["DB_PATH"] = str(DB)

from app.bot import COMMANDS, build_dispatcher  # noqa: E402
from app.bot.keyboards import (  # noqa: E402
    accounts_kb,
    back_to_menu,
    files_kb,
    main_menu,
    miniapp_button,
    settings_kb,
    sync_kb,
)
from app.bot.helpers import target_path, usage_line  # noqa: E402
from app.core import user_settings as us  # noqa: E402
from app.core.i18n import tr  # noqa: E402
from app.core.users import upsert_tg_user  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.storage.base import RemoteFile  # noqa: E402
from app.storage.factory import local_storage  # noqa: E402

ok = 0
fail = 0
failures: list[str] = []


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail += 1
        failures.append(f"{name} {extra}".strip())
        print(f"  FAIL {name} {extra}")


def flat(kb) -> list[str]:
    return [b.text for row in kb.inline_keyboard for b in row]


def flat_cb(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]


async def main():
    print("\n=== 1. Команды бота ===")
    names = [c.command for c in COMMANDS]
    for need in ("start", "menu", "files", "upload", "get", "all", "mkdir", "del",
                 "accounts", "link", "unlink", "sync", "syncstatus", "settings", "set",
                 "stats", "panel", "help"):
        check(f"команда /{need} зарегистрирована", need in names)
    check("команды уникальны", len(names) == len(set(names)), names)
    check("у всех есть описание", all(c.description for c in COMMANDS))
    check("нет /startmenu опечатки", "startmenu" not in names)

    print("\n=== 2. Роутеры и хендлеры ===")
    dp = build_dispatcher()
    routers = {r.name: r for r in dp.sub_routers}
    check("4 роутера подключены", len(routers) == 4, list(routers))
    for name in ("core", "files", "settings", "sync"):
        check(f"роутер {name} есть", name in routers)
    for name, r in routers.items():
        check(f"{name}: есть message-хендлеры", len(r.message.handlers) > 0, len(r.message.handlers))
        check(f"{name}: есть callback-хендлеры", len(r.callback_query.handlers) > 0, len(r.callback_query.handlers))
    total = sum(len(r.message.handlers) + len(r.callback_query.handlers) for r in routers.values())
    check("всего хендлеров >= 25", total >= 25, total)

    print("\n=== 3. Клавиатуры ===")
    for loc in ("ru", "en"):
        kb = main_menu(loc)
        # 7 действий + кнопка мини-аппа (если включена)
        app_btn = miniapp_button()
        expect = 7 + (1 if app_btn else 0)
        check(f"меню {loc}: {expect} кнопок", len(flat(kb)) == expect, flat(kb))
        check(f"меню {loc}: есть callback-данные", all(flat_cb(kb)), flat_cb(kb))
        check(f"меню {loc}: кнопки локализованы", all(tr(k, loc) in " ".join(flat(kb))
              for k in ("files", "settings", "sync", "stats")))
        check(f"меню {loc}: кнопка мини-аппа", bool(app_btn) == any(b.web_app for row in kb.inline_keyboard for b in row))
        check(f"меню {loc}: мини-апп первая", bool(kb.inline_keyboard[0][0].web_app) == bool(app_btn))
        check(f"назад {loc}", flat(back_to_menu(loc)) == [tr("back", loc)])
        acc = accounts_kb({"google"}, loc)
        check(f"аккаунты {loc}: 4 строки провайдеров + назад", len(flat(acc)) == 6, flat(acc))
        check(f"аккаунты {loc}: привязанный отмечен", any("Google" in t and "✅" in t for t in flat(acc)))
        check(f"аккаунты {loc}: google даёт инфо и отвязку",
              "acc:info:google" in flat_cb(acc) and "acc:unlink:google" in flat_cb(acc), flat_cb(acc))
        check(f"аккаунты {loc}: остальные только логин",
              all(f"acc:login:{p}" in flat_cb(acc) for p in ("github", "discord", "yandex")), flat_cb(acc))
        check(f"аккаунты {loc}: одна галочка", sum("✅" in t for t in flat(acc)) == 1, flat(acc))
        empty = accounts_kb(set(), loc)
        check(f"аккаунты {loc}: без привязок нет unlink",
              not any("acc:unlink:" in c for c in flat_cb(empty)), flat_cb(empty))
        check(f"аккаунты {loc}: без привязок 4 логина",
              sum(1 for c in flat_cb(empty) if c.startswith("acc:login:")) == 4, flat_cb(empty))

    print("\n=== 4. Клавиатура настроек ===")
    await init_db()
    async with session_scope() as s:
        u = await upsert_tg_user(s, tg_id=5150, username="botuser", full_name="Bot User")
        uid = u.id
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    root = settings_kb(prefs, "ru")
    sections = [d for d in us.SETTINGS if d.section]
    check("корень: 5 разделов + назад", len(flat(root)) == 6, flat(root))
    check("корень: callback на каждый раздел",
          all(f"set:sec:{s}" in flat_cb(root) for s in ("general", "files", "sync", "notify", "security")))
    check("разделы покрыты настройками", {d.section for d in sections} >=
          {"general", "files", "sync", "notify", "security"}, sorted({d.section for d in sections}))
    for sec in ("general", "files", "sync", "notify", "security"):
        kb = settings_kb(prefs, "ru", sec)
        defs = [d for d in us.SETTINGS if d.section == sec]
        check(f"раздел {sec}: все параметры", len(flat(kb)) == len(defs) + 2, (len(flat(kb)), len(defs)))
        check(f"раздел {sec}: callback на каждый", all(f"set:val:{d.key}" in flat_cb(kb) for d in defs))
        check(f"раздел {sec}: кнопки обрезаны до 60 символов", all(len(t) <= 64 for t in flat(kb)),
              [t for t in flat(kb) if len(t) > 64])
    sync_kb_s = settings_kb(prefs, "ru", "sync")
    bools = {d.key for d in us.SETTINGS if d.type == "bool"}
    check("bool показан галочкой ✅/❌",
          any(("✅" if prefs.get(k) else "❌") in t for k in bools for t in flat(sync_kb_s)))
    check("int показан значением", any("60" in t for t in flat(sync_kb_s)), flat(sync_kb_s))
    choice_keys = [d for d in us.SETTINGS if d.type == "choice" and d.section == "sync"]
    if choice_keys:
        d = choice_keys[0]
        expect = d.label(prefs.get(d.key, d.default), "ru")
        check("choice показан лейблом, а не dict", expect and expect in " ".join(flat(sync_kb_s)),
              expect)
        check("в кнопке choice нет repr(dict)", "{" not in " ".join(flat(sync_kb_s)),
              [t for t in flat(sync_kb_s) if "{" in t])
    check("назад из раздела ведёт в меню настроек", "menu:settings" in flat_cb(sync_kb_s))
    check("закрыть ведёт в главное меню", "menu" in flat_cb(sync_kb_s))

    print("\n=== 5. Клавиатура файлов и синка ===")
    items = [
        RemoteFile(path="Uploads", size=0, mime="inode/directory"),
        RemoteFile(path="a.txt", size=10, mime="text/plain"),
        RemoteFile(path="b/c.pdf", size=20, mime="application/pdf"),
    ]
    fk = files_kb(items, "ru")
    check("файлы: 3 объекта + действия", len(flat(fk)) == 3 + 3, flat(fk))
    check("файлы: путь в callback", "fs:open:a.txt" in flat_cb(fk))
    check("файлы: вложенный путь сохраняется целиком", "fs:open:b/c.pdf" in flat_cb(fk))
    check("файлы: имя без пути", any(t.endswith("a.txt") for t in flat(fk)), flat(fk))
    check("файлы: папка отмечена иконкой", any("\U0001f4c1" in t for t in flat(fk)), flat(fk))
    check("файлы: есть ZIP", any("ZIP" in t for t in flat(fk)), flat(fk))
    check("файлы: длинный список обрезан до 20", len(flat(files_kb(
        [RemoteFile(path=f"f{i}.txt", size=1) for i in range(50)], "ru"))) == 20 + 3)
    sk = sync_kb({"google": object()}, prefs, "ru")
    check("синк: два провайдера", len(flat(sk)) == 3, flat(sk))
    check("синк: google отмечен", any("Google Drive" in t and "✅" in t for t in flat(sk)))
    check("синк: yandex не отмечен", any("Яндекс" in t and "✅" not in t for t in flat(sk)))
    sk2 = sync_kb({}, prefs, "ru")
    check("синк: без привязок ни одной галочки", not any("✅" in t for t in flat(sk2)), flat(sk2))

    print("\n=== 6. Путь загрузки по настройкам ===")
    p = dict(prefs)
    p["uploads_folder"] = "Uploads"
    p["organize_by_type"] = False
    check("без сортировки: в папку загрузок", target_path(p, "photo.jpg") == "Uploads/photo.jpg",
          target_path(p, "photo.jpg"))
    p["organize_by_type"] = True
    check("фото -> photos/", target_path(p, "a.jpg") == "Uploads/photos/a.jpg", target_path(p, "a.jpg"))
    check("видео -> videos/", target_path(p, "a.mp4") == "Uploads/videos/a.mp4", target_path(p, "a.mp4"))
    check("аудио -> audio/", target_path(p, "a.mp3") == "Uploads/audio/a.mp3", target_path(p, "a.mp3"))
    check("прочее -> docs/", target_path(p, "a.pdf") == "Uploads/docs/a.pdf", target_path(p, "a.pdf"))
    p["uploads_folder"] = "Мои файлы"
    check("своя папка уважается", target_path(p, "a.pdf").startswith("Мои файлы/"),
          target_path(p, "a.pdf"))
    p["uploads_folder"] = ""
    p["organize_by_type"] = False
    check("пустая настройка -> дефолт Uploads",
          target_path(p, "a.pdf") == "Uploads/a.pdf", target_path(p, "a.pdf"))
    p["uploads_folder"] = "  Docs/Inbox/  "
    check("пробелы и слэши по краям срезаны",
          target_path(p, "a.pdf") == "Docs/Inbox/a.pdf", target_path(p, "a.pdf"))
    p["uploads_folder"] = "Uploads"
    check("сортировка отключена -> плоско", target_path(p, "a.jpg") == "Uploads/a.jpg",
          target_path(p, "a.jpg"))

    print("\n=== 7. Строка использования ===")
    local = local_storage(uid)
    await local.upload("Uploads/x.bin", b"z" * 2048)
    line = await usage_line(uid, p)
    check("показан занятый объём", "2.0 KB" in line, line)
    p_nolimit = dict(p)
    p_nolimit["user_quota_mb"] = 0
    line2 = await usage_line(uid, p_nolimit)
    check("без квоты только занятое", "/" not in line2, line2)
    p_quota = dict(p)
    p_quota["user_quota_mb"] = 10
    check("с квотой показан лимит", "/" in await usage_line(uid, p_quota))

    print("\n=== 8. i18n ===")
    for key in ("menu", "menu_title", "menu_text", "account", "files", "settings",
                "sync", "folder", "stats", "back", "close", "no_files", "saved",
                "quota", "help", "upload", "download", "not_linked", "linked",
                "need_account", "err_generic", "login_choose", "welcome",
                "web_panel", "usage", "free", "sync_done"):
        ru, en = tr(key, "ru"), tr(key, "en")
        check(f"{key}: ru != en и оба непустые", bool(ru) and bool(en) and ru != en, (ru, en))
    check("неизвестный ключ возвращается как есть", tr("no_such_key", "ru") == "no_such_key")
    check("ru переводит подстановку", "50" in tr("file_too_big", "ru", limit=50))
    check("en переводит подстановку", "50 MB" in tr("file_too_big", "en", limit=50))
    check("лишняя подстановка не ломает", "{" not in tr("file_too_big", "ru", unknown=1))

    print("\n=== 9. Роутинг апдейтов ===")

    class FakeCQ:
        """Минимальный объект: хендлеры фильтруют через F.data."""

        def __init__(self, data: str):
            self.data = data
            self.callback_data = data
            self.from_user = None
            self.message = None

        async def answer(self, *a, **kw):
            return None

    def matches(data: str) -> list[str]:
        """Имена хендлеров, чей фильтр ловит такой callback_data."""
        ev = FakeCQ(data)
        hits = []
        for r in routers.values():
            for h in r.callback_query.handlers:
                for f in getattr(h, "filters", None) or ():
                    cb = getattr(f, "callback", None)
                    if cb is None:
                        continue
                    try:
                        res = cb(ev)
                    except Exception:
                        continue
                    if isinstance(res, tuple):
                        res = res[0]
                    if res is True:
                        hits.append(h.callback.__name__)
                        break
        return hits

    for data, label in (
        ("fs:open:a.txt", "открытие файла"),
        ("fs:zipall", "скачать всё архивом"),
        ("fs:zip:a.txt", "zip одного файла"),
        ("menu:upload", "загрузка из меню"),
        ("menu:folders", "папки из меню"),
        ("menu:accounts", "аккаунты из меню"),
        ("menu:settings", "настройки из меню"),
        ("menu:stats", "статистика из меню"),
        ("acc:login:google", "логин провайдера"),
        ("acc:unlink:google", "отвязка провайдера"),
        ("sync:go:google", "запуск синка"),
        ("sync:go:yandex", "запуск синка яндекса"),
        ("set:sec:sync", "раздел настроек"),
        ("set:val:auto_sync", "изменение настройки"),
    ):
        hits = matches(data)
        check(f"{label} ({data}) обрабатывается", bool(hits), data)
    check("неизвестный callback ничем не ловится", not matches("nonsense:xyz"))
    check("menu:unknown не ловится файловым роутером",
          "cb_browse" not in matches("menu:unknown"))

    print("\n" + "=" * 46)
    for f in failures:
        print("FAILED: " + f)
    print(f"TOTAL: {ok} passed, {fail} failed")
    print("=" * 46)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
