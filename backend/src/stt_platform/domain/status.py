"""Job lifecycle status machine.

PostgreSQL stores every transition; this module is the single source of
truth for which transitions are legal. ``PARTIALLY_SUCCEEDED`` is
deliberately absent until its semantics are defined (spec §9).
"""
from enum import Enum


class JobStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    VALIDATING = "VALIDATING"
    STORED = "STORED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.ACCEPTED: frozenset({JobStatus.VALIDATING}),
    JobStatus.VALIDATING: frozenset({JobStatus.STORED, JobStatus.FAILED}),
    JobStatus.STORED: frozenset({JobStatus.QUEUED}),
    JobStatus.QUEUED: frozenset({JobStatus.PROCESSING, JobStatus.CANCELLED}),
    JobStatus.PROCESSING: frozenset(
        {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.RETRY_WAIT,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.RETRY_WAIT: frozenset({JobStatus.QUEUED, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def is_transition_allowed(from_status: JobStatus, to_status: JobStatus) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())
