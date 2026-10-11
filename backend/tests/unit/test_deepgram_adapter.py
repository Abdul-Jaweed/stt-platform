"""Deepgram adapter tests.

Vendor payloads are synthesized fixtures modelled on the documented
response shape (see the adapter docstring for doc links). respx
intercepts every call: no network, no paid API usage.
"""
import json
from pathlib import Path

import httpx
import pytest
import respx

from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import TranscriptionRequest
from stt_platform.providers.managed.deepgram import DeepgramProvider

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "deepgram_listen.json"
LISTEN_URL = "https://api.deepgram.com/v1/listen"


def make_provider(**overrides) -> DeepgramProvider:
    settings = Settings(deepgram_api_key="dg-test-key", **overrides)
    return DeepgramProvider(settings=settings)


def audio_file(tmp_path) -> Path:
    path = tmp_path / "sample.wav"
    path.write_bytes(b"RIFF-fake-wav-bytes")
    return path


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


def _transcribe(tmp_path, provider, language="en"):
    request = TranscriptionRequest(audio_path=audio_file(tmp_path), language=language)
    return provider.transcribe(request)


@respx.mock
def test_request_shape(tmp_path):
    route = respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(200, json=_payload())
    )

    _transcribe(tmp_path, make_provider())

    call = route.calls.last.request
    assert call.headers["authorization"] == "Token dg-test-key"
    assert call.headers["content-type"] == "audio/wav"
    assert call.content == b"RIFF-fake-wav-bytes"
    query = dict(httpx.URL(str(call.url)).params)
    assert query["model"] == "nova-3"
    assert query["smart_format"] == "true"
    assert query["punctuate"] == "true"
    assert query["utterances"] == "true"
    assert query["language"] == "en"


@respx.mock
def test_language_param_omitted_when_not_requested(tmp_path):
    route = respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(200, json=_payload())
    )

    _transcribe(tmp_path, make_provider(), language=None)

    query = dict(httpx.URL(str(route.calls.last.request.url)).params)
    assert "language" not in query


@respx.mock
def test_normalization_prefers_punctuated_words_and_utterance_segments(tmp_path):
    respx.post(LISTEN_URL).mock(return_value=httpx.Response(200, json=_payload()))

    transcript = _transcribe(tmp_path, make_provider())

    assert transcript.text == "Hello world. This is a test."
    assert transcript.language == "en"
    assert transcript.model == "nova-3"
    assert transcript.model_version == "2024-01-09.29447"
    assert transcript.audio_duration_s == 2.4
    assert transcript.words_available is True

    # Segments come from utterances (speaker-labelled).
    assert [s.speaker for s in transcript.segments] == [0, 1]
    assert transcript.segments[0].text == "Hello world."
    assert transcript.segments[0].start == 0.1
    assert transcript.segments[0].end == 0.8

    # punctuated_word wins over word; confidence and speaker carried.
    first = transcript.segments[0].words[0]
    assert first.text == "Hello"
    assert first.confidence == 0.99
    assert first.speaker == 0

    # The degenerate word (end == start) is dropped, never clamped.
    assert [w.text for w in transcript.segments[1].words] == [
        "This",
        "a",
        "test.",
    ]
    assert any("degenerate" in warning for warning in transcript.warnings)


@respx.mock
def test_segments_fall_back_to_whole_transcript_without_utterances(tmp_path):
    payload = _payload()
    del payload["results"]["utterances"]
    respx.post(LISTEN_URL).mock(return_value=httpx.Response(200, json=payload))

    transcript = _transcribe(tmp_path, make_provider())

    assert len(transcript.segments) == 1
    segment = transcript.segments[0]
    assert segment.text == "Hello world. This is a test."
    assert segment.start == 0.1
    assert segment.end == 1.7
    assert segment.words  # words still attached


@respx.mock
def test_rate_limit_is_retryable(tmp_path):
    respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(429, text="rate limited")
    )
    with pytest.raises(ProviderError) as err:
        _transcribe(tmp_path, make_provider())
    assert err.value.retryable is True


@respx.mock
def test_unexpected_payload_is_permanent_malformed(tmp_path):
    respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(200, json={"unexpected": True})
    )
    with pytest.raises(ProviderError) as err:
        _transcribe(tmp_path, make_provider())
    assert err.value.code == "MALFORMED_RESPONSE"
    assert err.value.retryable is False


def test_capabilities_are_documented_not_verified():
    caps = make_provider().capabilities()
    assert caps.word_timestamps is True
    assert caps.diarization is True
    assert caps.async_processing is False
    assert caps.confidence_scores is True
    for capability in ("word_timestamps", "diarization", "confidence_scores"):
        assert caps.capability_sources[capability] == "documented"


def test_health_without_key_is_unavailable():
    provider = DeepgramProvider(settings=Settings(deepgram_api_key=None))
    assert provider.health().status == "unavailable"
