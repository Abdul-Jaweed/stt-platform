"""Faster-Whisper provider adapter (local, CUDA).

Import order matters on Windows: ``stt_platform.core.cuda`` registers the
cuBLAS/cuDNN DLL directories before ctranslate2 loads them.

The engine is constructed lazily on first use and reused for the process
lifetime (one load per worker lifecycle, spec §9). The model cache is the
canonical ``models/faster-whisper`` directory via explicit
``download_root`` — never the user-level HF cache (ADR-003).

Long inputs (``STT_CHUNK_SECONDS`` > 0) are decoded once and transcribed
chunk by chunk: each slice is shifted back onto the original timeline via
``domain.timeline.shift_segments`` -- timestamps are never re-derived.
"""
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

import stt_platform.core.cuda  # noqa: F401  (Windows DLL registration side effect)
from stt_platform.core.config import Settings, get_settings
from stt_platform.domain.errors import ProviderError
from stt_platform.domain.timeline import shift_segments
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

#: Everything downstream (streaming decoder, chunking) agrees on this rate.
SAMPLE_RATE_HZ = 16000


class FasterWhisperProvider:
    id = "faster-whisper"
    name = "Faster-Whisper (local)"
    is_local = True

    def __init__(
        self,
        settings: Settings | None = None,
        engine_factory: Callable[..., object] | None = None,
        semaphore: threading.BoundedSemaphore | None = None,
        audio_decoder: Callable[[Path], np.ndarray] | None = None,
        duration_probe: Callable[[Path], float] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._engine_factory = engine_factory
        self._audio_decoder = audio_decoder
        self._duration_probe = duration_probe
        self._engine = None
        # GPU work is serialized by this semaphore (worker activity slots
        # cover GPU + managed + overhead; this is the real GPU gate).
        self._semaphore = semaphore or threading.BoundedSemaphore(
            max(1, self._settings.gpu_concurrency)
        )

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
            streaming=True,
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
                # verified end-to-end by scripts/e2e_phase8.py: the streamed
                # take and the same audio as a job agreed token for token
                # (WER 0.179 on the 10 s sample, live stack, large-v3-turbo)
                "streaming": "verified",
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

        chunk_seconds = self._settings.chunk_seconds
        if chunk_seconds > 0 and self._is_longer_than_chunk(
            request.audio_path, chunk_seconds
        ):
            return self._transcribe_chunked(engine, request, chunk_seconds)

        started = time.perf_counter()
        with self._semaphore:
            raw_segments, info = self._run_engine(
                engine, str(request.audio_path), request.language
            )
        processing_duration_s = time.perf_counter() - started

        return _to_provider_transcript(
            raw_segments,
            info,
            model=self._settings.whisper_model,
            processing_duration_s=processing_duration_s,
        )

    def transcribe_pcm(
        self, samples: np.ndarray, language: str | None = None
    ) -> ProviderTranscript:
        """Transcribe one 16 kHz mono float window that is already in memory.

        The streaming session decodes the stream and cuts the utterances
        itself, so the samples go straight to the engine: no ffmpeg, and no
        ``vad_filter`` -- a second VAD would re-cut a window the session's
        segmenter deliberately kept whole, silently dropping speech. Everything
        downstream (canonical mapping, error translation, the GPU semaphore)
        is shared with ``transcribe``, because a streamed window and an
        uploaded file are the same audio to the model.
        """
        engine = self._get_engine()
        import time

        started = time.perf_counter()
        with self._semaphore:
            raw_segments, info = self._run_engine(
                engine, samples, language or None, vad_filter=False
            )
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

    def _is_longer_than_chunk(self, audio_path: Path, chunk_seconds: float) -> bool:
        probe = self._duration_probe or _default_duration_probe
        return probe(audio_path) > chunk_seconds

    def _transcribe_chunked(
        self, engine, request: TranscriptionRequest, chunk_seconds: float
    ) -> ProviderTranscript:
        """Decode once, transcribe each slice, shift every slice back.

        A word crossing a chunk boundary may be clipped or repeated -- the
        boundary is a fixed sample offset, not a silence. Timestamps stay
        exact: each slice's real transcript is shifted by the slice's own
        offset, never re-derived from scratch.
        """
        import time

        decode = self._audio_decoder or _default_audio_decoder
        audio = decode(request.audio_path)
        total_samples = int(audio.shape[0])
        chunk_samples = max(1, round(chunk_seconds * SAMPLE_RATE_HZ))

        started = time.perf_counter()
        if total_samples <= chunk_samples:
            # The probe said "long"; the decode says otherwise: one call.
            with self._semaphore:
                raw_segments, info = self._run_engine(
                    engine, audio, request.language
                )
            return _to_provider_transcript(
                raw_segments,
                info,
                model=self._settings.whisper_model,
                processing_duration_s=time.perf_counter() - started,
            )

        segments: list[TranscriptSegment] = []
        language: str | None = None
        language_probability: float | None = None
        with self._semaphore:
            for start in range(0, total_samples, chunk_samples):
                end = min(start + chunk_samples, total_samples)
                raw_segments, info = self._run_engine(
                    engine, audio[start:end], request.language
                )
                if language is None:
                    # Language is detected once, on the first slice: every
                    # slice must be transcribed in one language or the merged
                    # transcript would mix scripts mid-stream.
                    language = getattr(info, "language", None)
                    language_probability = getattr(info, "language_probability", None)
                segments.extend(
                    shift_segments(
                        _to_segments(raw_segments), start / SAMPLE_RATE_HZ
                    )
                )
        processing_duration_s = time.perf_counter() - started

        return ProviderTranscript(
            text=" ".join(s.text for s in segments).strip(),
            language=language,
            language_probability=language_probability,
            segments=segments,
            words_available=any(s.words for s in segments),
            model=self._settings.whisper_model,
            model_version=None,
            audio_duration_s=round(total_samples / SAMPLE_RATE_HZ, 3),
            processing_duration_s=round(processing_duration_s, 3),
        )

    def _run_engine(self, engine, audio, language, *, vad_filter: bool = True):
        """One engine call with the platform's parameters and error mapping.

        Shared by the single-pass path, the streamed-window path, and every
        chunk, so a streamed window, a chunk and a whole file are the same
        audio to the model.
        """
        try:
            segments_iter, info = engine.transcribe(
                audio,
                language=language,
                beam_size=5,
                vad_filter=vad_filter,
                word_timestamps=True,
                condition_on_previous_text=False,
            )
            # Inference executes as the generator is consumed.
            return list(segments_iter), info
        except ValueError as exc:
            raise ProviderError(
                "DECODE_FAILED", f"could not decode audio: {exc}", retryable=False
            ) from exc
        except Exception as exc:
            raise ProviderError(
                "INFERENCE_FAILED", f"inference failed: {exc}", retryable=True
            ) from exc


def _default_engine_factory(model: str, **kwargs):
    from faster_whisper import WhisperModel  # deferred: heavy import

    return WhisperModel(model, cpu_threads=4, **kwargs)


def _default_audio_decoder(path: Path) -> np.ndarray:
    """Decode a file to 16 kHz mono float32 with the engine's own decoder."""
    from faster_whisper.audio import decode_audio  # deferred: heavy import

    return decode_audio(str(path))


def _default_duration_probe(path: Path) -> float:
    """Cheap duration check (ffprobe) before a decode is worth it."""
    from stt_platform.audio.inspect import inspect_audio  # deferred

    return inspect_audio(Path(path)).duration_s


def _to_segments(raw_segments: list) -> list[TranscriptSegment]:
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
    return segments


def _to_provider_transcript(
    raw_segments: list,
    info,
    *,
    model: str,
    processing_duration_s: float,
) -> ProviderTranscript:
    segments = _to_segments(raw_segments)

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
