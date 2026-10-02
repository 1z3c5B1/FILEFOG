"""Проверка выбора базы: SQLite по умолчанию, Postgres по DATABASE_URL.

Ошибка здесь стоит дорого: если URL не распарсится, приложение не стартует
на Render вообще. Если распарсится не тот драйвер - упадёт на первом же
запросе. Проверяем и то, и другое, без подключения к настоящей базе.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db.session import build_engine_url, is_sqlite  # noqa: E402

ok = 0
fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


DB = "./data/trambot.db"

print("=== 1. Без DATABASE_URL остаётся SQLite ===")
u = build_engine_url("", DB)
check("пусто -> sqlite", u == f"sqlite+aiosqlite:///{DB}", u)
check("это sqlite", is_sqlite(u))

u = build_engine_url("   ", DB)
check("пробелы -> sqlite", is_sqlite(u), u)

print("\n=== 2. Схемы, которые дают провайдеры ===")
cases = [
    (
        "postgres://u:p@host/db",
        "postgresql+asyncpg://u:p@host/db",
        "старая схема postgres://",
    ),
    (
        "postgresql://u:p@host/db",
        "postgresql+asyncpg://u:p@host/db",
        "схема postgresql:// без драйвера",
    ),
    (
        "postgresql+psycopg2://u:p@host/db",
        "postgresql+asyncpg://u:p@host/db",
        "синхронный драйвер заменён",
    ),
    (
        "postgresql+asyncpg://u:p@host/db",
        "postgresql+asyncpg://u:p@host/db",
        "уже готовый asyncpg",
    ),
    (
        "postgres://u:p@host/db?sslmode=require",
        "postgresql+asyncpg://u:p@host/db?sslmode=require",
        "параметры сохранились",
    ),
]
for raw, want, label in cases:
    got = build_engine_url(raw, DB)
    check(label, got == want, f"получено {got}")

print("\n=== 3. Режим определяется верно ===")
check("postgres не sqlite", not is_sqlite(build_engine_url("postgres://a/b", DB)))
check("sqlite помечен как sqlite", is_sqlite(build_engine_url("", DB)))
check(
    "sqlite+aiosqlite:// не ломается",
    build_engine_url("sqlite+aiosqlite:///./x.db", DB) == "sqlite+aiosqlite:///./x.db",
)

print("\n=== 4. Неизвестная схема не молча ломается ===")
got = build_engine_url("mysql://u:p@host/db", DB)
check("mysql не превращён в postgres", got == "mysql://u:p@host/db", got)
check("mysql не считается sqlite", not is_sqlite(got))

print("\n=== 5. Пароли со спецсимволами не ломаются ===")
tricky = "postgres://user:p%40ss%3Aword@host:5432/db?sslmode=require&channel_binding=require"
got = build_engine_url(tricky, DB)
check("схема заменена", got.startswith("postgresql+asyncpg://"), got)
check("хост и порт на месте", "@host:5432/db" in got, got)
check("оба параметра на месте", "sslmode=require" in got and "channel_binding=require" in got, got)

print("\n=== 6. Универсальный случай Neon ===")
neon = "postgresql://epic-name-123456/a1b2c3d4?sslmode=require"
got = build_engine_url(neon, DB)
check("Neon-ссылка принимается", got == "postgresql+asyncpg://epic-name-123456/a1b2c3d4?sslmode=require", got)

print(f"\n{'=' * 46}\nИТОГО: {ok} успешно, {fail} провалено\n{'=' * 46}")
raise SystemExit(1 if fail else 0)
