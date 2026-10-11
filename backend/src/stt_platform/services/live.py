"""Transient live-preview transcription.

While a browser is recording, it periodically sends the audio captured so far
and gets back the text so far. Nothing is persisted: no job row, no object in
storage, no evaluation, no trail. The preview runs on the **local** provider
only — the spec forbids routing audio to a cloud vendor without the caller
asking for it, and a preview that fired every few seconds at a managed API
would spend someone's quota by surprise.

The audio is re-transcribed from the beginning each time, which is what makes
the text grow monotonically instead of producing seams; the cap keeps the
cost of that choice bounded (see ``max_live_preview_duration_s``).
"""
import logging
import time
from pathlib import Path

from stt_platform.audio.inspect import inspect_audio
from stt_platform.core.config import Settings
from stt_platform.domain.errors import AudioTooLong, LivePreviewUnsupported
from stt_platform.providers.base import ProviderProtocol, TranscriptionRequest
from stt_platform.schemas.live import LivePreviewResponse

log = logging.getLogger(__name__)


def preview_transcription(
    *,
    audio_path: Path,
    provider: ProviderProtocol,
    language: str | None,
    settings: Settings,
) -> LivePreviewResponse:
    """Transcribe ``audio_path`` transiently, or explain why we will not.

    Raises :class:`LivePreviewUnsupported` for a managed provider and
    :class:`AudioTooLong` past the configured preview cap; audio problems
    surface as :class:`~stt_platform.domain.errors.AudioValidationError`.
    """
    if not provider.is_local:
        raise LivePreviewUnsupported(
            f"live preview is offered by the local provider only; "
            f"{provider.id!r} is a managed provider"
        )

    info = inspect_audio(audio_path)
    if info.duration_s > settings.max_live_preview_duration_s:
        raise AudioTooLong(
            f"live preview is capped at "
            f"{settings.max_live_preview_duration_s:.0f}s of audio "
            f"(this clip is {info.duration_s:.1f}s) — stop recording to "
            f"transcribe it in full"
        )

    started = time.perf_counter()
    transcript = provider.transcribe(
        TranscriptionRequest(audio_path=audio_path, language=language or None)
    )
    elapsed = time.perf_counter() - started

    has_words = any(segment.words for segment in transcript.segments)
    log.info(
        "live preview: provider=%s audio=%.1fs processing=%.2fs segments=%d",
        provider.id,
        info.duration_s,
        elapsed,
        len(transcript.segments),
    )

    return LivePreviewResponse(
        text=transcript.text,
        language=transcript.language,
        language_probability=transcript.language_probability,
        segments=transcript.segments,
        timestamp_granularity="word" if has_words else "segment",
        # The file's own duration is authoritative, not the decoder's opinion.
        audio_duration_s=info.duration_s,
        processing_duration_s=round(elapsed, 3),
        provider=provider.id,
        model=transcript.model,
        model_version=transcript.model_version,
    )
