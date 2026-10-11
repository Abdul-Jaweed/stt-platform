import pytest
from pydantic import ValidationError

from stt_platform.domain.status import (
    ALLOWED_TRANSITIONS,
    JobStatus,
    is_transition_allowed,
)
from stt_platform.schemas.jobs import AudioMetadata, JobFailure, JobRecord


def test_status_enum_values():
    assert {s.value for s in JobStatus} == {
        "ACCEPTED",
        "VALIDATING",
        "STORED",
        "QUEUED",
        "PROCESSING",
        "SUCCEEDED",
        "RETRY_WAIT",
        "FAILED",
        "CANCELLED",
    }


def test_happy_path_transitions_allowed():
    chain = [
        JobStatus.ACCEPTED,
        JobStatus.VALIDATING,
        JobStatus.STORED,
        JobStatus.QUEUED,
        JobStatus.PROCESSING,
        JobStatus.SUCCEEDED,
    ]
    for frm, to in zip(chain, chain[1:]):
        assert is_transition_allowed(frm, to), f"{frm} -> {to}"


def test_invalid_transitions_rejected():
    assert not is_transition_allowed(JobStatus.SUCCEEDED, JobStatus.QUEUED)
    assert not is_transition_allowed(JobStatus.ACCEPTED, JobStatus.PROCESSING)
    assert not is_transition_allowed(JobStatus.FAILED, JobStatus.SUCCEEDED)


def test_retry_and_cancel_paths_allowed():
    assert is_transition_allowed(JobStatus.FAILED, JobStatus.QUEUED)
    assert is_transition_allowed(JobStatus.PROCESSING, JobStatus.RETRY_WAIT)
    assert is_transition_allowed(JobStatus.RETRY_WAIT, JobStatus.QUEUED)
    assert is_transition_allowed(JobStatus.QUEUED, JobStatus.CANCELLED)
    assert is_transition_allowed(JobStatus.PROCESSING, JobStatus.CANCELLED)


def test_terminal_states_have_no_outgoing_transitions():
    assert ALLOWED_TRANSITIONS[JobStatus.SUCCEEDED] == frozenset()
    assert ALLOWED_TRANSITIONS[JobStatus.CANCELLED] == frozenset()


def test_job_record_defaults():
    job = JobRecord(id="j1", status=JobStatus.ACCEPTED)
    assert job.scope == "local"
    assert job.attempt_count == 0
    assert job.audio is None and job.failure is None
    assert job.created_at.tzinfo is not None


def test_job_record_rejects_unknown_status():
    with pytest.raises(ValidationError):
        JobRecord(id="j1", status="NOPE")


def test_job_failure_model():
    f = JobFailure(code="AUDIO_TOO_LONG", message="too long", retryable=False)
    assert f.retryable is False and f.provider_code is None


def test_audio_metadata_validation():
    meta = AudioMetadata(
        format_name="wav",
        codec="pcm_s16le",
        sample_rate=16000,
        channels=1,
        duration_s=10.0,
        size_bytes=320044,
    )
    assert meta.channels == 1
    with pytest.raises(ValidationError):
        AudioMetadata(
            format_name="wav", sample_rate=0, channels=1, duration_s=10.0, size_bytes=10
        )
