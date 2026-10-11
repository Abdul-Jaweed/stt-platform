"""Retention and deletion: one code path for objects, one for rows.

Deleting a job means deleting what the platform stored for it — the original
audio, the transcript artifact, any retained raw provider response, and the
local working copy — and then the row (the database cascades transitions,
provider attempts and outbox rows).

``select_expired`` is deliberately pure and conservative: only *terminal*
jobs are ever candidates, so a purge can never delete work in flight.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from stt_platform.persistence.models import JobRow
from stt_platform.storage.keys import artifact_key

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
# Artifacts the platform may have written for a job.
ARTIFACT_NAMES = ("transcript.json", "raw.json")


@dataclass
class PurgeReport:
    """What a purge run saw and what it did (dry runs delete nothing)."""

    candidates: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "candidates": self.candidates,
            "deleted": self.deleted,
            "candidate_count": len(self.candidates),
            "deleted_count": len(self.deleted),
        }


def select_expired(
    rows: Iterable[Sequence[Any]],
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> list[str]:
    """Job ids that are terminal *and* older than the retention window.

    ``rows`` are ``(job_id, status, updated_at)`` triples.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=older_than_days)
    expired: list[str] = []
    for job_id, status, updated_at in rows:
        if status not in TERMINAL_STATUSES:
            continue
        if updated_at is None or updated_at >= cutoff:
            continue
        expired.append(job_id)
    return expired


def delete_job_objects(
    store: Any | None,
    *,
    scope: str,
    job_id: str,
    original_object_key: str | None = None,
    audio_path: str | None = None,
) -> None:
    """Remove everything object storage holds for one job."""
    if store is not None:
        if original_object_key:
            store.delete(store.bucket_audio, original_object_key)
        for name in ARTIFACT_NAMES:
            store.delete(store.bucket_artifacts, artifact_key(scope, job_id, name))
    if audio_path:
        Path(audio_path).unlink(missing_ok=True)


def purge_expired(
    session: Session,
    store: Any | None,
    *,
    older_than_days: int,
    dry_run: bool = False,
    now: datetime | None = None,
) -> PurgeReport:
    """Delete expired terminal jobs (objects then rows) — or report them."""
    rows = session.execute(
        select(
            JobRow.id,
            JobRow.scope,
            JobRow.status,
            JobRow.updated_at,
            JobRow.original_object_key,
            JobRow.audio_path,
        )
    ).all()
    by_id = {row[0]: row for row in rows}
    candidates = select_expired(
        [(row[0], row[2], row[3]) for row in rows],
        older_than_days=older_than_days,
        now=now,
    )

    report = PurgeReport(candidates=candidates, dry_run=dry_run)
    if dry_run:
        return report

    for job_id in candidates:
        _id, scope, _status, _updated, original_key, audio_path = by_id[job_id]
        delete_job_objects(
            store,
            scope=scope,
            job_id=job_id,
            original_object_key=original_key,
            audio_path=audio_path,
        )
        session.execute(sql_delete(JobRow).where(JobRow.id == job_id))
        report.deleted.append(job_id)
    return report
