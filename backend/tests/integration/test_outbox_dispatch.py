"""Transactional outbox + dispatcher behavior against real PostgreSQL.

The outbox guarantees that a committed job always eventually reaches
Temporal (at-least-once dispatch) while duplicate re-dispatch after a
crash is harmless (workflow id is derived from the outbox row).
"""
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from stt_platform.domain.status import JobStatus
from stt_platform.persistence.models import Base, JobRow, OutboxRow
from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.schemas.jobs import JobRecord
from stt_platform.services.dispatch import dispatch_pending


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(pg_engine):
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    return sessionmaker(bind=pg_engine, future=True)


@pytest.fixture()
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


class RecordingStarter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, payload: dict) -> None:
        self.calls.append(payload)


class BoomStarter:
    def __call__(self, payload: dict) -> None:
        raise RuntimeError("temporal down")


def _queued_job(repo: JobRepository) -> JobRecord:
    return repo.create_with_outbox(
        JobRecord(id=str(uuid.uuid4()), status=JobStatus.QUEUED)
    )


def test_create_with_outbox_writes_both_rows_atomically(session):
    repo = JobRepository(session)
    job = _queued_job(repo)
    rows = session.execute(select(OutboxRow)).scalars().all()
    assert len(rows) == 1
    assert rows[0].job_id == job.id
    assert rows[0].topic == "transcription.start"
    assert rows[0].payload["job_id"] == job.id
    assert rows[0].dispatched_at is None

    # Duplicate primary key must roll back both inserts (no orphan outbox row).
    with pytest.raises(IntegrityError):
        repo.create_with_outbox(JobRecord(id=job.id, status=JobStatus.QUEUED))
    assert len(session.execute(select(OutboxRow)).scalars().all()) == 1
    assert len(session.execute(select(JobRow)).scalars().all()) == 1


def test_dispatcher_starts_once_then_marks(session_factory):
    s1 = session_factory()
    try:
        repo = JobRepository(s1)
        outbox = OutboxRepository(s1)
        j1 = _queued_job(repo)
        j2 = _queued_job(repo)
        starter = RecordingStarter()

        assert dispatch_pending(outbox, starter) == 2
        assert {p["job_id"] for p in starter.calls} == {j1.id, j2.id}
        assert all(p["workflow_id"].startswith("transcribe.") for p in starter.calls)
        assert all(p["scope"] == "local" for p in starter.calls)

        # Nothing left to dispatch: no duplicate starts after a rerun.
        assert dispatch_pending(outbox, starter) == 0
        assert len(starter.calls) == 2
    finally:
        s1.close()


def test_failed_dispatch_keeps_row_for_retry(session_factory):
    s1 = session_factory()
    try:
        repo = JobRepository(s1)
        outbox = OutboxRepository(s1)
        _queued_job(repo)

        assert dispatch_pending(outbox, BoomStarter()) == 0
        row = s1.execute(select(OutboxRow)).scalar_one()
        assert row.dispatched_at is None
        assert row.attempt_count == 1
        assert "temporal down" in (row.last_error or "")

        # A healthy starter later still dispatches the same row.
        starter = RecordingStarter()
        assert dispatch_pending(outbox, starter) == 1
        assert s1.execute(select(OutboxRow)).scalar_one().dispatched_at is not None
    finally:
        s1.close()


def test_dispatcher_skips_and_spends_terminal_jobs(session_factory):
    s1 = session_factory()
    try:
        repo = JobRepository(s1)
        outbox = OutboxRepository(s1)
        job = _queued_job(repo)
        repo.append_transition(job.id, JobStatus.CANCELLED)

        starter = RecordingStarter()
        assert dispatch_pending(outbox, starter) == 0
        assert starter.calls == []
        # The row is spent (marked dispatched): it must not linger forever.
        assert s1.execute(select(OutboxRow)).scalar_one().dispatched_at is not None
    finally:
        s1.close()


def test_claim_batch_skips_locked_rows_between_dispatchers(session_factory):
    s1 = session_factory()
    s2 = session_factory()
    try:
        repo = JobRepository(s1)
        out1 = OutboxRepository(s1)
        _queued_job(repo)

        claimed = out1.claim_batch(10)  # transaction stays open -> rows locked
        assert len(claimed) == 1

        out2 = OutboxRepository(s2)
        assert out2.claim_batch(10) == []  # SKIP LOCKED: not handed to a 2nd dispatcher

        s1.commit()
        assert len(out2.claim_batch(10)) == 1
        s2.commit()
    finally:
        s1.close()
        s2.close()
