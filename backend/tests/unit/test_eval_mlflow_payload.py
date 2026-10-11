"""What we send to MLflow: params, metrics, tags, artifacts — and what we don't.

MLflow metric values must be floats: an unmeasurable value is *omitted*,
never sent as 0.0, so a missing measurement can never be read as a good one.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_platform.evaluation.datasets import Clip, Dataset
from stt_platform.evaluation.mlflow_logging import (
    build_run_payload,
    log_evaluation,
)
from stt_platform.evaluation.runner import ClipOutcome, run_evaluation


def _dataset() -> Dataset:
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
        sha256="abc",
    )
    return Dataset(
        dataset_id="stt-eval-core",
        version="1.0.0",
        language="en",
        audio_root=Path("/tmp"),
        clips=(clip,),
        missing_audio=(),
        path=Path("/tmp/manifest.json"),
    )


def _result():
    def transcribe(clip: Clip) -> ClipOutcome:
        return ClipOutcome(
            text="hello world",
            segments=(SimpleNamespace(start=0.0, end=4.0, words=[]),),
            granularity="segment",
            timings={
                "queue_wait_s": 0.4,
                "processing_s": 1.0,
                "end_to_end_s": 1.5,
                "real_time_factor": 0.25,
            },
            attempts=1,
            retries=0,
            provider="fake",
            model="fake-v1",
        )

    return run_evaluation(
        dataset=_dataset(),
        transcribe=transcribe,
        provider="fake",
        model="fake-v1",
        decoding={"language": "en"},
    )


def test_payload_records_dataset_provider_and_configuration():
    payload = build_run_payload(_result())

    assert payload["experiment_name"] == "stt-evaluation"
    assert payload["run_name"] == "stt-eval-core@1.0.0-fake-fake-v1"
    assert payload["params"]["dataset_id"] == "stt-eval-core"
    assert payload["params"]["dataset_version"] == "1.0.0"
    assert payload["params"]["provider"] == "fake"
    assert payload["params"]["model"] == "fake-v1"
    assert payload["params"]["normalization"] == "stt-normalize-v1"
    assert '"language": "en"' in payload["params"]["decoding"]
    assert payload["tags"]["dataset_id"] == "stt-eval-core"
    assert payload["tags"]["provider"] == "fake"


def test_payload_metrics_are_floats_and_unmeasurable_values_are_omitted():
    payload = build_run_payload(_result())
    metrics = payload["metrics"]

    assert metrics["wer_micro"] == 0.0
    assert metrics["clips_scored"] == 1.0
    assert all(isinstance(value, float) for value in metrics.values())
    # no words in the hypothesis → availability is unknown, so it is absent
    assert "word_availability_mean" not in metrics
    assert "boundary_error_mean_s" not in metrics


def test_payload_names_the_clip_count_next_to_the_scores():
    payload = build_run_payload(_result())
    assert payload["params"]["clips_total"] == "1"
    assert payload["tags"]["insufficient_evidence"] == "true"


class _FakeClient:
    """Records what would have been sent; never talks to a server."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.experiment: str | None = None
        self.run_name: str | None = None
        self.params: dict[str, str] = {}
        self.metrics: dict[str, float] = {}
        self.tags: dict[str, str] = {}
        self.artifacts: list[str] = []
        self.finished: str | None = None

    def set_experiment(self, name: str) -> None:
        self.experiment = name

    def start_run(self, run_name: str | None = None):
        if self.fail:
            raise RuntimeError("tracking server unreachable")
        self.run_name = run_name
        outer = self

        class _ActiveRun:
            info = SimpleNamespace(run_id="run-123")

            def __enter__(self):
                return self

            def __exit__(self, *exc_info: object) -> None:
                outer.set_terminated("FINISHED")

        return _ActiveRun()

    def log_params(self, params: dict) -> None:
        self.params.update(params)

    def log_metrics(self, metrics: dict) -> None:
        self.metrics.update(metrics)

    def set_tags(self, tags: dict) -> None:
        self.tags.update(tags)

    def log_artifact(self, path: str) -> None:
        self.artifacts.append(path)

    def set_terminated(self, status: str) -> None:
        self.finished = status


def test_logging_sends_everything_and_reports_the_run_id(tmp_path):
    client = _FakeClient()
    outcome = log_evaluation(
        _result(), client=client, artifact_dir=tmp_path
    )

    assert outcome["status"] == "logged"
    assert outcome["run_id"] == "run-123"
    assert client.experiment == "stt-evaluation"
    assert client.params["provider"] == "fake"
    assert client.metrics["wer_micro"] == 0.0
    assert client.tags["dataset_version"] == "1.0.0"
    assert client.finished == "FINISHED"
    assert client.artifacts and client.artifacts[0].endswith("result.json")
    assert Path(client.artifacts[0]).is_file()


def test_a_dead_tracking_server_never_fails_an_evaluation(tmp_path):
    outcome = log_evaluation(
        _result(), client=_FakeClient(fail=True), artifact_dir=tmp_path
    )
    assert outcome["status"] == "error"
    assert "unreachable" in str(outcome["reason"])


@pytest.mark.parametrize("status", ["skipped", "error"])
def test_outcome_is_always_json_safe(status: str):
    import json

    outcome = log_evaluation(
        _result(), client=_FakeClient(fail=True), artifact_dir=Path("/nonexistent")
    )
    json.dumps(outcome)
    assert status in {"skipped", "error"}
