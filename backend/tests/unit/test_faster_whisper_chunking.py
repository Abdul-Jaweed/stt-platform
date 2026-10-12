"""Chunked local transcription of long audio (fake engine, no GPU).

Long audio used to be a single pass bounded by whatever the deployment's
duration caps allowed.  The local provider now chunks it itself: the file is
probed (ffprobe duration), decoded once, sliced at *exact* 16 kHz sample
offsets, each slice is transcribed by the engine, and every slice's
transcript is shifted back onto the original audio timeline using
``domain.timeline.shift_segments`` -- timestamps are never re-derived from
scratch, and a slice's text is never re-timed or invented.

Chunking is opt-in: ``STT_CHUNK_SECONDS=0`` (the library default) keeps the
single-pass path byte-for-byte; the shipped compose stack enables it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import TranscriptionRequest
from stt_platform.providers.faster_whisper import FasterWhisperProvider

SR = 16000


class FakeWord:
    def __init__(self, start, end, word, probability=None):
        self.start, self.end, self.word, self.probability = (
            start,
            end,
            word,
            probability,
        )


class FakeSegment:
    def __init__(self, start, end, text, words):
        self.start, self.end, self.text, self.words = start, end, text, words


class FakeInfo:
    def __init__(self, language="en", language_probability=0.98, duration=0.0):
        self.language = language
        self.language_probability = language_probability
        self.duration = duration


class ScriptedEngine:
    """Returns a scripted (segments, info) -- or raises -- per call."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.audio: list[object] = []
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):
        self.audio.append(audio)
        self.calls.append(kwargs)
        idx = len(self.audio) - 1
        if idx >= len(self.scripts):
            raise RuntimeError("engine called more times than the script provides")
        raw = self.scripts[idx]
        if isinstance(raw, BaseException):
            raise raw
        segments, info = raw
        return iter(segments), info


def seg(start, end, text, words=()):
    return FakeSegment(start, end, text, list(words))


def word(start, end, text, probability=0.9):
    return FakeWord(start, end, text, probability)


def provider_for(engine, decode, probe, *, chunk_seconds):
    settings = Settings(chunk_seconds=chunk_seconds, whisper_model="small")
    return FasterWhisperProvider(
        settings=settings,
        engine_factory=lambda *a, **k: engine,
        audio_decoder=decode,
        duration_probe=probe,
    )


def request() -> TranscriptionRequest:
    return TranscriptionRequest(audio_path=Path("long.wav"), language=None)


def test_audio_at_or_below_the_chunk_size_stays_a_single_pass_on_the_path():
    samples = np.zeros(100 * SR, dtype=np.float32)
    engine = ScriptedEngine([([seg(1.0, 2.0, "hello")], FakeInfo(duration=100.0))])
    decode_calls: list[object] = []
    provider = provider_for(
        engine,
        decode=lambda path: (decode_calls.append(path), samples)[1],
        probe=lambda path: 100.0,
        chunk_seconds=600.0,
    )

    transcript = provider.transcribe(request())

    assert len(engine.audio) == 1
    assert isinstance(engine.audio[0], str), "short audio keeps the path fast-path"
    assert decode_calls == [], "short audio is never decoded by the chunker"
    assert [(s.start, s.end, s.text) for s in transcript.segments] == [
        (1.0, 2.0, "hello")
    ]
    assert transcript.audio_duration_s == 100.0


def test_long_audio_is_chunked_on_exact_sample_offsets_and_shifted():
    total = 1500 * SR
    samples = np.arange(total, dtype=np.float32)
    engine = ScriptedEngine(
        [
            ([seg(1.0, 2.0, "one")], FakeInfo()),
            ([seg(0.5, 1.5, "two", [word(0.5, 0.9, "two")])], FakeInfo()),
            ([seg(2.0, 3.0, "three")], FakeInfo()),
        ]
    )
    decode_calls: list[object] = []
    provider = provider_for(
        engine,
        decode=lambda path: (decode_calls.append(path), samples)[1],
        probe=lambda path: 1500.0,
        chunk_seconds=600.0,
    )

    transcript = provider.transcribe(request())

    assert decode_calls == [Path("long.wav")], "the file is decoded exactly once"
    assert [len(a) for a in engine.audio] == [600 * SR, 600 * SR, 300 * SR]
    assert np.array_equal(engine.audio[0], samples[: 600 * SR])
    assert np.array_equal(engine.audio[1], samples[600 * SR : 1200 * SR])
    assert np.array_equal(engine.audio[2], samples[1200 * SR :])
    assert [(s.start, s.end, s.text) for s in transcript.segments] == [
        (1.0, 2.0, "one"),
        (600.5, 601.5, "two"),
        (1202.0, 1203.0, "three"),
    ]
    assert (transcript.segments[1].words[0].start, transcript.segments[1].words[0].end) == (
        600.5,
        600.9,
    )
    assert transcript.text == "one two three"
    assert transcript.audio_duration_s == 1500.0
    assert transcript.words_available is True
    assert transcript.model == "small"


