"""Evaluation schemas: what the API serves and the frontend consumes.

A metric that could not be measured is ``None`` here — never 0.0 — so a
missing measurement cannot be mistaken for a good one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EvaluationClipSummary(BaseModel):
    clip_id: str
    status: str
    provider: str
    model: str | None = None
    audio_sha256: str | None = None
    wer: float | None = None
    cer: float | None = None
    word_errors: dict[str, Any] = Field(default_factory=dict)
    char_errors: dict[str, Any] = Field(default_factory=dict)
    timings: dict[str, Any] = Field(default_factory=dict)
    timestamps: dict[str, Any] | None = None
    error: str | None = None


class EvaluationRecord(BaseModel):
    evaluation_id: str
    dataset_id: str
    dataset_version: str
    provider: str
    model: str | None = None
    status: str = "COMPLETED"
    decoding: dict[str, Any] = Field(default_factory=dict)
    normalization: dict[str, Any] = Field(default_factory=dict)
    aggregate: dict[str, Any] = Field(default_factory=dict)
    by_group: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    insufficient_evidence: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float = 0.0
    mlflow: dict[str, Any] | None = None
    artifact_key: str | None = None
    clips: list[EvaluationClipSummary] = Field(default_factory=list)


class EvaluationComparisonRow(BaseModel):
    """One evaluation, flat enough for a comparison table."""

    evaluation_id: str
    provider: str
    model: str | None = None
    dataset_id: str
    dataset_version: str
    clips_scored: int = 0
    clips_total: int = 0
    wer_micro: float | None = None
    wer_macro: float | None = None
    cer_micro: float | None = None
    rtf_mean: float | None = None
    failures: int = 0
    segment_availability_mean: float | None = None
    word_availability_mean: float | None = None
    insufficient_evidence: bool = False
    started_at: datetime | None = None


class EvaluationRequest(BaseModel):
    """Run an evaluation: dataset + provider + decoding options."""

    dataset_id: str = "stt-eval-core"
    provider: str | None = None
    language: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class EvaluationList(BaseModel):
    evaluations: list[EvaluationComparisonRow] = Field(default_factory=list)
    count: int = 0


class MetricExplanation(BaseModel):
    """One evaluation metric, explained next to the code that computes it.

    ``computed_by`` names the callable the number actually comes from; the
    text fields say what the number means, where it can mislead, and when it
    is absent instead of zero.
    """

    name: str
    applies_to: str
    unit: str
    direction: str
    formula: str
    inputs: str
    normalization: str
    edge_behaviour: str
    limitations: str
    computed_by: str


class MetricExplanationList(BaseModel):
    metrics: list[MetricExplanation]


class EvaluationDatasetInfo(BaseModel):
    """One dataset manifest, as choosing an evaluation needs it.

    ``audio_ready`` answers the question that actually matters: the corpus
    audio is generated (``scripts/make_eval_corpus.py``) and never committed,
    so "the manifest exists" and "a run can start" are different statements.
    A manifest that cannot be loaded is still listed, with ``load_error``
    set — disappearing silently would hide the problem.
    """

    dataset_id: str
    version: str
    language: str
    description: str | None = None
    clips_total: int
    clips_scored: int
    clips_unscored: int
    audio_ready: bool
    clips_missing_audio: int
    load_error: str | None = None


class EvaluationDatasetList(BaseModel):
    datasets: list[EvaluationDatasetInfo]
