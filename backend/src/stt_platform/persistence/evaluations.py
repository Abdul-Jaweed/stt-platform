"""Repository over the evaluations tables.

PostgreSQL keeps the numbers so they can be listed and compared; the full
result — transcripts included — is written to object storage under an
opaque key. Nothing here invents a value it did not receive: a metric that
was not measurable stays ``None`` all the way to the API.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from stt_platform.evaluation.runner import EvaluationResult
from stt_platform.persistence.models import EvaluationClipRow, EvaluationRow
from stt_platform.schemas.evaluations import (
    EvaluationClipSummary,
    EvaluationComparisonRow,
    EvaluationRecord,
)
from stt_platform.storage.keys import evaluation_artifact_key


class EvaluationNotFound(LookupError):
    def __init__(self, evaluation_id: str) -> None:
        super().__init__(f"unknown evaluation: {evaluation_id}")
        self.evaluation_id = evaluation_id


def _to_record(row: EvaluationRow) -> EvaluationRecord:
    return EvaluationRecord(
        evaluation_id=row.id,
        dataset_id=row.dataset_id,
        dataset_version=row.dataset_version,
        provider=row.provider,
        model=row.model,
        status=row.status,
        decoding=row.decoding or {},
        normalization=row.normalization or {},
        aggregate=row.aggregate or {},
        by_group=row.by_group or {},
        insufficient_evidence=row.insufficient_evidence,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_s=row.duration_s,
        mlflow=row.mlflow,
        artifact_key=row.artifact_key,
        clips=[
            EvaluationClipSummary(
                clip_id=clip.clip_id,
                status=clip.status,
                provider=clip.provider,
                model=clip.model,
                audio_sha256=clip.audio_sha256,
                wer=clip.wer,
                cer=clip.cer,
                word_errors=clip.word_errors or {},
                char_errors=clip.char_errors or {},
                timings=clip.timings or {},
                timestamps=clip.timestamps,
                error=clip.error,
            )
            for clip in row.clips
        ],
    )


class EvaluationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        result: EvaluationResult,
        *,
        store: Any | None = None,
        mlflow: dict[str, Any] | None = None,
    ) -> EvaluationRecord:
        """Persist a finished evaluation (and its artifact, when given a store)."""
        evaluation_id = _new_id()
        artifact_key: str | None = None
        if store is not None:
            artifact_key = evaluation_artifact_key(evaluation_id)
            store.put_bytes(
                store.bucket_artifacts,
                artifact_key,
                _artifact_bytes(result, mlflow),
                content_type="application/json",
            )

        row = EvaluationRow(
            id=evaluation_id,
            dataset_id=result.dataset_id,
            dataset_version=result.dataset_version,
            provider=result.provider,
            model=result.model,
            status="COMPLETED",
            decoding=result.decoding,
            normalization=result.normalization.describe(),
            aggregate=result.aggregate,
            by_group=result.by_group,
            insufficient_evidence=result.insufficient_evidence,
            clips_total=int(result.aggregate.get("clips_total") or 0),
            clips_scored=int(result.aggregate.get("clips_scored") or 0),
            wer_micro=result.aggregate.get("wer_micro"),
            cer_micro=result.aggregate.get("cer_micro"),
            mlflow=mlflow or result.mlflow,
            artifact_key=artifact_key,
            duration_s=result.duration_s,
        )
        for clip in result.clips:
            accuracy = clip.accuracy
            row.clips.append(
                EvaluationClipRow(
                    clip_id=clip.clip_id,
                    status=clip.status,
                    provider=clip.provider,
                    model=clip.model,
                    audio_sha256=clip.audio_sha256,
                    wer=accuracy.wer if accuracy else None,
                    cer=accuracy.cer if accuracy else None,
                    word_errors=accuracy.word_errors.as_dict() if accuracy else None,
                    char_errors=accuracy.char_errors.as_dict() if accuracy else None,
                    timings=clip.timings,
                    timestamps=clip.timestamps.as_dict() if clip.timestamps else None,
                    error=clip.error,
                )
            )
        self._session.add(row)
        self._session.flush()
        return _to_record(row)

    def get(self, evaluation_id: str) -> EvaluationRecord:
        row = self._session.get(
            EvaluationRow,
            evaluation_id,
            options=[selectinload(EvaluationRow.clips)],
        )
        if row is None:
            raise EvaluationNotFound(evaluation_id)
        return _to_record(row)

    def list(self, *, limit: int = 50) -> list[EvaluationRecord]:
        rows = (
            self._session.execute(
                select(EvaluationRow)
                .options(selectinload(EvaluationRow.clips))
                .order_by(EvaluationRow.created_at.desc(), EvaluationRow.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_to_record(row) for row in rows]

    def comparison(self, *, limit: int = 50) -> list[EvaluationComparisonRow]:
        """One row per evaluation, ready for a provider comparison table.

        ``insufficient_evidence`` rides along: a comparison that spans a
        single recording must say so rather than imply a ranking.
        """
        return [
            EvaluationComparisonRow(
                evaluation_id=record.evaluation_id,
                provider=record.provider,
                model=record.model,
                dataset_id=record.dataset_id,
                dataset_version=record.dataset_version,
                clips_scored=int(record.aggregate.get("clips_scored") or 0),
                clips_total=int(record.aggregate.get("clips_total") or 0),
                wer_micro=record.aggregate.get("wer_micro"),
                wer_macro=record.aggregate.get("wer_macro"),
                cer_micro=record.aggregate.get("cer_micro"),
                rtf_mean=record.aggregate.get("rtf_mean"),
                failures=int(record.aggregate.get("failures") or 0),
                segment_availability_mean=record.aggregate.get(
                    "segment_availability_mean"
                ),
                word_availability_mean=record.aggregate.get(
                    "word_availability_mean"
                ),
                insufficient_evidence=record.insufficient_evidence,
                started_at=record.started_at,
            )
            for record in self.list(limit=limit)
        ]

    def delete(self, evaluation_id: str) -> None:
        row = self._session.get(EvaluationRow, evaluation_id)
        if row is None:
            raise EvaluationNotFound(evaluation_id)
        self._session.delete(row)
        self._session.flush()


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex


def _artifact_bytes(
    result: EvaluationResult, mlflow: dict[str, Any] | None
) -> bytes:
    import json

    payload = result.as_dict()
    if mlflow is not None:
        payload["mlflow"] = mlflow
    return json.dumps(payload, indent=2).encode("utf-8")
