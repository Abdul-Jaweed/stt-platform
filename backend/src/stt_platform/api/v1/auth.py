"""Login, logout, and "who am I" for the browser UI.

The session cookie is the only credential the browser ever holds. It is
``HttpOnly`` (script cannot read it), ``SameSite=Lax`` (a cross-site POST does
not carry it, which is what stops CSRF against the JSON API), and ``Secure``
whenever the deployment says so — mandatory in production.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Request, Response

from stt_platform.api.ratelimit import SlidingWindowLimiter
from stt_platform.api.security import SESSION_COOKIE, resolve_session
from stt_platform.core.config import Settings
from stt_platform.domain.errors import Unauthorized
from stt_platform.schemas.auth import LoginRequest, LoginResponse, SessionInfo
from stt_platform.services.auth import SessionTokens, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _tokens(request: Request) -> SessionTokens:
    return request.app.state.sessions


def _password_hash(request: Request) -> str | None:
    return getattr(request.app.state, "ui_password_hash", None)


def _client_key(request: Request, username: str) -> str:
    """Charge failed logins to the caller's address *and* the account, so one
    noisy client cannot lock everyone out and one account cannot be sprayed."""
    host = request.client.host if request.client else "unknown"
    return f"{host}:{username.lower()}"


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest, request: Request, response: Response
) -> LoginResponse:
    settings = _settings(request)
    limiter: SlidingWindowLimiter | None = getattr(
        request.app.state, "login_limiter", None
    )
    if limiter is not None:
        limiter.check(_client_key(request, payload.username))

    username_ok = hmac.compare_digest(payload.username, settings.ui_username)
    password_ok = verify_password(payload.password, _password_hash(request))
    if not (username_ok and password_ok):
        # One message for both failures: a caller cannot tell a wrong password
        # from an unknown account.
        raise Unauthorized("invalid username or password")

    tokens = _tokens(request)
    token = tokens.issue(settings.ui_username, settings.ui_tenant)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=tokens.ttl_s,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    claims = tokens.verify(token)
    return LoginResponse(
        username=claims.username,
        tenant=claims.tenant,
        expires_at=claims.expires_at,
        token=token,
    )


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    """Clear the cookie. The token itself is stateless, so this is the whole
    server-side effect: nothing to revoke, nothing left behind."""
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=SessionInfo)
def me(request: Request) -> SessionInfo:
    settings = _settings(request)
    claims = resolve_session(request)
    if claims is not None:
        return SessionInfo(
            authenticated=True,
            username=claims.username,
            tenant=claims.tenant,
            ui_auth_enabled=settings.ui_auth_enabled,
            expires_at=claims.expires_at,
        )
    if not settings.ui_auth_enabled:
        # No login required: the UI learns that and goes straight to the app.
        return SessionInfo(
            authenticated=False,
            tenant=settings.ui_tenant or "local",
            ui_auth_enabled=False,
        )
    raise Unauthorized("sign in to continue")
