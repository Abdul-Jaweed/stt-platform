"""Temporal activities: the only place worker side effects happen.

Activities are written to be safely retryable:

- ``transition`` is a no-op when the job already has the target status
  (a replayed write must never raise InvalidTransition);
- ``execute_attempt`` tolerates re-entry after a worker crash (statuses
  other than QUEUED/PROCESSING surface as a permanent outcome instead of
  corrupting the trail);
- provider-visible failures are RETURNED as outcome dicts (the workflow
  decides retries); unexpected infrastructure exceptions are also
  returned as transient so a flaky dependency can never wedge a job.
"""
from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from stt_platform.audio.validation import validate_upload
from stt_platform.core.config import Settings
from stt_platform.domain.errors import DomainError, ProviderError
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.base import ProviderTranscript, TranscriptionRequest
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.schemas.transcript import TranscriptResult
from stt_platform.storage.keys import artifact_key
from stt_platform.storage.s3 import S3ObjectStore

log = logging.getLogger(__name__)


@dataclass
class ActivityDeps:
    """Everything the activities need; injected so tests can use fakes."""

    settings: Settings
    session_factory: sessionmaker[Session]
    registry: ProviderRegistry
    store: S3ObjectStore | None


class TranscriptionActivities:
    def __init__(self, deps: ActivityDeps) -> None:
        self._deps = deps

    # ------------------------------------------------------------------
    # transitions
    # ------------------------------------------------------------------
    @activity.defn(name="transition")
    def transition(
        self,
        job_id: str,
        scope: str,
        to_status: str,
        detail: dict | None = None,
        failure: dict | None = None,
        fields: dict | None = None,
    ) -> None:
        """Write one status transition; replay-safe (same status = no-op)."""
        target = JobStatus(to_status)
        with self._deps.session_factory() as session:
            repo = JobRepository(session)
            record = repo.get(job_id, scope)
            if record.status is target:
                return
            repo.append_transition(
                job_id,
                target,
                scope=scope,
                detail=detail,
                failure=failure,
                **(fields or {}),
            )

    # ------------------------------------------------------------------
    # one transcription attempt
    # ------------------------------------------------------------------
    @activity.defn(name="execute_attempt")
    def execute_attempt(self, job_id: str, scope: str, attempt: int) -> dict:
        deps = self._deps
        with deps.session_factory() as session:
            repo = JobRepository(session)
            record = repo.get(job_id, scope)
            if record.status is JobStatus.QUEUED:
                repo.append_transition(job_id, JobStatus.PROCESSING, scope=scope)
            elif record.status is not JobStatus.PROCESSING:
                return {
                    "outcome": "permanent",
                    "code": "INVALID_STATE",
                    "message": f"job is {record.status.value}, not processable",
                }
            provider_id = record.requested_provider
            language = record.language_requested
            object_key = record.original_object_key
            audio_path = record.audio_path

        provider = deps.registry.get(provider_id)

        tmp_path: Path | None = None
        try:
            if object_key:
                if deps.store is None:
                    return {
                        "outcome": "transient",
                        "code": "STORAGE_NOT_CONFIGURED",
                        "message": "job references an object but no store is wired",
                    }
                data = deps.store.get_bytes(deps.store.bucket_audio, object_key)
                suffix = Path(object_key).suffix or ".bin"
                with tempfile.NamedTemporaryFile(
                    dir=deps.settings.temp_dir, suffix=suffix, delete=False
                ) as fh:
                    fh.write(data)
                tmp_path = Path(fh.name)
                source = tmp_path
            elif audio_path and Path(audio_path).is_file():
                source = Path(audio_path)
            else:
                return {
                    "outcome": "permanent",
                    "code": "NO_AUDIO_SOURCE",
                    "message": "job has neither an object key nor a local file",
                }

            # Stored bytes are never trusted: re-validate content before
            # transcribing (a presigned upload may have placed anything).
            validate_upload(
                source,
                deps.settings,
                max_duration_s=deps.settings.max_async_duration_s,
            )

            transcript = provider.transcribe(
                TranscriptionRequest(audio_path=source, language=language)
            )
            result = _build_result(job_id, provider.id, transcript)
            with deps.session_factory() as session:
                JobRepository(session).save_result(job_id, result, scope=scope)

            if deps.store is not None:
                deps.store.put_bytes(
                    deps.store.bucket_artifacts,
                    artifact_key(scope, job_id, "transcript.json"),
                    result.model_dump_json().encode("utf-8"),
                    content_type="application/json",
                )
                if transcript.raw is not None:
                    deps.store.put_bytes(
                        deps.store.bucket_artifacts,
                        artifact_key(scope, job_id, "raw.json"),
                        json.dumps(transcript.raw).encode("utf-8"),
                        content_type="application/json",
                    )

            return {
                "outcome": "ok",
                "attempt": attempt,
                "provider": provider.id,
                "model": transcript.model,
                "model_version": transcript.model_version,
                "language": transcript.language,
            }
        except ProviderError as exc:
            return {
                "outcome": "transient" if exc.retryable else "permanent",
                "code": exc.code,
                "message": exc.message,
            }
        except DomainError as exc:
            # Validation failures (content, limits) do not fix themselves.
            return {"outcome": "permanent", "code": exc.code, "message": exc.message}
        except Exception as exc:  # noqa: BLE001 - infra failure: retryable
            log.warning("attempt %d for job %s failed: %r", attempt, job_id, exc)
            return {
                "outcome": "transient",
                "code": type(exc).__name__,
                "message": str(exc)[:300],
            }
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)


def _build_result(
    job_id: str, provider_id: str, transcript: ProviderTranscript
) -> TranscriptResult:
    """Same canonicalization as the synchronous path (services.jobs)."""
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


def make_activities(deps: ActivityDeps) -> list:
    """Bound activity callables for a Worker (the SDK needs callables
    carrying their ``@activity.defn`` marker; bound methods do)."""
    instance = TranscriptionActivities(deps)
    return [instance.transition, instance.execute_attempt]
