"""API tests: /v1/transcriptions over ASGI with a fake provider (real PG)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.main import create_app
from stt_platform.persistence.models import Base
from stt_platform.providers.registry import ProviderRegistry
from tests.audio_fixtures import make_wav
from tests.fakes import FakeProvider

WAV_NAME = "sample.wav"


class FlakyOnce(FakeProvider):
    """Fails the first transcription transiently, then succeeds."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def transcribe(self, request):
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("FAKE_TRANSIENT", "temporary failure", retryable=True)
        return super().transcribe(request)


class NarrowProvider(FakeProvider):
    """Declares a duration cap far below the platform's own."""

    def capabilities(self):
        return super().capabilities().model_copy(
            update={
                "max_duration_s": 0.5,
                "capability_sources": {"max_duration_s": "documented"},
            }
        )


class Exploding(FakeProvider):
    def transcribe(self, request):
        raise RuntimeError("provider exploded")


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def make_client(pg_engine, pg_url, tmp_path):
    built = []

    def _make(*providers, client_kwargs=None, **setting_overrides):
        Base.metadata.drop_all(pg_engine)
        Base.metadata.create_all(pg_engine)
        values = dict(
            _env_file=None,
            repo_root=tmp_path,
            temp_dir=tmp_path / "t",
            audio_store_dir=tmp_path / "uploads",
            database_url=pg_url,
            max_sync_duration_s=60.0,
        )
        values.update(setting_overrides)
        registry = ProviderRegistry()
        for provider in providers or (FakeProvider(),):
            registry.register(provider)
        app = create_app(settings=Settings(**values), registry=registry)
        client = TestClient(app, **(client_kwargs or {}))
        built.append((client, app))
        return client

    yield _make

    for client, app in built:
        client.close()
        app.state.engine.dispose()


def _upload(client, wav, **extra):
    files = {"file": (WAV_NAME, wav.read_bytes(), "audio/wav")}
    return client.post("/v1/transcriptions", files=files, **extra)


def test_upload_transcribe_and_fetch_result(make_client, tmp_path):
    client = make_client()
    wav = make_wav(tmp_path, seconds=1.0)

    r = _upload(
        client,
        wav,
        data={"language": "en"},
        headers={"Idempotency-Key": "k-1"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "SUCCEEDED"
    assert body["actual_provider"] == "fake"
    job_id = body["id"]
    assert r.headers["location"] == f"/v1/transcriptions/{job_id}"

    detail = client.get(f"/v1/transcriptions/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "SUCCEEDED"

    result = client.get(f"/v1/transcriptions/{job_id}/result")
    assert result.status_code == 200
    payload = result.json()
    assert payload["job_id"] == job_id
    assert payload["text"] == "hello world"
    assert payload["timestamp_granularity"] == "word"
    assert payload["segments"][0]["words"][0]["text"] == "hello"


def test_idempotent_replay_creates_single_job(make_client, tmp_path):
    client = make_client()
    wav = make_wav(tmp_path, seconds=1.0)
    headers = {"Idempotency-Key": "replay-1"}

    r1 = _upload(client, wav, headers=headers)
    r2 = _upload(client, wav, headers=headers)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


def test_non_audio_upload_rejected_415(make_client):
    client = make_client()
    r = client.post(
        "/v1/transcriptions",
        files={"file": ("bomb.wav", b"this is not audio " * 60, "audio/wav")},
    )
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA"


def test_oversized_upload_rejected_413(make_client, tmp_path):
    client = make_client(max_upload_bytes=1000)
    wav = make_wav(tmp_path, seconds=1.0)
    r = _upload(client, wav)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


def test_too_long_audio_rejected_422(make_client, tmp_path):
    client = make_client(max_sync_duration_s=0.5)
    wav = make_wav(tmp_path, seconds=1.0)
    r = _upload(client, wav)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "AUDIO_TOO_LONG"


def test_unknown_provider_rejected_422(make_client, tmp_path):
    client = make_client()
    wav = make_wav(tmp_path, seconds=1.0)
    r = _upload(client, wav, data={"provider": "nope"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "PROVIDER_NOT_FOUND"


def test_transient_failure_then_retry_via_api(make_client, tmp_path):
    client = make_client(FlakyOnce())
    wav = make_wav(tmp_path, seconds=1.0)

    r = _upload(client, wav)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "FAILED"
    assert body["failure"]["retryable"] is True
    job_id = body["id"]

    r2 = client.post(f"/v1/transcriptions/{job_id}/retry")
    assert r2.status_code == 200
    assert r2.json()["status"] == "SUCCEEDED"
    assert r2.json()["attempt_count"] == 2


def test_retry_terminal_job_conflict_409(make_client, tmp_path):
    client = make_client()
    wav = make_wav(tmp_path, seconds=1.0)
    job_id = _upload(client, wav).json()["id"]

    r = client.post(f"/v1/transcriptions/{job_id}/retry")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_TRANSITION"


def test_unknown_job_404(make_client):
    client = make_client()
    r = client.get("/v1/transcriptions/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "JOB_NOT_FOUND"
    assert client.get("/v1/transcriptions/nope/result").status_code == 404


def test_cancel_terminal_job_conflicts_409(make_client, tmp_path):
    client = make_client()
    wav = make_wav(tmp_path, seconds=1.0)
    job_id = _upload(client, wav).json()["id"]

    r = client.post(f"/v1/transcriptions/{job_id}/cancel")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_TRANSITION"


def test_missing_file_field_422_error_shape(make_client):
    client = make_client()
    r = client.post("/v1/transcriptions", data={"language": "en"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unhandled_provider_crash_is_500_internal(make_client, tmp_path):
    # Starlette re-raises server errors to the test transport by design;
    # disable that so the JSON 500 body is observable.
    client = make_client(
        Exploding(), client_kwargs={"raise_server_exceptions": False}
    )
    wav = make_wav(tmp_path, seconds=1.0)
    r = _upload(client, wav)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"


def test_provider_duration_cap_rejected_422(make_client, tmp_path):
    """The chosen provider's own limit is enforced before any vendor call."""
    client = make_client(NarrowProvider())
    wav = make_wav(tmp_path, seconds=1.0)

    r = _upload(client, wav)

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "AUDIO_TOO_LONG"
    assert "fake" in error["message"]
