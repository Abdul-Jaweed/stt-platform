"""Evaluating through the real pipeline: jobs, timings, storage, providers.

The runner measures whatever a transcriber reports; this module is the
production transcriber. An evaluation's latency numbers therefore come from
the same job records the API serves and Phase 5 exports, instead of a
second stopwatch that could disagree with them.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from stt_platform.domain.status import JobStatus
from stt_platform.evaluation.datasets import Clip
from stt_platform.evaluation.runner import ClipOutcome, EvaluationError
from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.services.jobs import JobService


def _seconds_between(start: Any, end: Any) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


@dataclass
class PlatformTranscriber:
    """Transcribe a clip as a real job and report what the job recorded."""

    service: Any
    provider_id: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    scope: str = "local"

    def __call__(self, clip: Clip) -> ClipOutcome:
        record = self.service.create_and_process(
            audio_path=clip.audio,
            provider_id=self.provider_id,
            options=dict(self.options),
            scope=self.scope,
        )
        if record.status is not JobStatus.SUCCEEDED:
            failure = record.failure
            detail = f": {failure.code}" if failure is not None else ""
            raise EvaluationError(
                f"job {record.id} ended {record.status.value}{detail}"
            )

        result = self.service.get_result(record.id, self.scope)
        audio_duration_s = result.audio_duration_s or clip.duration_s
        processing_s = result.processing_duration_s
        real_time_factor = (
            processing_s / audio_duration_s
            if processing_s is not None and audio_duration_s
            else None
        )
        return ClipOutcome(
            text=result.text,
            segments=tuple(result.segments),
            granularity=result.timestamp_granularity,
            timings={
                "queue_wait_s": _seconds_between(
                    record.created_at, record.started_at
                ),
                "processing_s": processing_s,
                "end_to_end_s": _seconds_between(
                    record.created_at, record.completed_at
                ),
                "real_time_factor": real_time_factor,
            },
            attempts=record.attempt_count,
            retries=max(0, record.attempt_count - 1),
            provider=record.actual_provider or record.requested_provider or "unknown",
            model=record.model,
        )


@contextmanager
def state_transcriber(
    state: Any,
    *,
    provider_id: str | None = None,
    options: dict[str, Any] | None = None,
    scope: str = "local",
) -> Iterator[PlatformTranscriber]:
    """A transcriber wired from an app state, mirroring the API's service."""
    session = state.session_factory()
    try:
        starter = getattr(state, "starter", None)
        service = JobService(
            JobRepository(session),
            state.registry,
            state.settings,
            store=getattr(state, "store", None),
            outbox=OutboxRepository(session),
            starter=starter,
            canceller=(starter.cancel if starter is not None else None),
        )
        yield PlatformTranscriber(
            service=service,
            provider_id=provider_id,
            options=options or {},
            scope=scope,
        )
    finally:
        session.close()
