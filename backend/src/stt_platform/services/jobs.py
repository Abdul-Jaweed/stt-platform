"""Job service: idempotent creation, sync path, async enqueue, cancel.

Two execution paths share one status machine:

* ``create_and_process`` — short audio, executed inline (Phase 1 contract
  unchanged: by the time POST returns the job is terminal).
* ``create_async`` — long audio: the original is preserved in object
  storage and the job is enqueued in the transactional outbox; the worker
  dispatcher starts the Temporal workflow that owns the rest. The bytes
  may arrive either as a local file (multipart upload) or via a presigned
  PUT that went straight to storage (``object_key``).

Originals live in object storage under opaque keys (opaque to clients,
tenant-scoped); the local working copy is kept for dev-speed retries —
retention/deletion policy lands in the hardening phase.
"""
from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import uuid
from collections.abc import Callable
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
    InvalidObjectKey,
    InvalidRequest,
    InvalidTransition,
    JobNotReady,
    ProviderError,
    StorageNotConfigured,
    UploadTooLarge,
)
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.base import (
    ProviderProtocol,
    ProviderTranscript,
    TranscriptionRequest,
)
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.schemas.jobs import JobFailure, JobRecord
from stt_platform.schemas.transcript import TranscriptResult
from stt_platform.services.dispatch import dispatch_pending, workflow_id_for
from stt_platform.storage.keys import artifact_key, is_original_key, original_key
from stt_platform.storage.s3 import S3ObjectStore

log = logging.getLogger(__name__)

_CHUNK_BYTES = 1024 * 1024
PROVIDER_CONTRACT_VIOLATION = "PROVIDER_CONTRACT_VIOLATION"


