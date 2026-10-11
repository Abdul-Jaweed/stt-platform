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
from stt_platform.services.auth import SessionClaims

LOCAL_SCOPE = "local"
#: The browser's only credential. HttpOnly, SameSite=Lax, and signed.
SESSION_COOKIE = "stt_session"


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


def presented_session_token(request: Request) -> str | None:
    """The session token this request carries: cookie first, then ``Bearer``.

    Browsers use the cookie (script cannot read it); CLI clients and tests use
    the header. Either way the token is the same signed value.
    """
    settings: Settings = request.app.state.settings
    name = getattr(settings, "session_cookie_name", SESSION_COOKIE) or SESSION_COOKIE
    cookie = request.cookies.get(name)
    if cookie:
        return cookie
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def resolve_session(request: Request) -> SessionClaims | None:
    """The verified session behind this request, or ``None``.

    Deliberately does not raise: an absent session is normal (machine clients
    present keys), and an *invalid* one is judged by the caller — with auth
    enabled it ends as a 401, with auth off it degrades to the local scope.
    """
    tokens = getattr(request.app.state, "sessions", None)
    if tokens is None:
        return None
    token = presented_session_token(request)
    if not token:
        return None
    try:
        claims = tokens.verify(token)
    except Unauthorized:
        return None
    request.state.session_claims = claims
    return claims


def get_scope(request: Request) -> str:
    """FastAPI dependency: the authenticated tenant for this request.

    Three ways in, one rule out — the *server* decides the scope: a signed
    session (UI), a presented API key (machines), or ``local`` when auth is
    off entirely.
    """
    settings: Settings = request.app.state.settings
    claims = resolve_session(request)
    if claims is not None:
        return claims.tenant
    if not settings.auth_enabled:
        return LOCAL_SCOPE
    return resolve_scope(
        request.headers.get("x-api-key"), parse_api_keys(settings.api_keys)
    )
