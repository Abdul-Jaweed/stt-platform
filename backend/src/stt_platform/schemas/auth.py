"""Login and session payloads."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class LoginResponse(BaseModel):
    username: str
    tenant: str
    expires_at: float
    #: The same token the cookie carries, for non-browser clients that cannot
    #: hold cookies (they send it as ``Authorization: Bearer``).
    token: str


class SessionInfo(BaseModel):
    """``GET /v1/auth/me`` — who the caller is, and whether login is required.

    When ``STT_UI_AUTH_ENABLED=false`` this answers 200 with
    ``authenticated: false``: the local developer flow runs without a login,
    and the UI still learns that it does not have to show one.
    """

    authenticated: bool
    username: str | None = None
    tenant: str
    ui_auth_enabled: bool
    expires_at: float | None = None
