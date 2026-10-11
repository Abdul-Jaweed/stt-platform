"""Unit tests for the Faster-Whisper adapter (fake engine, no GPU)."""
from pathlib import Path

import pytest

from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import TranscriptionRequest
from stt_platform.providers.faster_whisper import FasterWhisperProvider


class FakeWord:
    def __init__(self, start, end, word, probability=None):
        self.start, self.end, self.word, self.probability = start, end, word, probability


class FakeSegment:
    def __init__(self, start, end, text, words):
        self.start, self.end, self.text, self.words = start, end, text, words


class FakeInfo:
    language = "en"
    language_probability = 0.987
    duration = 2.0


class FakeEngine:
    def __init__(self, *, raises=None, calls=None, words_present=True, words=None):
        self.raises = raises
        self.calls = calls if calls is not None else []
        self.words_present = words_present
        self.words = words

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        if self.words is not None:
            words_a = self.words
        elif self.words_present:
            words_a = [
                FakeWord(0.0, 0.5, " hello", 0.9),
                FakeWord(0.5, 1.0, " world", 0.8),
            ]
        else:
            words_a = []
        return iter(
            [
                FakeSegment(0.0, 1.0, " hello world", words_a),
                FakeSegment(1.0, 1.5, " again", []),
            ]
        ), FakeInfo()


def make_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
    )


def make_adapter(engine, settings, counter=None) -> FasterWhisperProvider:
    def factory(model, **kwargs):
        if counter is not None:
            counter.append((model, kwargs))
        return engine

    return FasterWhisperProvider(settings, engine_factory=factory)


def test_maps_segments_and_words_to_canonical(tmp_path):
    calls = []
    engine = FakeEngine(calls=calls)
    adapter = make_adapter(engine, make_settings(tmp_path))
    tr = adapter.transcribe(
        TranscriptionRequest(audio_path=Path("a.wav"), language="en")
    )
    assert tr.text == "hello world again"
    assert tr.language == "en"
    assert tr.words_available is True
    assert tr.segments[0].words[0].text == "hello"
    assert tr.segments[0].words[0].confidence == pytest.approx(0.9)
    assert tr.model == "large-v3-turbo"
    assert tr.audio_duration_s == 2.0
    assert calls[0]["word_timestamps"] is True
    assert calls[0]["language"] == "en"


def test_engine_constructed_once_with_canonical_cache(tmp_path):
    counter = []
    adapter = make_adapter(FakeEngine(), make_settings(tmp_path), counter=counter)
    req = TranscriptionRequest(audio_path=Path("a.wav"))
    adapter.transcribe(req)
    adapter.transcribe(req)
    assert len(counter) == 1
    model_name, kwargs = counter[0]
    assert model_name == "large-v3-turbo"
    assert Path(kwargs["download_root"]) == tmp_path / "models" / "faster-whisper"


def test_decode_failure_is_permanent(tmp_path):
    engine = FakeEngine(raises=ValueError("Failed to decode audio"))
    adapter = make_adapter(engine, make_settings(tmp_path))
    with pytest.raises(ProviderError) as excinfo:
        adapter.transcribe(TranscriptionRequest(audio_path=Path("bad.wav")))
    assert excinfo.value.retryable is False
    assert excinfo.value.code == "DECODE_FAILED"


def test_inference_crash_is_transient(tmp_path):
    engine = FakeEngine(raises=RuntimeError("boom"))
    adapter = make_adapter(engine, make_settings(tmp_path))
    with pytest.raises(ProviderError) as excinfo:
        adapter.transcribe(TranscriptionRequest(audio_path=Path("a.wav")))
    assert excinfo.value.retryable is True


def test_words_available_false_for_segment_only_output(tmp_path):
    adapter = make_adapter(
        FakeEngine(words_present=False), make_settings(tmp_path)
    )
    tr = adapter.transcribe(TranscriptionRequest(audio_path=Path("a.wav")))
    assert tr.words_available is False
    assert all(not s.words for s in tr.segments)


def test_degenerate_word_timestamps_are_dropped_not_fabricated(tmp_path):
    # Whisper occasionally emits zero-length or inverted words (seen on a
    # real 200 s run). The canonical schema requires end > start, so the
    # unsupported timestamps are dropped -- never clamped or invented.
    engine = FakeEngine(
        words=[
            FakeWord(0.0, 0.5, " hello", 0.9),
            FakeWord(0.5, 0.5, " uh", 0.4),      # zero length
            FakeWord(0.6, 0.55, " hmm", 0.3),    # inverted
            FakeWord(1.0001, 1.0002, " tiny", None),  # rounds to zero length
            FakeWord(1.2, 1.4, " world", 0.8),
        ]
    )
    adapter = make_adapter(engine, make_settings(tmp_path))

    tr = adapter.transcribe(TranscriptionRequest(audio_path=Path("a.wav")))

    kept = [w.text for w in tr.segments[0].words]
    assert kept == ["hello", "world"]
    assert tr.words_available is True
    # The segment text itself is untouched: dropping a word's timestamp
    # must not rewrite the transcript.
    assert tr.segments[0].text == "hello world"


def test_capabilities_are_local_and_document_verification(tmp_path):
    adapter = make_adapter(FakeEngine(), make_settings(tmp_path))
    caps = adapter.capabilities()
    assert caps.word_timestamps is True
    assert caps.segment_timestamps is True
    assert caps.async_processing is False
    assert caps.streaming is False
    assert caps.diarization is False
    assert "en" in caps.languages and "hi" in caps.languages
    assert len(caps.languages) >= 95
    assert caps.capability_sources.get("word_timestamps") == "verified"


def test_health_reports_status(tmp_path):
    adapter = make_adapter(FakeEngine(), make_settings(tmp_path))
    assert adapter.health().status in {"ok", "degraded", "unavailable"}
