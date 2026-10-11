"""Request/job context for logs and span attributes.

Contextvars rather than function arguments: the API middleware sets the
correlation id once and every log record and span in that request — across
the service, the repository, and provider calls — can read it without
threading it through call signatures.
"""
from __future__ import annotations

from contextvars import ContextVar, Token

_correlation_id: ContextVar[str | None] = ContextVar(
    "stt_correlation_id", default=None
)
_job_id: ContextVar[str | None] = ContextVar("stt_job_id", default=None)


def set_correlation_id(value: str | None) -> Token:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token) -> None:
    _correlation_id.reset(token)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def set_job_id(value: str | None) -> Token:
    return _job_id.set(value)


def reset_job_id(token: Token) -> None:
    _job_id.reset(token)


def current_job_id() -> str | None:
    return _job_id.get()
