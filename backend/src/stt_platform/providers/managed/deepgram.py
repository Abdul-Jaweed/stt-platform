"""Deepgram pre-recorded transcription adapter (synchronous, word-level).

API shape verified against vendor documentation at implementation time:

* https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded.mdx
* https://developers.deepgram.com/docs/pre-recorded-audio
* https://developers.deepgram.com/docs/diarization

Request: ``POST {base}/v1/listen`` with query parameters
``model=nova-3&smart_format=true&punctuate=true&utterances=true`` (plus
``language`` when requested), the raw audio bytes as the body, and
``Authorization: Token <key>``.

Response: ``results.channels[0].alternatives[0]`` carries ``transcript``
and ``words[]`` (``word``/``punctuated_word``, ``start``, ``end``,
``confidence``, ``speaker``); ``results.utterances[]`` carries
sentence-level segments with ``speaker`` labels; ``metadata.duration`` is
the audio duration and ``metadata.model_info`` the model version.

Fidelity: degenerate words (end <= start) are dropped with a warning —
never clamped, never invented; a word without usable confidence keeps
``None``. Segments fall back to a single whole-transcript segment when the
vendor returns no utterances.
"""
from __future__ import annotations

import mimetypes
import threading
import time
from pathlib import Path

from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import ProviderTranscript, TranscriptionRequest
from stt_platform.providers.managed.http_base import ManagedHttpClient
from stt_platform.schemas.providers import ProviderCapabilities, ProviderHealth
from stt_platform.schemas.transcript import TranscriptSegment, TranscriptWord

MODEL = "nova-3"

# Deepgram documents broad multilingual support; we only claim what the
# vendor documents, so the list stays conservative and honest.
_LANGUAGES = [
    "en", "es", "fr", "de", "hi", "it", "ja", "ko", "nl", "pl", "pt", "ru",
    "sv", "tr", "uk", "zh", "ar", "da", "fi", "id", "ms", "no", "th", "vi",
]


class DeepgramProvider:
    id = "deepgram"
    name = "Deepgram (nova-3)"
    is_local = False

    def __init__(
        self,
        settings: Settings,
        semaphore: threading.BoundedSemaphore | None = None,
    ) -> None:
        self._settings = settings
        self._client = ManagedHttpClient(
            base_url=settings.deepgram_base_url,
            api_key=settings.deepgram_api_key or "",
            auth_header="Authorization",
            auth_scheme="Token",
            timeout_s=settings.provider_timeout_s,
            connect_timeout_s=settings.provider_connect_timeout_s,
            max_response_bytes=settings.max_provider_response_bytes,
            semaphore=semaphore,
        )

    # ------------------------------------------------------------------
    # contract
    # ------------------------------------------------------------------
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            languages=list(_LANGUAGES),
            language_detection=True,
            segment_timestamps=True,
            word_timestamps=True,
            diarization=True,
            streaming=False,
            batch=True,
            async_processing=False,
            confidence_scores=True,
            formats=["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "webm"],
            capability_sources={
                "word_timestamps": "documented",
                "segment_timestamps": "documented",
                "diarization": "documented",
                "confidence_scores": "documented",
                "language_detection": "documented",
            },
        )

    def health(self) -> ProviderHealth:
        # There is no key-less reachability probe, and a real transcription
        # would cost money: report credential availability only and never
        # claim verified reachability.
        if not self._settings.deepgram_api_key:
            return ProviderHealth(
                status="unavailable", detail="STT_DEEPGRAM_API_KEY not set"
            )
        return ProviderHealth(
            status="ok", detail="credential configured; reachability unverified"
        )

    def transcribe(self, request: TranscriptionRequest) -> ProviderTranscript:
        started = time.perf_counter()
        params = {
            "model": MODEL,
            "smart_format": "true",
            "punctuate": "true",
            "utterances": "true",
        }
        if request.language:
            params["language"] = request.language
        content_type = (
            mimetypes.guess_type(str(request.audio_path))[0]
            or "application/octet-stream"
        )
        payload = self._client.request(
            "POST",
            "/v1/listen",
            params=params,
            content=Path(request.audio_path).read_bytes(),
            headers={"Content-Type": content_type},
        )
        return _normalize(
            payload, processing_duration_s=time.perf_counter() - started
        )


