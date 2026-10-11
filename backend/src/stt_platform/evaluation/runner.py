"""The evaluation runner: dataset x provider → per-clip results + aggregate.

One clip failing is data, not a crash: it is recorded, counted in the
failure rate, and the run continues. A clip whose audio is missing is a
different thing entirely — that is an unusable dataset and it fails loudly.

Timings come from whoever transcribes (in production, the job record), so
an evaluation and the observability metrics describe the same pipeline.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stt_platform.evaluation.datasets import Clip, Dataset
from stt_platform.evaluation.metrics import (
    AccuracyScore,
    NormalizationRules,
    score,
)
from stt_platform.evaluation.metrics import (
    aggregate as aggregate_scores,
)
from stt_platform.evaluation.timestamps import (
    TimestampReport,
    evaluate_timestamps,
)

MIN_SCORED_CLIPS = 2


class EvaluationError(RuntimeError):
    """The evaluation cannot be run as configured."""


@dataclass(frozen=True)
class ClipOutcome:
    """What a transcription attempt produced for one clip."""

    text: str
    segments: tuple[Any, ...]
    granularity: str | None
    timings: dict[str, float | None]
    attempts: int
    retries: int
    provider: str
    model: str | None = None


@dataclass(frozen=True)
class ClipResult:
    clip_id: str
    status: str  # scored | failed
    provider: str
    model: str | None
    audio_sha256: str | None
    transcript_text: str | None
    accuracy: AccuracyScore | None
    timestamps: TimestampReport | None
    timings: dict[str, float | None]
    attempts: int
    retries: int
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "audio_sha256": self.audio_sha256,
            "transcript_text": self.transcript_text,
            "accuracy": self.accuracy.as_dict() if self.accuracy else None,
            "timestamps": self.timestamps.as_dict() if self.timestamps else None,
            "timings": self.timings,
            "attempts": self.attempts,
            "retries": self.retries,
            "error": self.error,
        }


@dataclass(frozen=True)
class EvaluationResult:
    dataset_id: str
    dataset_version: str
    provider: str
    model: str | None
    decoding: dict[str, Any]
    normalization: NormalizationRules
    clips: tuple[ClipResult, ...]
    aggregate: dict[str, Any]
    by_group: dict[str, dict[str, dict[str, Any]]]
    insufficient_evidence: bool
    started_at: str
    finished_at: str
    duration_s: float
    mlflow: dict[str, Any] | None = field(default=None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": {
                "dataset_id": self.dataset_id,
                "version": self.dataset_version,
            },
            "provider": self.provider,
            "model": self.model,
            "decoding": self.decoding,
            "normalization": self.normalization.describe(),
            "clips": [clip.as_dict() for clip in self.clips],
            "aggregate": self.aggregate,
            "by_group": self.by_group,
            "insufficient_evidence": self.insufficient_evidence,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "mlflow": self.mlflow,
        }


def _percentile(values: Iterable[float | None], fraction: float) -> float | None:
    usable = sorted(value for value in values if value is not None)
    if not usable:
        return None
    index = min(len(usable) - 1, max(0, round(fraction * (len(usable) - 1))))
    return usable[index]


def _mean(values: Iterable[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


def _group_summary(results: list[ClipResult]) -> dict[str, Any]:
    scored = [item for item in results if item.accuracy is not None]
    summary = aggregate_scores([item.accuracy for item in scored if item.accuracy])
    return {
        "clips": len(results),
        "clips_scored": summary["clips_scored"],
        "wer_micro": summary["wer_micro"],
        "wer_macro": summary["wer_macro"],
        "cer_micro": summary["cer_micro"],
    }


def sha256_of(path: Path) -> str | None:
    """Hash the exact bytes that were scored (provenance, not decoration)."""
    import hashlib

    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _aggregate(clips: list[ClipResult]) -> dict[str, Any]:
    scored = [item for item in clips if item.accuracy is not None]
    summary = dict(aggregate_scores([item.accuracy for item in scored if item.accuracy]))
    failures = len(clips) - len(scored)
    summary.update(
        {
            "clips_total": len(clips),
            "failures": failures,
            "failure_rate": failures / len(clips) if clips else None,
            "retries_total": sum(item.retries for item in clips),
            "attempts_total": sum(item.attempts for item in clips),
            "queue_wait_p50": _percentile(
                (item.timings.get("queue_wait_s") for item in clips), 0.5
            ),
            "queue_wait_p95": _percentile(
                (item.timings.get("queue_wait_s") for item in clips), 0.95
            ),
            "processing_p50": _percentile(
                (item.timings.get("processing_s") for item in clips), 0.5
            ),
            "processing_p95": _percentile(
                (item.timings.get("processing_s") for item in clips), 0.95
            ),
            "end_to_end_p50": _percentile(
                (item.timings.get("end_to_end_s") for item in clips), 0.5
            ),
            "end_to_end_p95": _percentile(
                (item.timings.get("end_to_end_s") for item in clips), 0.95
            ),
            "rtf_mean": _mean(item.timings.get("real_time_factor") for item in clips),
            "segment_availability_mean": _mean(
                item.timestamps.segment_availability
                for item in clips
                if item.timestamps
            ),
            "word_availability_mean": _mean(
                item.timestamps.word_availability
                for item in clips
                if item.timestamps
            ),
            "boundary_error_mean_s": _mean(
                item.timestamps.boundary_error_s
                for item in clips
                if item.timestamps
            ),
            "coverage_mean": _mean(
                item.timestamps.coverage for item in clips if item.timestamps
            ),
            "monotonicity_violations_total": sum(
                item.timestamps.monotonicity_violations
                for item in clips
                if item.timestamps
            ),
            "degenerate_spans": sum(
                1
                for item in clips
                if item.timestamps and item.timestamps.degenerate_single_span
            ),
        }
    )
    return summary


def _group_by(
    clips: list[Clip], results: list[ClipResult], field_name: str
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[ClipResult]] = {}
    by_id = {item.clip_id: item for item in results}
    for clip in clips:
        result = by_id.get(clip.clip_id)
        if result is None:
            continue
        key = str(getattr(clip, field_name) or "unknown")
        buckets.setdefault(key, []).append(result)
    return {key: _group_summary(items) for key, items in sorted(buckets.items())}


def run_evaluation(
    *,
    dataset: Dataset,
    transcribe: Callable[[Clip], ClipOutcome],
    provider: str,
    model: str | None = None,
    decoding: dict[str, Any] | None = None,
    rules: NormalizationRules | None = None,
) -> EvaluationResult:
    """Score every scorable clip in ``dataset`` and aggregate the result."""
    missing = dataset.missing_scored_audio()
    if missing:
        raise EvaluationError(
            "dataset is unusable: missing audio for scored clip(s) "
            f"{', '.join(missing)} — run scripts/make_eval_corpus.py"
        )

    rules = rules or NormalizationRules()
    scored_clips = dataset.scored_clips()
    started = time.time()
    results: list[ClipResult] = []

    for clip in scored_clips:
        try:
            outcome = transcribe(clip)
        except Exception as exc:  # a failing clip is data, not a crash
            results.append(
                ClipResult(
                    clip_id=clip.clip_id,
                    status="failed",
                    provider=provider,
                    model=model,
                    audio_sha256=sha256_of(clip.audio) or clip.sha256,
                    transcript_text=None,
                    accuracy=None,
                    timestamps=None,
                    timings={},
                    attempts=0,
                    retries=0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        accuracy = score(clip.reference_text or "", outcome.text, rules)
        timestamps = evaluate_timestamps(
            reference_segments=clip.reference_segments,
            hypothesis_segments=outcome.segments,
            reference_duration_s=clip.duration_s,
            granularity=outcome.granularity,
        )
        results.append(
            ClipResult(
                clip_id=clip.clip_id,
                status="scored",
                provider=outcome.provider,
                model=outcome.model or model,
                audio_sha256=sha256_of(clip.audio) or clip.sha256,
                transcript_text=outcome.text,
                accuracy=accuracy,
                timestamps=timestamps,
                timings=dict(outcome.timings),
                attempts=outcome.attempts,
                retries=outcome.retries,
                error=None,
            )
        )

    scored_ids = {clip.clip_id for clip in scored_clips}
    finished = time.time()
    # The provider that actually ran is what the record must name; the
    # requested model is only a fallback when a provider reports none.
    model = model or next((clip.model for clip in results if clip.model), None)
    return EvaluationResult(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        provider=provider,
        model=model,
        decoding=dict(decoding or {}),
        normalization=rules,
        clips=tuple(results),
        aggregate=_aggregate(results),
        by_group={
            name: _group_by(
                [clip for clip in dataset.clips if clip.clip_id in scored_ids],
                results,
                name,
            )
            for name in ("language", "domain", "noise", "accent")
        },
        insufficient_evidence=len(results) < MIN_SCORED_CLIPS,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(started)),
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(finished)),
        duration_s=round(finished - started, 3),
    )
