"""Tenant isolation through the API: authentication on, scopes enforced.

The tenant is never taken from the request — only from the presented key.
Cross-tenant access must not even confirm that the object exists.
"""
from __future__ import annotations

import logging
import pathlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.persistence.models import Base
from stt_platform.providers.registry import ProviderRegistry
from tests.audio_fixtures import make_wav
from tests.fakes import FakeProvider

ACME = "key-acme"
GLOBEX = "key-globex"
KEYS = f"acme:{ACME},globex:{GLOBEX}"


class _MemoryStore:
    """Enough object store for the presign flow (no S3 in this suite)."""

    bucket_audio = "audio"
    bucket_artifacts = "artifacts"

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def presign_put(self, bucket: str, key: str, expires_s: int = 900) -> str:
        return f"http://storage.invalid/{bucket}/{key}?sig=stub"

    def put_bytes(self, bucket: str, key: str, data: bytes, **kwargs) -> None:
        self.objects[(bucket, key)] = data

    def put_file(self, bucket: str, key: str, path) -> None:
        self.objects[(bucket, key)] = pathlib.Path(path).read_bytes()

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]

    def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects

    def delete(self, bucket: str, key: str) -> None:
        self.objects.pop((bucket, key), None)


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def make_client(pg_engine, pg_url, tmp_path):
    built = []
    store = _MemoryStore()

    def _make(**setting_overrides):
        Base.metadata.drop_all(pg_engine)
        Base.metadata.create_all(pg_engine)
        values = dict(
            _env_file=None,
            repo_root=tmp_path,
            temp_dir=tmp_path / "t",
            audio_store_dir=tmp_path / "uploads",
            database_url=pg_url,
            max_sync_duration_s=60.0,
            auth_enabled=True,
            api_keys=KEYS,
            mlflow_enabled=False,
        )
        values.update(setting_overrides)
        app = create_app(
            settings=Settings(**values), registry=ProviderRegistry(), store=store
        )
        registry = app.state.registry
        registry.register(FakeProvider())
        client = TestClient(app)
        built.append(client)
        return client

    yield _make

    for client in built:
        client.close()


def _upload(client, tmp_path, *, headers=None, name="clip.wav"):
    audio = make_wav(tmp_path / name, seconds=1.0)
    with audio.open("rb") as handle:
        return client.post(
            "/v1/transcriptions",
            files={"file": (name, handle, "audio/wav")},
            data={"provider": "fake"},
            headers=headers or {},
        )


