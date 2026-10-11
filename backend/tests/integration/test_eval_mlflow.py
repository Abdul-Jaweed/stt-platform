"""Against the real MLflow tracking server (docker compose, port 5000).

Skipped honestly when the server is not running: an evaluation must never
depend on it, but when it *is* there we prove the numbers land where the
UI can see them — and that they can be read back and compared.

Start it with:
    cd mlflow/docker-compose && cp .env.dev.example .env && docker compose up -d
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_platform.evaluation.datasets import Clip, Dataset
from stt_platform.evaluation.mlflow_logging import log_evaluation
from stt_platform.evaluation.runner import ClipOutcome, run_evaluation

TRACKING_URI = "http://127.0.0.1:5000"
EXPERIMENT = "stt-evaluation"


def _server_is_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5000), timeout=1.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _server_is_up(),
    reason="MLflow tracking server not reachable on 127.0.0.1:5000",
)


def _result(provider: str, hypothesis: str):
    def transcribe(clip: Clip) -> ClipOutcome:
        return ClipOutcome(
            text=hypothesis,
            segments=(
                SimpleNamespace(
                    start=0.0, end=4.0, words=[SimpleNamespace(start=0.0, end=4.0)]
                ),
            ),
            granularity="word",
            timings={
                "queue_wait_s": 0.2,
                "processing_s": 0.9,
                "end_to_end_s": 1.2,
                "real_time_factor": 0.22,
            },
            attempts=1,
            retries=0,
            provider=provider,
            model=f"{provider}-model",
        )

    clip = Clip(
        clip_id="clip-a",
        audio=Path("/tmp/clip-a.wav"),
        reference_text="hello world",
        duration_s=4.0,
        language="en",
        domain="technical",
        accent="synthetic-us",
        noise="none",
        reference_source="synthetic-tts",
        reference_segments=(),
        sha256="deadbeef",
    )
    dataset = Dataset(
        dataset_id="stt-eval-core",
        version="1.0.0",
        language="en",
        audio_root=Path("/tmp"),
        clips=(clip,),
        missing_audio=(),
        path=Path("/tmp/manifest.json"),
    )
    return run_evaluation(
        dataset=dataset,
        transcribe=transcribe,
        provider=provider,
        model=f"{provider}-model",
        decoding={"language": "en"},
    )


@pytest.fixture()
def mlflow_client():
    import mlflow

    mlflow.set_tracking_uri(TRACKING_URI)
    return mlflow.MlflowClient(tracking_uri=TRACKING_URI)


def test_evaluation_lands_in_mlflow_and_can_be_read_back(tmp_path, mlflow_client):
    outcome = log_evaluation(
        _result("faster-whisper", "hello world"),
        tracking_uri=TRACKING_URI,
        artifact_dir=tmp_path / "eval-a",
    )

    assert outcome["status"] == "logged", outcome
    run = mlflow_client.get_run(outcome["run_id"])
    assert run.data.params["dataset_id"] == "stt-eval-core"
    assert run.data.params["dataset_version"] == "1.0.0"
    assert run.data.params["provider"] == "faster-whisper"
    assert run.data.params["normalization"] == "stt-normalize-v1"
    assert run.data.metrics["wer_micro"] == 0.0
    assert run.data.metrics["clips_scored"] == 1.0
    assert run.data.tags["insufficient_evidence"] == "true"
    assert run.info.status == "FINISHED"

    artifacts = [a.path for a in mlflow_client.list_artifacts(outcome["run_id"])]
    assert "result.json" in artifacts
    experiment = mlflow_client.get_experiment(run.info.experiment_id)
    assert experiment.name == EXPERIMENT


def test_two_providers_are_comparable_in_mlflow(tmp_path, mlflow_client):
    first = log_evaluation(
        _result("faster-whisper", "hello world"),
        tracking_uri=TRACKING_URI,
        artifact_dir=tmp_path / "eval-a",
    )
    second = log_evaluation(
        _result("deepgram", "hello there"),
        tracking_uri=TRACKING_URI,
        artifact_dir=tmp_path / "eval-b",
    )
    assert first["status"] == "logged" and second["status"] == "logged"

    experiment = mlflow_client.get_experiment_by_name(EXPERIMENT)
    runs = mlflow_client.search_runs(
        experiment_ids=[experiment.experiment_id],
        filter_string=f"attributes.run_id IN ('{first['run_id']}', '{second['run_id']}')",
    )
    by_provider = {
        run.data.params["provider"]: run for run in runs
    }
    assert set(by_provider) == {"faster-whisper", "deepgram"}
    assert by_provider["faster-whisper"].data.metrics["wer_micro"] == 0.0
    assert by_provider["deepgram"].data.metrics["wer_micro"] == pytest.approx(0.5)
    assert by_provider["deepgram"].data.params["model"] == "deepgram-model"


def test_artifact_content_matches_the_local_record(tmp_path, mlflow_client):
    outcome = log_evaluation(
        _result("faster-whisper", "hello world"),
        tracking_uri=TRACKING_URI,
        artifact_dir=tmp_path / "eval-a",
    )
    local = json.loads((tmp_path / "eval-a" / "result.json").read_text())

    # The client's chunked downloader retries against the server-proxied
    # artifact store and is slow here, so read the artifact back through the
    # tracking server's HTTP endpoint instead (the UI reads it the same way).
    import httpx

    response = httpx.get(
        f"{TRACKING_URI}/get-artifact",
        params={"path": "result.json", "run_uuid": outcome["run_id"]},
        timeout=30.0,
    )
    assert response.status_code == 200, response.text
    remote = response.json()

    assert remote == local
    assert remote["clips"][0]["transcript_text"] == "hello world"
    assert remote["normalization"]["name"] == "stt-normalize-v1"