def compute_fingerprint(
    *,
    provider_id: str,
    language: str | None,
    options: dict[str, Any],
    audio_path: Path | None = None,
    object_key: str | None = None,
) -> str:
    """sha256 over request parameters plus the audio bytes (when local).

    Two requests are idempotent replays iff their fingerprints match.
    Unreadable files contribute only the parameters; the job then fails
    validation later, which is the honest outcome.
    """
    hasher = hashlib.sha256()
    params = json.dumps(
        {
            "provider": provider_id,
            "language": language,
            "options": options,
            "object_key": object_key,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    hasher.update(params)
    if audio_path is not None and audio_path.is_file():
        with open(audio_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK_BYTES), b""):
                hasher.update(chunk)
    return hasher.hexdigest()


class JobService:
    """Owns the job lifecycle for both execution paths."""

    def __init__(
        self,
        repo: JobRepository,
        registry: ProviderRegistry,
        settings: Settings | None = None,
        store: S3ObjectStore | None = None,
        *,
        outbox: OutboxRepository | None = None,
        starter: Callable[[dict], None] | None = None,
        canceller: Callable[[str], None] | None = None,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._settings = settings or get_settings()
        self._store = store
        self._outbox = outbox
        self._starter = starter
        self._canceller = canceller

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
        existing = self._replay_or_conflict(scope, idempotency_key, fingerprint)
        if existing is not None:
            return existing

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

    def create_async(
        self,
        *,
        audio_path: Path | None = None,
        object_key: str | None = None,
        provider_id: str | None = None,
        language: str | None = None,
        idempotency_key: str | None = None,
        options: dict[str, Any] | None = None,
        scope: str = "local",
    ) -> JobRecord:
        """Persist the original, then enqueue; execution happens elsewhere.

        Exactly one source must be given: a local ``audio_path`` (the
        bytes are uploaded here) or an ``object_key`` already uploaded via
        the presigned flow. A presigned key is verified against this scope
        before use; the worker re-validates whatever storage holds.
        """
        if audio_path is not None:
            audio_path = Path(audio_path)
        options = options or {}
        provider = self._registry.get(provider_id)
        if self._store is None:
            raise StorageNotConfigured()
        if (audio_path is None) == (object_key is None):
            raise InvalidRequest(
                "exactly one of audio_path or object_key is required"
            )
        if object_key is not None and not is_original_key(scope, object_key):
            raise InvalidObjectKey()

        fingerprint = compute_fingerprint(
            provider_id=provider.id,
            language=language,
            options=options,
            audio_path=audio_path,
            object_key=object_key,
        )
        existing = self._replay_or_conflict(scope, idempotency_key, fingerprint)
        if existing is not None:
            return existing

        record = self._repo.create_with_outbox(
            JobRecord(
                id=str(uuid.uuid4()),
                scope=scope,
                status=JobStatus.ACCEPTED,
                requested_provider=provider.id,
                language_requested=language,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                options=options,
            ),
            topic="transcription.start",
        )
        job_id = record.id
        self._repo.append_transition(job_id, JobStatus.VALIDATING, scope=scope)

        if object_key is not None:
            # Bytes arrived via presigned PUT; there is nothing local to
            # probe. The worker downloads and re-validates the object.
            self._repo.append_transition(job_id, JobStatus.STORED, scope=scope)
            record = self._repo.append_transition(
                job_id,
                JobStatus.QUEUED,
                scope=scope,
                original_object_key=object_key,
            )
            self._dispatch_best_effort()
            return record

        assert audio_path is not None  # exclusivity checked above
        try:
            info = validate_upload(
                audio_path,
                self._settings,
                max_duration_s=self._settings.max_async_duration_s,
            )
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
        )
        object_key_local = original_key(scope, job_id, audio_path.suffix)
        try:
            self._store.put_file(
                self._store.bucket_audio, object_key_local, audio_path
            )
        except Exception as exc:  # noqa: BLE001 - infra failure at intake
            # Nothing is retryable client-side here: with no local copy the
            # original never reached storage, so re-submission is the path.
            return self._fail(
                job_id,
                DomainError(
                    "STORAGE_ERROR",
                    f"object storage upload failed: {type(exc).__name__}",
                ),
                retryable=False,
                scope=scope,
            )

        record = self._repo.append_transition(
            job_id,
            JobStatus.QUEUED,
            scope=scope,
            original_object_key=object_key_local,
        )
        self._dispatch_best_effort()
        return record

    def cancel(self, job_id: str, scope: str = "local") -> JobRecord:
        """Cancel a job: directly while QUEUED, via Temporal once started."""
        record = self._repo.get(job_id, scope)
        if record.status is JobStatus.QUEUED:
            return self._repo.append_transition(
                job_id, JobStatus.CANCELLED, scope=scope
            )
        if record.status in (JobStatus.PROCESSING, JobStatus.RETRY_WAIT):
            row = self._outbox_repo().latest_for_job(job_id)
            if row is None or self._canceller is None:
                # An inline (synchronous) job has no workflow to cancel; it
                # is only observable here for the duration of one request.
                raise InvalidTransition(
                    record.status.value, JobStatus.CANCELLED.value
                )
            self._canceller(workflow_id_for(job_id, row.id))
            # The workflow owns the final write: it observes the request,
            # records CANCELLED, and ends. The caller can poll the job.
            return record
        raise InvalidTransition(record.status.value, JobStatus.CANCELLED.value)

    def retry_async(self, job_id: str, scope: str = "local") -> JobRecord:
        """Re-enqueue a failed asynchronous job (new outbox row)."""
        record = self._repo.get(job_id, scope)
        retryable = (
            record.status is JobStatus.FAILED
            and record.failure is not None
            and record.failure.retryable
            and bool(record.original_object_key)
        )
        if not retryable:
            raise InvalidTransition(record.status.value, JobStatus.QUEUED.value)
        self._repo.append_transition(job_id, JobStatus.QUEUED, scope=scope)
        self._outbox_repo().enqueue(job_id, scope=scope)
        self._dispatch_best_effort()
        return self._repo.get(job_id, scope)

    def retry(self, job_id: str, scope: str = "local") -> JobRecord:
        record = self._repo.get(job_id, scope)
        retryable = (
            record.status is JobStatus.FAILED
            and record.failure is not None
            and record.failure.retryable
        )
        if not retryable:
            raise InvalidTransition(record.status.value, JobStatus.QUEUED.value)

        source: Path | None = None
        if record.audio_path and Path(record.audio_path).is_file():
            source = Path(record.audio_path)
        downloaded: Path | None = None
        if source is None and record.original_object_key and self._store is not None:
            data = self._store.get_bytes(
                self._store.bucket_audio, record.original_object_key
            )
            suffix = Path(record.original_object_key).suffix or ".bin"
            with tempfile.NamedTemporaryFile(
                dir=self._settings.temp_dir, suffix=suffix, delete=False
            ) as fh:
                fh.write(data)
            downloaded = Path(fh.name)
            source = downloaded
        if source is None:
            raise InvalidTransition(record.status.value, JobStatus.QUEUED.value)

        provider = self._registry.get(record.requested_provider)
        try:
            self._repo.append_transition(job_id, JobStatus.QUEUED, scope=scope)
            return self._process(
                job_id,
                provider,
                audio_path=source,
                language=record.language_requested,
                validate=False,
                scope=scope,
            )
        finally:
            if downloaded is not None:
                downloaded.unlink(missing_ok=True)

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
    def _replay_or_conflict(
        self, scope: str, idempotency_key: str | None, fingerprint: str
    ) -> JobRecord | None:
        if not idempotency_key:
            return None
        existing = self._repo.find_by_idempotency(scope, idempotency_key)
        if existing is None:
            return None
        if existing.request_fingerprint == fingerprint:
            return existing
        raise IdempotencyConflict(idempotency_key)

    def _outbox_repo(self) -> OutboxRepository:
        if self._outbox is None:
            raise RuntimeError("outbox repository is not wired")
        return self._outbox

    def _dispatch_best_effort(self) -> None:
        """Nudge dispatch right after commit; the worker loop is the backstop."""
        if self._starter is None:
            return
        try:
            dispatch_pending(self._outbox_repo(), self._starter)
        except Exception as exc:  # noqa: BLE001 - the worker loop will retry
            log.warning("best-effort dispatch failed: %r", exc)

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
            # Preserve the original in object storage before queueing. A
            # storage outage is a retryable failure, never a 500.
            object_key: str | None = None
            if self._store is not None:
                object_key = original_key(scope, job_id, audio_path.suffix)
                try:
                    self._store.put_file(self._store.bucket_audio, object_key, audio_path)
                except Exception as exc:  # noqa: BLE001 - infra, retryable
                    return self._fail(
                        job_id,
                        DomainError(
                            "STORAGE_ERROR",
                            f"object storage upload failed: {type(exc).__name__}",
                        ),
                        retryable=True,
                        scope=scope,
                    )
            self._repo.append_transition(
                job_id,
                JobStatus.QUEUED,
                scope=scope,
                original_object_key=object_key,
            )

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
        record = self._repo.append_transition(
            job_id,
            JobStatus.SUCCEEDED,
            scope=scope,
            actual_provider=provider.id,
            model=transcript.model,
            model_version=transcript.model_version,
            language_detected=transcript.language,
        )
        self._store_artifact(job_id, result, scope=scope)
        return record

    def _store_artifact(
        self, job_id: str, result: TranscriptResult, *, scope: str
    ) -> None:
        """Best-effort transcript artifact; the PG row is the source of truth."""
        if self._store is None:
            return
        try:
            self._store.put_bytes(
                self._store.bucket_artifacts,
                artifact_key(scope, job_id, "transcript.json"),
                result.model_dump_json().encode("utf-8"),
                content_type="application/json",
            )
        except Exception as exc:  # noqa: BLE001 - never fail a SUCCEEDED job
            log.warning("artifact write failed for job %s: %r", job_id, exc)

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
