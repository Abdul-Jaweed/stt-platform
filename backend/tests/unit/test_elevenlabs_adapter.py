"""ElevenLabs Scribe adapter tests.

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
from stt_platform.providers.managed.elevenlabs import ElevenLabsProvider
from tests.audio_fixtures import make_wav

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "elevenlabs_stt.json"
STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def make_provider(**overrides) -> ElevenLabsProvider:
    settings = Settings(elevenlabs_api_key="el-test-key", **overrides)
    return ElevenLabsProvider(settings=settings)


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


def _transcribe(tmp_path, provider, language="en"):
    wav = make_wav(tmp_path / "sample.wav", seconds=1.0)
    request = TranscriptionRequest(audio_path=wav, language=language)
    return provider.transcribe(request)


@respx.mock
def test_multipart_request_shape(tmp_path):
    route = respx.post(STT_URL).mock(
        return_value=httpx.Response(200, json=_payload())
    )

    _transcribe(tmp_path, make_provider())

    call = route.calls.last.request
    assert call.headers["xi-api-key"] == "el-test-key"
    assert call.headers["content-type"].startswith("multipart/form-data")
    body = call.content
    assert b'name="model_id"' in body and b"scribe_v2" in body
    assert b'name="timestamps_granularity"' in body and b"word" in body
    # Diarization is requested explicitly — the advertised capability is a
    # real parameter, never an assumption about the vendor default.
    assert b'name="diarize"' in body and b"true" in body
    assert b'name="language_code"' in body and b"en" in body
    assert b'name="file"' in body and b"sample.wav" in body


@respx.mock
def test_language_code_omitted_when_not_requested(tmp_path):
    route = respx.post(STT_URL).mock(
        return_value=httpx.Response(200, json=_payload())
    )

    _transcribe(tmp_path, make_provider(), language=None)

    assert b'name="language_code"' not in route.calls.last.request.content


@respx.mock
def test_normalization_keeps_only_words_and_maps_speakers(tmp_path):
    respx.post(STT_URL).mock(return_value=httpx.Response(200, json=_payload()))

    transcript = _transcribe(tmp_path, make_provider())

    assert transcript.text == "Hello world. This is a test."
    assert transcript.language == "eng"  # vendor value kept verbatim
    assert transcript.language_probability == 0.98
    assert transcript.model == "scribe_v2"
    assert transcript.words_available is True
    assert abs(transcript.audio_duration_s - 1.0) < 0.2  # probed locally

    # spacing + audio_event tokens are not words and are dropped by design.
    texts = [w.text for s in transcript.segments for w in s.words]
    assert "(laughter)" not in texts
    assert " " not in texts
    assert texts == ["Hello", "world.", "This", "a", "test."]

    # Scribe returns no per-word confidence: it stays None, never invented.
    assert all(w.confidence is None for s in transcript.segments for w in s.words)

    # speaker_0 / speaker_1 map to ints; segments group by speaker runs.
    assert [s.speaker for s in transcript.segments] == [0, 1]
    assert transcript.segments[0].text == "Hello world."
    assert transcript.segments[1].text == "This a test."

    # Degenerate word dropped with a warning.
    assert any("degenerate" in warning for warning in transcript.warnings)


@respx.mock
def test_auth_error_is_permanent(tmp_path):
    respx.post(STT_URL).mock(return_value=httpx.Response(401, text="bad key"))
    with pytest.raises(ProviderError) as err:
        _transcribe(tmp_path, make_provider())
    assert err.value.code == "AUTH_ERROR"
    assert err.value.retryable is False


@respx.mock
def test_unexpected_payload_is_permanent_malformed(tmp_path):
    respx.post(STT_URL).mock(
        return_value=httpx.Response(200, json={"unexpected": True})
    )
    with pytest.raises(ProviderError) as err:
        _transcribe(tmp_path, make_provider())
    assert err.value.code == "MALFORMED_RESPONSE"
    assert err.value.retryable is False


def test_capabilities_never_claim_confidence():
    caps = make_provider().capabilities()
    assert caps.word_timestamps is True
    assert caps.diarization is True
    assert caps.confidence_scores is False
    assert caps.async_processing is False
    # Word timings + language code came back from the 2026-10-11 live run;
    # diarization is now requested (diarize=true) but has not been exercised
    # live yet, so it stays "documented".
    assert caps.capability_sources["word_timestamps"] == "verified"
    assert caps.capability_sources["language_detection"] == "verified"
    assert caps.capability_sources["diarization"] == "documented"


def test_health_without_key_is_unavailable():
    provider = ElevenLabsProvider(settings=Settings(elevenlabs_api_key=None))
    assert provider.health().status == "unavailable"
