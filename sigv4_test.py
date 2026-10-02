"""Проверка подписи AWS SigV4 по официальным тест-векторам.

Ошибка в подписи даёт 403 от R2/B2, который трудно отличить от неверных
кредов, поэтому сверка идёт с эталоном из документации AWS.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.storage.s3 import build_authorization, canonical_uri, signing_key  # noqa: E402

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


# --- вектор 1: AWS S3 GET Object, "get-vanilla" из docs -------------------
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
STAMP = "20130524T000000Z"

print("=== 1. AWS GET Object (документация AWS) ===")
# В примере AWS для подписи заголовка CanonicalQueryString пустой.
# Строка "?lifecycle=" из документации относится к отдельному примеру
# presigned-URL и в этот эталон не входит.
auth = build_authorization(
    method="GET",
    url_path="/test.txt",
    query="",
    headers={
        "host": "examplebucket.s3.amazonaws.com",
        "x-amz-content-sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "x-amz-date": STAMP,
        "range": "bytes=0-9",
    },
    payload=b"",
    access_key=AWS_KEY,
    secret_key=AWS_SECRET,
    region="us-east-1",
    stamp=STAMP,
)
# эталон из https://docs.aws.amazon.com/AmazonS3/latest/API/sigv4-header-auth.html
EXPECT = (
    "AWS4-HMAC-SHA256 "
    "Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
    "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
    "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
)
check("Authorization совпадает с эталоном AWS", auth == EXPECT)
if auth != EXPECT:
    print(f"     получено: {auth}")
    print(f"     ждём   : {EXPECT}")

print("\n=== 2. Подпись не должна попадать в SignedHeaders ===")
check("authorization исключён", "authorization;" not in auth.split("SignedHeaders=")[1].split(",")[0])

print("\n=== 3. Ключ подписи детерминирован ===")
k1 = signing_key(AWS_SECRET, "20130524", "us-east-1")
k2 = signing_key(AWS_SECRET, "20130524", "us-east-1")
check("одинаковые входы дают одинаковый ключ", k1 == k2)
check("ключ 32 байта", len(k1) == 32, len(k1))
check("разный регион даёт другой ключ", k1 != signing_key(AWS_SECRET, "20130524", "eu-west-1"))
check("разная дата даёт другой ключ", k1 != signing_key(AWS_SECRET, "20130525", "us-east-1"))
check("разный секрет даёт другой ключ", k1 != signing_key("other", "20130524", "us-east-1"))

print("\n=== 4. Подпись меняется при смене тела и пути ===")
base = dict(
    method="PUT",
    url_path="/a.txt",
    query="",
    headers={"host": "b.r2.cloudflarestorage.com", "x-amz-date": STAMP, "x-amz-content-sha256": "x" * 64},
    payload=b"",
    access_key=AWS_KEY,
    secret_key=AWS_SECRET,
    region="auto",
    stamp=STAMP,
)
a1 = build_authorization(**base)
a2 = build_authorization(**{**base, "payload": b"hello"})
a3 = build_authorization(**{**base, "url_path": "/b.txt"})
a4 = build_authorization(**{**base, "query": "list-type=2"})
check("другое тело меняет подпись", a1 != a2)
check("другой путь меняет подпись", a1 != a3)
check("другая query меняет подпись", a1 != a4)
check("заголовок уходит с region=auto", "/auto/s3/aws4_request" in a1, a1)

print("\n=== 5. Экранирование пути ===")
check("пробел экранируется", canonical_uri("/a b.txt") == "/a%20b.txt", canonical_uri("/a b.txt"))
check("слэш сохраняется", canonical_uri("/папка/файл.txt") == "/%D0%BF%D0%B0%D0%BF%D0%BA%D0%B0/%D1%84%D0%B0%D0%B9%D0%BB.txt")
check("юникод ключа безопасен", canonical_uri("/ключ") == "/%D0%BA%D0%BB%D1%8E%D1%87")

print(f"\n{'=' * 46}\nИТОГО: {ok} успешно, {fail} провалено\n{'=' * 46}")
raise SystemExit(1 if fail else 0)