# ----------------------------------------------------------------------
# normalization
# ----------------------------------------------------------------------
def _normalize(payload, *, processing_duration_s: float) -> ProviderTranscript:
    if not isinstance(payload, dict):
        raise ProviderError(
            "MALFORMED_RESPONSE", "Deepgram returned a non-JSON payload", retryable=False
        )
    results = payload.get("results") or {}
    metadata = payload.get("metadata") or {}
    try:
        channel = results["channels"][0]
        alternative = channel["alternatives"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(
            "MALFORMED_RESPONSE",
            "Deepgram payload has no results.channels[0].alternatives[0]",
            retryable=False,
        ) from exc

    warnings: list[str] = []
    words = _words(alternative.get("words") or [], warnings)
    segments = _segments(results, alternative, warnings)

    duration = metadata.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        audio_duration_s = float(duration)
    else:
        ends = [s.end for s in segments] or [w.end for w in words]
        audio_duration_s = max(ends) if ends else 0.0

    return ProviderTranscript(
        text=(alternative.get("transcript") or "").strip(),
        language=channel.get("detected_language"),
        language_probability=_unit_float(channel.get("language_confidence")),
        segments=segments,
        words_available=bool(words),
        model=MODEL,
        model_version=_model_version(metadata),
        audio_duration_s=audio_duration_s,
        processing_duration_s=round(processing_duration_s, 3),
        warnings=warnings,
        raw=payload,
    )


def _model_version(metadata: dict) -> str | None:
    model_ids = metadata.get("models") or []
    info = metadata.get("model_info") or {}
    if model_ids and isinstance(info, dict):
        entry = info.get(model_ids[0]) or {}
        version = entry.get("version")
        if isinstance(version, str):
            return version
    return None


def _unit_float(value) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= float(value) <= 1
    ):
        return float(value)
    return None


def _words(raw_words: list, warnings: list[str]) -> list[TranscriptWord]:
    out: list[TranscriptWord] = []
    for raw in raw_words:
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            warnings.append("dropped word without usable timestamps")
            continue
        text = (raw.get("punctuated_word") or raw.get("word") or "").strip()
        if not text:
            warnings.append("dropped word with empty text")
            continue
        if end <= start:
            warnings.append(
                f"dropped degenerate word {text!r} ({start} -> {end})"
            )
            continue
        speaker = raw.get("speaker")
        out.append(
            TranscriptWord(
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                confidence=_unit_float(raw.get("confidence")),
                speaker=(
                    int(speaker)
                    if isinstance(speaker, int) and not isinstance(speaker, bool)
                    else None
                ),
            )
        )
    return out


def _segments(
    results: dict, alternative: dict, warnings: list[str]
) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for utterance in results.get("utterances") or []:
        try:
            start = float(utterance["start"])
            end = float(utterance["end"])
        except (KeyError, TypeError, ValueError):
            warnings.append("dropped utterance without usable timestamps")
            continue
        if end <= start:
            warnings.append(
                f"dropped degenerate utterance ({start} -> {end})"
            )
            continue
        utterance_words = _words(utterance.get("words") or [], warnings)
        text = (utterance.get("transcript") or "").strip()
        if not text:
            text = " ".join(w.text for w in utterance_words).strip()
        if not text:
            warnings.append("dropped utterance with no text or words")
            continue
        speaker = utterance.get("speaker")
        segments.append(
            TranscriptSegment(
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                confidence=_unit_float(utterance.get("confidence")),
                speaker=(
                    int(speaker)
                    if isinstance(speaker, int) and not isinstance(speaker, bool)
                    else None
                ),
                words=utterance_words,
            )
        )
    if segments:
        return segments

    # No utterances: one honest whole-transcript segment built from the
    # words we actually received (never a fabricated span).
    all_words = _words(alternative.get("words") or [], warnings)
    text = (alternative.get("transcript") or "").strip()
    if all_words:
        segments.append(
            TranscriptSegment(
                start=all_words[0].start,
                end=all_words[-1].end,
                text=text or " ".join(w.text for w in all_words),
                words=all_words,
            )
        )
    return segments
