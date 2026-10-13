"""OpenAPI contract: the pinned /v1 surface."""
import json
import re
from pathlib import Path

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.providers.registry import ProviderRegistry
from tests.fakes import FakeProvider

REPO_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_PATHS = {
    "/v1/transcriptions",
    "/v1/transcriptions/{job_id}",
    "/v1/transcriptions/{job_id}/result",
    "/v1/transcriptions/{job_id}/retry",
    "/v1/transcriptions/{job_id}/cancel",
    "/v1/providers",
    "/v1/health/live",
    "/v1/health/ready",
}


def _schema(tmp_path) -> dict:
    values = dict(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/db",
    )
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    app = create_app(settings=Settings(**values), registry=registry)
    return app.openapi()


def test_all_pinned_paths_present(tmp_path):
    schema = _schema(tmp_path)
    assert set(schema["paths"]) >= EXPECTED_PATHS


def test_post_transcriptions_is_multipart_with_pinned_fields(tmp_path):
    schema = _schema(tmp_path)
    post = schema["paths"]["/v1/transcriptions"]["post"]
    content = post["requestBody"]["content"]
    assert "multipart/form-data" in content
    body_schema = content["multipart/form-data"]["schema"]
    if "$ref" in body_schema:
        name = body_schema["$ref"].rsplit("/", 1)[-1]
        body_schema = schema["components"]["schemas"][name]
    props = body_schema.get("properties", {})
    assert "file" in props
    assert "provider" in props
    assert "language" in props


def test_post_transcriptions_declares_201_job_record(tmp_path):
    schema = _schema(tmp_path)
    responses = schema["paths"]["/v1/transcriptions"]["post"]["responses"]
    assert "201" in responses
    ref = json.dumps(responses["201"])
    assert "JobRecord" in ref


# --------------------------------------------------------------------- #
# completeness: the schema, the routes and the docs have to agree
# --------------------------------------------------------------------- #

# Everything the app actually serves over HTTP, pinned. A route added
# without a thought fails here first, and the fix is a deliberate edit.
PINNED_HTTP_PATHS = {
    "/v1/transcriptions",
    "/v1/transcriptions/uploads/presign",
    "/v1/transcriptions/preview",
    "/v1/transcriptions/{job_id}",
    "/v1/transcriptions/{job_id}/result",
    "/v1/transcriptions/{job_id}/retry",
    "/v1/transcriptions/{job_id}/cancel",
    "/v1/providers",
    "/v1/health/live",
    "/v1/health/ready",
    "/v1/auth/config",
    "/v1/auth/login",
    "/v1/auth/logout",
    "/v1/auth/me",
    "/v1/evaluations",
    "/v1/evaluations/datasets",
    "/v1/evaluations/metrics",
    "/v1/evaluations/{evaluation_id}",
    "/v1/evaluations/{evaluation_id}/artifact",
}
SOCKET_URL = "/v1/transcriptions/stream"


def _app(tmp_path):
    values = dict(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/db",
    )
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    return create_app(settings=Settings(**values), registry=registry)


def _flatten(app) -> list:
    """The route table, flattened.

    FastAPI 0.133 keeps included routers lazy: ``app.routes`` holds
    ``_IncludedRouter`` wrappers whose real routes appear once
    ``effective_candidates()`` is asked. Anything that inspects routes has
    to go through this, or it sees an empty surface (which is exactly how
    the first version of this test failed).
    """
    flat: list = []
    for route in app.routes:
        candidates = getattr(route, "effective_candidates", None)
        flat.extend(candidates() if callable(candidates) else [route])
    return flat


def _mounted_http_paths(app) -> set[str]:
    return {
        candidate.path
        for candidate in _flatten(app)
        if getattr(candidate, "include_in_schema", False)
        and getattr(candidate, "path", "")
    }


def _placeholders_normalized(text: str) -> str:
    return re.sub(r"\{[a-z_]+\}", "{}", text)


def test_the_pinned_surface_is_exactly_what_is_mounted(tmp_path):
    assert _mounted_http_paths(_app(tmp_path)) == PINNED_HTTP_PATHS


def test_the_schema_covers_exactly_the_mounted_http_surface(tmp_path):
    """Nothing is served that the schema hides, nothing is described that
    is not served."""
    app = _app(tmp_path)
    assert set(app.openapi()["paths"]) == _mounted_http_paths(app) == PINNED_HTTP_PATHS


def test_the_streaming_socket_is_mounted_and_documented(tmp_path):
    """OpenAPI cannot describe a WebSocket, so the contract is pinned
    against the route table and the docs instead of the schema."""
    from starlette.routing import WebSocketRoute

    from stt_platform.api.v1 import streaming as streaming_routes

    sockets = [
        r for r in streaming_routes.router.routes if isinstance(r, WebSocketRoute)
    ]
    assert len(sockets) == 1
    # The router's own path, plus the /v1 prefix create_app mounts it under.
    assert sockets[0].path == "/transcriptions/stream"
    assert SOCKET_URL.endswith(sockets[0].path)

    app = _app(tmp_path)
    mounted = [getattr(r, "original_router", None) for r in app.routes]
    assert any(
        router is streaming_routes.router for router in mounted
    ), "the streaming router is not mounted"

    for doc in ("api.md", "streaming.md"):
        text = (REPO_ROOT / "docs" / doc).read_text(encoding="utf-8")
        assert SOCKET_URL in text, f"{SOCKET_URL} is undocumented in docs/{doc}"


def test_every_endpoint_is_documented_in_docs_api_md(tmp_path):
    app = _app(tmp_path)
    documented = _placeholders_normalized(
        (REPO_ROOT / "docs" / "api.md").read_text(encoding="utf-8")
    )
    missing = sorted(
        path
        for path in _mounted_http_paths(app) | {SOCKET_URL}
        if _placeholders_normalized(path) not in documented
    )
    assert not missing, f"undocumented in docs/api.md: {missing}"


def test_the_committed_spec_matches_the_application(tmp_path):
    """docs/openapi.json is generated, committed, and may not drift.

    Regenerate with: cd backend && uv run python ../scripts/export_openapi.py
    """
    spec_path = REPO_ROOT / "docs" / "openapi.json"
    assert spec_path.is_file(), "docs/openapi.json is missing — export it"
    committed = json.loads(spec_path.read_text(encoding="utf-8"))
    live = _app(tmp_path).openapi()
    assert set(committed["paths"]) == set(live["paths"])
    assert committed["info"]["title"] == live["info"]["title"]
