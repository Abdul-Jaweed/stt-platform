"""Evaluating through the real pipeline: jobs, timings, storage, providers.

The runner measures whatever the transcriber reports; this module is the
production transcriber, so an evaluation's latency numbers come from the
same job records the API serves and the observability phase exports.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.evaluation.datasets import Clip, Dataset
from stt_platform.evaluation.platform import PlatformTranscriber
from stt_platform.evaluation.runner import EvaluationError, run_evaluation
from stt_platform.persistence.models import Base
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.base import (
    ProviderCapabilities,
    ProviderHealth,
    ProviderTranscript,
)
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.schemas.transcript import TranscriptSegment, TranscriptWord
from stt_platform.services.jobs import JobService
from tests.audio_fixtures import make_wav
from tests.fakes import FakeProvider
from tests.integration.harness import build_store, make_settings
from tests.rustfs_container import RustFSContainer


@pytest.fixture(scope="module")
def rustfs():
    with RustFSContainer() as container:
        yield container


@pytest.fixture(scope="module")
def store(rustfs):
    return build_store(rustfs)


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(pg_engine):
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    return sessionmaker(bind=pg_engine, future=True)


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


def make_service(session_factory, settings, provider, store=None):
    session = session_factory()
    registry = ProviderRegistry()
    registry.register(provider, default=True)
    service = JobService(JobRepository(session), registry, settings, store=store)
    return service, session


class ScriptedProvider:
    """Transcript derived from the audio file's stem, so each clip differs."""

    id = "scripted"
    name = "Scripted Provider"
    is_local = True

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            languages=["en"],
            segment_timestamps=True,
            word_timestamps=True,
            formats=["wav"],
            capability_sources={"word_timestamps": "verified"},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(status="ok")

    def transcribe(self, request: Any) -> ProviderTranscript:
        words = Path(request.audio_path).stem.split("-")
        text = " ".join(words)
        span = 4.0 / max(1, len(words))
        return ProviderTranscript(
            text=text,
            language="en",
            language_probability=0.99,
            segments=[
                TranscriptSegment(
                    start=0.0,
                    end=4.0,
                    text=text,
                    words=[
                        TranscriptWord(
                            start=index * span,
                            end=(index + 1) * span,
                            text=word,
                            confidence=0.9,
                        )
                        for index, word in enumerate(words)
                    ],
                )
            ],
            words_available=True,
            model="scripted-model",
            model_version="1",
            audio_duration_s=4.0,
            processing_duration_s=0.2,
        )


def _clip(clip_id: str, audio: Path, text: str, duration: float = 4.0) -> Clip:
    return Clip(
        clip_id=clip_id,
        audio=audio,
        reference_text=text,
        duration_s=duration,
        language="en",
        domain="technical",
        accent="synthetic-us",
        noise="none",
        reference_source="synthetic-tts",
        reference_segments=(),
        sha256="x",
    )


def _dataset(*clips: Clip) -> Dataset:
    return Dataset(
        dataset_id="platform-set",
        version="1.0.0",
        language="en",
        audio_root=clips[0].audio.parent,
        clips=clips,
        missing_audio=(),
        path=Path("manifest.json"),
    )


def test_platform_transcriber_measures_the_real_job(
    session_factory, settings, store, tmp_path
):
    audio = make_wav(tmp_path / "clip-alpha.wav", seconds=4.0)
    service, session = make_service(session_factory, settings, ScriptedProvider(), store)
    try:
        transcriber = PlatformTranscriber(
            service=service, provider_id="scripted", scope="local"
        )
        outcome = transcriber(_clip("clip-alpha", audio, "clip alpha"))

        assert outcome.text == "clip alpha"
        assert outcome.provider == "scripted"
        assert outcome.model == "scripted-model"
        assert outcome.attempts == 1
        assert outcome.retries == 0
        assert outcome.timings["queue_wait_s"] is not None
        assert outcome.timings["queue_wait_s"] >= 0
        assert outcome.timings["processing_s"] == pytest.approx(0.2)
        assert outcome.timings["end_to_end_s"] >= 0
        assert outcome.timings["real_time_factor"] == pytest.approx(0.05)
        assert outcome.granularity in {"segment", "word"}
    finally:
        session.close()


def test_evaluation_through_the_pipeline_scores_and_times_every_clip(
    session_factory, settings, store, tmp_path
):
    first = make_wav(tmp_path / "hello-world.wav", seconds=4.0)
    second = make_wav(tmp_path / "goodbye-moon.wav", seconds=4.0)
    service, session = make_service(session_factory, settings, ScriptedProvider(), store)
    try:
        result = run_evaluation(
            dataset=_dataset(
                _clip("clip-a", first, "hello world"),
                _clip("clip-b", second, "goodbye mars"),
            ),
            transcribe=PlatformTranscriber(service=service, provider_id="scripted"),
            provider="scripted",
            decoding={"language": "en"},
        )
    finally:
        session.close()

    assert result.aggregate["clips_scored"] == 2
    assert result.aggregate["wer_micro"] == pytest.approx(1 / 4)
    assert result.aggregate["failures"] == 0
    assert result.aggregate["rtf_mean"] == pytest.approx(0.05)
    assert result.aggregate["segment_availability_mean"] == 1.0
    assert result.aggregate["word_availability_mean"] == 1.0
    assert result.by_group["domain"]["technical"]["clips"] == 2
    assert result.insufficient_evidence is False


def test_a_failing_job_is_a_recorded_failure_not_a_crash(
    session_factory, settings, store, tmp_path
):
    audio = make_wav(tmp_path / "clip-a.wav", seconds=4.0)
    service, session = make_service(
        session_factory, settings, FakeProvider(fail="permanent"), store
    )
    try:
        transcriber = PlatformTranscriber(service=service, provider_id="fake")
        with pytest.raises(EvaluationError, match="FAKE_PERMANENT"):
            transcriber(_clip("clip-a", audio, "hello world"))

        result = run_evaluation(
            dataset=_dataset(_clip("clip-a", audio, "hello world")),
            transcribe=transcriber,
            provider="fake",
        )
    finally:
        session.close()

    assert result.clips[0].status == "failed"
    assert result.aggregate["failures"] == 1
    assert result.aggregate["clips_scored"] == 0
