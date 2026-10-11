"""Shared HTTP plumbing for managed (cloud) provider adapters.

One place for: auth-header injection, timeouts, error normalization into
the retryable/permanent ``ProviderError`` split, a response-size guard, key
redaction in every message, and an optional semaphore so a burst of
managed calls can never exceed ``managed_concurrency``.

The API key must never leak: response detail is redacted before it is
attached to an error, and request headers are never echoed in messages.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from stt_platform.domain.errors import ProviderError

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_AUTH_STATUS = frozenset({401, 403})
_DETAIL_LIMIT = 500


class ManagedHttpClient:
    """Thin, policy-carrying wrapper over ``httpx.Client``.

    ``request`` returns parsed JSON when the response is JSON and raw
    bytes otherwise (file downloads). All failures raise ``ProviderError``
    so the workflow's retry policy sees the same shape as local failures.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        auth_header: str,
        auth_scheme: str | None = "Token",
        timeout_s: float = 300.0,
        connect_timeout_s: float = 10.0,
        max_response_bytes: int = 25_000_000,
        base_url_override: str | None = None,
        transport: httpx.BaseTransport | None = None,
        semaphore: threading.BoundedSemaphore | None = None,
    ) -> None:
        self.base_url = (base_url_override or base_url).rstrip("/")
        self._api_key = api_key
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme
        self._timeout = httpx.Timeout(timeout_s, connect=connect_timeout_s)
        self._max_response_bytes = max_response_bytes
        self._transport = transport
        self._semaphore = semaphore

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: dict[str, Any] | None = None,
        files: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = (
            path
            if path.startswith(("http://", "https://"))
            else f"{self.base_url}{path}"
        )
        semaphore = self._semaphore
        acquired = False
        if semaphore is not None:
            semaphore.acquire()
            acquired = True
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    files=files,
                    content=content,
                    headers=self._auth_headers(headers),
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "TIMEOUT", f"{method} {path} timed out", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "NETWORK_ERROR",
                f"{method} {path} failed: {type(exc).__name__}",
                retryable=True,
            ) from exc
        finally:
            if acquired and semaphore is not None:
                semaphore.release()
        return self._parse(response, method, path)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _auth_headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            value = (
                self._api_key
                if self._auth_scheme is None
                else f"{self._auth_scheme} {self._api_key}"
            )
            headers[self._auth_header] = value
        if extra:
            headers.update(extra)
        return headers

    def _redact(self, text: str) -> str:
        if self._api_key and self._api_key in text:
            return text.replace(self._api_key, "***")
        return text

    def _parse(self, response: httpx.Response, method: str, path: str) -> Any:
        if response.status_code >= 400:
            self._raise_for_status(response, method, path)

        declared = response.headers.get("content-length")
        if (
            declared is not None
            and declared.isdigit()
            and int(declared) > self._max_response_bytes
        ):
            raise ProviderError(
                "RESPONSE_TOO_LARGE",
                f"{method} {path} declared {declared} bytes "
                f"(limit {self._max_response_bytes})",
                retryable=False,
            )
        body = response.content
        if len(body) > self._max_response_bytes:
            raise ProviderError(
                "RESPONSE_TOO_LARGE",
                f"{method} {path} returned {len(body)} bytes "
                f"(limit {self._max_response_bytes})",
                retryable=False,
            )

        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderError(
                    "MALFORMED_RESPONSE",
                    f"{method} {path} returned invalid JSON",
                    retryable=False,
                ) from exc
        return body

    def _raise_for_status(
        self, response: httpx.Response, method: str, path: str
    ) -> None:
        status = response.status_code
        detail = self._redact(response.text[:_DETAIL_LIMIT])
        message = f"{method} {path} -> {status}: {detail}"
        if status == 429:
            raise ProviderError("RATE_LIMITED", message, retryable=True)
        if status in _RETRYABLE_STATUS:
            raise ProviderError(f"HTTP_{status}", message, retryable=True)
        if status in _AUTH_STATUS:
            raise ProviderError("AUTH_ERROR", message, retryable=False)
        raise ProviderError(f"HTTP_{status}", message, retryable=False)
