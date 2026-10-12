"""The streaming session core: PCM in, transcript events out.

Behaviour, not I/O: the WebSocket endpoint (``api/v1/streaming.py``) owns
threads, decoding containers and the socket; this owns the voice-path rules.

* **One decode at a time.** A tick re-decodes the *open* window for a
  ``partial`` and a closed window is decoded exactly once, for ``final``.
  Nothing is ever queued behind a slow decode -- the next tick simply waits.
* **Timestamps are session-absolute.** A window's transcript is shifted by
  the window's own offset, so a client can join finals to the recording it is
  making without arithmetic.
* **Bounds end the session, they do not surprise it.** The duration cap
  produces ``capped`` then ``done``; an idle session produces ``done`` with
  ``reason: "idle"``; the stop button flushes the open window first.
* **A failure is an event, not a crash.** A provider error is reported as
  ``error`` and the caller keeps talking.
* **Nothing is persisted.** No job, no object, no row -- the durable
  artefact is still the recording that goes through ``/v1/transcriptions``.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import ProviderTranscript
from stt_platform.schemas.transcript import TranscriptSegment, TranscriptWord
from stt_platform.streaming.vad import UtteranceSegmenter, VadConfig

__all__ = ["StreamConfig", "StreamEvent", "StreamingSession", "WindowTranscriber"]


class WindowTranscriber(Protocol):
    """What the session needs from a provider: one window in, one transcript out."""

    provider_id: str
    model_name: str

    def transcribe_window(
        self, samples: np.ndarray, language: str | None
    ) -> ProviderTranscript: ...


@dataclass(frozen=True)
class StreamEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        """The JSON frame a client receives."""
        return {"type": self.type, **self.payload}


@dataclass(frozen=True)
class StreamConfig:
    max_duration_s: float = 300.0
    partial_interval_s: float = 1.0
    #: A window shorter than this never becomes a partial (nothing useful yet).
    min_partial_s: float = 0.5
    idle_timeout_s: float = 30.0
    language: str | None = None
    vad: VadConfig = field(default_factory=VadConfig)


def _malformed_event(exc: Exception) -> StreamEvent:
    return StreamEvent(
        "error",
        {
            "code": "STREAM_MALFORMED_TRANSCRIPT",
            "message": f"{type(exc).__name__}: {exc}",
        },
    )


class StreamingSession:
    def __init__(
        self,
        *,
        transcriber: WindowTranscriber,
        config: StreamConfig | None = None,
        session_id: str = "",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transcriber = transcriber
        self.config = config or StreamConfig()
        self.session_id = session_id
        self._clock = clock
        self._vad = UtteranceSegmenter(self.config.vad)
        self._sample_rate = self.config.vad.sample_rate
        self._buffer = np.zeros(0, dtype=np.float32)
        self._total = 0
        self._capped = False
        self._ended = False
        self._last_audio_at = clock()
        self._last_partial_at = clock()
        self._finals: list[str] = []

    # ------------------------------------------------------------------ state
    @property
    def capped(self) -> bool:
        return self._capped

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def duration_s(self) -> float:
        return self._total / self._sample_rate

    @property
    def text(self) -> str:
        return " ".join(self._finals).strip()

    def ready_payload(self) -> dict[str, Any]:
        return {
            "provider": self.transcriber.provider_id,
            "model": self.transcriber.model_name,
            "session_id": self.session_id,
            "max_duration_s": self.config.max_duration_s,
            "partial_interval_s": self.config.partial_interval_s,
        }

    # ------------------------------------------------------------------ input
    def on_audio(self, samples: np.ndarray) -> list[StreamEvent]:
        """Consume PCM; return the finals that closed on this block."""
        if self._ended or self._capped:
            return []
        block = np.asarray(samples, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return []
        self._append(block)
        self._last_audio_at = self._clock()
        events: list[StreamEvent] = []
        for window in self._vad.feed(block):
            events.extend(self._final_for(window))
        return events

    def poll(self) -> list[StreamEvent]:
        """Whatever the clock says is due: the cap, an idle end, or a partial."""
        if self._ended:
            return []
        now = self._clock()
        if self.duration_s > self.config.max_duration_s:
            self._capped = True
            self._ended = True
            return [
                StreamEvent(
                    "capped", {"max_duration_s": self.config.max_duration_s}
                ),
                StreamEvent("done", self._done_payload("capped")),
            ]
        if now - self._last_audio_at >= self.config.idle_timeout_s:
            self._ended = True
            return [StreamEvent("done", self._done_payload("idle"))]

        window = self._vad.open_window
        if window is None:
            return []
        length_s = (window.end_sample - window.start_sample) / self._sample_rate
        if length_s < self.config.min_partial_s:
            return []
        if now - self._last_partial_at < self.config.partial_interval_s:
            return []
        self._last_partial_at = now
        offset_s = window.start_sample / self._sample_rate
        transcript, error = self._decode(window.start_sample, window.end_sample)
        if error is not None:
            return [error]
        assert transcript is not None  # for the type checker; error covers the rest
        try:
            segments = self._segments(transcript, offset_s)
        except Exception as exc:  # a transcript the schema cannot represent
            return [_malformed_event(exc)]
        return [
            StreamEvent(
                "partial",
                {
                    "text": transcript.text,
                    "segments": segments,
                    "window": {
                        "start_s": round(offset_s, 3),
                        "end_s": round(window.end_sample / self._sample_rate, 3),
                    },
                    "is_final": False,
                },
            )
        ]

    def stop(self, reason: str = "stop") -> list[StreamEvent]:
        """Flush the open window, then end the session."""
        if self._ended:
            return []
        events: list[StreamEvent] = []
        window = self._vad.flush()
        if window is not None:
            events.extend(self._final_for(window))
        self._ended = True
        events.append(StreamEvent("done", self._done_payload(reason)))
        return events

    # ------------------------------------------------------------------ internals
    def _done_payload(self, reason: str) -> dict[str, Any]:
        return {
            "reason": reason,
            "duration_s": round(self.duration_s, 3),
            "text": self.text,
        }

    def _final_for(self, window) -> list[StreamEvent]:
        offset_s = window.start_sample / self._sample_rate
        transcript, error = self._decode(window.start_sample, window.end_sample)
        if error is not None:
            return [error]
        assert transcript is not None
        try:
            segments = self._segments(transcript, offset_s)
        except Exception as exc:  # a transcript the schema cannot represent
            return [_malformed_event(exc)]
        text = transcript.text.strip() or " ".join(
            seg["text"] for seg in segments
        ).strip()
        if text:
            self._finals.append(text)
        return [
            StreamEvent(
                "final",
                {
                    "text": text,
                    "segments": segments,
                    "window": {
                        "start_s": round(offset_s, 3),
                        "end_s": round(window.end_sample / self._sample_rate, 3),
                    },
                },
            )
        ]

    def _decode(
        self, start_sample: int, end_sample: int
    ) -> tuple[ProviderTranscript | None, StreamEvent | None]:
        samples = self._buffer[start_sample:end_sample].copy()
        try:
            transcript = self.transcriber.transcribe_window(
                samples, self.config.language
            )
        except ProviderError as exc:
            return None, StreamEvent(
                "error",
                {"code": "STREAM_INFERENCE_FAILED", "message": str(exc)},
            )
        except Exception as exc:  # pragma: no cover - defensive: never kill the socket
            return None, StreamEvent(
                "error",
                {"code": "STREAM_INFERENCE_FAILED", "message": f"{type(exc).__name__}: {exc}"},
            )
        return transcript, None

    def _segments(
        self, transcript: ProviderTranscript, offset_s: float
    ) -> list[dict[str, Any]]:
        """Shift a window's transcript onto the session timeline.

        The shifted segments are *re-validated* against the canonical schema,
        not patched around it: a segment the schema rejects raises, and the
        caller reports it as an error event. Segment text is never silently
        dropped -- that rule comes from the batch path, where a degenerate
        segment fails the job loudly instead of vanishing. Degenerate *word*
        timings are dropped, exactly as the batch path drops them.
        """
        out: list[dict[str, Any]] = []
        for segment in transcript.segments:
            start = max(0.0, round(segment.start + offset_s, 3))
            end = round(segment.end + offset_s, 3)
            words: list[TranscriptWord] = []
            for word in segment.words:
                word_start = max(0.0, round(word.start + offset_s, 3))
                word_end = round(word.end + offset_s, 3)
                if word_end <= word_start:
                    continue
                words.append(
                    TranscriptWord(
                        start=word_start,
                        end=word_end,
                        text=word.text,
                        confidence=word.confidence,
                        speaker=word.speaker,
                    )
                )
            out.append(
                TranscriptSegment(
                    start=start,
                    end=end,
                    text=segment.text,
                    confidence=segment.confidence,
                    speaker=segment.speaker,
                    words=words,
                ).model_dump(mode="json")
            )
        return out

    def _append(self, block: np.ndarray) -> None:
        need = self._total + int(block.size)
        if need > self._buffer.size:
            capacity = max(self._buffer.size * 2, need, self._sample_rate * 30)
            grown = np.zeros(capacity, dtype=np.float32)
            grown[: self._total] = self._buffer[: self._total]
            self._buffer = grown
        self._buffer[self._total : need] = block
        self._total = need
