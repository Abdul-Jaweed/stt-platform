"""POST /v1/transcriptions/preview — the transient live path.

Asserts the two things that make it safe to call every few seconds: nothing
is persisted, and no managed provider is ever dialled.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.persistence.models import Base
from stt_platform.providers.registry import ProviderRegistry
from tests.audio_fixtures import make_wav
from tests.fakes import FakeAsyncProvider, FakeProvider

KEYS = "acme:key-acme"  # tenant:key


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


def _client(pg_engine, tmp_path, database_url, **overrides):
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    values = dict(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
        database_url=database_url,
        **overrides,
    )
    registry = ProviderRegistry()
    registry.register(FakeProvider(), default=True)
    registry.register(FakeAsyncProvider())
    app = create_app(settings=Settings(**values), registry=registry)
    return app, TestClient(app)


def _job_count(app) -> int:
    with app.state.engine.connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM jobs")).scalar_one())


def test_preview_returns_text_and_persists_nothing(tmp_path, pg_engine, pg_url):
    app, client = _client(pg_engine, tmp_path, pg_url)
    wav = make_wav(tmp_path, seconds=2.0)
    before = _job_count(app)
    try:
        with open(wav, "rb") as fh:
            response = client.post(
                "/v1/transcriptions/preview",
                files={"file": ("clip.wav", fh, "audio/wav")},
                data={"language": "en"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["text"].strip()
        assert body["is_final"] is False
        assert body["provider"] == "fake"
        assert body["segments"]
        assert body["audio_duration_s"] > 0

        # No job row, no audio left behind.
        assert _job_count(app) == before
        leftovers = list((tmp_path / "a").glob("*"))
        assert leftovers == []
    finally:
        client.close()
        app.state.engine.dispose()


def test_preview_refuses_a_managed_provider(tmp_path, pg_engine, pg_url):
    app, client = _client(pg_engine, tmp_path, pg_url)
    wav = make_wav(tmp_path, seconds=2.0)
    try:
        with open(wav, "rb") as fh:
            response = client.post(
                "/v1/transcriptions/preview",
                files={"file": ("clip.wav", fh, "audio/wav")},
                data={"provider": "fake-async"},
            )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "LIVE_PREVIEW_LOCAL_ONLY"
        assert _job_count(app) == 0
    finally:
        client.close()
        app.state.engine.dispose()


def test_preview_refuses_audio_over_the_cap(tmp_path, pg_engine, pg_url):
    app, client = _client(pg_engine, tmp_path, pg_url, max_live_preview_duration_s=1.0)
    wav = make_wav(tmp_path, seconds=3.0)
    try:
        with open(wav, "rb") as fh:
            response = client.post(
                "/v1/transcriptions/preview",
                files={"file": ("clip.wav", fh, "audio/wav")},
            )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "AUDIO_TOO_LONG"
    finally:
        client.close()
        app.state.engine.dispose()


def test_preview_needs_a_key_when_auth_is_on(tmp_path, pg_engine, pg_url):
    app, client = _client(pg_engine, tmp_path, pg_url, auth_enabled=True, api_keys=KEYS)
    wav = make_wav(tmp_path, seconds=2.0)
    try:
        with open(wav, "rb") as fh:
            anonymous = client.post(
                "/v1/transcriptions/preview",
                files={"file": ("clip.wav", fh, "audio/wav")},
            )
        assert anonymous.status_code == 401

        with open(wav, "rb") as fh:
            keyed = client.post(
                "/v1/transcriptions/preview",
                files={"file": ("clip.wav", fh, "audio/wav")},
                headers={"X-API-Key": "key-acme"},
            )
        assert keyed.status_code == 200, keyed.text
    finally:
        client.close()
        app.state.engine.dispose()


def test_preview_route_does_not_shadow_job_lookup(tmp_path, pg_engine, pg_url):
    """POST /preview is literal; GET /{job_id} keeps working for any id."""
    app, client = _client(pg_engine, tmp_path, pg_url)
    wav = make_wav(tmp_path, seconds=2.0)
    try:
        # The literal POST route answers, and it is not a job lookup.
        with open(wav, "rb") as fh:
            preview = client.post(
                "/v1/transcriptions/preview",
                files={"file": ("clip.wav", fh, "audio/wav")},
            )
        assert preview.status_code == 200, preview.text
        assert "job_id" not in preview.json()

        # A GET for the same path is a job lookup, and says so.
        missing = client.get("/v1/transcriptions/preview")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "JOB_NOT_FOUND"

        job = client.get("/v1/transcriptions/does-not-exist")
        assert job.status_code == 404
        assert job.json()["error"]["code"] == "JOB_NOT_FOUND"
    finally:
        client.close()
        app.state.engine.dispose()
