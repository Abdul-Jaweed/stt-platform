"""Shared FastAPI dependencies."""
from collections.abc import Iterator

from fastapi import Request

from stt_platform.persistence.repositories import JobRepository
from stt_platform.services.jobs import JobService


def get_service(request: Request) -> Iterator[JobService]:
    """Per-request service bound to a fresh session (closed on exit)."""
    session = request.app.state.session_factory()
    try:
        yield JobService(
            JobRepository(session),
            request.app.state.registry,
            request.app.state.settings,
        )
    finally:
        session.close()
