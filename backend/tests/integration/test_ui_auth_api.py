"""The UI login: credentials, the session cookie, and route protection.

The browser must never hold a secret: it gets an HttpOnly session cookie whose
tenant the server assigns. API keys keep working unchanged for machine
clients, and a session cookie works even where keys are required — otherwise
the UI could not talk to an authenticated API without shipping a key.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.persistence.models import Base
from stt_platform.providers.registry import ProviderRegistry
from tests.fakes import FakeProvider

DEFAULT_CREDS = {"username": "stt", "password": "stt"}
SECRET = "integration-test-secret"


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def make_client(pg_engine, pg_url, tmp_path):
    built = []

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
            mlflow_enabled=False,
            ui_auth_enabled=True,
            session_secret=SECRET,
        )
        values.update(setting_overrides)
        app = create_app(settings=Settings(**values), registry=ProviderRegistry())
        app.state.registry.register(FakeProvider())
        client = TestClient(app)
        built.append(client)
        return client

    yield _make

    for client in built:
        client.close()


def test_login_with_the_default_credentials_sets_a_session_cookie(make_client):
    client = make_client()
    response = client.post("/v1/auth/login", json=DEFAULT_CREDS)

    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    assert "stt_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.replace("samesite=lax", "SameSite=lax")
    assert response.json()["username"] == "stt"
    assert response.json()["tenant"] == "local"
    assert "password" not in response.text


def test_login_with_a_wrong_password_is_401_and_sets_no_cookie(make_client):
    client = make_client()
    response = client.post(
        "/v1/auth/login", json={"username": "stt", "password": "wrong"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert "set-cookie" not in response.headers


def test_login_does_not_reveal_whether_the_user_exists(make_client):
    client = make_client()
    unknown = client.post(
        "/v1/auth/login", json={"username": "nobody", "password": "stt"}
    )
    wrong = client.post(
        "/v1/auth/login", json={"username": "stt", "password": "nope"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_login_requires_both_fields(make_client):
    client = make_client()

    assert client.post("/v1/auth/login", json={"username": "stt"}).status_code == 422


def test_me_without_a_session_is_401_when_ui_auth_is_on(make_client):
    client = make_client()

    response = client.get("/v1/auth/me")

    assert response.status_code == 401


def test_me_reports_the_anonymous_local_user_when_ui_auth_is_off(make_client):
    client = make_client(ui_auth_enabled=False)

    response = client.get("/v1/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False
    assert body["tenant"] == "local"
    assert body["ui_auth_enabled"] is False


def test_me_reports_the_session_after_login(make_client):
    client = make_client()
    client.post("/v1/auth/login", json=DEFAULT_CREDS)

    body = client.get("/v1/auth/me").json()

    assert body["authenticated"] is True
    assert body["username"] == "stt"
    assert body["ui_auth_enabled"] is True
    assert body["expires_at"]


def test_logout_clears_the_cookie(make_client):
    client = make_client()
    client.post("/v1/auth/login", json=DEFAULT_CREDS)

    response = client.post("/v1/auth/logout")

    assert response.status_code == 200
    assert "stt_session=" in response.headers.get("set-cookie", "")
    assert client.get("/v1/auth/me").status_code == 401


def test_the_session_authenticates_where_api_keys_are_required(make_client):
    client = make_client(auth_enabled=True, api_keys="acme:key-acme", ui_tenant="acme")

    assert client.get("/v1/providers").status_code == 401
    client.post("/v1/auth/login", json=DEFAULT_CREDS)
    response = client.get("/v1/providers")

    assert response.status_code == 200, response.text


def test_the_session_tenant_is_server_assigned(make_client):
    client = make_client(auth_enabled=True, api_keys="acme:key-acme", ui_tenant="acme")
    client.post("/v1/auth/login", json=DEFAULT_CREDS)

    # A client-supplied tenant hint is ignored: the scope comes from the server.
    response = client.get("/v1/providers?tenant=globex", headers={"X-Tenant": "globex"})

    assert response.status_code == 200
    assert client.get("/v1/auth/me").json()["tenant"] == "acme"


def test_a_bearer_token_works_for_non_browser_clients(make_client):
    client = make_client(auth_enabled=True, api_keys="acme:key-acme", ui_tenant="acme")
    token = client.post("/v1/auth/login", json=DEFAULT_CREDS).json()["token"]

    anonymous = TestClient(client.app)
    response = anonymous.get(
        "/v1/providers", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200, response.text


def test_a_tampered_cookie_is_rejected(make_client):
    client = make_client(auth_enabled=True, api_keys="acme:key-acme")
    client.post("/v1/auth/login", json=DEFAULT_CREDS)
    good = client.cookies["stt_session"]

    client.cookies.set("stt_session", good[:-3] + "AAA")
    response = client.get("/v1/providers")

    assert response.status_code == 401


def test_repeated_failed_logins_are_rate_limited(make_client):
    client = make_client(login_max_attempts_per_minute=3)

    codes = [
        client.post(
            "/v1/auth/login", json={"username": "stt", "password": "wrong"}
        ).status_code
        for _ in range(4)
    ]

    assert codes[:3] == [401, 401, 401]
    assert codes[3] == 429


def test_production_refuses_the_default_password(make_client):
    with pytest.raises(ValueError, match="STT_UI_PASSWORD"):
        make_client(environment="production", session_cookie_secure=True)


def test_production_refuses_a_short_password(make_client):
    with pytest.raises(ValueError, match="at least"):
        make_client(
            environment="production", ui_password="elevenchars", session_cookie_secure=True
        )


def test_production_requires_a_session_secret(make_client):
    with pytest.raises(ValueError, match="STT_SESSION_SECRET"):
        make_client(
            environment="production",
            session_secret=None,
            ui_password="a-proper-production-passphrase",
            session_cookie_secure=True,
        )


def test_production_accepts_a_strong_password_and_a_secret(make_client):
    client = make_client(
        environment="production",
        session_secret="production-secret",
        ui_password="a-proper-production-passphrase",
        session_cookie_secure=True,
    )

    response = client.post(
        "/v1/auth/login",
        json={"username": "stt", "password": "a-proper-production-passphrase"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_a_password_hash_setting_replaces_the_plaintext_one(make_client):
    from stt_platform.services.auth import hash_password

    client = make_client(
        environment="production",
        session_secret="production-secret",
        ui_password=None,
        ui_password_hash=hash_password("hashed-in-config"),
        session_cookie_secure=True,
    )

    assert (
        client.post(
            "/v1/auth/login",
            json={"username": "stt", "password": "hashed-in-config"},
        ).status_code
        == 200
    )
