"""CORS: the browser frontend runs on its own origin and calls the API."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.persistence.models import Base

FRONTEND_ORIGIN = "http://127.0.0.1:4173"


@pytest.fixture()
def client(pg_url, tmp_path):
    engine = create_engine(pg_url, future=True)
    Base.metadata.create_all(engine)
    app = create_app(
        settings=Settings(
            _env_file=None,
            repo_root=tmp_path,
            temp_dir=tmp_path / "t",
            audio_store_dir=tmp_path / "uploads",
            database_url=pg_url,
        )
    )
    test_client = TestClient(app)
    yield test_client
    test_client.close()
    app.state.engine.dispose()
    engine.dispose()


def test_preflight_allows_the_frontend_origin(client):
    response = client.options(
        "/v1/transcriptions",
        headers={
            "Origin": FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,idempotency-key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
    allow_headers = response.headers["access-control-allow-headers"].lower()
    assert "idempotency-key" in allow_headers


def test_actual_request_carries_allow_origin(client):
    response = client.get(
        "/v1/health/live", headers={"Origin": FRONTEND_ORIGIN}
    )
    assert response.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN


def test_unlisted_origin_is_not_allowed(client):
    response = client.options(
        "/v1/transcriptions",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers
