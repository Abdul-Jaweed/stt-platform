"""T5 - transcribing one in-memory PCM window (fake engine, no GPU).

The streaming path hands the engine 16 kHz mono float samples it decoded
itself, so the adapter must not re-open the audio through ffmpeg and must not
run its own VAD (the session's segmenter already decided where the utterance
ends). Everything else -- canonical mapping, error translation, GPU
serialization -- has to behave exactly like the batch path, because a
streamed window and an uploaded file are the same audio to the model.
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.faster_whisper import FasterWhisperProvider

SR = 16000


class FakeWord:
    def __init__(self, start, end, word, probability=None):
        self.start, self.end, self.word, self.probability = start, end, word, probability


class FakeSegment:
    def __init__(self, start, end, text, words):
        self.start, self.end, self.text, self.words = start, end, text, words


class FakeInfo:
    language = "en"
    language_probability = 0.98
    duration = 1.0


class FakeEngine:
    def __init__(self, *, raises=None, segments=None, info=None):
        self.raises = raises
        self.segments = segments
        self.info = info or FakeInfo()
        self.audio: list[object] = []
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):
        self.audio.append(audio)
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        segments = self.segments
        if segments is None:
            segments = [
                FakeSegment(
                    0.0,
                    1.0,
                    " hello world",
                    [FakeWord(0.0, 0.5, " hello", 0.9), FakeWord(0.5, 1.0, " world", 0.8)],
                )
            ]
        return iter(segments), self.info


def make_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
    )


def make_adapter(engine, settings, semaphore=None) -> FasterWhisperProvider:
    return FasterWhisperProvider(
        settings,
        engine_factory=lambda *a, **k: engine,
        semaphore=semaphore,
    )


def test_a_pcm_window_reaches_the_engine_as_samples(tmp_path):
    engine = FakeEngine()
    provider = make_adapter(engine, make_settings(tmp_path))
    window = np.zeros(SR, dtype=np.float32)

    provider.transcribe_pcm(window, "en")

    assert len(engine.audio) == 1
    audio = engine.audio[0]
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert audio.size == SR
    kwargs = engine.calls[0]
    assert kwargs["language"] == "en"
    assert kwargs["word_timestamps"] is True
    # The session VAD already placed this window: a second VAD would re-cut it
    # and silently drop speech the segmenter deliberately kept.
    assert kwargs["vad_filter"] is False


def test_pcm_results_map_to_the_canonical_transcript(tmp_path):
    engine = FakeEngine(
        segments=[
            FakeSegment(
                0.1,
                0.9,
                " hello world",
                [
                    FakeWord(0.1, 0.4, " hello", 0.9),
                    FakeWord(0.4, 0.4, "  ", 0.5),  # degenerate: dropped, never clamped
                    FakeWord(0.4, 0.9, " world", 0.8),
                ],
            )
        ]
    )
    provider = make_adapter(engine, make_settings(tmp_path))

    transcript = provider.transcribe_pcm(np.zeros(SR, dtype=np.float32))

    assert transcript.text == "hello world"
    assert transcript.model == provider._settings.whisper_model
    assert transcript.words_available is True
    assert transcript.audio_duration_s == 1.0
    [segment] = transcript.segments
    assert (segment.start, segment.end) == (0.1, 0.9)
    assert [w.text for w in segment.words] == ["hello", "world"]


def test_engine_failure_maps_to_a_retryable_provider_error(tmp_path):
    engine = FakeEngine(raises=RuntimeError("cuda went away"))
    provider = make_adapter(engine, make_settings(tmp_path))

    with pytest.raises(ProviderError) as caught:
        provider.transcribe_pcm(np.zeros(SR, dtype=np.float32))

    assert caught.value.code == "INFERENCE_FAILED"
    assert caught.value.retryable is True


def test_pcm_inference_waits_for_the_gpu(tmp_path):
    """A stream must never run inference beside a batch job on the same GPU."""
    engine = FakeEngine()
    semaphore = threading.BoundedSemaphore(1)
    provider = make_adapter(engine, make_settings(tmp_path), semaphore=semaphore)
    semaphore.acquire()  # a batch transcription is on the GPU right now

    finished = threading.Event()

    def stream_one_window() -> None:
        provider.transcribe_pcm(np.zeros(SR, dtype=np.float32))
        finished.set()

    worker = threading.Thread(target=stream_one_window, daemon=True)
    worker.start()
    try:
        assert not finished.wait(0.5), "streamed inference ran while the GPU was busy"
    finally:
        semaphore.release()
    assert finished.wait(10.0)
    worker.join(timeout=5)
    assert engine.audio, "the window never reached the engine"
