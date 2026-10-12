"""Building blocks for the streaming path (Phase 8).

The route owns the socket; the session owns the voice-path rules; this module
owns the small pieces that connect them -- the window adapter, the per-process
concurrency bound, and the error codes the wire speaks. Kept out of the route
so each piece is testable (and importable) without a WebSocket.
"""
from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, cast

import numpy as np

from stt_platform.core.config import Settings
from stt_platform.providers.base import ProviderTranscript
from stt_platform.streaming.session import StreamConfig, StreamEvent
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
    "STREAM_UNVERIFIED",
    "ManagedStream",
    "ManagedStreamConfig",
    "ManagedStreamProvider",
    "PcmWindowProvider",
    "ProviderWindowTranscriber",
    "StreamLimiter",
    "as_managed_stream_provider",
    "as_window_provider",
    "stream_config",
]

#: Every stream decodes to this rate before it reaches the model.
STREAM_SAMPLE_RATE = 16000

# The codes the stream speaks. Documented in docs/streaming.md; tests assert
# them literally, so a rename is a visible break, never a silent one.
STREAM_LOCAL_ONLY = "STREAM_LOCAL_ONLY"
#: A managed provider that either has no stream adapter or has not yet been
#: live-verified: the capability flag gates service, not just advertising.
STREAM_UNVERIFIED = "STREAM_UNVERIFIED"
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


# --------------------------------------------------------------------- #
# managed streaming: the same wire contract, a vendor socket behind it
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class ManagedStreamConfig:
    """What a vendor stream is opened with. The decoder has already turned
    the client's audio into 16 kHz mono PCM16, so every adapter speaks the
    same format and none of them touches the container."""

    sample_rate: int = 16000
    channels: int = 1
    language: str | None = None
    model: str | None = None
    max_duration_s: float = 300.0


class ManagedStream(Protocol):
    """A live vendor session: PCM in, platform events out.

    ``events()`` yields the same ``partial`` / ``final`` events the local
    session yields — the wire contract is the platform's, not the vendor's,
    and an adapter is what stands between the two.
    """

    async def send(self, pcm16: bytes) -> None: ...

    def events(self) -> AsyncIterator[StreamEvent]: ...

    async def close(self) -> None: ...


class ManagedStreamProvider(Protocol):
    """A provider that can serve a stream through a vendor socket."""

    def open_stream(
        self, config: ManagedStreamConfig
    ) -> AbstractAsyncContextManager[ManagedStream]: ...


def as_managed_stream_provider(provider: object) -> ManagedStreamProvider | None:
    """``provider`` as a managed-stream provider, or ``None``.

    Structural, like ``as_window_provider``: the capability flag says what
    was verified, the method says what exists. Capability flags are flipped
    only after a live run, so both checks matter — a provider may implement
    the method and still not advertise ``streaming`` yet.
    """
    if callable(getattr(provider, "open_stream", None)):
        return cast(ManagedStreamProvider, provider)
    return None
