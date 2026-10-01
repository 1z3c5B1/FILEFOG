"""Прогон всех наборов тестов и сводка."""
import re
import subprocess
import sys

SUITES = [
    "bot_test",
    "smoke_test",
    "sync_test",
    "oauth_test",
    "profile_test",
    "miniapp_test",
    "ignore_rules_test",
]
OK_WORDS = r"(?:passed|\u0443\u0441\u043f\u0435\u0448\u043d\u043e)"
BAD_WORDS = r"(?:failed|\u043f\u0440\u043e\u0432\u0430\u043b\u0435\u043d\u043e)"

total = 0
bad_suites = 0

for name in SUITES:
    r = subprocess.run(
        [sys.executable, name + ".py"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = r.stdout
    m = re.findall(r"(\d+)\s+" + OK_WORDS, out)
    f = re.findall(r"(\d+)\s+" + BAD_WORDS, out)
    ok = int(m[-1]) if m else 0
    bad = int(f[-1]) if f else 0
    total += ok
    good = r.returncode == 0 and bad == 0
    if not good:
        bad_suites += 1
    print(f"{'OK ' if good else 'BAD'} {name:<14} ok={ok:<4} fail={bad} exit={r.returncode}")

print("-" * 46)
print(f"TOTAL: {total}   bad suites: {bad_suites}")
sys.exit(1 if bad_suites else 0)
