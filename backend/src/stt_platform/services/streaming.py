"""Building blocks for the streaming path (Phase 8).

The route owns the socket; the session owns the voice-path rules; this module
owns the small pieces that connect them -- the window adapter, the per-process
concurrency bound, and the error codes the wire speaks. Kept out of the route
so each piece is testable (and importable) without a WebSocket.
"""
from __future__ import annotations

import threading
from typing import Protocol, cast

import numpy as np

from stt_platform.core.config import Settings
from stt_platform.providers.base import ProviderTranscript
from stt_platform.streaming.session import StreamConfig
from stt_platform.streaming.vad import VadConfig

__all__ = [
    "STREAM_BAD_REQUEST",
    "STREAM_BUSY",
    "STREAM_DECODE_FAILED",
    "STREAM_INFERENCE_FAILED",
    "STREAM_INTERNAL",
    "STREAM_LOCAL_ONLY",
    "STREAM_MALFORMED_TRANSCRIPT",
    "STREAM_SAMPLE_RATE",
    "PcmWindowProvider",
    "ProviderWindowTranscriber",
    "StreamLimiter",
    "as_window_provider",
    "stream_config",
]

#: Every stream decodes to this rate before it reaches the model.
STREAM_SAMPLE_RATE = 16000

# The codes the stream speaks. Documented in docs/streaming.md; tests assert
# them literally, so a rename is a visible break, never a silent one.
STREAM_LOCAL_ONLY = "STREAM_LOCAL_ONLY"
STREAM_BAD_REQUEST = "STREAM_BAD_REQUEST"
STREAM_BUSY = "STREAM_BUSY"
STREAM_DECODE_FAILED = "STREAM_DECODE_FAILED"
STREAM_INFERENCE_FAILED = "STREAM_INFERENCE_FAILED"
STREAM_MALFORMED_TRANSCRIPT = "STREAM_MALFORMED_TRANSCRIPT"
STREAM_INTERNAL = "STREAM_INTERNAL"


class PcmWindowProvider(Protocol):
    """A local provider that can transcribe a window already in memory."""

    id: str
    is_local: bool

    def transcribe_pcm(
        self, samples: np.ndarray, language: str | None = None
    ) -> ProviderTranscript: ...


def as_window_provider(provider: object) -> PcmWindowProvider | None:
    """The provider as a window API, or ``None`` when it cannot stream."""
    if callable(getattr(provider, "transcribe_pcm", None)):
        return cast(PcmWindowProvider, provider)
    return None


class ProviderWindowTranscriber:
    """Adapts a provider to the session's ``transcribe_window`` contract."""

    def __init__(self, provider: PcmWindowProvider, *, model_name: str) -> None:
        self._provider = provider
        self.provider_id = provider.id
        self.model_name = model_name

    def transcribe_window(
        self, samples: np.ndarray, language: str | None
    ) -> ProviderTranscript:
        return self._provider.transcribe_pcm(samples, language)


class StreamLimiter:
    """Concurrent streams per process, like the rate limiter: local by design.

    A stream holds a GPU slot for its whole life, so the bound is small and
    the refusal is explicit (``STREAM_BUSY``) instead of a queue that makes
    everyone slower. Behind several replicas each process keeps its own count;
    that is the same trade the quota limiter documents.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)


def stream_config(settings: Settings, *, language: str | None) -> StreamConfig:
    return StreamConfig(
        max_duration_s=settings.stream_max_duration_s,
        idle_timeout_s=settings.stream_idle_timeout_s,
        language=language,
        vad=VadConfig(),
    )
