"""Async API path: 202 routing, cancellation, retry re-enqueue, caps."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from stt_platform.core.config import Settings
from stt_platform.domain.status import JobStatus
from stt_platform.main import create_app
from stt_platform.persistence.models import Base, OutboxRow
from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.schemas.jobs import JobFailure
from stt_platform.services.dispatch import dispatch_pending
from tests.audio_fixtures import make_wav
from tests.fakes import FakeProvider
from tests.integration.harness import build_store
from tests.rustfs_container import RustFSContainer


class RecordingStarter:
    """Fake Temporal starter: records starts and cancellations."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.cancels: list[str] = []

    def __call__(self, payload: dict) -> None:
        self.calls.append(payload)

    def cancel(self, workflow_id: str) -> None:
        self.cancels.append(workflow_id)


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
def make_client(pg_engine, pg_url, tmp_path, store):
    built = []

    def _make(*providers, starter=None, client_kwargs=None, **setting_overrides):
        Base.metadata.drop_all(pg_engine)
        Base.metadata.create_all(pg_engine)
        values = dict(
            _env_file=None,
            repo_root=tmp_path,
            temp_dir=tmp_path / "t",
            audio_store_dir=tmp_path / "uploads",
            database_url=pg_url,
            max_sync_duration_s=0.5,
        )
        values.update(setting_overrides)
        registry = ProviderRegistry()
        for provider in providers or (FakeProvider(),):
            registry.register(provider)
        app = create_app(
            settings=Settings(**values),
            registry=registry,
            store=store,
            starter=starter,
        )
        client = TestClient(app, **(client_kwargs or {}))
        built.append((client, app))
        return client

    yield _make

    for client, app in built:
        client.close()
        app.state.engine.dispose()


def _upload(client, wav):
    files = {"file": ("sample.wav", wav.read_bytes(), "audio/wav")}
    return client.post("/v1/transcriptions", files=files)


def test_long_audio_returns_202_and_queued(make_client, tmp_path):
    starter = RecordingStarter()
    client = make_client(starter=starter)
    wav = make_wav(tmp_path, seconds=1.0)  # 1.0s > 0.5s sync cap

    r = _upload(client, wav)

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "QUEUED"
    assert body["original_object_key"]
    assert body["audio_path"] is None
    # Best-effort dispatch right after commit.
    assert len(starter.calls) == 1
    assert starter.calls[0]["job_id"] == body["id"]
    assert starter.calls[0]["workflow_id"].startswith(f"transcribe.{body['id']}.")

    session = client.app.state.session_factory()
    try:
        row = session.execute(
            select(OutboxRow).where(OutboxRow.job_id == body["id"])
        ).scalar_one()
        assert row.dispatched_at is not None
    finally:
        session.close()


def test_longer_than_async_cap_rejected(make_client, tmp_path):
    client = make_client(max_async_duration_s=0.6)
    wav = make_wav(tmp_path, seconds=1.0)

    r = _upload(client, wav)

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "AUDIO_TOO_LONG"


def test_short_audio_still_takes_the_sync_path(make_client, tmp_path):
    client = make_client()  # sync cap 0.5 is below, but wav is 0.3s
    wav = make_wav(tmp_path, seconds=0.3, freq=330)

    r = _upload(client, wav)

    assert r.status_code == 201, r.text
    assert r.json()["status"] == "SUCCEEDED"


def test_cancel_queued_job(make_client, tmp_path):
    client = make_client()  # no starter: nothing dispatches before the cancel
    wav = make_wav(tmp_path, seconds=1.0)
    job = _upload(client, wav).json()
    assert job["status"] == "QUEUED"

    r = client.post(f"/v1/transcriptions/{job['id']}/cancel")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELLED"

    # A dispatcher tick afterwards must not start the cancelled job.
    recorder = RecordingStarter()
    session = client.app.state.session_factory()
    try:
        started = dispatch_pending(OutboxRepository(session), recorder)
    finally:
        session.close()
    assert started == 0
    assert recorder.calls == []


def test_cancel_processing_job_requests_workflow_cancel(make_client, tmp_path):
    starter = RecordingStarter()
    client = make_client(starter=starter)
    wav = make_wav(tmp_path, seconds=1.0)
    job = _upload(client, wav).json()

    session = client.app.state.session_factory()
    try:
        repo = JobRepository(session)
        repo.append_transition(job["id"], JobStatus.PROCESSING)
        row = session.execute(
            select(OutboxRow).where(OutboxRow.job_id == job["id"])
        ).scalar_one()
        expected_workflow_id = starter.calls[0]["workflow_id"]
        assert expected_workflow_id == f"transcribe.{job['id']}.{row.id}"
    finally:
        session.close()

    r = client.post(f"/v1/transcriptions/{job['id']}/cancel")

    assert r.status_code == 200, r.text
    assert starter.cancels == [expected_workflow_id]
    # The workflow owns the final write; until it lands the job is PROCESSING.
    assert r.json()["status"] == "PROCESSING"

    # Simulate the workflow observing the cancellation.
    session = client.app.state.session_factory()
    try:
        JobRepository(session).append_transition(job["id"], JobStatus.CANCELLED)
    finally:
        session.close()
    assert (
        client.get(f"/v1/transcriptions/{job['id']}").json()["status"]
        == "CANCELLED"
    )


def test_cancel_terminal_job_conflicts(make_client, tmp_path):
    client = make_client()
    wav = make_wav(tmp_path, seconds=0.3, freq=300)
    job = _upload(client, wav).json()
    assert job["status"] == "SUCCEEDED"

    r = client.post(f"/v1/transcriptions/{job['id']}/cancel")

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_TRANSITION"


def test_retry_async_failed_enqueues_new_outbox_row(make_client, tmp_path):
    starter = RecordingStarter()
    client = make_client(starter=starter)
    wav = make_wav(tmp_path, seconds=1.0)
    job = _upload(client, wav).json()

    # Simulate the workflow failing the job retryably (e.g. provider flake).
    session = client.app.state.session_factory()
    try:
        repo = JobRepository(session)
        repo.append_transition(job["id"], JobStatus.PROCESSING)
        repo.append_transition(
            job["id"],
            JobStatus.FAILED,
            failure=JobFailure(code="FLAKY", message="not ready", retryable=True),
        )
    finally:
        session.close()

    r = client.post(f"/v1/transcriptions/{job['id']}/retry")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "QUEUED"
    session = client.app.state.session_factory()
    try:
        rows = (
            session.execute(
                select(OutboxRow).where(OutboxRow.job_id == job["id"])
            )
            .scalars()
            .all()
        )
    finally:
        session.close()
    assert len(rows) == 2  # original + re-enqueue
    assert rows[0].id != rows[1].id
    # Both dispatches happened (best-effort at create and at retry).
    assert len(starter.calls) == 2
    assert starter.calls[0]["workflow_id"] != starter.calls[1]["workflow_id"]


def test_retry_sync_failed_uses_local_file_path(make_client, tmp_path):
    """A storeless (sync) deployment keeps the Phase 1 retry semantics."""

    class FlakyOnce(FakeProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def transcribe(self, request):
            self.calls += 1
            if self.calls == 1:
                from stt_platform.domain.errors import ProviderError

                raise ProviderError("FLAKY", "not ready", retryable=True)
            return super().transcribe(request)

    client = make_client(FlakyOnce())
    wav = make_wav(tmp_path, seconds=0.3, freq=280)
    job = _upload(client, wav).json()
    assert job["status"] == "FAILED"
    assert job["failure"]["retryable"] is True

    r = client.post(f"/v1/transcriptions/{job['id']}/retry")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SUCCEEDED"
