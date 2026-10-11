"""API keys: parsing, and constant-time resolution to a tenant scope."""
from __future__ import annotations

import hmac

import pytest

from stt_platform.api.security import parse_api_keys, resolve_scope
from stt_platform.domain.errors import Unauthorized


def test_parses_tenant_key_pairs():
    mapping = parse_api_keys("acme:key-a,globex:key-b")
    assert mapping == {"key-a": "acme", "key-b": "globex"}


def test_skips_malformed_entries_and_whitespace():
    mapping = parse_api_keys(" acme:key-a , broken , :nokey , noscope: ,, ")
    assert mapping == {"key-a": "acme"}


def test_empty_configuration_means_no_keys():
    assert parse_api_keys("") == {}
    assert parse_api_keys(None) == {}


def test_later_entries_win_when_a_key_is_reused():
    mapping = parse_api_keys("acme:shared,globex:shared")
    assert mapping == {"shared": "globex"}


def test_resolves_a_known_key_to_its_tenant():
    keys = parse_api_keys("acme:key-a")
    assert resolve_scope("key-a", keys) == "acme"


@pytest.mark.parametrize("presented", [None, "", "wrong", "key-a "])
def test_unknown_or_missing_keys_are_rejected(presented):
    keys = parse_api_keys("acme:key-a")
    with pytest.raises(Unauthorized):
        resolve_scope(presented, keys)


def test_comparison_is_constant_time(monkeypatch):
    """A timing oracle would let an attacker recover keys character by character."""
    calls: list[tuple[str, str]] = []
    original = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    keys = parse_api_keys("acme:key-a")
    assert resolve_scope("key-a", keys) == "acme"
    assert calls, "keys must be compared with hmac.compare_digest"
