"""Проверка .gitignore и .dockerignore на вложенных путях.

Имитирует правила движков git/docker: паттерн без ведущего слэша
совпадает с каталогом на любой глубине, поэтому `storage/` съедает
пакет `app/storage/`. С ведущим слэшем - только корень.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent

MUST_INCLUDE = [
    "app/storage/__init__.py",
    "app/storage/base.py",
    "app/storage/factory.py",
    "app/storage/gdrive.py",
    "app/storage/local.py",
    "app/storage/ydisk.py",
    "app/web/templates/base.html",
    "app/web/static/style.css",
    "app/bot/__init__.py",
    "app/db/session.py",
    "requirements.txt",
    "Dockerfile",
]

MUST_EXCLUDE = [
    ".env",
    "out.txt",
    "res.txt",
    "data/users.db",
    "storage/some/file.bin",
]


def parse_rules(name: str) -> list[tuple[str, bool, bool]]:
    """Возвращает (паттерн, только_корень, отрицание)."""
    rules = []
    for raw in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        anchored = line.startswith("/")
        rules.append((line.lstrip("/"), anchored, negated))
    return rules


def to_regex(pattern: str, anchored: bool) -> re.Pattern:
    """Собирает регулярку по сегментам пути.

    Паттерн-каталог (`storage/`) должен совпадать и с самим каталогом,
    и со всем, что внутри. Без ведущего слэша совпадение ищется
    на любой глубине, иначе только от корня контекста.
    """
    is_dir = pattern.endswith("/")
    segments = pattern.strip("/").split("/")
    built = []
    for seg in segments:
        piece = []
        for ch in seg:
            piece.append("[^/]*" if ch == "*" else re.escape(ch))
        built.append("".join(piece))
    body = "/".join(built)
    prefix = "^" if anchored else "^(?:.*/)?"
    tail = "(?:/.*)?$" if is_dir else "$"
    return re.compile(prefix + body + tail)


def ignored(path: str, rules) -> bool:
    result = False
    for pattern, anchored, negated in rules:
        if to_regex(pattern, anchored).match(path):
            result = not negated
    return result


def git_says(path: str) -> bool:
    """Реальная проверка через git check-ignore."""
    r = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", path],
        cwd=ROOT, capture_output=True,
    )
    return r.returncode == 0


failures: list[str] = []

git_rules = parse_rules(".gitignore")
docker_rules = parse_rules(".dockerignore")

for path in MUST_INCLUDE:
    for label, rules in ((".gitignore", git_rules), (".dockerignore", docker_rules)):
        if ignored(path, rules):
            failures.append(f"{label} исключает нужный файл {path}")

for path in MUST_EXCLUDE:
    for label, rules in ((".gitignore", git_rules), (".dockerignore", docker_rules)):
        if not ignored(path, rules):
            failures.append(f"{label} НЕ исключает {path}")

for path in ("app/storage/base.py", ".env", "out.txt"):
    if git_says(path) != ignored(path, git_rules):
        failures.append(f"расхождение git check-ignore для {path}")

for path in MUST_INCLUDE:
    if not (ROOT / path).is_file():
        failures.append(f"файл отсутствует на диске: {path}")

# Ловушка, из-за которой пропал app/storage: паттерн-каталог без
# ведущего слэша совпадает с тем же именем на любой глубине.
# Если этот контроль перестанет срабатывать, тест выше станет пустым.
if not ignored("app/storage/base.py", [("storage/", False, False)]):
    failures.append("матчер не воспроизводит ловушку storage/ -> app/storage/")
if ignored("app/storage/base.py", [("/storage/", True, False)]):
    failures.append("anchored /storage/ всё ещё задевает app/storage/")

print("=== проверка ignore-правил ===")
for path in MUST_INCLUDE:
    g = "ok" if not ignored(path, git_rules) else "ВЫПАЛ"
    d = "ok" if not ignored(path, docker_rules) else "ВЫПАЛ"
    print(f"  {g:>5} git / {d:<5} docker   {path}")
print("--- должны исключаться ---")
for path in MUST_EXCLUDE:
    g = "ok" if ignored(path, git_rules) else "ПРОПУЩЕН"
    d = "ok" if ignored(path, docker_rules) else "ПРОПУЩЕН"
    print(f"  {g:>5} git / {d:<5} docker   {path}")

print("-" * 50)
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print(f"OK: правил проверено {len(MUST_INCLUDE) + len(MUST_EXCLUDE)}, ошибок 0")
