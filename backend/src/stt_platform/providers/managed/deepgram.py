"""Deepgram pre-recorded transcription adapter (synchronous, word-level).

API shape verified against vendor documentation at implementation time:

* https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded.mdx
* https://developers.deepgram.com/docs/pre-recorded-audio
* https://developers.deepgram.com/docs/diarization

Request: ``POST {base}/v1/listen`` with query parameters
``model=nova-3&smart_format=true&punctuate=true&utterances=true&diarize=true``
plus either ``language`` (when the caller pins one) or
``detect_language=true`` (otherwise, so the vendor reports the dominant
language), the raw audio bytes as the body, and
``Authorization: Token <key>`` as the only auth header.

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

import asyncio
import contextlib
import json
import logging
import mimetypes
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import websockets

from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import ProviderTranscript, TranscriptionRequest
from stt_platform.providers.managed.http_base import ManagedHttpClient
from stt_platform.schemas.providers import ProviderCapabilities, ProviderHealth
from stt_platform.schemas.transcript import TranscriptSegment, TranscriptWord
from stt_platform.services.streaming import ManagedStreamConfig
from stt_platform.streaming.session import StreamEvent

MODEL = "nova-3"

log = logging.getLogger(__name__)

#: Deepgram's real-time endpoint. The query parameters pin everything the
#: platform already guarantees on its own wire: 16 kHz mono PCM16 in,
#: interim results on (the console shows them), final results punctuated.
#: https://developers.deepgram.com/reference/speech-to-text/listen-streaming
STREAM_PATH = "/v1/listen"
_STREAM_QUERY = (
    "encoding=linear16&sample_rate={sample_rate}&channels={channels}"
    "&interim_results=true&punctuate=true&smart_format=true"
)
_OPEN_TIMEOUT_S = 15.0

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
            # Vendor docs (see docs/providers.md): "File size: Maximum 2 GB"
            # for pre-recorded audio. No duration limit is documented there,
            # so none is claimed.
            max_file_bytes=2 * 1024**3,
            capability_sources={
                # Live-verified 2026-10-11 (results/live_providers/result.json):
                # 2 utterance segments, 25 words with confidence + speakers,
                # detected language "en", vendor duration 10.0 s.
                "word_timestamps": "verified",
                "segment_timestamps": "verified",
                "diarization": "verified",
                "confidence_scores": "verified",
                "language_detection": "verified",
                "max_file_bytes": "documented",
            },
        )

    def open_stream(self, config: ManagedStreamConfig) -> DeepgramStreamSession:
        """A real-time session with Deepgram (see ``docs/streaming.md``).

        The capability flag stays ``streaming: false`` until a live run
        proves this path end to end; the method exists so the platform can
        offer it the moment that run is recorded.
        """
        return DeepgramStreamSession(
            url=_stream_url(
                self._settings.deepgram_base_url, config, config.model or MODEL
            ),
            api_key=self._settings.deepgram_api_key or "",
            model=config.model or MODEL,
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
            # Requested so the advertised diarization capability is real:
            # speakers land on words and utterances.
            "diarize": "true",
        }
        if request.language:
            params["language"] = request.language
        else:
            # No pinned language: ask the vendor to detect it, otherwise the
            # response carries no detected_language and the canonical result
            # silently loses its language field.
            params["detect_language"] = "true"
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


def _stream_url(base_url: str, config: ManagedStreamConfig, model: str) -> str:
    """``https://…`` becomes ``wss://…`` and the query carries the format."""
    scheme, _, rest = base_url.partition("://")
    ws_scheme = {"https": "wss", "http": "ws"}.get(scheme)
    if ws_scheme is None:
        raise ProviderError(f"deepgram base_url has no ws scheme: {base_url!r}")
    query = _STREAM_QUERY.format(
        sample_rate=config.sample_rate, channels=config.channels
    )
    return f"{ws_scheme}://{rest}{STREAM_PATH}?{query}&model={model}"


def _words_from_alternative(alternative: dict[str, Any]) -> list[TranscriptWord]:
    """Vendor words -> canonical words. Degenerate spans (end <= start) are
    dropped, never clamped — the same rule the batch path follows."""
    words: list[TranscriptWord] = []
    for raw in alternative.get("words") or []:
        start = raw.get("start")
        end = raw.get("end")
        if start is None or end is None or end <= start:
            continue
        words.append(
            TranscriptWord(
                start=float(start),
                end=float(end),
                text=str(raw.get("punctuated_word") or raw.get("word") or ""),
                confidence=(
                    float(raw["confidence"])
                    if raw.get("confidence") is not None
                    else None
                ),
            )
        )
    return words


