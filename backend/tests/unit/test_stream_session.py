"""T2 - the streaming session core (RED first).

The session is where the voice-path rules live: one decode at a time, a
bounded partial cadence, session-absolute timestamps, a duration cap that
ends the session instead of surprising it, and nothing persisted anywhere.
It is deliberately synchronous and clock-injected: the endpoint (T4) owns
threads and I/O, this owns behaviour.
"""
from __future__ import annotations

import numpy as np
import pytest

from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import ProviderTranscript
from stt_platform.schemas.transcript import TranscriptSegment
from stt_platform.streaming.session import StreamConfig, StreamingSession
from stt_platform.streaming.vad import VadConfig

SR = 16000


def tone(seconds: float, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SR), dtype=np.float64) / SR
    return (amplitude * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTranscriber:
    """Answers with fixed geometry so offsets are checkable by hand."""

    provider_id = "faster-whisper"
    model_name = "fake-model"

    def __init__(self, text: str = "hello") -> None:
        self.text = text
        self.calls: list[tuple[int, str | None]] = []

    def transcribe_window(self, samples: np.ndarray, language: str | None) -> ProviderTranscript:
        self.calls.append((int(samples.size), language))
        return ProviderTranscript(
            text=self.text,
            language="en",
            segments=[
                TranscriptSegment(start=0.1, end=0.5, text=self.text),
            ],
            words_available=False,
            model=self.model_name,
            model_version=None,
            audio_duration_s=round(samples.size / SR, 3),
            processing_duration_s=0.01,
        )


def make(clock: FakeClock, transcriber: FakeTranscriber | None = None, **overrides):
    config = StreamConfig(
        vad=VadConfig(sample_rate=SR),
        partial_interval_s=1.0,
        **overrides,
    )
    return StreamingSession(
        transcriber=transcriber or FakeTranscriber(),
        config=config,
        session_id="sess-1",
        clock=clock,
    )


def types(events) -> list[str]:
    return [e.type for e in events]


# ----------------------------------------------------------------------
def test_ready_payload_describes_the_session():
    session = make(FakeClock())
    payload = session.ready_payload()
    assert payload["provider"] == "faster-whisper"
    assert payload["model"] == "fake-model"
    assert payload["session_id"] == "sess-1"
    assert payload["max_duration_s"] == 300.0
    assert payload["partial_interval_s"] == 1.0


def test_speech_then_silence_emits_one_final_and_decodes_the_window_once():
    clock = FakeClock()
    transcriber = FakeTranscriber()
    session = make(clock, transcriber)

    assert session.on_audio(tone(1.0)) == []
    events = session.on_audio(silence(0.8))

    assert types(events) == ["final"]
    final = events[0]
    assert final.payload["text"] == "hello"
    [segment] = final.payload["segments"]
    assert segment["start"] == pytest.approx(0.1, abs=0.05)
    assert segment["end"] == pytest.approx(0.5, abs=0.05)

    assert len(transcriber.calls) == 1
    samples, language = transcriber.calls[0]
    assert language is None
    assert samples == pytest.approx(int(1.0 * SR), rel=0.02), (
        "the window is the speech, not the pause after it"
    )


def test_partials_follow_the_cadence_and_are_never_final():
    clock = FakeClock()
    transcriber = FakeTranscriber()
    session = make(clock, transcriber, min_partial_s=0.5)

    session.on_audio(tone(2.0))
    assert session.poll() == [], "no time has passed since the audio arrived"

    clock.advance(1.0)
    [partial] = session.poll()
    assert partial.type == "partial"
    assert partial.payload["is_final"] is False
    assert partial.payload["window"]["end_s"] == pytest.approx(2.0, abs=0.05)

    # the cadence is not a loop: asking again immediately decodes nothing
    assert session.poll() == []
    assert len(transcriber.calls) == 1

    clock.advance(1.0)
    assert types(session.poll()) == ["partial"]


def test_a_window_that_closes_after_a_partial_becomes_a_final():
    clock = FakeClock()
    transcriber = FakeTranscriber()
    session = make(clock, transcriber, min_partial_s=0.5)

    session.on_audio(tone(1.0))
    clock.advance(1.1)
    assert types(session.poll()) == ["partial"]

    events = session.on_audio(silence(0.8))
    assert types(events) == ["final"], "the closed window is decoded once, as final"

    # ...and no stale partial for the window that just closed
    clock.advance(2.0)
    assert session.poll() == []


def test_timestamps_are_session_absolute_across_utterances():
    clock = FakeClock()
    session = make(clock)  # the fake answers segments at 0.1..0.5 relative

    session.on_audio(tone(1.0))
    [first] = session.on_audio(silence(0.8))
    assert first.payload["segments"][0]["start"] == pytest.approx(0.1, abs=0.05)

    # second utterance starts at 1.8 s: its 0.1 s segment must land at ~1.9 s
    session.on_audio(tone(1.0))
    [second] = session.on_audio(silence(0.8))
    start = second.payload["segments"][0]["start"]
    assert start == pytest.approx(1.9, abs=0.08), start


def test_an_unrepresentable_segment_is_an_error_not_a_silent_drop():
    """The canonical schema says end > start. A provider that breaks it gets a
    loud error event -- segment text is never silently dropped."""
    clock = FakeClock()

    class Broken(FakeTranscriber):
        def transcribe_window(self, samples, language):
            self.calls.append((int(samples.size), language))
            # model_construct bypasses validation, standing in for a provider
            # that hands back a segment the canonical schema cannot represent.
            bad = TranscriptSegment.model_construct(
                start=3.0, end=3.0, text="zero", words=[]
            )
            return ProviderTranscript(
                text="zero",
                language=None,
                segments=[bad],
                words_available=False,
                model=self.model_name,
                model_version=None,
                audio_duration_s=1.0,
                processing_duration_s=0.01,
            )

    session = make(clock, Broken())
    session.on_audio(tone(1.0))
    [event] = session.on_audio(silence(0.8))
    assert event.type == "error"
    assert "segment" in event.payload["message"].lower()


def test_the_cap_ends_the_session_with_capped_then_done():
    clock = FakeClock()
    session = make(clock, max_duration_s=2.0)

    events = session.on_audio(tone(3.0))
    assert types(events) == [], "audio inside the cap is not an error"

    ended = session.poll()
    assert types(ended) == ["capped", "done"]
    assert ended[0].payload["max_duration_s"] == 2.0
    assert ended[1].payload["reason"] == "capped"

    assert session.capped is True
    assert session.on_audio(tone(0.5)) == [], "audio after the cap is refused"


def test_idle_sessions_end_with_done_reason_idle():
    clock = FakeClock()
    session = make(clock, idle_timeout_s=30.0)
    session.on_audio(tone(0.5))

    clock.advance(31.0)
    [done] = session.poll()
    assert done.type == "done"
    assert done.payload["reason"] == "idle"


def test_stop_flushes_the_open_window_and_reports_the_full_text():
    clock = FakeClock()
    session = make(clock)

    session.on_audio(tone(1.0))
    session.on_audio(silence(0.8))
    session.on_audio(tone(1.2))

    events = session.stop()
    assert types(events) == ["final", "done"]
    assert events[0].payload["text"] == "hello"
    done = events[1]
    assert done.payload["reason"] == "stop"
    assert done.payload["text"] == "hello hello", "finals concatenate in order"
    assert done.payload["duration_s"] == pytest.approx(3.0, abs=0.1)


def test_stop_twice_is_not_two_sessions():
    clock = FakeClock()
    session = make(clock)
    session.on_audio(tone(0.8))
    assert types(session.stop()) == ["final", "done"]
    assert session.stop() == []


def test_a_failed_decode_is_an_error_event_and_the_session_survives():
    """A provider failure must not kill the socket: the caller keeps talking."""
    clock = FakeClock()

    class Broken(FakeTranscriber):
        def transcribe_window(self, samples, language):
            self.calls.append((int(samples.size), language))
            if len(self.calls) == 1:
                raise ProviderError("INFERENCE_FAILED", "gpu fell over", retryable=True)
            return super().transcribe_window(samples, language)

    session = make(clock, Broken())
    session.on_audio(tone(1.0))
    [error] = session.on_audio(silence(0.8))
    assert error.type == "error"
    assert error.payload["code"] == "STREAM_INFERENCE_FAILED"
    assert "gpu fell over" in error.payload["message"]

    session.on_audio(tone(1.0))
    [recovered] = session.on_audio(silence(0.8))
    assert recovered.type == "final", "the next utterance still transcribes"
