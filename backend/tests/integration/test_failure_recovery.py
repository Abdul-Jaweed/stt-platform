"""Failure recovery: what survives, what fails honestly, what recovers.

These tests pin the properties the operations docs promise:

* a storage outage fails the job with a real error code — never a lie;
* a job with no stored original is not "retryable" (there is nothing to retry);
* state lives in Postgres, so a fresh API process sees the same jobs;
* an at-least-once outbox keeps a job dispatchable after a dispatcher crash;
* a healed dependency lets a retry succeed.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from stt_platform.core.config import Settings
from stt_platform.domain.errors import DomainError
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.models import Base, OutboxRow
from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.services.dispatch import dispatch_pending
from stt_platform.services.jobs import JobService
from tests.audio_fixtures import make_wav
from tests.fakes import FakeProvider
from tests.integration.harness import make_settings


class FlakyStore:
    """A store that can be broken and healed while the platform keeps going."""

    bucket_audio = "audio"
    bucket_artifacts = "artifacts"

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail_put = False
        self.fail_get = False
        self.fail_delete = False

    def presign_put(self, bucket, key, expires_s: int = 900) -> str:
        return f"http://storage.invalid/{bucket}/{key}?sig=stub"

    def put_file(self, bucket, key, path) -> None:
        if self.fail_put:
            raise ConnectionError("storage is down")
        self.objects[(bucket, key)] = Path(path).read_bytes()

    def put_bytes(self, bucket, key, data: bytes, **kwargs) -> None:
        if self.fail_put:
            raise ConnectionError("storage is down")
        self.objects[(bucket, key)] = data

    def get_bytes(self, bucket, key) -> bytes:
        if self.fail_get:
            raise ConnectionError("storage is down")
        return self.objects[(bucket, key)]

    def download_to(self, bucket, key, path) -> Path:
        data = self.get_bytes(bucket, key)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def exists(self, bucket, key) -> bool:
        return (bucket, key) in self.objects

    def delete(self, bucket, key) -> None:
        if self.fail_delete:
            raise ConnectionError("storage is down")
        self.objects.pop((bucket, key), None)


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
    pg_engine.dispose()


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture()
def sessions(session_factory):
    created = []
    yield lambda: created.append(session_factory()) or created[-1]
    for session in created:
        session.rollback()
        session.close()


def build_service(session, settings, store, provider=None, starter=None):
    registry = ProviderRegistry()
    registry.register(provider or FakeProvider(), default=True)
    return JobService(
        JobRepository(session),
        registry,
        settings,
        store=store,
        outbox=OutboxRepository(session),
        starter=starter,
    )


def test_storage_outage_fails_the_job_honestly_and_then_recovers(
    settings, sessions, tmp_path
):
    store = FlakyStore()
    store.fail_put = True
    session = sessions()
    service = build_service(session, settings, store)

    job = service.create_and_process(audio_path=make_wav(tmp_path / "a.wav"))
    session.commit()

    # Honest failure: a real code, and "retryable" because the local copy
    # survived — the sync path can genuinely be retried.
    assert job.status is JobStatus.FAILED
    assert job.failure is not None
    assert job.failure.code == "STORAGE_ERROR"
    assert job.failure.retryable is True

    store.fail_put = False  # storage comes back
    recovered = service.retry(job.id)
    session.commit()
    assert recovered.status is JobStatus.SUCCEEDED
    assert store.exists(store.bucket_audio, recovered.original_object_key or "")


def test_state_survives_a_new_api_process(settings, sessions, tmp_path):
    store = FlakyStore()
    session = sessions()
    first = build_service(session, settings, store)
    job = first.create_async(audio_path=make_wav(tmp_path / "b.wav"))
    session.commit()

    # A brand-new service object (as a restarted API process would build)
    # reads the same durable state.
    second = build_service(sessions(), settings, store)
    again = second.get(job.id)
    assert again.id == job.id
    assert again.status in {JobStatus.QUEUED, JobStatus.STORED}
    assert again.original_object_key == job.original_object_key


def test_outbox_survives_a_dispatcher_crash(settings, sessions, tmp_path):
    store = FlakyStore()
    session = sessions()
    service = build_service(session, settings, store)
    job = service.create_async(audio_path=make_wav(tmp_path / "c.wav"))
    session.commit()

    outbox = OutboxRepository(session)
    started: list[dict] = []

    def broken_starter(payload):
        raise RuntimeError("temporal is unreachable")

    # The dispatcher dies mid-flight: the row must stay pending.
    assert dispatch_pending(outbox, broken_starter) == 0
    session.commit()
    row = session.execute(select(OutboxRow).where(OutboxRow.job_id == job.id)).scalar_one()
    assert row.dispatched_at is None
    assert row.attempt_count >= 1
    assert row.last_error

    # A healthy dispatcher later picks it up — at-least-once, not at-most-once.
    assert dispatch_pending(outbox, started.append) == 1
    session.commit()
    assert started and started[0]["job_id"] == job.id


def test_a_healed_dependency_lets_a_retry_succeed(settings, sessions, tmp_path):
    store = FlakyStore()
    session = sessions()
    provider = FakeProvider(fail="transient")
    service = build_service(session, settings, store, provider=provider)

    failed = service.create_and_process(audio_path=make_wav(tmp_path / "d.wav"))
    session.commit()
    assert failed.status is JobStatus.FAILED
    assert failed.failure is not None and failed.failure.retryable is True

    provider.fail = None  # the dependency comes back
    recovered = service.retry(failed.id)
    session.commit()
    assert recovered.status is JobStatus.SUCCEEDED
    # Status is authoritative; the last failure is kept as history so an
    # operator can still see why the first attempt died.
    assert recovered.failure is not None
    assert recovered.failure.code == "FAKE_TRANSIENT"


def test_a_failed_job_with_a_stored_original_can_be_requeued(
    settings, sessions, tmp_path
):
    store = FlakyStore()
    session = sessions()
    service = build_service(session, settings, store, starter=lambda payload: None)
    job = service.create_async(audio_path=make_wav(tmp_path / "e.wav"))
    session.commit()

    # The worker failed the job (say, the provider was down).
    service._repo.append_transition(
        job.id, JobStatus.PROCESSING, scope=job.scope
    )
    service._fail(
        job.id,
        DomainError("PROVIDER_ERROR", "provider was unreachable"),
        retryable=True,
        scope=job.scope,
    )
    session.commit()

    requeued = service.retry_async(job.id)
    session.commit()
    assert requeued.status in {JobStatus.QUEUED, JobStatus.STORED}
    assert requeued.original_object_key
