"""Status machine transitions used by the durable-processing flows."""
from stt_platform.domain.status import (
    ALLOWED_TRANSITIONS,
    JobStatus,
    is_transition_allowed,
)


def test_retry_wait_can_be_cancelled():
    assert is_transition_allowed(JobStatus.RETRY_WAIT, JobStatus.CANCELLED)


def test_retry_wait_retries_through_queued_only():
    assert is_transition_allowed(JobStatus.RETRY_WAIT, JobStatus.QUEUED)
    assert is_transition_allowed(JobStatus.RETRY_WAIT, JobStatus.FAILED)
    assert not is_transition_allowed(JobStatus.RETRY_WAIT, JobStatus.PROCESSING)
    assert not is_transition_allowed(JobStatus.RETRY_WAIT, JobStatus.SUCCEEDED)


def test_failed_can_be_requeued_but_not_processed_directly():
    assert is_transition_allowed(JobStatus.FAILED, JobStatus.QUEUED)
    assert not is_transition_allowed(JobStatus.FAILED, JobStatus.PROCESSING)


def test_queued_and_processing_can_be_cancelled():
    assert is_transition_allowed(JobStatus.QUEUED, JobStatus.CANCELLED)
    assert is_transition_allowed(JobStatus.PROCESSING, JobStatus.CANCELLED)


def test_succeeded_and_cancelled_are_final():
    assert ALLOWED_TRANSITIONS[JobStatus.SUCCEEDED] == frozenset()
    assert ALLOWED_TRANSITIONS[JobStatus.CANCELLED] == frozenset()


def test_stored_can_fail_on_storage_errors():
    # Original preservation happens between STORED and QUEUED; a storage
    # outage there is a retryable failure, not a stuck job.
    assert is_transition_allowed(JobStatus.STORED, JobStatus.FAILED)
    assert is_transition_allowed(JobStatus.STORED, JobStatus.QUEUED)
