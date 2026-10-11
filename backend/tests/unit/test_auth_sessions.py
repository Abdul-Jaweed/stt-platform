"""Password hashing and session tokens: the primitives behind the UI login.

Nothing here touches the database or the API — these are the units the login
endpoint is built from, tested on their own so a failure says exactly which
property broke.
"""
from __future__ import annotations

import pytest

from stt_platform.domain.errors import Unauthorized
from stt_platform.services.auth import (
    SessionTokens,
    hash_password,
    is_weak_password,
    verify_password,
)

SECRET = "unit-test-secret"


def test_password_hash_round_trip():
    stored = hash_password("stt")

    assert verify_password("stt", stored) is True
    assert verify_password("stt ", stored) is False
    assert verify_password("", stored) is False


def test_password_hash_is_salted():
    first, second = hash_password("stt"), hash_password("stt")

    assert first != second, "two hashes of one password must not be identical"
    assert verify_password("stt", first) and verify_password("stt", second)


def test_password_hash_never_contains_the_password():
    stored = hash_password("hunter2")

    assert "hunter2" not in stored
    assert stored.startswith("scrypt$"), stored


def test_verify_password_survives_a_malformed_hash():
    for broken in ("", "nonsense", "scrypt$16384$8$1$only-four-parts"):
        assert verify_password("stt", broken) is False


def test_session_token_round_trip():
    tokens = SessionTokens(SECRET, ttl_s=3600)
    token = tokens.issue("stt", "acme", now=1_000.0)

    claims = tokens.verify(token, now=1_001.0)

    assert claims.username == "stt"
    assert claims.tenant == "acme"
    assert claims.expires_at == pytest.approx(4_600.0)


def test_session_token_rejects_a_tampered_payload():
    tokens = SessionTokens(SECRET, ttl_s=3600)
    token = tokens.issue("stt", "acme", now=1_000.0)
    version, payload, signature = token.split(".")

    forged = f"{version}.{payload[:-4]}AAAA.{signature}"

    with pytest.raises(Unauthorized):
        tokens.verify(forged, now=1_001.0)


def test_session_token_rejects_a_tampered_signature():
    tokens = SessionTokens(SECRET, ttl_s=3600)
    token = tokens.issue("stt", "acme", now=1_000.0)

    with pytest.raises(Unauthorized):
        tokens.verify(token[:-3] + "AAA", now=1_001.0)


def test_session_token_rejects_a_different_secret():
    issued = SessionTokens(SECRET, ttl_s=3600).issue("stt", "acme", now=1_000.0)

    with pytest.raises(Unauthorized):
        SessionTokens("another-secret", ttl_s=3600).verify(issued, now=1_001.0)


def test_session_token_expires():
    tokens = SessionTokens(SECRET, ttl_s=60)
    token = tokens.issue("stt", "acme", now=1_000.0)

    with pytest.raises(Unauthorized):
        tokens.verify(token, now=1_061.0)


def test_session_token_rejects_garbage():
    tokens = SessionTokens(SECRET, ttl_s=3600)

    for junk in ("", "not-a-token", "v1.only-two-parts", "v9.AAAA.BBBB"):
        with pytest.raises(Unauthorized):
            tokens.verify(junk, now=1_001.0)


@pytest.mark.parametrize(
    ("password", "weak"),
    [
        ("stt", True),
        ("", True),
        ("short", True),
        ("elevenchars", True),
        ("twelve-chars", False),
        ("a-long-and-unremarkable-passphrase", False),
    ],
)
def test_weak_password_rule(password, weak):
    assert is_weak_password(password) is weak
