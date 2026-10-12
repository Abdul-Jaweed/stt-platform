"""Capability provenance is pinned, not vibes.

Every entry in a provider's ``capability_sources`` is either ``documented``
(vendor docs only) or ``verified`` (our own live run). The ``verified``
claims below are backed by the on-disk, git-ignored evidence of
2026-10-11 in ``results/live_providers/result.json`` (Deepgram 1 call,
ElevenLabs 1 call, Sarvam 1 batch job) plus the 2026-10-12 ElevenLabs call
that exercised ``diarize=true``; flipping one back or forward is a
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
        "max_file_bytes": "documented",
    }


def test_elevenlabs_provenance():
    provider = ElevenLabsProvider(settings=Settings())
    assert _sources(provider) == {
        "word_timestamps": "verified",
        "language_detection": "verified",
        "diarization": "verified",
        "max_file_bytes": "documented",
        "max_duration_s": "documented",
    }


def test_sarvam_provenance():
    provider = SarvamProvider(settings=Settings())
    assert _sources(provider) == {
        "async_processing": "verified",
        "segment_timestamps": "verified",
        "diarization": "verified",
        "language_detection": "verified",
        "max_duration_s": "documented",
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


def test_declared_limits_are_the_vendors_own_numbers():
    """Every populated limit is a number a vendor page states, and says so.

    Sources checked 2026-10-12, linked in ``docs/providers.md``:
    Deepgram pre-recorded ("File size: Maximum 2 GB"), ElevenLabs Speech to
    Text ("Maximum file size: 3 GB", "Maximum duration: 10 hours"), Sarvam
    Batch API ("up to 2 hours" per file).
    """
    deepgram = DeepgramProvider(settings=Settings()).capabilities()
    assert deepgram.max_file_bytes == 2 * 1024**3
    # Pre-recorded audio documents no duration limit, so none is claimed.
    assert deepgram.max_duration_s is None
    assert deepgram.capability_sources["max_file_bytes"] == "documented"

    eleven = ElevenLabsProvider(settings=Settings()).capabilities()
    assert eleven.max_file_bytes == 3 * 1024**3
    assert eleven.max_duration_s == 10 * 3600.0
    assert eleven.capability_sources["max_duration_s"] == "documented"

    sarvam = SarvamProvider(settings=Settings()).capabilities()
    assert sarvam.max_duration_s == 2 * 3600.0
    # No file-size limit is documented for the batch API: never invented.
    assert sarvam.max_file_bytes is None
    assert sarvam.capability_sources["max_duration_s"] == "documented"