def test_health_stays_open_for_probes(make_client):
    client = make_client()
    assert client.get("/v1/health/live").status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/transcriptions"),
        ("get", "/v1/transcriptions/whatever"),
        ("get", "/v1/transcriptions/whatever/result"),
        ("post", "/v1/transcriptions/whatever/retry"),
        ("post", "/v1/transcriptions/whatever/cancel"),
        ("post", "/v1/transcriptions/uploads/presign"),
        ("get", "/v1/providers"),
        ("get", "/v1/evaluations"),
    ],
)
def test_every_v1_route_requires_a_key(make_client, method, path):
    client = make_client()
    response = getattr(client, method)(path)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_a_wrong_key_is_rejected(make_client):
    client = make_client()
    response = client.get("/v1/providers", headers={"X-API-Key": "not-a-key"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_a_valid_key_creates_a_job_in_its_own_scope(make_client, tmp_path):
    client = make_client()
    created = _upload(client, tmp_path, headers={"X-API-Key": ACME})
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    mine = client.get(f"/v1/transcriptions/{job_id}", headers={"X-API-Key": ACME})
    assert mine.status_code == 200

    theirs = client.get(
        f"/v1/transcriptions/{job_id}", headers={"X-API-Key": GLOBEX}
    )
    assert theirs.status_code == 404
    assert theirs.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_cross_tenant_result_retry_and_cancel_are_all_404(make_client, tmp_path):
    client = make_client()
    job_id = _upload(client, tmp_path, headers={"X-API-Key": ACME}).json()["id"]

    for method, path in [
        ("get", f"/v1/transcriptions/{job_id}/result"),
        ("post", f"/v1/transcriptions/{job_id}/retry"),
        ("post", f"/v1/transcriptions/{job_id}/cancel"),
    ]:
        response = getattr(client, method)(
            path, headers={"X-API-Key": GLOBEX}
        )
        assert response.status_code == 404, f"{method} {path}: {response.text}"


def test_the_same_idempotency_key_is_per_tenant(make_client, tmp_path):
    """One tenant's idempotency key must not replay into another tenant's job."""
    client = make_client()
    headers = {"X-API-Key": ACME, "Idempotency-Key": "shared-key"}
    first = _upload(client, tmp_path, headers=headers, name="a.wav")
    assert first.status_code == 201

    other = _upload(
        client,
        tmp_path,
        headers={"X-API-Key": GLOBEX, "Idempotency-Key": "shared-key"},
        name="b.wav",
    )
    assert other.status_code == 201
    assert other.json()["id"] != first.json()["id"]


def test_presigned_upload_keys_are_bound_to_the_tenant(make_client, tmp_path):
    client = make_client()
    presigned = client.post(
        "/v1/transcriptions/uploads/presign",
        json={"filename": "clip.wav", "content_type": "audio/wav"},
        headers={"X-API-Key": ACME},
    )
    assert presigned.status_code in (200, 201), presigned.text
    object_key = presigned.json()["object_key"]
    assert object_key.startswith("audio/acme/")

    # The same key handed to another tenant is refused, not re-scoped.
    stolen = client.post(
        "/v1/transcriptions",
        data={"object_key": object_key},
        headers={"X-API-Key": GLOBEX},
    )
    assert stolen.status_code == 422
    assert stolen.json()["error"]["code"] == "INVALID_OBJECT_KEY"


def test_auth_disabled_keeps_the_local_developer_flow(make_client, tmp_path):
    client = make_client(auth_enabled=False, api_keys="")
    created = _upload(client, tmp_path)
    assert created.status_code == 201
    assert (
        client.get(f"/v1/transcriptions/{created.json()['id']}").status_code == 200
    )


def test_deleting_your_own_job_removes_it(make_client, tmp_path):
    client = make_client()
    job_id = _upload(client, tmp_path, headers={"X-API-Key": ACME}).json()["id"]

    deleted = client.delete(
        f"/v1/transcriptions/{job_id}", headers={"X-API-Key": ACME}
    )
    assert deleted.status_code == 204
    assert (
        client.get(
            f"/v1/transcriptions/{job_id}", headers={"X-API-Key": ACME}
        ).status_code
        == 404
    )


def test_another_tenant_cannot_delete_your_job(make_client, tmp_path):
    client = make_client()
    job_id = _upload(client, tmp_path, headers={"X-API-Key": ACME}).json()["id"]

    refused = client.delete(
        f"/v1/transcriptions/{job_id}", headers={"X-API-Key": GLOBEX}
    )
    assert refused.status_code == 404
    assert refused.json()["error"]["code"] == "JOB_NOT_FOUND"
    # The owner's job is untouched.
    assert (
        client.get(
            f"/v1/transcriptions/{job_id}", headers={"X-API-Key": ACME}
        ).status_code
        == 200
    )


def test_deleting_an_unknown_job_is_404(make_client):
    client = make_client()
    response = client.delete(
        "/v1/transcriptions/does-not-exist", headers={"X-API-Key": ACME}
    )
    assert response.status_code == 404


def test_a_tenant_over_its_quota_gets_429(make_client, tmp_path):
    client = make_client(max_requests_per_minute=2)
    for _ in range(2):
        assert _upload(client, tmp_path, headers={"X-API-Key": ACME}).status_code in (
            200,
            201,
        )
    limited = _upload(client, tmp_path, headers={"X-API-Key": ACME})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    # A different tenant still has its own budget.
    assert _upload(client, tmp_path, headers={"X-API-Key": GLOBEX}).status_code in (
        200,
        201,
    )


def test_the_quota_is_off_by_default(make_client, tmp_path):
    client = make_client()  # no max_requests_per_minute → 0 → disabled
    for _ in range(5):
        assert _upload(client, tmp_path, headers={"X-API-Key": ACME}).status_code in (
            200,
            201,
        )


def test_the_api_key_never_reaches_the_logs(make_client, tmp_path, caplog):
    client = make_client()
    with caplog.at_level(logging.DEBUG):
        assert _upload(client, tmp_path, headers={"X-API-Key": ACME}).status_code in (
            200,
            201,
        )
    assert ACME not in caplog.text
    assert "x-api-key" not in caplog.text.lower()


def test_transcript_text_never_reaches_the_logs(make_client, tmp_path, caplog):
    """The fake provider returns "hello world"; it must not be in the logs."""
    client = make_client()
    with caplog.at_level(logging.DEBUG):
        job_id = _upload(client, tmp_path, headers={"X-API-Key": ACME}).json()["id"]
        result = client.get(
            f"/v1/transcriptions/{job_id}/result", headers={"X-API-Key": ACME}
        )
    assert result.status_code == 200
    assert result.json()["text"] == "hello world"
    assert "hello world" not in caplog.text
