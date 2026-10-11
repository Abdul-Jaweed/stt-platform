from pathlib import Path

import pytest

from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import ProviderProtocol, TranscriptionRequest
from stt_platform.schemas.transcript import TranscriptResult
from tests.fakes import FakeProvider


def _req() -> TranscriptionRequest:
    return TranscriptionRequest(audio_path=Path("x.wav"), language="en")


def test_fake_provider_satisfies_contract():
    p = FakeProvider()
    assert isinstance(p, ProviderProtocol)
    caps = p.capabilities()
    assert caps.segment_timestamps and caps.formats and caps.languages
    assert p.health().status in {"ok", "degraded", "unavailable"}


def test_transcript_converts_to_canonical_result():
    p = FakeProvider()
    tr = p.transcribe(_req())
    result = TranscriptResult(
        job_id="j1",
        text=tr.text,
        language=tr.language,
        segments=tr.segments,
        timestamp_granularity="word" if tr.words_available else "segment",
        audio_duration_s=tr.audio_duration_s,
        processing_duration_s=tr.processing_duration_s,
        provider=p.id,
        model=tr.model,
    )
    assert result.segments[0].words[0].text == "hello"


def test_transient_failure_marked_retryable():
    with pytest.raises(ProviderError) as excinfo:
        FakeProvider(fail="transient").transcribe(_req())
    assert excinfo.value.retryable is True


def test_permanent_failure_marked_not_retryable():
    with pytest.raises(ProviderError) as excinfo:
        FakeProvider(fail="permanent").transcribe(_req())
    assert excinfo.value.retryable is False


def test_request_defaults():
    req = TranscriptionRequest(audio_path=Path("a.wav"))
    assert req.language is None
    assert req.options == {}
