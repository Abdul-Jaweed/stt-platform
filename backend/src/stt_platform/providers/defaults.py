"""Runtime provider registry built from settings.

The local engine is always present and is the default. A managed provider
registers only when its API key is configured: an unconfigured provider
must not appear in ``/v1/providers`` (and, more importantly, must never be
selected). The three managed adapters share one semaphore so a burst of
cloud calls cannot exceed ``managed_concurrency``.
"""
from __future__ import annotations

import threading

from stt_platform.core.config import Settings
from stt_platform.providers.registry import ProviderRegistry


def build_default_registry(settings: Settings) -> ProviderRegistry:
    from stt_platform.providers.faster_whisper import FasterWhisperProvider

    registry = ProviderRegistry()
    registry.register(FasterWhisperProvider(settings=settings), default=True)

    managed_semaphore = threading.BoundedSemaphore(
        max(1, settings.managed_concurrency)
    )
    if settings.deepgram_api_key:
        from stt_platform.providers.managed.deepgram import DeepgramProvider

        registry.register(
            DeepgramProvider(settings=settings, semaphore=managed_semaphore)
        )
    if settings.elevenlabs_api_key:
        from stt_platform.providers.managed.elevenlabs import ElevenLabsProvider

        registry.register(
            ElevenLabsProvider(settings=settings, semaphore=managed_semaphore)
        )
    if settings.sarvam_api_key:
        from stt_platform.providers.managed.sarvam import SarvamProvider

        registry.register(
            SarvamProvider(settings=settings, semaphore=managed_semaphore)
        )
    return registry
