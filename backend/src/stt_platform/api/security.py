"""Authentication: an API key resolves to a tenant scope.

The scope is *server-assigned* — it comes from the presented key, never from
a header or body field the caller controls (spec: "never trust client tenant
IDs"). Keys are compared with ``hmac.compare_digest`` so a caller cannot
recover a key character by character from response timing.

With ``STT_AUTH_ENABLED=false`` (the local default) every request runs in the
``local`` scope, which is what the developer flow and the e2e drivers use.
With it on, an unknown or missing key is a 401 and an empty key list fails
closed rather than open.
"""
from __future__ import annotations

import hmac

from fastapi import Request

from stt_platform.core.config import Settings
from stt_platform.domain.errors import Unauthorized

LOCAL_SCOPE = "local"


def parse_api_keys(raw: str | None) -> dict[str, str]:
    """``"tenant:key,other:key2"`` → ``{key: tenant}``; bad entries ignored."""
    mapping: dict[str, str] = {}
    for raw_entry in (raw or "").split(","):
        entry = raw_entry.strip()
        if not entry or ":" not in entry:
            continue
        tenant, _, key = entry.partition(":")
        tenant, key = tenant.strip(), key.strip()
        if tenant and key:
            mapping[key] = tenant
    return mapping


def resolve_scope(presented: str | None, keys: dict[str, str]) -> str:
    """The tenant behind ``presented``, or ``Unauthorized``."""
    if not presented:
        raise Unauthorized("a valid X-API-Key header is required")
    for key, tenant in keys.items():
        if hmac.compare_digest(presented, key):
            return tenant
    raise Unauthorized("a valid X-API-Key header is required")


def get_scope(request: Request) -> str:
    """FastAPI dependency: the authenticated tenant for this request."""
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return LOCAL_SCOPE
    return resolve_scope(
        request.headers.get("x-api-key"), parse_api_keys(settings.api_keys)
    )
