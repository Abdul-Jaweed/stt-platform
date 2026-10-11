"""Job service: idempotent creation and synchronous short-audio processing.

Phase 1 runs the whole lifecycle in-process (spec §9 "synchronous
short-audio path"). Phase 2 moves execution behind Temporal; the status
machine enforced here stays identical.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from stt_platform.audio.validation import validate_upload
from stt_platform.core.config import Settings, get_settings
from stt_platform.domain.errors import (
    AudioTooLong,
    AudioValidationError,
    DomainError,
    IdempotencyConflict,
    InvalidTransition,
    JobNotReady,
    ProviderError,
    UploadTooLarge,
)
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.base import (
    ProviderProtocol,
    ProviderTranscript,
    TranscriptionRequest,
)
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.schemas.jobs import JobFailure, JobRecord
from stt_platform.schemas.transcript import TranscriptResult

_CHUNK_BYTES = 1024 * 1024
PROVIDER_CONTRACT_VIOLATION = "PROVIDER_CONTRACT_VIOLATION"


def compute_fingerprint(
    *,
    provider_id: str,
    language: str | None,
    options: dict[str, Any],
    audio_path: Path,
) -> str:
    """sha256 over request parameters plus the audio bytes.

    Two requests are idempotent replays iff their fingerprints match.
    Unreadable files contribute only the parameters; the job then fails
    validation later, which is the honest outcome.
    """
    hasher = hashlib.sha256()
    params = json.dumps(
        {"provider": provider_id, "language": language, "options": options},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    hasher.update(params)
    if audio_path.is_file():
        with open(audio_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK_BYTES), b""):
                hasher.update(chunk)
    return hasher.hexdigest()


class JobService:
    """Owns the Phase 1 job lifecycle end to end."""

    def __init__(
        self,
        repo: JobRepository,
        registry: ProviderRegistry,
        settings: Settings | None = None,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def create_and_process(
        self,
        *,
        audio_path: Path,
        provider_id: str | None = None,
        language: str | None = None,
        idempotency_key: str | None = None,
        options: dict[str, Any] | None = None,
        scope: str = "local",
    ) -> JobRecord:
        audio_path = Path(audio_path)
        options = options or {}
        provider = self._registry.get(provider_id)

        fingerprint = compute_fingerprint(
            provider_id=provider.id,
            language=language,
            options=options,
            audio_path=audio_path,
        )
        if idempotency_key:
            existing = self._repo.find_by_idempotency(scope, idempotency_key)
            if existing is not None:
                if existing.request_fingerprint == fingerprint:
                    return existing
                raise IdempotencyConflict(idempotency_key)

        record = self._repo.create(
            JobRecord(
                id=str(uuid.uuid4()),
                scope=scope,
                status=JobStatus.ACCEPTED,
                requested_provider=provider.id,
                language_requested=language,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                options=options,
            )
        )
        return self._process(
            record.id,
            provider,
            audio_path=audio_path,
            language=language,
            validate=True,
            scope=scope,
        )

    def retry(self, job_id: str, scope: str = "local") -> JobRecord:
        record = self._repo.get(job_id, scope)
        retryable = (
            record.status is JobStatus.FAILED
            and record.failure is not None
            and record.failure.retryable
        )
        if not retryable:
            raise InvalidTransition(record.status.value, JobStatus.QUEUED.value)
        if not record.audio_path or not Path(record.audio_path).is_file():
            raise InvalidTransition(record.status.value, JobStatus.QUEUED.value)

        provider = self._registry.get(record.requested_provider)
        self._repo.append_transition(job_id, JobStatus.QUEUED, scope=scope)
        return self._process(
            job_id,
            provider,
            audio_path=Path(record.audio_path),
            language=record.language_requested,
            validate=False,
            scope=scope,
        )

    def get(self, job_id: str, scope: str = "local") -> JobRecord:
        return self._repo.get(job_id, scope)

    def get_result(self, job_id: str, scope: str = "local") -> TranscriptResult:
        record = self._repo.get(job_id, scope)
        result = self._repo.load_result(job_id, scope)
        if record.status is not JobStatus.SUCCEEDED or result is None:
            raise JobNotReady(job_id, record.status.value)
        return result

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _process(
        self,
        job_id: str,
        provider: ProviderProtocol,
        *,
        audio_path: Path,
        language: str | None,
        validate: bool,
        scope: str,
    ) -> JobRecord:
        if validate:
            self._repo.append_transition(job_id, JobStatus.VALIDATING, scope=scope)
            try:
                info = validate_upload(audio_path, self._settings)
            except (AudioValidationError, UploadTooLarge, AudioTooLong) as exc:
                return self._fail(job_id, exc, retryable=False, scope=scope)
            self._repo.append_transition(
                job_id,
                JobStatus.STORED,
                scope=scope,
                audio_format=info.format_name,
                audio_codec=info.codec,
                audio_sample_rate=info.sample_rate,
                audio_channels=info.channels,
                audio_duration_s=info.duration_s,
                audio_size_bytes=info.size_bytes,
                audio_path=str(audio_path),
            )
            self._repo.append_transition(job_id, JobStatus.QUEUED, scope=scope)

        self._repo.append_transition(job_id, JobStatus.PROCESSING, scope=scope)
        try:
            transcript = provider.transcribe(
                TranscriptionRequest(audio_path=audio_path, language=language)
            )
            result = self._build_result(job_id, provider.id, transcript)
        except ProviderError as exc:
            return self._fail(job_id, exc, retryable=exc.retryable, scope=scope)
        except ValidationError as exc:
            first = exc.errors()[0]["msg"] if exc.errors() else "unknown"
            return self._repo.append_transition(
                job_id,
                JobStatus.FAILED,
                scope=scope,
                failure=JobFailure(
                    code=PROVIDER_CONTRACT_VIOLATION,
                    message=(
                        "provider returned a transcript that violates the "
                        f"canonical schema ({exc.error_count()} error(s); "
                        f"first: {first})"
                    ),
                    retryable=False,
                ),
            )

        self._repo.save_result(job_id, result, scope=scope)
        return self._repo.append_transition(
            job_id,
            JobStatus.SUCCEEDED,
            scope=scope,
            actual_provider=provider.id,
            model=transcript.model,
            model_version=transcript.model_version,
            language_detected=transcript.language,
        )

    def _fail(
        self, job_id: str, exc: DomainError, *, retryable: bool, scope: str
    ) -> JobRecord:
        return self._repo.append_transition(
            job_id,
            JobStatus.FAILED,
            scope=scope,
            failure=JobFailure(
                code=exc.code, message=exc.message, retryable=retryable
            ),
        )

    @staticmethod
    def _build_result(
        job_id: str, provider_id: str, transcript: ProviderTranscript
    ) -> TranscriptResult:
        has_words = any(segment.words for segment in transcript.segments)
        return TranscriptResult(
            job_id=job_id,
            text=transcript.text,
            language=transcript.language,
            language_probability=transcript.language_probability,
            segments=transcript.segments,
            timestamp_granularity="word" if has_words else "segment",
            audio_duration_s=transcript.audio_duration_s,
            processing_duration_s=transcript.processing_duration_s,
            provider=provider_id,
            model=transcript.model,
            model_version=transcript.model_version,
        )
