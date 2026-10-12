"""The evaluations API: run, read back, compare — with honest failures.

The dataset and audio root are settings, so these tests are hermetic: a
two-clip corpus in tmp_path, no dependency on the generated corpus.
"""
from __future__ import annotations

import json
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


class _MemoryStore:
    """Minimal object store: enough to run jobs and prove artifacts land."""

    bucket_audio = "audio"
    bucket_artifacts = "artifacts"

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

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
def corpus(tmp_path):
    audio = tmp_path / "corpus"
    audio.mkdir()
    make_wav(audio / "clip-a.wav", seconds=1.0)
    make_wav(audio / "clip-b.wav", seconds=1.0)
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    manifest = {
        "dataset_id": "mini-eval",
        "version": "0.1.0",
        "language": "en",
        "audio_root": "corpus",
        "clips": [
            {
                "clip_id": "clip-a",
                "audio": "clip-a.wav",
                "reference_text": "hello world",
                "duration_s": 1.0,
                "language": "en",
                "domain": "technical",
                "accent": "synthetic-us",
                "noise": "none",
                "reference_source": "synthetic-tts",
                "reference_segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
            },
            {
                "clip_id": "clip-b",
                "audio": "clip-b.wav",
                "reference_text": "hello there",
                "duration_s": 1.0,
                "language": "en",
                "domain": "conversational",
                "accent": "synthetic-us",
                "noise": "none",
                "reference_source": "synthetic-tts",
                "reference_segments": [{"start": 0.0, "end": 1.0, "text": "hello there"}],
            },
        ],
    }
    (datasets / "mini-eval.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def memory_store():
    return _MemoryStore()


@pytest.fixture()
def make_client(pg_engine, pg_url, corpus, memory_store):
    built = []
    store = memory_store

    def _make(*providers, with_store: bool = True, **setting_overrides):
        store.objects.clear()
        Base.metadata.drop_all(pg_engine)
        Base.metadata.create_all(pg_engine)
        values = dict(
            _env_file=None,
            repo_root=corpus,
            temp_dir=corpus / "t",
            audio_store_dir=corpus / "uploads",
            database_url=pg_url,
            max_sync_duration_s=60.0,
            evaluation_datasets_dir=corpus / "datasets",
            evaluation_audio_root=corpus / "corpus",
            mlflow_enabled=False,
        )
        values.update(setting_overrides)
        registry = ProviderRegistry()
        for provider in providers or (FakeProvider(),):
            registry.register(provider)
        app = create_app(
            settings=Settings(**values),
            registry=registry,
            store=store if with_store else None,
        )
        client = TestClient(app)
        built.append((client, app))
        return client

    yield _make

    for client, _app in built:
        client.close()


def test_post_evaluation_runs_and_returns_the_record(make_client, memory_store):
    client = make_client()
    response = client.post(
        "/v1/evaluations", json={"dataset_id": "mini-eval", "language": "en"}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["dataset_id"] == "mini-eval"
    assert body["dataset_version"] == "0.1.0"
    assert body["provider"] == "fake"
    assert body["status"] == "COMPLETED"
    assert body["aggregate"]["clips_total"] == 2
    assert body["aggregate"]["clips_scored"] == 2
    assert body["aggregate"]["wer_micro"] is not None
    assert body["normalization"]["name"] == "stt-normalize-v1"
    assert body["decoding"]["language"] == "en"
    assert body["artifact_key"].startswith("artifacts/evaluation/")
    assert body["mlflow"]["status"] == "disabled"
    assert [clip["clip_id"] for clip in body["clips"]] == ["clip-a", "clip-b"]
    assert body["clips"][0]["timings"]["processing_s"] is not None
    assert body["insufficient_evidence"] is False

    # The full result — transcripts included — is an artifact, not a row.
    artifact = json.loads(
        memory_store.objects[("artifacts", body["artifact_key"])]
    )
    assert artifact["clips"][0]["transcript_text"] == "hello world"
    assert artifact["aggregate"]["clips_scored"] == 2


def test_evaluation_is_readable_by_id_and_listed_for_comparison(make_client):
    client = make_client()
    created = client.post(
        "/v1/evaluations", json={"dataset_id": "mini-eval", "language": "en"}
    ).json()

    fetched = client.get(f"/v1/evaluations/{created['evaluation_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created

    listed = client.get("/v1/evaluations")
    assert listed.status_code == 200
    body = listed.json()
    assert body["count"] == 1
    row = body["evaluations"][0]
    assert row["evaluation_id"] == created["evaluation_id"]
    assert row["provider"] == "fake"
    assert row["clips_scored"] == 2
    assert row["wer_micro"] == created["aggregate"]["wer_micro"]


def test_unknown_evaluation_is_404(make_client):
    client = make_client()
    response = client.get("/v1/evaluations/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVALUATION_NOT_FOUND"


def test_unknown_dataset_is_404(make_client):
    client = make_client()
    response = client.post("/v1/evaluations", json={"dataset_id": "nope"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_dataset_without_generated_audio_is_409(make_client, corpus):
    for path in (corpus / "corpus").glob("*.wav"):
        path.unlink()
    client = make_client()
    response = client.post("/v1/evaluations", json={"dataset_id": "mini-eval"})
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "DATASET_NOT_READY"
    assert "make_eval_corpus" in body["message"]


def test_oversized_evaluation_is_refused_with_a_clear_error(make_client):
    client = make_client(evaluation_max_clips=1)
    response = client.post("/v1/evaluations", json={"dataset_id": "mini-eval"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EVALUATION_TOO_LARGE"


def test_unknown_provider_is_refused(make_client):
    client = make_client()
    response = client.post(
        "/v1/evaluations",
        json={"dataset_id": "mini-eval", "provider": "no-such-provider"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROVIDER_NOT_FOUND"


def test_a_dead_mlflow_server_does_not_fail_the_evaluation(make_client):
    client = make_client(
        mlflow_enabled=True,
        mlflow_tracking_uri="http://127.0.0.1:9",
        mlflow_timeout_s=1,
    )
    response = client.post("/v1/evaluations", json={"dataset_id": "mini-eval"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mlflow"]["status"] == "error"
    assert body["aggregate"]["clips_scored"] == 2


def test_evaluation_records_the_provider_that_actually_ran(make_client):
    client = make_client()
    body = client.post("/v1/evaluations", json={"dataset_id": "mini-eval"}).json()
    assert all(clip["provider"] == "fake" for clip in body["clips"])
    assert all(clip["model"] == "fake-whisper" for clip in body["clips"]), body["clips"][0]["error"]
    assert all(clip["audio_sha256"] for clip in body["clips"])


def test_the_metrics_endpoint_explains_every_metric_it_reports(make_client):
    client = make_client()
    """The explanations come from the specs the code computes with: the
    endpoint names the callable, the formula and the limits for each, and it
    covers the accuracy rates and the run-level numbers alike."""
    response = client.get("/v1/evaluations/metrics")

    assert response.status_code == 200, response.text
    metrics = {item["name"]: item for item in response.json()["metrics"]}
    assert {"wer", "cer", "wer_micro", "wer_macro", "cer_micro", "cer_macro"} <= set(metrics)
    assert {"failure_rate", "rtf_mean", "coverage_mean"} <= set(metrics)

    wer = metrics["wer"]
    assert wer["computed_by"] == "wer_from_counts"
    assert wer["applies_to"] == "per clip"
    assert "reference words" in wer["formula"]
    assert wer["edge_behaviour"] and wer["limitations"]
    assert metrics["failure_rate"]["computed_by"] == "failure_rate"


def test_the_metrics_endpoint_needs_a_session_like_everything_else(make_client):
    """Explanations are not a secret, but the /v1 surface has one door."""
    client = make_client(auth_enabled=True, api_keys="k1:local")
    assert client.get("/v1/evaluations/metrics").status_code == 401


def test_a_run_exposes_the_stored_artifact_with_its_per_clip_diff(make_client, memory_store):
    """The record is an index; the artifact is the evidence. The diff the UI
    renders -- reference, transcript, token-level ops -- comes from here, so
    that the numbers on the record and the words behind them are the same
    run's."""
    client = make_client()
    record = client.post(
        "/v1/evaluations",
        json={"dataset_id": "mini-eval", "provider": "fake"},
    ).json()

    response = client.get(f"/v1/evaluations/{record['evaluation_id']}/artifact")

    assert response.status_code == 200, response.text
    artifact = response.json()
    assert artifact["dataset"]["dataset_id"] == "mini-eval"
    clip = artifact["clips"][0]
    assert clip["reference_text"]
    assert clip["transcript_text"] is not None
    assert clip["alignment"]["code"]
    assert set(clip["alignment"]["alphabet"]) == {
        "equal", "substitute", "delete", "insert",
    }
    # The alignment walks the very token lists it ships.
    code = clip["alignment"]["code"]
    reference_ops = sum(1 for char in code if char in "MSD")
    hypothesis_ops = sum(1 for char in code if char in "MSI")
    assert reference_ops == len(clip["alignment"]["reference_tokens"])
    assert hypothesis_ops == len(clip["alignment"]["hypothesis_tokens"])


def test_an_unknown_run_has_no_artifact(make_client):
    client = make_client()
    assert client.get("/v1/evaluations/nope/artifact").status_code == 404


def test_a_run_without_a_store_says_so_instead_of_inventing_an_artifact(make_client):
    client = make_client(with_store=False)
    record = client.post(
        "/v1/evaluations",
        json={"dataset_id": "mini-eval", "provider": "fake"},
    ).json()

    response = client.get(f"/v1/evaluations/{record['evaluation_id']}/artifact")

    assert response.status_code == 503, response.text
    assert "STORAGE_NOT_CONFIGURED" in response.text
