"""Faster-Whisper provider adapter (local, CUDA).

Import order matters on Windows: ``stt_platform.core.cuda`` registers the
cuBLAS/cuDNN DLL directories before ctranslate2 loads them.

The engine is constructed lazily on first use and reused for the process
lifetime (one load per worker lifecycle, spec §9). The model cache is the
canonical ``models/faster-whisper`` directory via explicit
``download_root`` — never the user-level HF cache (ADR-003).
"""
from typing import Callable

import stt_platform.core.cuda  # noqa: F401  (Windows DLL registration side effect)

from stt_platform.core.config import Settings, get_settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import ProviderTranscript, TranscriptionRequest
from stt_platform.schemas.providers import ProviderCapabilities, ProviderHealth
from stt_platform.schemas.transcript import TranscriptSegment, TranscriptWord

# OpenAI Whisper's multilingual set (the exact count varies by release
# between 99 and 100 codes; tests assert >= 95 and spot-check en/hi).
WHISPER_LANGUAGE_CODES: list[str] = [
    "en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr",
    "pl", "ca", "nl", "ar", "sv", "it", "id", "hi", "fi", "vi",
    "he", "uk", "el", "ms", "cs", "ro", "da", "hu", "ta", "no",
    "th", "ur", "hr", "bg", "lt", "la", "mi", "ml", "cy", "sk",
    "te", "fa", "lv", "bn", "sr", "az", "sl", "kn", "et", "mk",
    "br", "eu", "is", "hy", "ne", "mn", "bs", "kk", "sq", "sw",
    "gl", "mr", "pa", "si", "km", "sn", "yo", "so", "af", "oc",
    "ka", "be", "tg", "sd", "gu", "am", "yi", "lo", "uz", "fo",
    "ht", "ps", "tk", "nn", "mt", "sa", "lb", "my", "bo", "tl",
    "mg", "as", "tt", "haw", "ln", "ha", "ba", "jw", "su", "yue",
]


class FasterWhisperProvider:
    id = "faster-whisper"
    name = "Faster-Whisper (local)"
    is_local = True

    def __init__(
        self,
        settings: Settings | None = None,
        engine_factory: Callable[..., object] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._engine_factory = engine_factory
        self._engine = None

    # ------------------------------------------------------------------
    # contract
    # ------------------------------------------------------------------
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            languages=list(WHISPER_LANGUAGE_CODES),
            language_detection=True,
            segment_timestamps=True,
            word_timestamps=True,
            diarization=False,
            streaming=False,
            batch=True,
            async_processing=False,
            confidence_scores=True,
            max_file_bytes=None,
            max_duration_s=None,
            formats=["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "webm"],
            capability_sources={
                # verified by tests/gpu/test_faster_whisper_gpu.py
                "segment_timestamps": "verified",
                "word_timestamps": "verified",
                "confidence_scores": "verified",
            },
        )

    def health(self) -> ProviderHealth:
        try:
            import ctranslate2

            count = ctranslate2.get_cuda_device_count()
        except Exception as exc:  # pragma: no cover - environment dependent
            return ProviderHealth(status="unavailable", detail=str(exc))
        detail = f"cuda_devices={count}"
        if self._settings.device == "cuda" and count < 1:
            return ProviderHealth(
                status="degraded",
                detail="no CUDA device; set STT_DEVICE=cpu to run on CPU",
            )
        return ProviderHealth(status="ok", detail=detail)

    def transcribe(self, request: TranscriptionRequest) -> ProviderTranscript:
        engine = self._get_engine()
        import time

        started = time.perf_counter()
        try:
            segments_iter, info = engine.transcribe(
                str(request.audio_path),
                language=request.language,
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
                condition_on_previous_text=False,
            )
            # Inference executes as the generator is consumed.
            raw_segments = list(segments_iter)
        except ValueError as exc:
            raise ProviderError(
                "DECODE_FAILED", f"could not decode audio: {exc}", retryable=False
            ) from exc
        except Exception as exc:
            raise ProviderError(
                "INFERENCE_FAILED", f"inference failed: {exc}", retryable=True
            ) from exc
        processing_duration_s = time.perf_counter() - started

        return _to_provider_transcript(
            raw_segments,
            info,
            model=self._settings.whisper_model,
            processing_duration_s=processing_duration_s,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _get_engine(self):
        if self._engine is None:
            self._settings.apply_runtime_env()
            factory = self._engine_factory or _default_engine_factory
            self._engine = factory(
                self._settings.whisper_model,
                device=self._settings.device,
                compute_type=self._settings.compute_type,
                download_root=str(self._settings.model_dir),
            )
        return self._engine


def _default_engine_factory(model: str, **kwargs):
    from faster_whisper import WhisperModel  # deferred: heavy import

    return WhisperModel(model, cpu_threads=4, **kwargs)


def _to_provider_transcript(
    raw_segments: list,
    info,
    *,
    model: str,
    processing_duration_s: float,
) -> ProviderTranscript:
    segments: list[TranscriptSegment] = []
    for raw in raw_segments:
        words: list[TranscriptWord] = []
        for word in raw.words or []:
            start = round(word.start, 3)
            end = round(word.end, 3)
            if end <= start:
                # Whisper occasionally emits zero-length or inverted words
                # (observed on real long audio). The canonical schema
                # requires end > start: the unsupported timestamp is
                # dropped -- never clamped or invented. Segment timestamps
                # are NOT filtered: a degenerate segment fails the job
                # loudly rather than silently dropping transcript text.
                continue
            words.append(
                TranscriptWord(
                    start=start,
                    end=end,
                    text=word.word.strip(),
                    confidence=getattr(word, "probability", None),
                )
            )
        segments.append(
            TranscriptSegment(
                start=round(raw.start, 3),
                end=round(raw.end, 3),
                text=raw.text.strip(),
                words=words,
            )
        )

    return ProviderTranscript(
        text=" ".join(s.text for s in segments).strip(),
        language=getattr(info, "language", None),
        language_probability=getattr(info, "language_probability", None),
        segments=segments,
        words_available=any(s.words for s in segments),
        model=model,
        model_version=None,
        audio_duration_s=float(getattr(info, "duration", 0.0)),
        processing_duration_s=round(processing_duration_s, 3),
    )