def test_chunk_size_dividing_exactly_creates_no_empty_tail_call():
    samples = np.zeros(1200 * SR, dtype=np.float32)
    engine = ScriptedEngine(
        [
            ([seg(1.0, 2.0, "a")], FakeInfo()),
            ([seg(1.0, 2.0, "b")], FakeInfo()),
        ]
    )
    provider = provider_for(
        engine,
        decode=lambda path: samples,
        probe=lambda path: 1200.0,
        chunk_seconds=600.0,
    )

    transcript = provider.transcribe(request())

    assert [len(a) for a in engine.audio] == [600 * SR, 600 * SR]
    assert [s.text for s in transcript.segments] == ["a", "b"]


def test_chunk_seconds_zero_disables_chunking_entirely():
    samples = np.zeros(1500 * SR, dtype=np.float32)
    engine = ScriptedEngine([([seg(1.0, 2.0, "whole")], FakeInfo(duration=1500.0))])
    decode_calls: list[object] = []
    provider = provider_for(
        engine,
        decode=lambda path: (decode_calls.append(path), samples)[1],
        probe=lambda path: pytest.fail("probe must not run when chunking is off"),
        chunk_seconds=0.0,
    )

    transcript = provider.transcribe(request())

    assert len(engine.audio) == 1 and isinstance(engine.audio[0], str)
    assert decode_calls == []
    assert transcript.text == "whole"


def test_degenerate_words_are_dropped_per_chunk_and_segments_are_kept():
    samples = np.zeros(700 * SR, dtype=np.float32)
    engine = ScriptedEngine(
        [
            ([seg(1.0, 2.0, "one", [word(1.0, 1.0, "zero-length")])], FakeInfo()),
            ([seg(0.5, 1.0, "two", [word(0.5, 0.8, "two")])], FakeInfo()),
        ]
    )
    provider = provider_for(
        engine,
        decode=lambda path: samples,
        probe=lambda path: 700.0,
        chunk_seconds=600.0,
    )

    transcript = provider.transcribe(request())

    assert transcript.segments[0].words == [], "zero-length word dropped, segment kept"
    assert transcript.segments[0].text == "one"
    assert (transcript.segments[1].start, transcript.segments[1].end) == (600.5, 601.0)


def test_every_chunk_is_transcribed_with_the_same_engine_parameters():
    samples = np.zeros(700 * SR, dtype=np.float32)
    engine = ScriptedEngine(
        [
            ([seg(1.0, 2.0, "one")], FakeInfo()),
            ([seg(1.0, 2.0, "two")], FakeInfo()),
        ]
    )
    provider = provider_for(
        engine,
        decode=lambda path: samples,
        probe=lambda path: 700.0,
        chunk_seconds=600.0,
    )

    provider.transcribe(TranscriptionRequest(audio_path=Path("x.wav"), language="hi"))

    for call in engine.calls:
        assert call["vad_filter"] is True
        assert call["word_timestamps"] is True
        assert call["condition_on_previous_text"] is False
        assert call["beam_size"] == 5
        assert call["language"] == "hi"


def test_language_and_probability_come_from_the_first_chunk():
    samples = np.zeros(700 * SR, dtype=np.float32)
    engine = ScriptedEngine(
        [
            ([seg(1.0, 2.0, "one")], FakeInfo(language="en", language_probability=0.98)),
            ([seg(1.0, 2.0, "two")], FakeInfo(language="hi", language_probability=0.40)),
        ]
    )
    provider = provider_for(
        engine,
        decode=lambda path: samples,
        probe=lambda path: 700.0,
        chunk_seconds=600.0,
    )

    transcript = provider.transcribe(request())

    assert transcript.language == "en"
    assert transcript.language_probability == 0.98


def test_a_failing_chunk_fails_the_whole_transcription_loudly():
    samples = np.zeros(1300 * SR, dtype=np.float32)
    engine = ScriptedEngine(
        [
            ([seg(1.0, 2.0, "one")], FakeInfo()),
            RuntimeError("boom"),
        ]
    )
    provider = provider_for(
        engine,
        decode=lambda path: samples,
        probe=lambda path: 1300.0,
        chunk_seconds=600.0,
    )

    with pytest.raises(ProviderError) as excinfo:
        provider.transcribe(request())

    assert excinfo.value.code == "INFERENCE_FAILED"
    assert excinfo.value.retryable is True


def test_an_undecodable_chunk_is_a_non_retryable_decode_failure():
    samples = np.zeros(700 * SR, dtype=np.float32)
    engine = ScriptedEngine(
        [
            ([seg(1.0, 2.0, "one")], FakeInfo()),
            ValueError("bad audio"),
        ]
    )
    provider = provider_for(
        engine,
        decode=lambda path: samples,
        probe=lambda path: 700.0,
        chunk_seconds=600.0,
    )

    with pytest.raises(ProviderError) as excinfo:
        provider.transcribe(request())

    assert excinfo.value.code == "DECODE_FAILED"
    assert excinfo.value.retryable is False
