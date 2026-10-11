"""Live provider verification — opt-in, key-gated, and *paid*.

Ordinary gates never run these (``addopts`` deselects ``live_provider``);
run them explicitly when a real key is available::

    STT_DEEPGRAM_API_KEY=... uv run pytest -m live_provider -q

Without a key each test skips. A passing run is the only evidence that may
flip a capability from ``documented`` to ``verified`` — this file asserts
the capability is still ``documented`` in code, so the flip stays a
deliberate, documented act rather than a silent claim.
"""
import os
from pathlib import Path

import pytest

from stt_platform.core.config import Settings
from stt_platform.providers.base import RemoteJobRef, TranscriptionRequest
from stt_platform.providers.managed.deepgram import DeepgramProvider
from stt_platform.providers.managed.elevenlabs import ElevenLabsProvider
from stt_platform.providers.managed.sarvam import SarvamProvider

pytestmark = pytest.mark.live_provider

SAMPLE = Path(__file__).resolve().parents[3] / "audio" / "sample.wav"


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        pytest.skip(f"{key} not set: live verification is opt-in")
    return value


def _assert_documented(provider, capability: str) -> None:
    caps = provider.capabilities()
    assert caps.capability_sources.get(capability) == "documented", (
        f"{provider.id}:{capability} is not marked documented — update docs "
        "with the evidence instead of changing this assertion"
    )


def test_deepgram_live_word_timestamps():
    key = _require("STT_DEEPGRAM_API_KEY")
    provider = DeepgramProvider(settings=Settings(deepgram_api_key=key))
    _assert_documented(provider, "word_timestamps")

    transcript = provider.transcribe(
        TranscriptionRequest(audio_path=SAMPLE, language=None)
    )

    words = [w for segment in transcript.segments for w in segment.words]
    assert words, "Deepgram returned no word timings"
    assert all(w.end > w.start for w in words)
    assert transcript.audio_duration_s > 0
    assert transcript.text.strip()


def test_elevenlabs_live_word_timestamps():
    key = _require("STT_ELEVENLABS_API_KEY")
    provider = ElevenLabsProvider(settings=Settings(elevenlabs_api_key=key))
    _assert_documented(provider, "word_timestamps")

    transcript = provider.transcribe(
        TranscriptionRequest(audio_path=SAMPLE, language=None)
    )

    words = [w for segment in transcript.segments for w in segment.words]
    assert words, "Scribe returned no word timings"
    assert all(w.end > w.start for w in words)
    assert all(w.confidence is None for w in words)  # never fabricated
    assert transcript.text.strip()


def test_sarvam_live_batch_job():
    import time

    key = _require("STT_SARVAM_API_KEY")
    settings = Settings(
        sarvam_api_key=key,
        remote_poll_interval_s=5.0,
        remote_max_wait_s=900.0,
    )
    provider = SarvamProvider(settings=settings)
    _assert_documented(provider, "async_processing")

    ref = provider.submit(
        TranscriptionRequest(audio_path=SAMPLE, language=None)
    )
    assert isinstance(ref, RemoteJobRef) and ref.provider_job_id

    deadline = time.time() + settings.remote_max_wait_s
    state = provider.poll(ref)
    while state.state == "pending" and time.time() < deadline:
        time.sleep(settings.remote_poll_interval_s)
        state = provider.poll(ref)

    assert state.state == "completed", state.error_message
    assert state.transcript is not None
    assert state.transcript.segments, "Sarvam returned no segments"
    assert state.transcript.words_available is False  # batch: segment only
