"""Shared FastAPI dependencies."""
from collections.abc import Iterator

from fastapi import Request

from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.services.jobs import JobService


def get_service(request: Request) -> Iterator[JobService]:
    """Per-request service bound to a fresh session (closed on exit)."""
    session = request.app.state.session_factory()
    try:
        starter = getattr(request.app.state, "starter", None)
        yield JobService(
            JobRepository(session),
            request.app.state.registry,
            request.app.state.settings,
            store=getattr(request.app.state, "store", None),
            outbox=OutboxRepository(session),
            starter=starter,
            canceller=(starter.cancel if starter is not None else None),
        )
    finally:
        session.close()
