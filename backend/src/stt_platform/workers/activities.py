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
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from opentelemetry.trace import Status, StatusCode
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from stt_platform.audio.validation import validate_upload
from stt_platform.core.config import Settings
from stt_platform.domain.errors import DomainError, InvalidTransition, ProviderError
from stt_platform.domain.status import JobStatus
from stt_platform.observability import context as obs_context
from stt_platform.observability import metrics
from stt_platform.observability.temporal import activity_span
from stt_platform.persistence.provider_attempts import ProviderAttemptRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.base import (
    AsyncProviderProtocol,
    ProviderTranscript,
    RemoteJobRef,
    TranscriptionRequest,
)
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.schemas.transcript import TranscriptResult
from stt_platform.storage.keys import artifact_key
from stt_platform.storage.s3 import S3ObjectStore

log = logging.getLogger(__name__)


def _provider_outcome(exc: BaseException) -> str:
    """Rate limits are separated from ordinary failures in the metrics."""
    if isinstance(exc, ProviderError) and exc.code == "RATE_LIMITED":
        return "throttled"
    return "error"


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
        with activity_span(
            "stt.transition",
            {"stt.job_id": job_id, "stt.scope": scope, "stt.to_status": to_status},
        ) as span:
            token = obs_context.set_job_id(job_id)
            try:
                written = self._transition(
                    job_id, scope, to_status, detail, failure, fields
                )
            finally:
                obs_context.reset_job_id(token)
            if span is not None:
                span.set_attribute("stt.written", written)
            if written:
                self._record_lifecycle_metrics(job_id, scope, to_status, detail)

    def _transition(
        self,
        job_id: str,
        scope: str,
        to_status: str,
        detail: dict | None = None,
        failure: dict | None = None,
        fields: dict | None = None,
    ) -> bool:
        """Apply the transition; ``False`` when it was already applied.

        Replays must not re-count lifecycle metrics, so the no-op path is
        reported back to the caller instead of being silently swallowed.
        """
        target = JobStatus(to_status)
        with self._deps.session_factory() as session:
            repo = JobRepository(session)
            record = repo.get(job_id, scope)
            if record.status is target:
                return False
            try:
                repo.append_transition(
                    job_id,
                    target,
                    scope=scope,
                    detail=detail,
                    failure=failure,
                    **(fields or {}),
                )
            except InvalidTransition as exc:
                # The job already moved on (usually cancelled) while this
                # workflow was in flight. The stored state wins: raising
                # would make Temporal retry a transition that can never
                # succeed, and overwriting it would rewrite history.
                log.warning("ignoring illegal transition for job %s: %s", job_id, exc)
                return False
            return True

    def _record_lifecycle_metrics(
        self, job_id: str, scope: str, to_status: str, detail: dict | None
    ) -> None:
        """Retry and end-to-end metrics need the job's own provider/time."""
        try:
            with self._deps.session_factory() as session:
                record = JobRepository(session).get(job_id, scope)
        except Exception:  # metrics must never fail a transition
            return
        provider = record.requested_provider or "unknown"
        if to_status == "RETRY_WAIT":
            metrics.record_retry(
                provider=provider,
                error_code=str((detail or {}).get("code") or "unknown"),
            )
        elif to_status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            created = record.created_at
            if created is None:
                return
            seconds = max(0.0, (datetime.now(UTC) - created).total_seconds())
            metrics.record_end_to_end(
                provider=provider, status=to_status, seconds=seconds
            )

    # ------------------------------------------------------------------
    # one transcription attempt
    # ------------------------------------------------------------------
    @activity.defn(name="execute_attempt")
    def execute_attempt(self, job_id: str, scope: str, attempt: int) -> dict:
        """One span per attempt; the job id rides on every child span."""
        with activity_span(
            "stt.attempt",
            {"stt.job_id": job_id, "stt.scope": scope, "stt.attempt": attempt},
        ) as span:
            token = obs_context.set_job_id(job_id)
            try:
                result = self._execute_attempt(job_id, scope, attempt)
            finally:
                obs_context.reset_job_id(token)
            if span is not None:
                outcome = str(result.get("outcome", "unknown"))
                span.set_attribute("stt.outcome", outcome)
                if outcome != "ok":
                    span.set_status(Status(StatusCode.ERROR))
            return result

    def _execute_attempt(self, job_id: str, scope: str, attempt: int) -> dict:
        deps = self._deps
        with deps.session_factory() as session:
            repo = JobRepository(session)
            record = repo.get(job_id, scope)
            if record.status is JobStatus.QUEUED:
                repo.append_transition(job_id, JobStatus.PROCESSING, scope=scope)
                created = record.created_at
                if created is not None:
                    metrics.record_queue_wait(
                        provider=record.requested_provider or "unknown",
                        seconds=max(
                            0.0,
                            (
                                datetime.now(UTC) - created
                            ).total_seconds(),
                        ),
                    )
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

            if isinstance(provider, AsyncProviderProtocol):
                # Asynchronous provider: submit once, hand polling to the
                # workflow (durable sleeps; the reference is persisted).
                return self._submit_remote(
                    job_id, scope, attempt, provider, source, language
                )

            started = time.perf_counter()
            try:
                with activity_span(
                    "stt.provider.transcribe",
                    {"stt.provider": provider.id, "stt.operation": "transcribe"},
                ):
                    transcript = provider.transcribe(
                        TranscriptionRequest(audio_path=source, language=language)
                    )
            except Exception as exc:
                metrics.record_provider_call(
                    provider=provider.id,
                    operation="transcribe",
                    outcome=_provider_outcome(exc),
                    duration_s=time.perf_counter() - started,
                )
                raise
            metrics.record_provider_call(
                provider=provider.id,
                operation="transcribe",
                outcome="ok",
                duration_s=time.perf_counter() - started,
            )
            return self._finalize(job_id, scope, provider.id, transcript)
        except ProviderError as exc:
            return {
                "outcome": "transient" if exc.retryable else "permanent",
                "code": exc.code,
                "message": exc.message,
            }
        except DomainError as exc:
            # Validation failures (content, limits) do not fix themselves.
            return {"outcome": "permanent", "code": exc.code, "message": exc.message}
        except Exception as exc:
            log.warning("attempt %d for job %s failed: %r", attempt, job_id, exc)
            return {
                "outcome": "transient",
                "code": type(exc).__name__,
                "message": str(exc)[:300],
            }
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # asynchronous provider jobs
    # ------------------------------------------------------------------
    def _submit_remote(
        self,
        job_id: str,
        scope: str,
        attempt: int,
        provider,
        source: Path,
        language: str | None,
    ) -> dict:
        """Submit once per attempt; a retry of the same attempt never resubmits."""
        deps = self._deps
        with deps.session_factory() as session:
            repo = ProviderAttemptRepository(session)
            existing = repo.get(job_id, attempt)
            if (
                existing is not None
                and existing.state == "SUBMITTED"
                and existing.provider_job_id
            ):
                return {
                    "outcome": "pending",
                    "provider_job_id": existing.provider_job_id,
                }
            if existing is not None and existing.state == "SUBMITTING":
                return {
                    "outcome": "transient",
                    "code": "SUBMISSION_UNCERTAIN",
                    "message": (
                        "a previous submission for this attempt never "
                        "received a provider response"
                    ),
                }
            repo.record(
                job_id=job_id,
                scope=scope,
                attempt=attempt,
                provider_id=provider.id,
                state="SUBMITTING",
            )
        started = time.perf_counter()
        try:
            with activity_span(
                "stt.provider.submit",
                {"stt.provider": provider.id, "stt.operation": "submit"},
            ):
                ref = provider.submit(
                    TranscriptionRequest(audio_path=source, language=language)
                )
        except Exception as exc:
            metrics.record_provider_call(
                provider=provider.id,
                operation="submit",
                outcome=_provider_outcome(exc),
                duration_s=time.perf_counter() - started,
            )
            raise
        metrics.record_provider_call(
            provider=provider.id,
            operation="submit",
            outcome="ok",
            duration_s=time.perf_counter() - started,
        )
        with deps.session_factory() as session:
            ProviderAttemptRepository(session).record(
                job_id=job_id,
                scope=scope,
                attempt=attempt,
                provider_id=provider.id,
                state="SUBMITTED",
                provider_job_id=ref.provider_job_id,
                detail=ref.raw,
            )
        return {"outcome": "pending", "provider_job_id": ref.provider_job_id}

    def _finalize(
        self,
        job_id: str,
        scope: str,
        provider_id: str,
        transcript: ProviderTranscript,
    ) -> dict:
        """Persist the canonical result + artifacts; return the ok outcome."""
        deps = self._deps
        metrics.record_processing(
            provider=provider_id,
            duration_s=transcript.processing_duration_s,
            audio_duration_s=transcript.audio_duration_s,
        )
        result = _build_result(job_id, provider_id, transcript)
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
            "provider": provider_id,
            "model": transcript.model,
            "model_version": transcript.model_version,
            "language": transcript.language,
        }

    @activity.defn(name="poll_remote")
    def poll_remote(self, job_id: str, scope: str, attempt: int) -> dict:
        """One poll of the provider-side job; transport errors keep the poll alive."""
        with activity_span(
            "stt.poll",
            {"stt.job_id": job_id, "stt.scope": scope, "stt.attempt": attempt},
        ):
            token = obs_context.set_job_id(job_id)
            try:
                return self._poll_remote(job_id, scope, attempt)
            finally:
                obs_context.reset_job_id(token)

    def _poll_remote(self, job_id: str, scope: str, attempt: int) -> dict:
        deps = self._deps
        with deps.session_factory() as session:
            job_repo = JobRepository(session)
            record = job_repo.get(job_id, scope)
            attempt_row = ProviderAttemptRepository(session).get(job_id, attempt)
            saved = job_repo.load_result(job_id, scope)
        if attempt_row is None or attempt_row.provider_job_id is None:
            return {
                "state": "failed",
                "code": "ATTEMPT_NOT_FOUND",
                "message": f"attempt {attempt} has no provider reference",
                "retryable": True,
            }
        provider = deps.registry.get(record.requested_provider)
        if not isinstance(provider, AsyncProviderProtocol):
            return {
                "state": "failed",
                "code": "NOT_ASYNC_PROVIDER",
                "message": (
                    f"provider {provider.id!r} does not implement the "
                    "asynchronous contract"
                ),
                "retryable": False,
            }
        if attempt_row.state == "COMPLETED":
            # Replay after the result was already persisted: idempotent done.
            return {
                "state": "ok",
                "provider": saved.provider if saved else provider.id,
                "model": saved.model if saved else None,
                "model_version": saved.model_version if saved else None,
                "language": saved.language if saved else None,
            }
        ref = RemoteJobRef(
            provider_job_id=attempt_row.provider_job_id,
            raw=attempt_row.detail if isinstance(attempt_row.detail, dict) else None,
        )
        started = time.perf_counter()
        try:
            with activity_span(
                "stt.provider.poll",
                {"stt.provider": provider.id, "stt.operation": "poll"},
            ):
                remote = provider.poll(ref)
        except ProviderError as exc:
            metrics.record_provider_call(
                provider=provider.id,
                operation="poll",
                outcome=_provider_outcome(exc),
                duration_s=time.perf_counter() - started,
            )
            return {"state": "error", "code": exc.code, "message": exc.message}
        except Exception as exc:
            metrics.record_provider_call(
                provider=provider.id,
                operation="poll",
                outcome="error",
                duration_s=time.perf_counter() - started,
            )
            return {
                "state": "error",
                "code": type(exc).__name__,
                "message": str(exc)[:300],
            }
        metrics.record_provider_call(
            provider=provider.id,
            operation="poll",
            outcome="ok",
            duration_s=time.perf_counter() - started,
        )

        if remote.state == "pending":
            with deps.session_factory() as session:
                ProviderAttemptRepository(session).record(
                    job_id=job_id,
                    scope=scope,
                    attempt=attempt,
                    provider_id=provider.id,
                    state="PENDING",
                )
            return {"state": "pending"}
        if remote.state == "failed":
            with deps.session_factory() as session:
                ProviderAttemptRepository(session).record(
                    job_id=job_id,
                    scope=scope,
                    attempt=attempt,
                    provider_id=provider.id,
                    state="FAILED",
                    detail={"code": remote.error_code},
                )
            return {
                "state": "failed",
                "code": remote.error_code or "REMOTE_FAILED",
                "message": remote.error_message or "provider-side job failed",
                "retryable": remote.retryable,
            }
        if remote.transcript is None:
            return {
                "state": "failed",
                "code": "REMOTE_NO_RESULT",
                "message": "provider completed without a transcript",
                "retryable": True,
            }
        outcome = self._finalize(job_id, scope, provider.id, remote.transcript)
        with deps.session_factory() as session:
            ProviderAttemptRepository(session).record(
                job_id=job_id,
                scope=scope,
                attempt=attempt,
                provider_id=provider.id,
                state="COMPLETED",
            )
        return {"state": "ok", **outcome}

    @activity.defn(name="cancel_remote")
    def cancel_remote(self, job_id: str, scope: str) -> dict:
        """Best-effort provider-side cancellation of the newest open attempt."""
        with activity_span(
            "stt.cancel", {"stt.job_id": job_id, "stt.scope": scope}
        ):
            token = obs_context.set_job_id(job_id)
            try:
                return self._cancel_remote(job_id, scope)
            finally:
                obs_context.reset_job_id(token)

    def _cancel_remote(self, job_id: str, scope: str) -> dict:
        deps = self._deps
        with deps.session_factory() as session:
            record = JobRepository(session).get(job_id, scope)
            row = ProviderAttemptRepository(session).latest_open(job_id, scope)
        if row is None or row.provider_job_id is None:
            return {"cancelled": False}
        provider = deps.registry.get(record.requested_provider)
        if not isinstance(provider, AsyncProviderProtocol):
            return {"cancelled": False}
        supported = False
        try:
            supported = bool(
                provider.cancel(RemoteJobRef(provider_job_id=row.provider_job_id))
            )
        except Exception:
            supported = False
        with deps.session_factory() as session:
            ProviderAttemptRepository(session).record(
                job_id=job_id,
                scope=scope,
                attempt=row.attempt,
                provider_id=row.provider_id,
                state="CANCELLED",
            )
        return {"cancelled": supported}


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
        warnings=list(transcript.warnings),
    )


def make_activities(deps: ActivityDeps) -> list:
    """Bound activity callables for a Worker (the SDK needs callables
    carrying their ``@activity.defn`` marker; bound methods do)."""
    instance = TranscriptionActivities(deps)
    return [
        instance.transition,
        instance.execute_attempt,
        instance.poll_remote,
        instance.cancel_remote,
    ]
