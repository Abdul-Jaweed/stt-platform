"""Live provider verification — opt-in, key-gated, and *paid*.

Ordinary gates never run these (``addopts`` deselects ``live_provider``);
run them explicitly when real keys are available in ``backend/.env``::

    uv run pytest -m live_provider -q            # all configured providers
    uv run pytest -m live_provider -q -k deepgram

Without a key each test skips. **Free-tier discipline**: one transcription
per provider of ``audio/sample.wav`` (10 s) — no retries on success, no
probing endpoints, speech-to-text only.

A passing run is the evidence that flips a capability from ``documented``
to ``verified`` in the adapter's ``capability_sources``; every run appends
a machine-readable record to ``results/live_providers/result.json``
(git-ignored, on-disk evidence) and prints a summary line.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from stt_platform.core.config import Settings
from stt_platform.providers.base import RemoteJobRef, TranscriptionRequest
from stt_platform.providers.managed.deepgram import DeepgramProvider
from stt_platform.providers.managed.elevenlabs import ElevenLabsProvider
from stt_platform.providers.managed.sarvam import SarvamProvider

pytestmark = pytest.mark.live_provider

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "audio" / "sample.wav"
EVIDENCE = ROOT / "results" / "live_providers" / "result.json"


def _settings(key_attr: str) -> Settings:
    settings = Settings()
    if not getattr(settings, key_attr):
        pytest.skip(f"{key_attr} not set: live verification is opt-in")
    return settings


def _record(provider: str, payload: dict) -> None:
    """Append this run to the on-disk evidence file (never committed)."""
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.loads(EVIDENCE.read_text(encoding="utf-8"))
        if EVIDENCE.exists()
        else {"runs": []}
    )
    data["runs"].append({"provider": provider, **payload})
    EVIDENCE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _audio_facts() -> dict:
    raw = SAMPLE.read_bytes()
    return {
        "path": str(SAMPLE),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _word_rows(transcript, limit: int = 5) -> list[dict]:
    rows = []
    for segment in transcript.segments:
        for word in segment.words:
            rows.append(
                {
                    "text": word.text,
                    "start": word.start,
                    "end": word.end,
                    "confidence": word.confidence,
                    "speaker": word.speaker,
                }
            )
    return rows[:limit]


def _common_evidence(provider, transcript, latency_s: float) -> dict:
    return {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "audio": _audio_facts(),
        "model": transcript.model,
        "model_version": transcript.model_version,
        "latency_s": round(latency_s, 2),
        "processing_duration_s": transcript.processing_duration_s,
        "audio_duration_s": transcript.audio_duration_s,
        "language": transcript.language,
        "segments": len(transcript.segments),
        "words": sum(len(s.words) for s in transcript.segments),
        "words_available": transcript.words_available,
        "speakers": sorted(
            {s.speaker for s in transcript.segments if s.speaker is not None}
        ),
        "warnings": list(transcript.warnings),
        "text": transcript.text,
        "first_words": _word_rows(transcript),
        "capability_sources": provider.capabilities().capability_sources,
        "outcome": "passed",
    }


def test_deepgram_live_word_timestamps():
    """Proves: word timestamps, utterance segments, word confidence,
    diarization, detected language, vendor-reported duration."""
    settings = _settings("deepgram_api_key")
    provider = DeepgramProvider(settings=settings)

    started = time.perf_counter()
    transcript = provider.transcribe(
        TranscriptionRequest(audio_path=SAMPLE, language=None)
    )
    latency = time.perf_counter() - started

    words = [w for s in transcript.segments for w in s.words]
    _record("deepgram", _common_evidence(provider, transcript, latency))
    assert words, "Deepgram returned no word timings"
    assert all(w.end > w.start for w in words)
    assert transcript.segments, "no utterance segments"
    assert any(w.confidence is not None for w in words), "no word confidence"
    assert any(s.speaker is not None for s in transcript.segments), (
        "no speaker labels — diarization was requested (diarize=true)"
    )
    assert transcript.language, "no detected language"
    assert transcript.audio_duration_s > 0

    print(
        f"[live] deepgram ok: {len(transcript.segments)} segments, "
        f"{len(words)} words, lang={transcript.language}, "
        f"dur={transcript.audio_duration_s}s, {latency:.2f}s"
    )


def test_elevenlabs_live_word_timestamps():
    """Proves: word timestamps, derived segments, absent word confidence
    (never fabricated), language code."""
    settings = _settings("elevenlabs_api_key")
    provider = ElevenLabsProvider(settings=settings)

    started = time.perf_counter()
    transcript = provider.transcribe(
        TranscriptionRequest(audio_path=SAMPLE, language=None)
    )
    latency = time.perf_counter() - started

    words = [w for s in transcript.segments for w in s.words]
    _record("elevenlabs", _common_evidence(provider, transcript, latency))
    assert words, "Scribe returned no word timings"
    assert all(w.end > w.start for w in words)
    assert transcript.segments, "no segments derived from word runs"
    assert all(w.confidence is None for w in words), (
        "Scribe reports no per-word confidence: it must stay None"
    )
    assert transcript.language, "no language code"
    assert transcript.audio_duration_s > 0

    print(
        f"[live] elevenlabs ok: {len(transcript.segments)} segments, "
        f"{len(words)} words, lang={transcript.language}, "
        f"dur={transcript.audio_duration_s}s, {latency:.2f}s"
    )


def test_sarvam_live_batch_job():
    """Proves: the asynchronous batch flow (submit → poll → fetch),
    segment timestamps, diarized speakers, and the absence of word
    timestamps and of a vendor cancel endpoint."""
    settings = _settings("sarvam_api_key")
    provider = SarvamProvider(settings=settings)

    started = time.perf_counter()
    ref = provider.submit(TranscriptionRequest(audio_path=SAMPLE, language=None))
    assert isinstance(ref, RemoteJobRef) and ref.provider_job_id

    deadline = time.time() + settings.remote_max_wait_s
    polls = 0
    state = provider.poll(ref)
    polls += 1
    while state.state == "pending" and time.time() < deadline:
        time.sleep(settings.remote_poll_interval_s)
        state = provider.poll(ref)
        polls += 1

    latency = time.perf_counter() - started
    assert state.state == "completed", (
        f"Sarvam job ended {state.state}: {state.error_message}"
    )
    transcript = state.transcript
    assert transcript is not None

    evidence = _common_evidence(provider, transcript, latency)
    evidence["polls"] = polls
    evidence["provider_job_id"] = ref.provider_job_id
    _record("sarvam", evidence)

    assert transcript.segments, "Sarvam returned no segments"
    assert transcript.words_available is False, (
        "Sarvam batch has no word timings: words_available must be False"
    )
    assert all(segment.end > segment.start for segment in transcript.segments)
    assert any(s.speaker is not None for s in transcript.segments), (
        "no speakers — diarization was requested (with_diarization=true)"
    )
    assert transcript.language, "no language code"
    assert transcript.audio_duration_s > 0
    assert provider.cancel(ref) is False, (
        "Sarvam documents no cancel endpoint: cancel must report False "
        "instead of inventing one"
    )

    print(
        f"[live] sarvam ok: {len(transcript.segments)} segments, "
        f"lang={transcript.language}, dur={transcript.audio_duration_s}s, "
        f"{polls} polls, {latency:.2f}s"
    )


def test_evidence_file_is_outside_git():
    """The evidence file must never be committable (it carries transcript
    text; the repo holds no audio and no transcripts)."""
    check = subprocess.run(
        ["git", "check-ignore", str(EVIDENCE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, f"{EVIDENCE} is not git-ignored"
    assert os.path.commonpath([str(EVIDENCE), str(ROOT)]) == str(ROOT)
