"""Shared FastAPI dependencies."""
from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.services.jobs import JobService


def get_session(request: Request) -> Iterator[Session]:
    """Per-request session (closed on exit); shared by other dependencies."""
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_service(
    request: Request, session: Session = Depends(get_session)
) -> Iterator[JobService]:
    """Per-request service bound to the request's session."""
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
