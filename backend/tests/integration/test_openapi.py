"""OpenAPI contract: the pinned /v1 surface."""
import json

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.providers.registry import ProviderRegistry
from tests.fakes import FakeProvider

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
    assert EXPECTED_PATHS <= set(schema["paths"])


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
