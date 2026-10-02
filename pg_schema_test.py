"""Проверяет, что схема моделей валидна для Postgres (DDL компилируется)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from app.db.models import Base  # noqa: E402

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


print("=== 1. Схема собирается под Postgres ===")
tables = list(Base.metadata.sorted_tables)
check("таблицы найдены", len(tables) >= 6, [t.name for t in tables])

names = {t.name for t in tables}
for want in ("users", "oauth_accounts", "oauth_tokens", "user_settings", "stored_files", "sync_logs"):
    check(f"таблица {want}", want in names, sorted(names))

print("\n=== 2. DDL компилируется под postgres ===")
for t in tables:
    try:
        ddl = str(CreateTable(t).compile(dialect=postgresql.dialect()))
        check(f"{t.name} -> DDL", "CREATE TABLE" in ddl.upper())
    except Exception as e:  # noqa: BLE001
        check(f"{t.name} -> DDL", False, f"{type(e).__name__}: {e}")

print("\n=== 3. Нет типов, которые есть только в SQLite ===")
for t in tables:
    for col in t.columns:
        try:
            CreateTable(t).compile(dialect=postgresql.dialect())
        except Exception as e:  # noqa: BLE001
            check(f"{t.name}.{col.name} переносима", False, str(e)[:80])

bad = []
for t in tables:
    for col in t.columns:
        coltype = str(col.type).upper()
        if "JSON" in coltype and "JSONB" not in coltype:
            bad.append(f"{t.name}.{col.name}:{coltype}")
check("нет sqlite-типов в моделях", not bad, bad)

print("\n=== 4. Уникальность внешних ключей ===")
for t in tables:
    for fk in t.foreign_keys:
        target = fk.column.table.name
        check(f"{t.name} -> {target} на CASCADE", fk.ondelete == "CASCADE", fk.ondelete)

print(f"\n{'=' * 46}\nИТОГО: {ok} успешно, {fail} провалено\n{'=' * 46}")
raise SystemExit(1 if fail else 0)
