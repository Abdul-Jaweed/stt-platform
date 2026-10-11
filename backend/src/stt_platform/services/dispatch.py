"""Dispatcher: drain the transactional outbox into Temporal workflow starts.

At-least-once dispatch: a crash between commit and dispatch leaves the
row pending; the workflow id derives from the outbox row id, so a
re-dispatch of the same row is a no-op on Temporal's side.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from stt_platform.domain.errors import JobNotFound
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.persistence.repositories import JobRepository

log = logging.getLogger(__name__)

Starter = Callable[[dict], None]

_TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


def workflow_id_for(job_id: str, outbox_id: int) -> str:
    """Deterministic workflow id derived from the outbox row.

    Re-dispatch of the same row maps to the same workflow id, so
    at-least-once delivery converges to exactly-once execution.
    """
    return f"transcribe.{job_id}.{outbox_id}"


def dispatch_pending(
    outbox: OutboxRepository, starter: Starter, *, limit: int = 10
) -> int:
    """Start workflows for pending outbox rows; returns the started count.

    ``starter`` receives ``{"job_id", "scope", "workflow_id"}``. Rows whose
    job is already terminal are spent (marked dispatched) without starting
    anything; rows whose start raises stay pending for the next tick.
    """
    jobs = JobRepository(outbox.session)
    started = 0
    for row in outbox.claim_batch(limit):
        payload = dict(row.payload)
        payload["workflow_id"] = workflow_id_for(row.job_id, row.id)
        try:
            job = jobs.get(row.job_id, scope=str(payload.get("scope", "local")))
        except JobNotFound:
            outbox.mark_dispatched(row.id)
            continue
        if job.status in _TERMINAL:
            outbox.mark_dispatched(row.id)
            continue
        try:
            starter(payload)
        except Exception as exc:
            log.warning("dispatch failed for job %s: %s", row.job_id, exc)
            outbox.mark_failed(row.id, f"{type(exc).__name__}: {exc}")
            continue
        outbox.mark_dispatched(row.id)
        started += 1
    return started
