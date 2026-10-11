"""Repository behavior against a real PostgreSQL (ephemeral container)."""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.domain.errors import InvalidTransition, JobNotFound
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.models import Base
from stt_platform.persistence.repositories import JobRepository
from stt_platform.schemas.jobs import AudioMetadata, JobRecord
from stt_platform.schemas.transcript import TranscriptResult, TranscriptSegment


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(pg_engine):
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    session = sessionmaker(bind=pg_engine, future=True)()
    yield JobRepository(session)
    session.close()


def _job(**overrides) -> JobRecord:
    base = dict(id=str(uuid.uuid4()), status=JobStatus.ACCEPTED)
    base.update(overrides)
    return JobRecord(**base)


def test_create_and_get_roundtrip(repo):
    audio = AudioMetadata(
        format_name="wav", codec="pcm_s16le", sample_rate=16000, channels=1,
        duration_s=10.0, size_bytes=320044,
    )
    job = repo.create(_job(), audio=audio, audio_path=".data/audio/x/original.wav")
    got = repo.get(job.id)
    assert got.id == job.id
    assert got.status is JobStatus.ACCEPTED
    assert got.audio == audio
    assert got.audio_path.endswith("original.wav")
    assert got.created_at.tzinfo is not None


def test_get_unknown_raises(repo):
    with pytest.raises(JobNotFound):
        repo.get("nope")


def test_scope_isolation(repo):
    job = repo.create(_job(scope="tenant-a"))
    with pytest.raises(JobNotFound):
        repo.get(job.id, scope="tenant-b")
    assert repo.get(job.id, scope="tenant-a").id == job.id


def test_transition_trail_and_validation(repo):
    job = repo.create(_job())
    chain = [
        JobStatus.VALIDATING, JobStatus.STORED, JobStatus.QUEUED,
        JobStatus.PROCESSING, JobStatus.SUCCEEDED,
    ]
    for status in chain:
        repo.append_transition(job.id, status)
    assert repo.transitions(job.id) == [
        ("ACCEPTED", "VALIDATING"),
        ("VALIDATING", "STORED"),
        ("STORED", "QUEUED"),
        ("QUEUED", "PROCESSING"),
        ("PROCESSING", "SUCCEEDED"),
    ]
    final = repo.get(job.id)
    assert final.status is JobStatus.SUCCEEDED
    assert final.completed_at is not None
    assert final.started_at is not None


def test_invalid_transition_rejected(repo):
    job = repo.create(_job())
    with pytest.raises(InvalidTransition):
        repo.append_transition(job.id, JobStatus.SUCCEEDED)


def test_attempt_count_increments_on_processing(repo):
    job = repo.create(_job())
    for status in [JobStatus.VALIDATING, JobStatus.STORED, JobStatus.QUEUED, JobStatus.PROCESSING]:
        repo.append_transition(job.id, status)
    assert repo.get(job.id).attempt_count == 1


def test_idempotency_lookup_and_unique_key(repo):
    j1 = repo.create(_job(idempotency_key="k1", request_fingerprint="f" * 64))
    found = repo.find_by_idempotency("local", "k1")
    assert found is not None
    assert found.id == j1.id
    assert found.request_fingerprint == "f" * 64
    assert repo.find_by_idempotency("local", "nope") is None
    assert repo.find_by_idempotency("other-scope", "k1") is None


def test_result_save_load_roundtrip(repo):
    job = repo.create(_job())
    for status in [
        JobStatus.VALIDATING, JobStatus.STORED, JobStatus.QUEUED,
        JobStatus.PROCESSING, JobStatus.SUCCEEDED,
    ]:
        repo.append_transition(job.id, status)
    transcript = TranscriptResult(
        job_id=job.id,
        text="hi",
        language="en",
        segments=[TranscriptSegment(start=0.0, end=1.0, text="hi")],
        audio_duration_s=1.0,
        processing_duration_s=0.2,
        provider="fake",
        model="fake-model",
    )
    repo.save_result(job.id, transcript)
    loaded = repo.load_result(job.id)
    assert loaded == transcript
