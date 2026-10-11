"""ElevenLabs Scribe transcription adapter (synchronous, word-level).

API shape verified against vendor documentation at implementation time:

* https://elevenlabs.io/docs/api-reference/speech-to-text/convert
* https://github.com/elevenlabs/skills (speech-to-text SKILL.md)

Request: ``POST {base}/v1/speech-to-text`` multipart/form-data with
``file``, ``model_id`` (default ``scribe_v2``; ``scribe_v1`` remains
selectable via ``STT_ELEVENLABS_MODEL_ID``), ``timestamps_granularity=word``
and an optional ``language_code``. Auth is the raw ``xi-api-key`` header.

Response: ``{text, language_code, language_probability, words[]}`` where
each word is ``{text, start, end, type, speaker_id, logprob}`` and
``type`` is ``word`` | ``spacing`` | ``audio_event``. Only ``word``
tokens become transcript words (spacing/audio_event are dropped by
design); ``speaker_id`` strings (``"speaker_0"``) map to ints and
unparseable ids (e.g. ``agent``/``customer``) stay ``None``.

Fidelity: Scribe returns ``logprob`` but no calibrated per-word
confidence, so ``confidence`` stays ``None`` — never fabricated from the
log probability. ``audio_duration_s`` is probed locally with ffprobe
(the response carries no duration). The vendor's ``language_code`` is
kept verbatim (e.g. ``eng``).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from stt_platform.audio.inspect import inspect_audio
from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import ProviderTranscript, TranscriptionRequest
from stt_platform.providers.managed.http_base import ManagedHttpClient
from stt_platform.schemas.providers import ProviderCapabilities, ProviderHealth
from stt_platform.schemas.transcript import TranscriptSegment, TranscriptWord

_LANGUAGES = [
    "en", "es", "fr", "de", "hi", "it", "ja", "ko", "nl", "pl", "pt", "ru",
    "sv", "tr", "uk", "zh", "ar", "da", "fi", "id", "ms", "no", "th", "vi",
]


class ElevenLabsProvider:
    id = "elevenlabs"
    name = "ElevenLabs Scribe"
    is_local = False

    def __init__(
        self,
        settings: Settings,
        semaphore: threading.BoundedSemaphore | None = None,
    ) -> None:
        self._settings = settings
        self._client = ManagedHttpClient(
            base_url=settings.elevenlabs_base_url,
            api_key=settings.elevenlabs_api_key or "",
            auth_header="xi-api-key",
            auth_scheme=None,
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
            segment_timestamps=False,
            word_timestamps=True,
            diarization=True,
            streaming=False,
            batch=True,
            async_processing=False,
            confidence_scores=False,
            formats=["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "webm"],
            capability_sources={
                "word_timestamps": "documented",
                "diarization": "documented",
                "language_detection": "documented",
            },
        )

    def health(self) -> ProviderHealth:
        if not self._settings.elevenlabs_api_key:
            return ProviderHealth(
                status="unavailable", detail="STT_ELEVENLABS_API_KEY not set"
            )
        return ProviderHealth(
            status="ok", detail="credential configured; reachability unverified"
        )

    def transcribe(self, request: TranscriptionRequest) -> ProviderTranscript:
        started = time.perf_counter()
        path = Path(request.audio_path)
        data = {
            "model_id": self._settings.elevenlabs_model_id,
            "timestamps_granularity": "word",
        }
        if request.language:
            data["language_code"] = request.language
        with path.open("rb") as handle:
            payload = self._client.request(
                "POST",
                "/v1/speech-to-text",
                data=data,
                files={
                    "file": (
                        path.name,
                        handle,
                        "application/octet-stream",
                    )
                },
            )
        duration_s = inspect_audio(path).duration_s
        return _normalize(
            payload,
            model=self._settings.elevenlabs_model_id,
            duration_s=duration_s,
            processing_duration_s=time.perf_counter() - started,
        )


# ----------------------------------------------------------------------
# normalization
# ----------------------------------------------------------------------
def _normalize(
    payload,
    *,
    model: str,
    duration_s: float,
    processing_duration_s: float,
) -> ProviderTranscript:
    if not isinstance(payload, dict):
        raise ProviderError(
            "MALFORMED_RESPONSE",
            "ElevenLabs returned a non-JSON payload",
            retryable=False,
        )
    raw_words = payload.get("words")
    if not isinstance(raw_words, list):
        raise ProviderError(
            "MALFORMED_RESPONSE",
            "ElevenLabs payload has no words[] array",
            retryable=False,
        )

    warnings: list[str] = []
    words = _words(raw_words, warnings)
    segments = _group_by_speaker(words)
    text = (payload.get("text") or "").strip()
    if not text and words:
        text = " ".join(w.text for w in words)

    return ProviderTranscript(
        text=text,
        language=payload.get("language_code"),
        language_probability=_unit_float(payload.get("language_probability")),
        segments=segments,
        words_available=bool(words),
        model=model,
        model_version=None,
        audio_duration_s=duration_s,
        processing_duration_s=round(processing_duration_s, 3),
        warnings=warnings,
        raw=payload,
    )


def _unit_float(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 0 <= float(value) <= 1:
            return float(value)
    return None


def _speaker_int(value) -> int | None:
    """``"speaker_0"`` -> 0; anything else (e.g. ``agent``) stays None."""
    if isinstance(value, str) and value.startswith("speaker_"):
        suffix = value[len("speaker_") :]
        if suffix.isdigit():
            return int(suffix)
    return None


def _words(raw_words: list, warnings: list[str]) -> list[TranscriptWord]:
    out: list[TranscriptWord] = []
    for raw in raw_words:
        if raw.get("type") != "word":
            continue  # spacing / audio_event are not transcript words
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            warnings.append("dropped word without usable timestamps")
            continue
        text = (raw.get("text") or "").strip()
        if not text:
            warnings.append("dropped word with empty text")
            continue
        if end <= start:
            warnings.append(f"dropped degenerate word {text!r} ({start} -> {end})")
            continue
        out.append(
            TranscriptWord(
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                confidence=None,  # Scribe has no per-word confidence
                speaker=_speaker_int(raw.get("speaker_id")),
            )
        )
    return out


def _group_by_speaker(words: list[TranscriptWord]) -> list[TranscriptSegment]:
    """Deterministic segmentation: runs of consecutive same-speaker words.

    Scribe returns no segment timings, so segments are derived from the
    word stream itself — never invented.
    """
    groups: list[dict] = []
    for word in words:
        if groups and (
            word.speaker == groups[-1]["speaker"] or word.speaker is None
        ):
            groups[-1]["words"].append(word)
        else:
            groups.append({"speaker": word.speaker, "words": [word]})

    segments: list[TranscriptSegment] = []
    for group in groups:
        group_words: list[TranscriptWord] = group["words"]
        segments.append(
            TranscriptSegment(
                start=group_words[0].start,
                end=group_words[-1].end,
                text=" ".join(w.text for w in group_words),
                speaker=group["speaker"],
                words=group_words,
            )
        )
    return segments
