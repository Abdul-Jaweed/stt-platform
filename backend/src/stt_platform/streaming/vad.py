"""Utterance segmentation for the streaming transcript path (Phase 8).

Whisper is a batch model, so streaming it honestly means deciding *where an
utterance ends* and then decoding it once. This module is that decision, kept
deliberately dumb and dependency-free: an RMS gate with hangover times.

The contract is geometry, not recognition:

* a window opens once ``min_speech_ms`` of voiced audio has accumulated
  (a click or a cough never becomes an utterance),
* a window closes ``silence_ms`` after the speech stops -- and it ends where
  the trailing silence *began*, so a final's end timestamp is the last speech,
  not the pause after it,
* a window that grows past ``max_window_ms`` rolls over immediately: the long
  monologue keeps producing finals, and the two windows share a boundary
  (no gap, no overlap),
* frames are aligned to the session start and leftover samples are carried, so
  the same signal produces the same windows no matter how it was chunked.

All sample counts here are 16 kHz mono indices on the *session's* timeline.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_SILENCE_FLOOR = 1e-9  # avoid log10(0); -180 dBFS is silence by any measure


@dataclass(frozen=True)
class VadConfig:
    sample_rate: int = 16000
    frame_ms: int = 30
    min_speech_ms: int = 240
    silence_ms: int = 600
    max_window_ms: int = 15000
    #: Speech measured below this is silence. -45 dBFS sits well under normal
    #: speech (-20 to -30) and well above a quiet room's noise floor.
    threshold_dbfs: float = -45.0

    @property
    def frame_len(self) -> int:
        return max(1, self.sample_rate * self.frame_ms // 1000)

    @property
    def min_speech_len(self) -> int:
        return max(1, self.sample_rate * self.min_speech_ms // 1000)

    @property
    def silence_len(self) -> int:
        return max(1, self.sample_rate * self.silence_ms // 1000)

    @property
    def max_window_len(self) -> int:
        return max(1, self.sample_rate * self.max_window_ms // 1000)


@dataclass(frozen=True)
class ClosedWindow:
    """A finished utterance: decode once, emit as ``final``."""

    start_sample: int
    end_sample: int
    reason: str  # "silence" | "rollover" | "flush"


@dataclass(frozen=True)
class OpenWindow:
    """The in-progress utterance: safe to re-decode for ``partial``."""

    start_sample: int
    end_sample: int


def rms_dbfs(frame: np.ndarray) -> float:
    if frame.size == 0:
        return -180.0
    rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
    return 20.0 * float(np.log10(max(rms, _SILENCE_FLOOR)))


class UtteranceSegmenter:
    def __init__(self, config: VadConfig | None = None) -> None:
        self.config = config or VadConfig()
        self._carry = np.zeros(0, dtype=np.float32)
        self._frame_pos = 0  # absolute sample index of the next frame start
        self._total = 0  # absolute samples received, carry included
        self._run_start: int | None = None  # contiguous voiced run start
        self._in_speech = False
        self._window_start = 0
        self._silence_start: int | None = None

    # ------------------------------------------------------------------
    @property
    def open_window(self) -> OpenWindow | None:
        if not self._in_speech:
            return None
        return OpenWindow(start_sample=self._window_start, end_sample=self._total)

    def feed(self, samples: np.ndarray) -> list[ClosedWindow]:
        """Consume samples; return every window that closed on this feed."""
        block = np.asarray(samples, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return []
        if self._carry.size:
            block = np.concatenate((self._carry, block))
        self._carry = np.zeros(0, dtype=np.float32)

        frame_len = self.config.frame_len
        closed: list[ClosedWindow] = []
        frames = block.size // frame_len
        for index in range(frames):
            frame = block[index * frame_len : (index + 1) * frame_len]
            closed.extend(self._consume_frame(frame))
        self._carry = block[frames * frame_len :].copy()
        self._total = self._frame_pos + int(self._carry.size)
        return closed

    def flush(self) -> ClosedWindow | None:
        """Close the open window at the current end (the stop button)."""
        if not self._in_speech:
            return None
        window = ClosedWindow(self._window_start, self._total, "flush")
        self._reset_speech()
        return window

    # ------------------------------------------------------------------
    def _consume_frame(self, frame: np.ndarray) -> list[ClosedWindow]:
        start = self._frame_pos
        end = start + int(frame.size)
        self._frame_pos = end
        voiced = rms_dbfs(frame) >= self.config.threshold_dbfs
        closed: list[ClosedWindow] = []

        if not self._in_speech:
            if not voiced:
                self._run_start = None
                return closed
            if self._run_start is None:
                self._run_start = start
            if end - self._run_start >= self.config.min_speech_len:
                self._in_speech = True
                self._window_start = self._run_start
                self._silence_start = None
            return closed

        # in speech
        if voiced:
            self._silence_start = None
        else:
            if self._silence_start is None:
                self._silence_start = start
            elif end - self._silence_start >= self.config.silence_len:
                closed.append(
                    ClosedWindow(self._window_start, self._silence_start, "silence")
                )
                self._reset_speech()
                return closed

        if end - self._window_start >= self.config.max_window_len:
            closed.append(ClosedWindow(self._window_start, end, "rollover"))
            # The monologue continues: the next window starts exactly where
            # this one ended, so a client stitching finals sees no seam.
            self._window_start = end
            self._run_start = end
            self._silence_start = None
        return closed

    def _reset_speech(self) -> None:
        self._in_speech = False
        self._run_start = None
        self._silence_start = None
