"""Repository over the provider_attempts table.

Rows are written *before* the provider call (``SUBMITTING``) so a retry of
the same attempt can never blindly resubmit; the ``SUBMITTED`` row carries
the provider job reference the workflow polls. This is the audit trail for
provider-side jobs and the basis for duplicate-charge avoidance.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from stt_platform.persistence.models import ProviderAttemptRow

_OPEN_STATES = ("SUBMITTING", "SUBMITTED", "PENDING")


class ProviderAttemptRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, job_id: str, attempt: int) -> ProviderAttemptRow | None:
        return self._session.execute(
            select(ProviderAttemptRow).where(
                ProviderAttemptRow.job_id == job_id,
                ProviderAttemptRow.attempt == attempt,
            )
        ).scalar_one_or_none()

    def record(
        self,
        *,
        job_id: str,
        scope: str,
        attempt: int,
        provider_id: str,
        state: str,
        provider_job_id: str | None = None,
        detail: dict | None = None,
    ) -> ProviderAttemptRow:
        """Create or update the row for (job, attempt).

        ``provider_job_id``/``detail`` are only overwritten when supplied,
        so later state updates cannot erase the submission reference.
        """
        row = self.get(job_id, attempt)
        if row is None:
            row = ProviderAttemptRow(
                job_id=job_id,
                scope=scope,
                attempt=attempt,
                provider_id=provider_id,
                state=state,
                provider_job_id=provider_job_id,
                detail=detail,
            )
            self._session.add(row)
        else:
            row.state = state
            if provider_job_id is not None:
                row.provider_job_id = provider_job_id
            if detail is not None:
                row.detail = detail
        self._session.commit()
        return row

    def latest_open(self, job_id: str, scope: str) -> ProviderAttemptRow | None:
        """The newest attempt row that is not in a terminal state."""
        return (
            self._session.execute(
                select(ProviderAttemptRow)
                .where(
                    ProviderAttemptRow.job_id == job_id,
                    ProviderAttemptRow.scope == scope,
                    ProviderAttemptRow.state.in_(_OPEN_STATES),
                )
                .order_by(ProviderAttemptRow.attempt.desc())
            )
            .scalars()
            .first()
        )

    def list_for_job(self, job_id: str, scope: str) -> list[ProviderAttemptRow]:
        return list(
            self._session.execute(
                select(ProviderAttemptRow)
                .where(
                    ProviderAttemptRow.job_id == job_id,
                    ProviderAttemptRow.scope == scope,
                )
                .order_by(ProviderAttemptRow.attempt)
            ).scalars()
        )
