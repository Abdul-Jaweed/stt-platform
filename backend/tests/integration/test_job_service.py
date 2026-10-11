"""Job service: idempotency, transition trail, sync processing (real PG)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.core.config import Settings
from stt_platform.domain.errors import (
    IdempotencyConflict,
    InvalidTransition,
    JobNotFound,
    JobNotReady,
    ProviderError,
    ProviderNotFound,
)
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.models import Base
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.schemas.transcript import TranscriptSegment
from stt_platform.services.jobs import JobService
from tests.audio_fixtures import make_wav
from tests.fakes import FakeProvider, sample_transcript


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def make_service(pg_engine, tmp_path):
    sessions = []

    def _make(*providers, **setting_overrides):
        Base.metadata.drop_all(pg_engine)
        Base.metadata.create_all(pg_engine)
        session = sessionmaker(bind=pg_engine, future=True)()
        sessions.append(session)
        repo = JobRepository(session)
        registry = ProviderRegistry()
        for provider in providers or (FakeProvider(),):
            registry.register(provider)
        values = dict(
            _env_file=None,
            repo_root=tmp_path,
            temp_dir=tmp_path / "t",
            audio_store_dir=tmp_path / "a",
            max_sync_duration_s=60.0,
        )
        values.update(setting_overrides)
        service = JobService(repo, registry, Settings(**values))
        return service, repo

    yield _make

    # Close every session this test created: an unclosed session leaves a
    # pooled connection idle-in-transaction, and the next test's drop_all
    # blocks on its locks forever.
    for session in sessions:
        session.close()


def test_happy_path_trail_and_result(make_service, tmp_path):
    service, repo = make_service()
    wav = make_wav(tmp_path, seconds=1.0)
    job = service.create_and_process(audio_path=wav, language="en")

    assert job.status is JobStatus.SUCCEEDED
    assert job.actual_provider == "fake"
    assert job.language_detected == "en"
    assert job.audio is not None and job.audio.format_name == "wav"
    assert repo.transitions(job.id) == [
        ("ACCEPTED", "VALIDATING"),
        ("VALIDATING", "STORED"),
        ("STORED", "QUEUED"),
        ("QUEUED", "PROCESSING"),
        ("PROCESSING", "SUCCEEDED"),
    ]

    result = service.get_result(job.id)
    assert result.job_id == job.id
    assert result.text == "hello world"
    assert result.timestamp_granularity == "word"
    assert result.provider == "fake"
    assert result.segments[0].words[0].text == "hello"


def test_attempt_count_is_one_on_first_attempt(make_service, tmp_path):
    service, _ = make_service()
    wav = make_wav(tmp_path, seconds=1.0)
    job = service.create_and_process(audio_path=wav)
    assert job.attempt_count == 1


def test_idempotent_replay_returns_same_job(make_service, tmp_path):
    service, repo = make_service()
    wav = make_wav(tmp_path, seconds=1.0)
    j1 = service.create_and_process(audio_path=wav, idempotency_key="k1")
    j2 = service.create_and_process(audio_path=wav, idempotency_key="k1")
    assert j1.id == j2.id
    processing = [t for t in repo.transitions(j1.id) if t[1] == "PROCESSING"]
    assert len(processing) == 1


def test_idempotency_conflict_on_different_params(make_service, tmp_path):
    service, _ = make_service()
    wav = make_wav(tmp_path, seconds=1.0)
    service.create_and_process(audio_path=wav, idempotency_key="k1")
    with pytest.raises(IdempotencyConflict):
        service.create_and_process(
            audio_path=wav, language="hi", idempotency_key="k1"
        )


def test_invalid_audio_fails_permanently(make_service, tmp_path):
    service, repo = make_service()
    bad = tmp_path / "bomb.wav"
    bad.write_bytes(b"definitely not audio " * 50)
    job = service.create_and_process(audio_path=bad)
    assert job.status is JobStatus.FAILED
    assert job.failure is not None
    assert job.failure.retryable is False
    assert job.failure.code == "UNDECODABLE"
    assert repo.transitions(job.id)[-1] == ("VALIDATING", "FAILED")


def test_audio_too_long_fails(make_service, tmp_path):
    service, _ = make_service(max_sync_duration_s=0.5)
    wav = make_wav(tmp_path, seconds=1.0)
    job = service.create_and_process(audio_path=wav)
    assert job.status is JobStatus.FAILED
    assert job.failure is not None
    assert job.failure.code == "AUDIO_TOO_LONG"
    assert job.failure.retryable is False


def test_transient_failure_then_retry_succeeds(make_service, tmp_path):
    class FlakyProvider(FakeProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def transcribe(self, request):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError(
                    "FAKE_TRANSIENT", "temporary failure", retryable=True
                )
            return super().transcribe(request)

    flaky = FlakyProvider()
    service, repo = make_service(flaky)
    wav = make_wav(tmp_path, seconds=1.0)
    job = service.create_and_process(audio_path=wav)
    assert job.status is JobStatus.FAILED
    assert job.failure is not None and job.failure.retryable is True

    job2 = service.retry(job.id)
    assert job2.status is JobStatus.SUCCEEDED
    assert job2.attempt_count == 2
    assert repo.transitions(job.id) == [
        ("ACCEPTED", "VALIDATING"),
        ("VALIDATING", "STORED"),
        ("STORED", "QUEUED"),
        ("QUEUED", "PROCESSING"),
        ("PROCESSING", "FAILED"),
        ("FAILED", "QUEUED"),
        ("QUEUED", "PROCESSING"),
        ("PROCESSING", "SUCCEEDED"),
    ]
    assert service.get_result(job.id).text == "hello world"


def test_retry_non_retryable_rejected(make_service, tmp_path):
    service, _ = make_service()
    bad = tmp_path / "bomb.wav"
    bad.write_bytes(b"definitely not audio " * 50)
    job = service.create_and_process(audio_path=bad)
    with pytest.raises(InvalidTransition):
        service.retry(job.id)


def test_unknown_provider_raises(make_service, tmp_path):
    service, _ = make_service()
    wav = make_wav(tmp_path, seconds=1.0)
    with pytest.raises(ProviderNotFound):
        service.create_and_process(audio_path=wav, provider_id="nope")


def test_get_result_before_success_raises(make_service, tmp_path):
    service, _ = make_service()
    bad = tmp_path / "bomb.wav"
    bad.write_bytes(b"definitely not audio " * 50)
    job = service.create_and_process(audio_path=bad)
    with pytest.raises(JobNotReady):
        service.get_result(job.id)


def test_scope_isolation_for_results(make_service, tmp_path):
    service, _ = make_service()
    wav = make_wav(tmp_path, seconds=1.0)
    job = service.create_and_process(audio_path=wav, scope="tenant-a")
    assert service.get_result(job.id, scope="tenant-a").job_id == job.id
    with pytest.raises(JobNotFound):
        service.get_result(job.id, scope="tenant-b")


def test_missing_audio_file_fails_validation(make_service, tmp_path):
    service, _ = make_service()
    job = service.create_and_process(audio_path=tmp_path / "absent.wav")
    assert job.status is JobStatus.FAILED
    assert job.failure is not None and job.failure.retryable is False


def test_provider_contract_violation_fails_permanently(make_service, tmp_path):
    class SloppyProvider(FakeProvider):
        def transcribe(self, request):
            return sample_transcript(
                audio_duration_s=1.0,
                segments=[TranscriptSegment(start=0.0, end=99.0, text="too long")],
                words_available=False,
            )

    service, _ = make_service(SloppyProvider())
    wav = make_wav(tmp_path, seconds=1.0)
    job = service.create_and_process(audio_path=wav)
    assert job.status is JobStatus.FAILED
    assert job.failure is not None
    assert job.failure.retryable is False
    assert job.failure.code == "PROVIDER_CONTRACT_VIOLATION"
