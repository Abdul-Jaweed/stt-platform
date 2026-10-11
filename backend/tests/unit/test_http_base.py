"""Unit tests for the shared managed-provider HTTP client.

No network: every call is intercepted by respx. The client must never
leak the API key into errors, must map status codes onto the retryable /
permanent ProviderError split, and must enforce a response-size guard.
"""
import threading
import time

import httpx
import pytest
import respx

from stt_platform.domain.errors import ProviderError
from stt_platform.providers.managed.http_base import ManagedHttpClient

API_KEY = "secret-key-123"


def make_client(**overrides) -> ManagedHttpClient:
    kwargs = dict(
        base_url="https://api.example.com",
        api_key=API_KEY,
        auth_header="Authorization",
        auth_scheme="Token",
        timeout_s=5.0,
        connect_timeout_s=2.0,
        max_response_bytes=1024,
    )
    kwargs.update(overrides)
    return ManagedHttpClient(**kwargs)


@respx.mock
def test_auth_header_is_attached_and_key_never_leaks():
    route = respx.post("https://api.example.com/v1/thing").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = make_client()

    assert client.request("POST", "/v1/thing", json={"a": 1}) == {"ok": True}
    assert route.calls.last.request.headers["authorization"] == f"Token {API_KEY}"

    # A vendor echoing the key back in an error body must not leak it.
    route.mock(return_value=httpx.Response(401, text=f"bad key {API_KEY}"))
    with pytest.raises(ProviderError) as err:
        client.request("POST", "/v1/thing")
    assert API_KEY not in str(err.value)
    assert err.value.code == "AUTH_ERROR"
    assert err.value.retryable is False


@respx.mock
def test_raw_scheme_sends_key_without_prefix():
    route = respx.post("https://api.example.com/v1/thing").mock(
        return_value=httpx.Response(200, json={})
    )
    client = make_client(auth_header="xi-api-key", auth_scheme=None)

    client.request("POST", "/v1/thing")

    assert route.calls.last.request.headers["xi-api-key"] == API_KEY


@respx.mock
def test_429_is_retryable_rate_limited():
    respx.get("https://api.example.com/v1/thing").mock(
        return_value=httpx.Response(429, json={"error": "slow down"})
    )
    with pytest.raises(ProviderError) as err:
        make_client().request("GET", "/v1/thing")
    assert err.value.code == "RATE_LIMITED"
    assert err.value.retryable is True


@respx.mock
def test_5xx_retryable_4xx_permanent():
    for status, retryable in ((503, True), (500, True), (404, False), (422, False)):
        respx.get("https://api.example.com/v1/thing").mock(
            return_value=httpx.Response(status, text="nope")
        )
        with pytest.raises(ProviderError) as err:
            make_client().request("GET", "/v1/thing")
        assert err.value.retryable is retryable, status


@respx.mock
def test_network_error_and_timeout_are_retryable():
    respx.get("https://api.example.com/v1/thing").mock(
        side_effect=httpx.ConnectError("boom")
    )
    with pytest.raises(ProviderError) as err:
        make_client().request("GET", "/v1/thing")
    assert err.value.code == "NETWORK_ERROR"
    assert err.value.retryable is True

    respx.get("https://api.example.com/v1/thing").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    with pytest.raises(ProviderError) as err:
        make_client().request("GET", "/v1/thing")
    assert err.value.code == "TIMEOUT"
    assert err.value.retryable is True


@respx.mock
def test_oversized_response_is_rejected():
    respx.get("https://api.example.com/v1/thing").mock(
        return_value=httpx.Response(
            200,
            content=b"x" * 4096,
            headers={"content-type": "application/octet-stream"},
        )
    )
    with pytest.raises(ProviderError) as err:
        make_client(max_response_bytes=1024).request("GET", "/v1/thing")
    assert err.value.code == "RESPONSE_TOO_LARGE"
    assert err.value.retryable is False


@respx.mock
def test_invalid_json_is_permanent_malformed_response():
    respx.get("https://api.example.com/v1/thing").mock(
        return_value=httpx.Response(
            200, content=b"{not json", headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(ProviderError) as err:
        make_client().request("GET", "/v1/thing")
    assert err.value.code == "MALFORMED_RESPONSE"
    assert err.value.retryable is False


@respx.mock
def test_binary_response_is_returned_as_bytes():
    payload = b"\x00\x01\x02"
    respx.get("https://api.example.com/v1/file").mock(
        return_value=httpx.Response(
            200, content=payload, headers={"content-type": "application/octet-stream"}
        )
    )
    assert make_client().request("GET", "/v1/file") == payload


@respx.mock
def test_managed_semaphore_caps_in_flight_requests():
    semaphore = threading.BoundedSemaphore(2)
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow(_request):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.2)
        with lock:
            state["now"] -= 1
        return httpx.Response(200, json={"ok": True})

    respx.get("https://api.example.com/v1/thing").mock(side_effect=slow)
    clients = [make_client(semaphore=semaphore) for _ in range(4)]
    threads = [
        threading.Thread(target=lambda c=c: c.request("GET", "/v1/thing"))
        for c in clients
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state["max"] == 2  # cap held and saturated


def test_base_url_override_wins():
    client = make_client(base_url_override="http://127.0.0.1:8765/")
    assert client.base_url == "http://127.0.0.1:8765"
