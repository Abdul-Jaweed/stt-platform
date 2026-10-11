"""Outbox repository: claim (SKIP LOCKED), mark dispatched/failed."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from stt_platform.persistence.models import OutboxRow, _utcnow


class OutboxRepository:
    def __init__(self, session: Session):
        self.session = session

    def enqueue(
        self,
        job_id: str,
        *,
        topic: str = "transcription.start",
        scope: str = "local",
    ) -> OutboxRow:
        """Fresh dispatch row for an existing job (re-enqueue after retry)."""
        row = OutboxRow(
            topic=topic,
            job_id=job_id,
            payload={"job_id": job_id, "scope": scope},
        )
        self.session.add(row)
        self.session.commit()
        return row

    def latest_for_job(self, job_id: str) -> OutboxRow | None:
        """Newest dispatch row for a job (used to derive its workflow id)."""
        return (
            self.session.execute(
                select(OutboxRow)
                .where(OutboxRow.job_id == job_id)
                .order_by(OutboxRow.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

    def claim_batch(self, limit: int = 10) -> list[OutboxRow]:
        """Pending rows, locked FOR UPDATE SKIP LOCKED, oldest first.

        Does not commit: the caller holds the locks while it starts the
        workflow, then calls ``mark_dispatched`` / ``mark_failed`` (each
        commits, releasing the locks).
        """
        return list(
            self.session.execute(
                select(OutboxRow)
                .where(OutboxRow.dispatched_at.is_(None))
                .order_by(OutboxRow.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).scalars()
        )

    def mark_dispatched(self, row_id: int) -> None:
        row = self.session.get(OutboxRow, row_id)
        if row is not None:
            row.dispatched_at = _utcnow()
        self.session.commit()

    def mark_failed(self, row_id: int, error: str) -> None:
        row = self.session.get(OutboxRow, row_id)
        if row is not None:
            row.attempt_count += 1
            row.last_error = error[:500]
        self.session.commit()
