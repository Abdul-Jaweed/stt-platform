"""Providers + health endpoints."""
from fastapi.testclient import TestClient

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.providers.registry import ProviderRegistry
from tests.fakes import FakeProvider

DEAD_DB_URL = "postgresql+psycopg://u:p@127.0.0.1:1/db?connect_timeout=1"


def _client(tmp_path, database_url):
    values = dict(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
        database_url=database_url,
    )
    registry = ProviderRegistry()
    registry.register(FakeProvider(), default=True)
    app = create_app(settings=Settings(**values), registry=registry)
    return app, TestClient(app)


def test_providers_listing_includes_capabilities(tmp_path):
    app, client = _client(tmp_path, DEAD_DB_URL)
    try:
        r = client.get("/v1/providers")
        assert r.status_code == 200
        providers = r.json()["providers"]
        assert [p["id"] for p in providers] == ["fake"]
        caps = providers[0]["capabilities"]
        assert caps["word_timestamps"] is True
        assert caps["capability_sources"]["word_timestamps"] == "verified"
    finally:
        client.close()
        app.state.engine.dispose()


def test_health_live(tmp_path):
    app, client = _client(tmp_path, DEAD_DB_URL)
    try:
        r = client.get("/v1/health/live")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
    finally:
        client.close()
        app.state.engine.dispose()


def test_health_ready_with_database(tmp_path, pg_url):
    app, client = _client(tmp_path, pg_url)
    try:
        r = client.get("/v1/health/ready")
        assert r.status_code == 200
        assert r.json() == {"status": "ready"}
    finally:
        client.close()
        app.state.engine.dispose()


def test_health_ready_reports_503_when_database_down(tmp_path):
    app, client = _client(tmp_path, DEAD_DB_URL)
    try:
        r = client.get("/v1/health/ready")
        assert r.status_code == 503
        assert r.json()["status"] == "not_ready"
    finally:
        client.close()
        app.state.engine.dispose()
