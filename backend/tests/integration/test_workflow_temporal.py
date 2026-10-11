"""Workflow end-to-end against the compose Temporal server.

Skips (with an explicit reason) when 127.0.0.1:7233 is unreachable, so a
machine without ``infrastructure/`` running still gets a green default
suite; the e2e in the phase report covers the same paths for real.
"""
import asyncio
import socket
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.domain.errors import ProviderError
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.models import Base
from stt_platform.persistence.repositories import JobRepository
from stt_platform.storage.keys import artifact_key
from stt_platform.workers.activities import make_activities
from stt_platform.workflows.transcription import TranscriptionWorkflow
from tests.fakes import FakeProvider, sample_transcript
from tests.integration.harness import (
    build_deps,
    build_store,
    make_settings,
    seed_job,
)
from tests.rustfs_container import RustFSContainer

TEMPORAL_HOST = "127.0.0.1:7233"

RETRY = {
    "max_attempts": 3,
    "base": 0.2,
    "multiplier": 2.0,
    "cap": 1.0,
    "jitter_ratio": 0.1,
}


def _temporal_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 7233), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _temporal_reachable(),
    reason=(
        "compose Temporal not reachable on 127.0.0.1:7233 "
        "(run: cd infrastructure && docker compose up -d)"
    ),
)


class FlakyProvider(FakeProvider):
    """Fails transiently the first ``fail_times`` calls, then succeeds."""

    def __init__(self, fail_times: int = 1):
        super().__init__()
        self.fail_times = fail_times
        self.calls = 0

    def transcribe(self, request):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError("FLAKY", "not ready", retryable=True)
        return sample_transcript()


class SlowProvider(FakeProvider):
    def transcribe(self, request):
        time.sleep(2.0)
        return sample_transcript()


@pytest.fixture(scope="module")
def rustfs():
    with RustFSContainer() as rc:
        yield rc


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
    return sessionmaker(bind=pg_engine, future=True)


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


async def _run_job(
    session_factory,
    store,
    settings,
    provider,
    job_id,
    *,
    cancel_after: float | None = None,
):
    """Run the real workflow on a real worker against the compose server.

    Returns the workflow result string, or the raised exception for
    failed/cancelled runs (callers assert final state via PostgreSQL).
    """
    from concurrent.futures import ThreadPoolExecutor

    from temporalio.client import Client
    from temporalio.worker import Worker

    client = await Client.connect(TEMPORAL_HOST)
    activities = make_activities(build_deps(session_factory, provider, store, settings))
    task_queue = f"test-q-{uuid.uuid4().hex[:8]}"
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[TranscriptionWorkflow],
            activities=activities,
            activity_executor=executor,
            max_concurrent_activities=1,
        ):
            handle = await client.start_workflow(
                "TranscriptionWorkflow",
                {"job_id": job_id, "scope": "local", "retry": RETRY},
                id=f"test-{job_id}",
                task_queue=task_queue,
            )
            if cancel_after is not None:
                await asyncio.sleep(cancel_after)
                await handle.cancel()
            try:
                return await handle.result()
            except Exception as err:  # noqa: BLE001 - assert final state via PG
                return err


def test_workflow_success_end_to_end(session_factory, store, settings):
    job_id = seed_job(session_factory, store)

    outcome = asyncio.run(
        _run_job(session_factory, store, settings, FakeProvider(), job_id)
    )

    assert outcome == "SUCCEEDED"
    with session_factory() as session:
        repo = JobRepository(session)
        record = repo.get(job_id)
        assert record.status is JobStatus.SUCCEEDED
        assert repo.transitions(job_id) == [
            ("QUEUED", "PROCESSING"),
            ("PROCESSING", "SUCCEEDED"),
        ]
        assert record.attempt_count == 1
        assert record.actual_provider == "fake"
        assert record.language_detected == "en"
        assert repo.load_result(job_id).text == "hello world"
    assert store.exists(
        store.bucket_artifacts, artifact_key("local", job_id, "transcript.json")
    )


def test_transient_failure_retries_with_visible_retry_wait(
    session_factory, store, settings
):
    job_id = seed_job(session_factory, store)
    provider = FlakyProvider(fail_times=1)

    outcome = asyncio.run(
        _run_job(session_factory, store, settings, provider, job_id)
    )

    assert outcome == "SUCCEEDED"
    assert provider.calls == 2
    with session_factory() as session:
        repo = JobRepository(session)
        assert repo.transitions(job_id) == [
            ("QUEUED", "PROCESSING"),
            ("PROCESSING", "RETRY_WAIT"),
            ("RETRY_WAIT", "QUEUED"),
            ("QUEUED", "PROCESSING"),
            ("PROCESSING", "SUCCEEDED"),
        ]
        assert repo.get(job_id).attempt_count == 2


def test_exhausted_retries_fail_retryable(session_factory, store, settings):
    job_id = seed_job(session_factory, store)

    outcome = asyncio.run(
        _run_job(
            session_factory, store, settings, FlakyProvider(fail_times=99), job_id
        )
    )

    assert outcome == "FAILED"
    with session_factory() as session:
        repo = JobRepository(session)
        record = repo.get(job_id)
        assert record.status is JobStatus.FAILED
        assert record.failure is not None
        assert record.failure.retryable is True
        assert record.attempt_count == 3
        trail = repo.transitions(job_id)
        assert trail[0] == ("QUEUED", "PROCESSING")
        assert ("PROCESSING", "RETRY_WAIT") in trail
        assert trail[-1] == ("PROCESSING", "FAILED")


def test_permanent_failure_goes_straight_to_failed(session_factory, store, settings):
    job_id = seed_job(session_factory, store)

    outcome = asyncio.run(
        _run_job(
            session_factory, store, settings, FakeProvider(fail="permanent"), job_id
        )
    )

    assert outcome == "FAILED"
    with session_factory() as session:
        repo = JobRepository(session)
        record = repo.get(job_id)
        assert record.status is JobStatus.FAILED
        assert record.failure is not None
        assert record.failure.retryable is False
        assert record.failure.code == "FAKE_PERMANENT"
        assert repo.transitions(job_id) == [
            ("QUEUED", "PROCESSING"),
            ("PROCESSING", "FAILED"),
        ]


def test_cancellation_lands_cancelled(session_factory, store, settings):
    job_id = seed_job(session_factory, store)

    outcome = asyncio.run(
        _run_job(
            session_factory,
            store,
            settings,
            SlowProvider(),
            job_id,
            cancel_after=0.5,
        )
    )

    assert not isinstance(outcome, str)  # cancellation raises in the caller
    with session_factory() as session:
        repo = JobRepository(session)
        assert repo.get(job_id).status is JobStatus.CANCELLED
        assert repo.transitions(job_id) == [
            ("QUEUED", "PROCESSING"),
            ("PROCESSING", "CANCELLED"),
        ]
