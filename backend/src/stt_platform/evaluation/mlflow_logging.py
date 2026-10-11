"""MLflow tracking for evaluations — reproducible, and never in the way.

MLflow is the index over evaluations: one run per (dataset version,
provider, configuration) with the parameters, metrics and the full result
artifact. The local record (PostgreSQL + object storage) stays the source
of truth, so a tracking server that is down or misconfigured downgrades the
result to ``status: "error"`` instead of failing the evaluation.

Metric values must be floats; an unmeasurable value is omitted rather than
sent as 0.0, because 0.0 reads as "perfect" on a dashboard.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from stt_platform.evaluation.runner import EvaluationResult

EXPERIMENT_NAME = "stt-evaluation"
DEFAULT_TRACKING_URI = "http://127.0.0.1:5000"

# Params are strings in MLflow; these are the ones worth slicing runs by.
_PARAM_KEYS = (
    "dataset_id",
    "dataset_version",
    "provider",
    "model",
    "clips_total",
    "normalization",
    "decoding",
)


def _metric_values(result: EvaluationResult) -> dict[str, float]:
    aggregate = result.aggregate
    candidates: dict[str, Any] = {
        "wer_micro": aggregate.get("wer_micro"),
        "wer_macro": aggregate.get("wer_macro"),
        "cer_micro": aggregate.get("cer_micro"),
        "cer_macro": aggregate.get("cer_macro"),
        "clips_scored": aggregate.get("clips_scored"),
        "clips_total": aggregate.get("clips_total"),
        "failures": aggregate.get("failures"),
        "failure_rate": aggregate.get("failure_rate"),
        "attempts_total": aggregate.get("attempts_total"),
        "retries_total": aggregate.get("retries_total"),
        "queue_wait_p50": aggregate.get("queue_wait_p50"),
        "queue_wait_p95": aggregate.get("queue_wait_p95"),
        "processing_p50": aggregate.get("processing_p50"),
        "processing_p95": aggregate.get("processing_p95"),
        "end_to_end_p50": aggregate.get("end_to_end_p50"),
        "end_to_end_p95": aggregate.get("end_to_end_p95"),
        "rtf_mean": aggregate.get("rtf_mean"),
        "segment_availability_mean": aggregate.get("segment_availability_mean"),
        "word_availability_mean": aggregate.get("word_availability_mean"),
        "boundary_error_mean_s": aggregate.get("boundary_error_mean_s"),
        "coverage_mean": aggregate.get("coverage_mean"),
        "monotonicity_violations_total": aggregate.get(
            "monotonicity_violations_total"
        ),
        "degenerate_spans": aggregate.get("degenerate_spans"),
        "evaluation_duration_s": result.duration_s,
    }
    return {
        name: float(value)
        for name, value in candidates.items()
        if value is not None and isinstance(value, (int, float))
    }


def build_run_payload(
    result: EvaluationResult, *, experiment_name: str = EXPERIMENT_NAME
) -> dict[str, Any]:
    """The exact run MLflow will receive — pure, so it can be asserted."""
    model_suffix = f"-{result.model}" if result.model else ""
    return {
        "experiment_name": experiment_name,
        "run_name": (
            f"{result.dataset_id}@{result.dataset_version}"
            f"-{result.provider}{model_suffix}"
        ),
        "params": {
            "dataset_id": result.dataset_id,
            "dataset_version": result.dataset_version,
            "provider": result.provider,
            "model": result.model or "unknown",
            "clips_total": str(result.aggregate.get("clips_total", 0)),
            "normalization": result.normalization.name,
            "decoding": json.dumps(result.decoding, sort_keys=True),
        },
        "metrics": _metric_values(result),
        "tags": {
            "dataset_id": result.dataset_id,
            "dataset_version": result.dataset_version,
            "provider": result.provider,
            "model": result.model or "unknown",
            "normalization": result.normalization.name,
            "insufficient_evidence": str(result.insufficient_evidence).lower(),
            "started_at": result.started_at,
            "finished_at": result.finished_at,
        },
    }


def _default_client(tracking_uri: str) -> Any:
    # Imported lazily so the app never pays for mlflow at startup, and the
    # agent hint it prints on import is silenced (we are not tracing LLMs).
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    return mlflow


def log_evaluation(
    result: EvaluationResult,
    *,
    client: Any | None = None,
    tracking_uri: str | None = None,
    artifact_dir: Path | None = None,
    experiment_name: str = EXPERIMENT_NAME,
) -> dict[str, Any]:
    """Log the evaluation; return a JSON-safe outcome, never raise."""
    payload = build_run_payload(result, experiment_name=experiment_name)
    directory = Path(artifact_dir) if artifact_dir else None
    try:
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
        artifact_path = (directory or Path(".")) / "result.json"
        artifact_path.write_text(
            json.dumps(result.as_dict(), indent=2), encoding="utf-8"
        )

        client = client or _default_client(
            tracking_uri or DEFAULT_TRACKING_URI
        )
        client.set_experiment(payload["experiment_name"])
        with client.start_run(run_name=payload["run_name"]) as run:
            client.log_params(payload["params"])
            client.log_metrics(payload["metrics"])
            client.set_tags(payload["tags"])
            if directory is not None:
                client.log_artifact(str(artifact_path))
            run_id = run.info.run_id
        return {
            "status": "logged",
            "run_id": run_id,
            "run_name": payload["run_name"],
            "experiment": payload["experiment_name"],
            "metrics_logged": sorted(payload["metrics"]),
        }
    except Exception as exc:  # a dead tracking server is not a failed evaluation
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
