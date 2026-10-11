"""Async-provider workflow: submit → poll → fetch, crash-safe, cancellable.

Runs the real workflow on a real worker against the compose Temporal
server; the provider is the in-memory FakeAsyncProvider. Skips when
127.0.0.1:7233 is unreachable.
"""
import asyncio
import socket
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.domain.status import JobStatus
from stt_platform.persistence.models import Base
from stt_platform.persistence.repositories import JobRepository
from stt_platform.workers.activities import make_activities
from stt_platform.workflows.transcription import TranscriptionWorkflow
from tests.fakes import FakeAsyncProvider
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
REMOTE = {"poll_interval_s": 0.3, "max_wait_s": 60.0}


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


async def _run_remote_job(
    session_factory,
    store,
    settings,
    provider,
    job_id,
    *,
    remote=REMOTE,
    retry=RETRY,
    cancel_after=None,
    restart_worker_after_polls=None,
):
    """Run the real workflow; returns result string or the raised exception.

    ``restart_worker_after_polls`` stops the worker once the provider has
    been polled N times, then starts a second worker — simulating a crash
    between polls.
    """
    from concurrent.futures import ThreadPoolExecutor

    from temporalio.client import Client
    from temporalio.worker import Worker

    client = await Client.connect(TEMPORAL_HOST)
    activities = make_activities(build_deps(session_factory, provider, store, settings))
    task_queue = f"test-q-{uuid.uuid4().hex[:8]}"

    def make_worker():
        executor = ThreadPoolExecutor(max_workers=4)
        worker = Worker(
            client,
            task_queue=task_queue,
            workflows=[TranscriptionWorkflow],
            activities=activities,
            activity_executor=executor,
            max_concurrent_activities=2,
        )
        return worker, executor

    handle = await client.start_workflow(
        "TranscriptionWorkflow",
        {
            "job_id": job_id,
            "scope": "local",
            "retry": retry,
            "remote": remote,
        },
        id=f"test-{job_id}",
        task_queue=task_queue,
    )

    if restart_worker_after_polls is not None:
        worker, executor = make_worker()
        async with worker:
            deadline = time.time() + 30
            while (
                provider.polls < restart_worker_after_polls
                and time.time() < deadline
            ):
                await asyncio.sleep(0.05)
        executor.shutdown(wait=False)
        worker2, executor2 = make_worker()
        async with worker2:
            try:
                result = await handle.result()
            except Exception as err:
                result = err
        executor2.shutdown(wait=False)
        return result

    worker, executor = make_worker()
    async with worker:
        if cancel_after is not None:
            # Cancel during polling: wait until the first poll returned
            # (the attempt row + provider reference are persisted), then
            # wait out the poll interval sleep and cancel mid-wait.
            deadline = time.time() + 30
            while provider.polls < 1 and time.time() < deadline:
                await asyncio.sleep(0.05)
            await asyncio.sleep(cancel_after)
            await handle.cancel()
        try:
            result = await handle.result()
        except Exception as err:
            result = err
    executor.shutdown(wait=False)
    return result


def _job(session_factory, job_id):
    with session_factory() as session:
        return JobRepository(session).get(job_id, "local")


def test_async_job_submits_once_polls_to_completion(
    session_factory, store, settings
):
    provider = FakeAsyncProvider(["pending", "pending", "completed"])
    job_id = seed_job(session_factory, store, provider="fake-async")

    result = asyncio.run(
        _run_remote_job(session_factory, store, settings, provider, job_id)
    )

    assert result == "SUCCEEDED"
    assert provider.submits == 1
    assert provider.polls == 3

    record = _job(session_factory, job_id)
    assert record.status is JobStatus.SUCCEEDED
    assert record.actual_provider == "fake-async"
    with session_factory() as session:
        trail = JobRepository(session).transitions(job_id, "local")
    assert ("QUEUED", "PROCESSING") in trail
    assert ("PROCESSING", "SUCCEEDED") in trail


def test_async_job_remote_failure_retries_as_new_submission(
    session_factory, store, settings
):
    provider = FakeAsyncProvider(["failed"])
    job_id = seed_job(session_factory, store, provider="fake-async")

    result = asyncio.run(
        _run_remote_job(
            session_factory,
            store,
            settings,
            provider,
            job_id,
            retry={**RETRY, "max_attempts": 2},
        )
    )

    assert result == "FAILED"
    assert provider.submits == 2  # one submission per attempt

    record = _job(session_factory, job_id)
    assert record.status is JobStatus.FAILED
    assert record.failure is not None
    assert record.failure.code == "REMOTE_FAIL"
    assert record.failure.retryable is True


def test_async_job_cancellation_during_polling_lands_cancelled(
    session_factory, store, settings
):
    provider = FakeAsyncProvider(["pending"])
    job_id = seed_job(session_factory, store, provider="fake-async")

    result = asyncio.run(
        _run_remote_job(
            session_factory,
            store,
            settings,
            provider,
            job_id,
            cancel_after=1.0,
        )
    )

    assert not isinstance(result, str)  # cancelled: an exception came back
    record = _job(session_factory, job_id)
    assert record.status is JobStatus.CANCELLED
    assert provider.cancels == 1


def test_async_job_max_wait_exceeded_fails_transient(
    session_factory, store, settings
):
    provider = FakeAsyncProvider(["pending"])
    job_id = seed_job(session_factory, store, provider="fake-async")

    result = asyncio.run(
        _run_remote_job(
            session_factory,
            store,
            settings,
            provider,
            job_id,
            remote={"poll_interval_s": 0.2, "max_wait_s": 1.0},
            retry={**RETRY, "max_attempts": 1},
        )
    )

    assert result == "FAILED"
    record = _job(session_factory, job_id)
    assert record.failure is not None
    assert record.failure.code == "REMOTE_TIMEOUT"


def test_worker_crash_mid_poll_resumes_without_resubmitting(
    session_factory, store, settings
):
    provider = FakeAsyncProvider(
        ["pending", "pending", "pending", "pending", "completed"]
    )
    job_id = seed_job(session_factory, store, provider="fake-async")

    result = asyncio.run(
        _run_remote_job(
            session_factory,
            store,
            settings,
            provider,
            job_id,
            restart_worker_after_polls=2,
        )
    )

    assert result == "SUCCEEDED"
    assert provider.submits == 1  # the crash must not duplicate the submission
    assert provider.polls >= 3
    assert _job(session_factory, job_id).status is JobStatus.SUCCEEDED
