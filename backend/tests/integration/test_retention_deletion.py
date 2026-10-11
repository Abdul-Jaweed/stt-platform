"""Deletion and retention: objects and rows go together, or not at all.

Deleting a job must remove what the platform stored for it — the original
audio, the transcript artifact, any retained raw provider response, the local
working copy — and every row that references it. A purge run applies the same
path to expired jobs, and never touches a job that is still running.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from stt_platform.core.config import Settings
from stt_platform.domain.errors import JobNotFound
from stt_platform.persistence.models import (
    Base,
    JobRow,
    JobTransitionRow,
    OutboxRow,
)
from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.services.jobs import JobService
from stt_platform.services.retention import purge_expired, select_expired
from stt_platform.storage.keys import artifact_key
from tests.audio_fixtures import make_wav
from tests.fakes import FakeProvider
from tests.integration.harness import build_store, make_settings
from tests.rustfs_container import RustFSContainer


@pytest.fixture(scope="module")
def rustfs():
    with RustFSContainer() as container:
        yield container


@pytest.fixture(scope="module")
def store(rustfs):
    return build_store(rustfs)


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(pg_engine):
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    yield sessionmaker(bind=pg_engine, future=True)
    # A session left open by a test holds ACCESS SHARE on the tables it read,
    # which makes the *next* test's drop_all wait for a lock forever. Close
    # every pooled connection between tests.
    pg_engine.dispose()


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture()
def make_service(session_factory, settings, store):
    """Build services whose sessions are closed at teardown.

    A live Session keeps its connection checked out, and that connection's
    read transaction holds ACCESS SHARE on every table it touched — the next
    test's drop_all then waits for a lock that never comes.
    """
    created: list = []

    def _make(provider=None):
        session = session_factory()
        created.append(session)
        registry = ProviderRegistry()
        registry.register(provider or FakeProvider(), default=True)
        service = JobService(
            JobRepository(session),
            registry,
            settings,
            store=store,
            outbox=OutboxRepository(session),
        )
        return service, session

    yield _make
    for session in created:
        session.rollback()
        session.close()


def _sync_job(service, settings, session, tmp_path, name="clip.wav"):
    job = service.create_and_process(
        audio_path=make_wav(tmp_path / name, seconds=1.0)
    )
    session.commit()
    return job


def test_deleting_a_job_removes_its_objects_and_its_rows(
    make_service, settings, store, tmp_path
):
    service, session = make_service()
    job = _sync_job(service, settings, session, tmp_path)
    scope, job_id = job.scope, job.id

    original = job.original_object_key
    transcript = artifact_key(scope, job_id, "transcript.json")
    assert store.exists(store.bucket_audio, original)
    assert store.exists(store.bucket_artifacts, transcript)

    service.delete(job_id, scope)
    session.commit()

    assert not store.exists(store.bucket_audio, original)
    assert not store.exists(store.bucket_artifacts, transcript)
    assert session.get(JobRow, job_id) is None
    for table in (JobTransitionRow, OutboxRow):
        remaining = session.execute(
            select(func.count()).select_from(table).where(
                table.job_id == job_id  # type: ignore[attr-defined]
            )
        ).scalar_one()
        assert remaining == 0, f"{table.__tablename__} rows survived deletion"


def test_deleting_an_unknown_job_is_not_found(make_service, settings, store):
    service, _session = make_service()
    with pytest.raises(JobNotFound):
        service.delete("nope", "local")


def test_deleting_another_tenants_job_is_not_found(make_service, settings, store, tmp_path
):
    service, session = make_service()
    job = _sync_job(service, settings, session, tmp_path)
    with pytest.raises(JobNotFound):
        service.delete(job.id, "globex")


def test_select_expired_only_returns_terminal_jobs():
    """A job that is still running is never a purge candidate."""
    now = datetime.now(UTC)
    rows = [
        ("old-succeeded", "SUCCEEDED", now - timedelta(days=40)),
        ("old-failed", "FAILED", now - timedelta(days=40)),
        ("old-cancelled", "CANCELLED", now - timedelta(days=40)),
        ("old-processing", "PROCESSING", now - timedelta(days=40)),
        ("old-queued", "QUEUED", now - timedelta(days=40)),
        ("fresh-succeeded", "SUCCEEDED", now - timedelta(days=1)),
    ]
    assert set(select_expired(rows, older_than_days=30, now=now)) == {
        "old-succeeded",
        "old-failed",
        "old-cancelled",
    }


def test_purge_dry_run_deletes_nothing(make_service, settings, store, tmp_path
):
    service, session = make_service()
    job = _sync_job(service, settings, session, tmp_path)
    # Backdate the row so it falls inside the retention window.
    row = session.get(JobRow, job.id)
    row.updated_at = datetime.now(UTC) - timedelta(days=90)
    session.commit()

    report = purge_expired(
        session, store, older_than_days=30, dry_run=True
    )
    session.commit()

    assert report.candidates == [job.id]
    assert report.deleted == []
    assert session.get(JobRow, job.id) is not None
    assert store.exists(store.bucket_audio, job.original_object_key)


def test_purge_deletes_expired_jobs_and_leaves_fresh_ones(make_service, settings, store, tmp_path
):
    service, session = make_service()
    expired = _sync_job(service, settings, session, tmp_path, name="old.wav")
    fresh = _sync_job(service, settings, session, tmp_path, name="new.wav")
    session.get(JobRow, expired.id).updated_at = datetime.now(UTC) - timedelta(days=90)
    session.commit()

    report = purge_expired(session, store, older_than_days=30)
    session.commit()

    assert report.candidates == [expired.id]
    assert report.deleted == [expired.id]
    assert session.get(JobRow, expired.id) is None
    assert not store.exists(store.bucket_audio, expired.original_object_key)
    assert session.get(JobRow, fresh.id) is not None
    assert store.exists(store.bucket_audio, fresh.original_object_key)


def test_settings_default_to_keeping_everything():
    assert Settings(_env_file=None).retention_days == 0
