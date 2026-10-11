"""Repository over the jobs tables.

Converts between ORM rows and the canonical Pydantic schemas; enforces
the job-status transition map on every state change.
"""
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from stt_platform.domain.errors import InvalidTransition, JobNotFound
from stt_platform.domain.status import JobStatus, is_transition_allowed
from stt_platform.persistence.models import JobRow, JobTransitionRow, OutboxRow, _utcnow
from stt_platform.schemas.jobs import AudioMetadata, JobFailure, JobRecord
from stt_platform.schemas.transcript import TranscriptResult

_TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class JobRepository:
    def __init__(self, session: Session):
        self._session = session

    # ------------------------------------------------------------------
    # conversion
    # ------------------------------------------------------------------
    @staticmethod
    def _to_record(row: JobRow) -> JobRecord:
        audio = None
        if row.audio_format is not None:
            audio = AudioMetadata(
                format_name=row.audio_format,
                codec=row.audio_codec,
                sample_rate=row.audio_sample_rate,
                channels=row.audio_channels,
                duration_s=row.audio_duration_s,
                size_bytes=row.audio_size_bytes,
            )
        failure = JobFailure(**row.failure) if row.failure else None
        return JobRecord(
            id=row.id,
            scope=row.scope,
            status=JobStatus(row.status),
            requested_provider=row.requested_provider,
            actual_provider=row.actual_provider,
            model=row.model,
            model_version=row.model_version,
            language_requested=row.language_requested,
            language_detected=row.language_detected,
            audio=audio,
            audio_path=row.audio_path,
            original_object_key=row.original_object_key,
            idempotency_key=row.idempotency_key,
            request_fingerprint=row.request_fingerprint,
            attempt_count=row.attempt_count,
            failure=failure,
            options=row.options or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )

    # ------------------------------------------------------------------
    # row building
    # ------------------------------------------------------------------
    def _build_row(
        self,
        record: JobRecord,
        *,
        audio: AudioMetadata | None = None,
        audio_path: str | None = None,
        original_object_key: str | None = None,
    ) -> JobRow:
        audio = audio or record.audio
        audio_path = audio_path or record.audio_path
        row = JobRow(
            id=record.id,
            scope=record.scope,
            status=record.status.value,
            requested_provider=record.requested_provider,
            idempotency_key=record.idempotency_key,
            request_fingerprint=record.request_fingerprint,
            options=record.options,
            attempt_count=record.attempt_count,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        if audio is not None:
            row.audio_format = audio.format_name
            row.audio_codec = audio.codec
            row.audio_sample_rate = audio.sample_rate
            row.audio_channels = audio.channels
            row.audio_duration_s = audio.duration_s
            row.audio_size_bytes = audio.size_bytes
        row.audio_path = audio_path
        row.original_object_key = original_object_key or record.original_object_key
        return row

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def create(
        self,
        record: JobRecord,
        *,
        audio: AudioMetadata | None = None,
        audio_path: str | None = None,
        original_object_key: str | None = None,
    ) -> JobRecord:
        row = self._build_row(
            record,
            audio=audio,
            audio_path=audio_path,
            original_object_key=original_object_key,
        )
        self._session.add(row)
        self._session.commit()
        return self._to_record(row)

    def create_with_outbox(
        self,
        record: JobRecord,
        *,
        audio: AudioMetadata | None = None,
        audio_path: str | None = None,
        original_object_key: str | None = None,
        topic: str = "transcription.start",
    ) -> JobRecord:
        """Insert the job row AND its outbox row in ONE transaction.

        Committed rows are therefore always eventually dispatched; a
        failure rolls back both (no orphan outbox entry, no invisible job).
        """
        row = self._build_row(
            record,
            audio=audio,
            audio_path=audio_path,
            original_object_key=original_object_key,
        )
        outbox = OutboxRow(
            job_id=record.id,
            topic=topic,
            payload={"job_id": record.id, "scope": record.scope},
        )
        self._session.add(row)
        self._session.add(outbox)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return self._to_record(row)

    def get(self, job_id: str, scope: str = "local") -> JobRecord:
        return self._to_record(self._get_row(job_id, scope))

    def delete(self, job_id: str, scope: str = "local") -> None:
        """Delete the job row; the database cascades transitions/attempts/outbox.

        Commits like every other write path here: the request session is
        closed, never committed, by the API layer.
        """
        row = self._get_row(job_id, scope)
        self._session.delete(row)
        self._session.commit()

    def find_by_idempotency(self, scope: str, key: str) -> JobRecord | None:
        row = self._session.execute(
            select(JobRow).where(
                JobRow.scope == scope, JobRow.idempotency_key == key
            )
        ).scalar_one_or_none()
        return self._to_record(row) if row is not None else None

    def append_transition(
        self,
        job_id: str,
        to_status: JobStatus,
        *,
        detail: dict | None = None,
        scope: str = "local",
        **fields: Any,
    ) -> JobRecord:
        row = self._get_row(job_id, scope)
        from_status = JobStatus(row.status)
        if not is_transition_allowed(from_status, to_status):
            raise InvalidTransition(from_status.value, to_status.value)

        self._session.add(
            JobTransitionRow(
                job_id=row.id,
                from_status=from_status.value,
                to_status=to_status.value,
                detail=detail,
            )
        )
        row.status = to_status.value
        now = _utcnow()
        row.updated_at = now
        if to_status is JobStatus.PROCESSING:
            row.started_at = row.started_at or now
            row.attempt_count = row.attempt_count + 1
        if to_status in _TERMINAL:
            row.completed_at = now
        for name, raw_value in fields.items():
            value = (
                raw_value.model_dump(mode="json")
                if hasattr(raw_value, "model_dump")
                else raw_value
            )
            setattr(row, name, value)
        self._session.commit()
        return self._to_record(row)

    def save_result(
        self, job_id: str, transcript: TranscriptResult, scope: str = "local"
    ) -> None:
        row = self._get_row(job_id, scope)
        row.transcript = transcript.model_dump(mode="json")
        row.updated_at = _utcnow()
        self._session.commit()

    def load_result(
        self, job_id: str, scope: str = "local"
    ) -> TranscriptResult | None:
        row = self._get_row(job_id, scope)
        if row.transcript is None:
            return None
        return TranscriptResult.model_validate(row.transcript)

    def transitions(self, job_id: str, scope: str = "local") -> list[tuple[str, str]]:
        row = self._get_row(job_id, scope)
        return [(t.from_status, t.to_status) for t in row.transitions]

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _get_row(self, job_id: str, scope: str = "local") -> JobRow:
        row = self._session.execute(
            select(JobRow).where(JobRow.id == job_id, JobRow.scope == scope)
        ).scalar_one_or_none()
        if row is None:
            raise JobNotFound(job_id)
        return row
