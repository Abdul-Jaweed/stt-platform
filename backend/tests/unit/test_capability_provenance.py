"""Capability provenance is pinned, not vibes.

Every entry in a provider's ``capability_sources`` is either ``documented``
(vendor docs only) or ``verified`` (our own live run). The ``verified``
claims below are backed by the on-disk, git-ignored evidence of
2026-10-11 in ``results/live_providers/result.json`` (Deepgram 1 call,
ElevenLabs 1 call, Sarvam 1 batch job); flipping one back or forward is a
deliberate edit to this test, never a side effect.
"""
from stt_platform.core.config import Settings
from stt_platform.providers.managed.deepgram import DeepgramProvider
from stt_platform.providers.managed.elevenlabs import ElevenLabsProvider
from stt_platform.providers.managed.sarvam import SarvamProvider

ALLOWED = {"documented", "verified"}


def _sources(provider) -> dict:
    sources = provider.capabilities().capability_sources
    assert sources, "a provider must state where its capabilities come from"
    assert set(sources.values()) <= ALLOWED, sources
    return sources


def test_deepgram_provenance():
    provider = DeepgramProvider(settings=Settings())
    assert _sources(provider) == {
        "word_timestamps": "verified",
        "segment_timestamps": "verified",
        "diarization": "verified",
        "confidence_scores": "verified",
        "language_detection": "verified",
    }


def test_elevenlabs_provenance():
    provider = ElevenLabsProvider(settings=Settings())
    assert _sources(provider) == {
        "word_timestamps": "verified",
        "language_detection": "verified",
        "diarization": "documented",
    }


def test_sarvam_provenance():
    provider = SarvamProvider(settings=Settings())
    assert _sources(provider) == {
        "async_processing": "verified",
        "segment_timestamps": "verified",
        "diarization": "verified",
        "language_detection": "verified",
    }


def test_unverified_capabilities_are_not_claimed():
    """Absence claims stay out of ``capability_sources`` entirely: a
    capability we advertise as *unsupported* (word timings on Sarvam, word
    confidence on Scribe) must not carry a provenance entry that implies we
    proved the opposite."""
    sarvam = SarvamProvider(settings=Settings()).capabilities()
    assert sarvam.word_timestamps is False
    assert "word_timestamps" not in sarvam.capability_sources

    eleven = ElevenLabsProvider(settings=Settings()).capabilities()
    assert eleven.confidence_scores is False
    assert "confidence_scores" not in eleven.capability_sources
