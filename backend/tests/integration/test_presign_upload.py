"""Presigned uploads: direct-to-storage PUT, then job creation by key."""
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.persistence.models import Base
from stt_platform.providers.registry import ProviderRegistry
from tests.audio_fixtures import make_wav
from tests.fakes import FakeProvider
from tests.integration.harness import build_store
from tests.integration.test_api_async import RecordingStarter
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
def make_client(pg_engine, pg_url, tmp_path, store):
    built = []

    def _make(*providers, starter=None, use_store=True, **setting_overrides):
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
            store=store if use_store else None,
            starter=starter,
        )
        client = TestClient(app)
        built.append((client, app))
        return client

    yield _make

    for client, app in built:
        client.close()
        app.state.engine.dispose()


def test_presign_then_put_then_create_job(make_client, tmp_path):
    starter = RecordingStarter()
    client = make_client(starter=starter)
    wav = make_wav(tmp_path, seconds=1.0)

    r = client.post(
        "/v1/transcriptions/uploads/presign", json={"filename": "big.wav"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object_key"].startswith("audio/local/")
    assert body["object_key"].endswith(".wav")
    assert body["method"] == "PUT"
    assert body["expires_in_s"] > 0

    # The client PUTs the bytes straight to storage (no multipart hop).
    put = httpx.put(
        body["upload_url"],
        content=wav.read_bytes(),
        headers={"Content-Type": "audio/wav"},
    )
    assert put.status_code == 200, put.text

    create = client.post(
        "/v1/transcriptions", data={"object_key": body["object_key"]}
    )
    assert create.status_code == 202, create.text
    job = create.json()
    assert job["status"] == "QUEUED"
    assert job["original_object_key"] == body["object_key"]
    assert job["audio_path"] is None
    assert len(starter.calls) == 1
    assert starter.calls[0]["job_id"] == job["id"]


def test_presign_rejects_unsafe_suffix(make_client):
    client = make_client()

    r = client.post(
        "/v1/transcriptions/uploads/presign",
        json={"filename": "evil.wav/../../etc/passwd"},
    )

    assert r.status_code == 200, r.text
    key = r.json()["object_key"]
    assert ".." not in key
    assert "/etc/" not in key


def test_foreign_or_malformed_object_key_rejected(make_client):
    client = make_client()

    for bad in (
        "audio/other-tenant/abc/original.wav",
        "audio/local/../../etc/original.wav",
        "artifacts/local/abc/transcript.json",
        "audio/local/abc/evil.sh",
    ):
        r = client.post("/v1/transcriptions", data={"object_key": bad})
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["error"]["code"] == "INVALID_OBJECT_KEY", bad


def test_file_and_object_key_are_mutually_exclusive(make_client, tmp_path):
    client = make_client()
    wav = make_wav(tmp_path, seconds=0.3, freq=250)

    both = client.post(
        "/v1/transcriptions",
        files={"file": ("s.wav", wav.read_bytes(), "audio/wav")},
        data={"object_key": "audio/local/x/original.wav"},
    )
    assert both.status_code == 422
    assert both.json()["error"]["code"] == "VALIDATION_ERROR"

    neither = client.post("/v1/transcriptions", data={"language": "en"})
    assert neither.status_code == 422
    assert neither.json()["error"]["code"] == "VALIDATION_ERROR"


def test_presign_requires_storage(make_client):
    client = make_client(use_store=False)

    r = client.post(
        "/v1/transcriptions/uploads/presign", json={"filename": "big.wav"}
    )

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "STORAGE_NOT_CONFIGURED"
