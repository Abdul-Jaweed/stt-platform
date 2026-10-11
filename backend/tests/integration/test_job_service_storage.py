"""Storage wiring: originals to S3, artifacts, retry-from-object-storage."""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.domain.errors import ProviderError
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.models import Base
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.base import ProviderTranscript, TranscriptionRequest
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.services.jobs import JobService
from stt_platform.storage.keys import artifact_key
from stt_platform.storage.s3 import S3ObjectStore
from tests.audio_fixtures import make_wav
from tests.fakes import FakeProvider, sample_transcript
from tests.integration.harness import build_store, make_settings
from tests.rustfs_container import RustFSContainer


class FlakyOnce(FakeProvider):
    """Fails the first transcribe transiently, then succeeds."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def transcribe(self, request: TranscriptionRequest) -> ProviderTranscript:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("FLAKY", "not ready", retryable=True)
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


def make_service(session_factory, settings, provider, store=None):
    session = session_factory()
    registry = ProviderRegistry()
    registry.register(provider, default=True)
    service = JobService(
        JobRepository(session), registry, settings, store=store
    )
    return service, session


def test_sync_upload_stores_original_and_artifact(
    session_factory, settings, store, tmp_path
):
    service, session = make_service(session_factory, settings, FakeProvider(), store)
    try:
        wav = make_wav(tmp_path / "in.wav", seconds=1.0)
        job = service.create_and_process(audio_path=wav)
        assert job.status is JobStatus.SUCCEEDED
        assert job.original_object_key
        assert store.exists(store.bucket_audio, job.original_object_key)
        artifact = store.get_bytes(
            store.bucket_artifacts, artifact_key("local", job.id, "transcript.json")
        )
        assert json.loads(artifact.decode("utf-8"))["text"] == "hello world"
    finally:
        session.close()


def test_upload_size_is_recorded_as_a_metric(
    session_factory, settings, store, tmp_path
):
    """The spec asks for upload size; it is measured where the bytes are known."""
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from stt_platform.core.config import Settings
    from stt_platform.observability import metrics

    reader = InMemoryMetricReader()
    metrics.shutdown_metrics()  # setup_metrics is idempotent per process
    metrics.setup_metrics(
        Settings(
            otel_enabled=True,
            otel_exporter="none",
            database_url="postgresql+psycopg://stt:stt@127.0.0.1:25432/stt",
        ),
        reader=reader,
    )
    service, session = make_service(session_factory, settings, FakeProvider(), store)
    try:
        wav = make_wav(tmp_path / "sized.wav", seconds=1.0)
        size = wav.stat().st_size
        service.create_and_process(audio_path=wav)

        data = reader.get_metrics_data()
        points = [
            point
            for resource_metrics in data.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
            if metric.name == "stt_upload_bytes"
            for point in metric.data.data_points
        ]
        assert points, "no stt_upload_bytes datapoint"
        assert sum(point.count for point in points) == 1
        assert points[0].sum == float(size)
    finally:
        session.close()
        metrics.shutdown_metrics()


def test_retry_without_local_file_fetches_from_s3(
    session_factory, settings, store, tmp_path
):
    service, session = make_service(session_factory, settings, FlakyOnce(), store)
    try:
        wav = make_wav(tmp_path / "in2.wav", seconds=1.0)
        job = service.create_and_process(audio_path=wav)
        assert job.status is JobStatus.FAILED
        assert job.original_object_key
        assert job.audio_path is not None
        Path(job.audio_path).unlink()  # local copy gone: S3 is the only source

        retried = service.retry(job.id)

        assert retried.status is JobStatus.SUCCEEDED
        assert service.get_result(job.id).text == "hello world"
    finally:
        session.close()


def test_create_and_process_without_store_keeps_phase1_behavior(
    session_factory, settings, tmp_path
):
    service, session = make_service(
        session_factory, settings, FakeProvider(), store=None
    )
    try:
        wav = make_wav(tmp_path / "in3.wav", seconds=1.0)
        job = service.create_and_process(audio_path=wav)
        assert job.status is JobStatus.SUCCEEDED
        assert job.original_object_key is None
    finally:
        session.close()


def test_storage_outage_fails_job_transient(
    session_factory, settings, tmp_path
):
    dead_store = S3ObjectStore(
        endpoint_url="http://127.0.0.1:1",
        access_key="stt",
        secret_key="stt123456",
        region="us-east-1",
        bucket_audio="stt-audio",
        bucket_artifacts="stt-artifacts",
    )
    service, session = make_service(
        session_factory, settings, FakeProvider(), dead_store
    )
    try:
        wav = make_wav(tmp_path / "in4.wav", seconds=1.0)
        job = service.create_and_process(audio_path=wav)
        assert job.status is JobStatus.FAILED
        assert job.failure is not None
        assert job.failure.retryable is True
        assert job.failure.code == "STORAGE_ERROR"
    finally:
        session.close()