def stream_event_from_message(
    message: dict[str, Any], *, fallback_start_s: float = 0.0
) -> StreamEvent | None:
    """One vendor message -> one platform event, or ``None`` for messages
    that carry no transcript (``Metadata``, ``UtteranceEnd``, ``SpeechStarted``).

    An ``Error``/``Exception`` message becomes an ``error`` event — the
    session must never continue as if the vendor were still listening.
    """
    kind = message.get("type")
    if kind in ("Metadata", "UtteranceEnd", "SpeechStarted", None):
        return None
    if kind in ("Error", "Exception"):
        detail = message.get("description") or message.get("message") or kind
        return StreamEvent("error", {"code": "PROVIDER_STREAM_ERROR", "message": str(detail)})

    if kind != "Results":
        return StreamEvent(
            "error",
            {
                "code": "PROVIDER_STREAM_ERROR",
                "message": f"deepgram sent an unrecognized message type {kind!r}",
            },
        )

    alternatives = ((message.get("channel") or {}).get("alternatives")) or [{}]
    alternative = alternatives[0] if alternatives else {}
    text = str(alternative.get("transcript") or "").strip()
    start_s = float(message.get("start") or fallback_start_s)
    duration_s = float(message.get("duration") or 0.0)
    end_s = start_s + duration_s
    words = _words_from_alternative(alternative)

    if message.get("is_final"):
        try:
            segments = [
                TranscriptSegment(
                    start=words[0].start if words else round(start_s, 3),
                    end=words[-1].end if words else round(end_s, 3),
                    text=text,
                    words=words,
                ).model_dump()
            ] if text else []
        except Exception as exc:  # a transcript the schema cannot represent
            return StreamEvent(
                "error",
                {
                    "code": "PROVIDER_STREAM_ERROR",
                    "message": f"deepgram returned a segment the schema rejects: {exc}",
                },
            )
        payload: dict[str, Any] = {
            "text": text,
            "segments": segments,
            "window": {"start_s": round(start_s, 3), "end_s": round(end_s, 3)},
        }
        return StreamEvent("final", payload)

    return StreamEvent(
        "partial",
        {
            "text": text,
            "segments": [],
            "window": {"start_s": round(start_s, 3), "end_s": round(end_s, 3)},
            "is_final": False,
        },
    )


class DeepgramStreamSession:
    """A Deepgram real-time session, speaking the platform's wire contract.

    Owns exactly one vendor socket. ``send`` writes PCM16 frames; ``events``
    yields platform events until the vendor stops sending or the socket
    closes; ``close`` asks the vendor to flush and then closes. Every
    failure ends the event iterator with an ``error`` event — the caller
    never sees a silent stop.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str,
        connect_timeout_s: float = _OPEN_TIMEOUT_S,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._model = model
        self._connect_timeout_s = connect_timeout_s
        self._socket: Any | None = None
        self._closed = False

    async def __aenter__(self) -> DeepgramStreamSession:
        self._socket = await asyncio.wait_for(
            websockets.connect(
                self._url,
                additional_headers={"Authorization": f"Token {self._api_key}"},
                max_size=None,
            ),
            timeout=self._connect_timeout_s,
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def send(self, pcm16: bytes) -> None:
        if self._socket is None or self._closed:
            raise ProviderError("deepgram stream is not open")
        await self._socket.send(pcm16)

    async def events(self) -> AsyncIterator[StreamEvent]:
        assert self._socket is not None, "open_stream() before events()"
        try:
            async for raw in self._socket:
                if isinstance(raw, bytes):
                    continue  # binary replies carry no transcript
                try:
                    message = json.loads(raw)
                except ValueError as exc:
                    yield StreamEvent(
                        "error",
                        {
                            "code": "PROVIDER_STREAM_ERROR",
                            "message": f"deepgram sent a non-JSON frame: {exc}",
                        },
                    )
                    return
                event = stream_event_from_message(message)
                if event is not None:
                    yield event
        except websockets.exceptions.ConnectionClosed:
            return  # the vendor hung up; the session is simply over
        except Exception as exc:  # pragma: no cover - transport-specific
            yield StreamEvent(
                "error",
                {"code": "PROVIDER_STREAM_ERROR", "message": f"{type(exc).__name__}: {exc}"},
            )

    async def close(self) -> None:
        if self._socket is None or self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self._socket.send(json.dumps({"type": "CloseStream"}))
        with contextlib.suppress(Exception):
            await self._socket.close()
