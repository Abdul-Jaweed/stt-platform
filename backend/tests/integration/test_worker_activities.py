"""Activity tests: real PostgreSQL + RustFS container, fake provider.

Covers the worker-side failure taxonomy the workflow depends on:
ok / transient / permanent outcomes, content re-validation of stored
bytes, and storage outages staying retryable.
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.domain.status import JobStatus
from stt_platform.persistence.models import Base
from stt_platform.persistence.repositories import JobRepository
from stt_platform.storage.keys import artifact_key
from stt_platform.storage.s3 import S3ObjectStore
from stt_platform.workers.activities import TranscriptionActivities
from tests.fakes import FakeProvider
from tests.integration.harness import (
    build_deps,
    build_store,
    make_settings,
    seed_job,
)
from tests.rustfs_container import RustFSContainer


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


def test_execute_attempt_success_saves_result_and_artifact(
    session_factory, store, settings
):
    job_id = seed_job(session_factory, store)
    acts = TranscriptionActivities(
        build_deps(session_factory, FakeProvider(), store, settings)
    )

    outcome = acts.execute_attempt(job_id, "local", 1)

    assert outcome["outcome"] == "ok"
    assert outcome["provider"] == "fake"
    assert outcome["language"] == "en"
    with session_factory() as session:
        repo = JobRepository(session)
        record = repo.get(job_id)
        # The workflow — not the activity — writes SUCCEEDED.
        assert record.status is JobStatus.PROCESSING
        assert repo.transitions(job_id) == [("QUEUED", "PROCESSING")]
        assert repo.load_result(job_id).text == "hello world"
    artifact = store.get_bytes(
        store.bucket_artifacts, artifact_key("local", job_id, "transcript.json")
    )
    assert json.loads(artifact.decode("utf-8"))["text"] == "hello world"


def test_execute_attempt_transient_failure_is_returned_not_raised(
    session_factory, store, settings
):
    job_id = seed_job(session_factory, store)
    acts = TranscriptionActivities(
        build_deps(session_factory, FakeProvider(fail="transient"), store, settings)
    )

    outcome = acts.execute_attempt(job_id, "local", 1)

    assert outcome == {
        "outcome": "transient",
        "code": "FAKE_TRANSIENT",
        "message": "temporary failure",
    }


def test_execute_attempt_permanent_failure_is_returned(
    session_factory, store, settings
):
    job_id = seed_job(session_factory, store)
    acts = TranscriptionActivities(
        build_deps(session_factory, FakeProvider(fail="permanent"), store, settings)
    )

    outcome = acts.execute_attempt(job_id, "local", 1)

    assert outcome["outcome"] == "permanent"
    assert outcome["code"] == "FAKE_PERMANENT"


def test_execute_attempt_revalidates_corrupt_stored_bytes(
    session_factory, store, settings
):
    job_id = seed_job(session_factory, store, corrupt=True)
    acts = TranscriptionActivities(
        build_deps(session_factory, FakeProvider(), store, settings)
    )

    outcome = acts.execute_attempt(job_id, "local", 1)

    # Content validation failure: permanent, and no transcript was saved.
    assert outcome["outcome"] == "permanent"
    with session_factory() as session:
        assert JobRepository(session).load_result(job_id) is None


def test_execute_attempt_storage_outage_is_transient(session_factory, store, settings):
    job_id = seed_job(session_factory, store)
    dead_store = S3ObjectStore(
        endpoint_url="http://127.0.0.1:1",
        access_key="stt",
        secret_key="stt123456",
        region="us-east-1",
        bucket_audio="stt-audio",
        bucket_artifacts="stt-artifacts",
    )
    acts = TranscriptionActivities(
        build_deps(session_factory, FakeProvider(), dead_store, settings)
    )

    outcome = acts.execute_attempt(job_id, "local", 1)

    # An unreachable object store must never wedge the job: retryable.
    assert outcome["outcome"] == "transient"
