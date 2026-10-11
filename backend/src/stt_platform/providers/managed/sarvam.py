"""Sarvam AI batch speech-to-text adapter (asynchronous provider contract).

Flow verified against vendor documentation at implementation time:

* https://docs.sarvam.ai/api-reference/speech-to-text/stt/job/initiate
* https://docs.sarvam.ai/api-reference/speech-to-text/stt/job/upload
* https://docs.sarvam.ai/api-reference/speech-to-text/stt/job/start
* https://docs.sarvam.ai/api-reference/speech-to-text/stt/job/status
* https://docs.sarvam.ai/api-reference/speech-to-text/stt/job/download
* https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/batch-api

Submit:  POST /speech-to-text/job/v1                -> job_id
         POST /speech-to-text/job/v1/upload-files   -> presigned PUT url
         PUT  <presigned>                           -> audio bytes
         POST /speech-to-text/job/v1/{job_id}/start
Poll:    GET  /speech-to-text/job/v1/{job_id}/status
         -> job_state in {Accepted, Pending, Running, Completed, Failed}
         -> on Completed: POST /speech-to-text/job/v1/download-files
            -> presigned GET url -> output JSON.

Auth is the ``api-subscription-key`` header; presigned storage URLs are
called **without** the credential (the key must never travel to a storage
host). The output JSON carries ``transcript``, ``language_code``,
``timestamps.chunks`` and (with diarization) ``diarized_transcript.entries``
with ``speaker_id`` strings — it has **no word-level timings**, so results
are segment granularity and ``words_available`` stays False. Degenerate
entries are dropped with a warning; the vendor's ``language_code`` is kept
verbatim. ``audio_duration_s`` is probed locally with ffprobe at submit
time and carried on the job reference. Sarvam documents no cancel
endpoint for batch jobs, so ``cancel`` reports unsupported.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from stt_platform.audio.inspect import inspect_audio
from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import (
    ProviderTranscript,
    RemoteJobRef,
    RemoteJobState,
    TranscriptionRequest,
)
from stt_platform.providers.managed.http_base import ManagedHttpClient
from stt_platform.schemas.providers import ProviderCapabilities, ProviderHealth
from stt_platform.schemas.transcript import TranscriptSegment

MODEL = "saaras:v4"
_JOB_BASE = "/speech-to-text/job/v1"

# Documented BCP-47 values (saaras:v4; the v3-only extras are included
# because saaras:v4 accepts the same set).
_LANGUAGE_CODES = {
    "hi-IN", "bn-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN",
    "te-IN", "en-IN", "gu-IN", "as-IN", "ur-IN", "ne-IN", "kok-IN", "ks-IN",
    "sd-IN", "sa-IN", "sat-IN", "mni-IN", "brx-IN", "mai-IN", "doi-IN",
}

_PENDING_STATES = {"accepted", "pending", "running"}
_DONE_STATES = {"completed", "partially_completed", "partiallycompleted"}
_FAILED_STATES = {"failed", "rejected"}


class SarvamProvider:
    id = "sarvam"
    name = "Sarvam AI (saaras batch)"
    is_local = False

    def __init__(
        self,
        settings: Settings,
        semaphore: threading.BoundedSemaphore | None = None,
    ) -> None:
        self._settings = settings
        self._client = ManagedHttpClient(
            base_url=settings.sarvam_base_url,
            api_key=settings.sarvam_api_key or "",
            auth_header="api-subscription-key",
            auth_scheme=None,
            timeout_s=settings.provider_timeout_s,
            connect_timeout_s=settings.provider_connect_timeout_s,
            max_response_bytes=settings.max_provider_response_bytes,
            semaphore=semaphore,
        )
        # Presigned storage transfers: same timeouts and size guard, but no
        # credential and no managed-concurrency slot (the API calls hold it).
        self._storage = ManagedHttpClient(
            base_url=settings.sarvam_base_url,
            api_key="",
            auth_header="api-subscription-key",
            auth_scheme=None,
            timeout_s=settings.provider_timeout_s,
            connect_timeout_s=settings.provider_connect_timeout_s,
            max_response_bytes=settings.max_provider_response_bytes,
        )

    # ------------------------------------------------------------------
    # contract
    # ------------------------------------------------------------------
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            languages=sorted(_LANGUAGE_CODES),
            language_detection=True,
            segment_timestamps=True,
            word_timestamps=False,
            diarization=True,
            streaming=False,
            batch=True,
            async_processing=True,
            confidence_scores=False,
            formats=["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "webm"],
            capability_sources={
                "async_processing": "documented",
                "segment_timestamps": "documented",
                "diarization": "documented",
                "language_detection": "documented",
            },
        )

    def health(self) -> ProviderHealth:
        if not self._settings.sarvam_api_key:
            return ProviderHealth(
                status="unavailable", detail="STT_SARVAM_API_KEY not set"
            )
        return ProviderHealth(
            status="ok", detail="credential configured; reachability unverified"
        )

    def submit(self, request: TranscriptionRequest) -> RemoteJobRef:
        path = Path(request.audio_path)
        duration_s = inspect_audio(path).duration_s
        filename = path.name
        params = {
            "model": MODEL,
            "language_code": _language_code(request.language),
            "with_timestamps": True,
            "with_diarization": True,
        }
        init = self._client.request(
            "POST", _JOB_BASE, json={"job_parameters": params}
        )
        job_id = (init or {}).get("job_id") if isinstance(init, dict) else None
        if not isinstance(job_id, str) or not job_id:
            raise ProviderError(
                "MALFORMED_RESPONSE",
                "Sarvam job initiation returned no job_id",
                retryable=False,
            )

        uploads = self._client.request(
            "POST",
            f"{_JOB_BASE}/upload-files",
            json={"job_id": job_id, "files": [filename]},
        )
        file_url = _presigned_url(uploads, "upload_urls", filename)
        self._storage.request(
            "PUT",
            file_url,
            content=path.read_bytes(),
            headers={
                "Content-Type": "application/octet-stream",
                # Required by Azure block-blob SAS uploads; ignored elsewhere.
                "x-ms-blob-type": "BlockBlob",
            },
        )
        self._client.request("POST", f"{_JOB_BASE}/{job_id}/start", json={})
        return RemoteJobRef(
            provider_job_id=job_id,
            raw={"filename": filename, "duration_s": duration_s},
        )

    def poll(self, ref: RemoteJobRef) -> RemoteJobState:
        status = self._client.request(
            "GET", f"{_JOB_BASE}/{ref.provider_job_id}/status"
        )
        if not isinstance(status, dict):
            raise ProviderError(
                "MALFORMED_RESPONSE",
                "Sarvam status returned a non-JSON payload",
                retryable=False,
            )
        state = str(status.get("job_state") or "").strip().lower()
        if state in _FAILED_STATES:
            return RemoteJobState(
                state="failed",
                error_code="SARVAM_JOB_FAILED",
                error_message=status.get("error_message") or "Sarvam job failed",
                retryable=True,
                raw=status,
            )
        if state not in _DONE_STATES:
            # Accepted / Pending / Running and any unknown state: keep polling.
            return RemoteJobState(state="pending", raw=status)

        started = time.perf_counter()
        payload = self._fetch_output(ref, status)
        duration_s = None
        if isinstance(ref.raw, dict):
            value = ref.raw.get("duration_s")
            if isinstance(value, (int, float)) and value > 0:
                duration_s = float(value)
        return RemoteJobState(
            state="completed",
            transcript=_normalize(
                payload,
                duration_s=duration_s,
                processing_duration_s=time.perf_counter() - started,
            ),
            raw=status,
        )

    def cancel(self, ref: RemoteJobRef) -> bool:
        """Sarvam documents no cancel endpoint for batch jobs."""
        return False

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _fetch_output(self, ref: RemoteJobRef, status: dict):
        filename = _first_output_name(status)
        if filename is None:
            raise ProviderError(
                "MALFORMED_RESPONSE",
                "Sarvam job completed without any output file",
                retryable=False,
            )
        downloads = self._client.request(
            "POST",
            f"{_JOB_BASE}/download-files",
            json={"job_id": ref.provider_job_id, "files": [filename]},
        )
        url = _presigned_url(downloads, "download_urls", filename)
        payload = self._storage.request("GET", url)
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                raise ProviderError(
                    "MALFORMED_RESPONSE",
                    "Sarvam output file is not JSON",
                    retryable=False,
                ) from exc
        if not isinstance(payload, dict):
            raise ProviderError(
                "MALFORMED_RESPONSE",
                "Sarvam output file has an unexpected shape",
                retryable=False,
            )
        return payload


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _language_code(language: str | None) -> str:
    """Map a requested language onto Sarvam's documented codes.

    ``None`` -> ``unknown`` (vendor-side auto-detect). A bare ``xx`` maps
    onto the documented ``xx-IN``; anything Sarvam cannot serve is a
    permanent error rather than a silent fallback.
    """
    if not language:
        return "unknown"
    if language in _LANGUAGE_CODES:
        return language
    candidate = f"{language}-IN"
    if candidate in _LANGUAGE_CODES:
        return candidate
    raise ProviderError(
        "UNSUPPORTED_LANGUAGE",
        f"Sarvam batch STT does not support language {language!r}",
        retryable=False,
    )


def _presigned_url(payload, field: str, filename: str) -> str:
    if not isinstance(payload, dict):
        raise ProviderError(
            "MALFORMED_RESPONSE",
            f"Sarvam {field} returned a non-JSON payload",
            retryable=False,
        )
    entry = (payload.get(field) or {}).get(filename) or {}
    url = entry.get("file_url")
    if not isinstance(url, str) or not url:
        raise ProviderError(
            "MALFORMED_RESPONSE",
            f"Sarvam {field} has no file_url for {filename!r}",
            retryable=False,
        )
    return url


def _first_output_name(status: dict) -> str | None:
    details = status.get("job_details") or []
    successful = [d for d in details if d.get("status") == "success"] or details
    for detail in successful:
        for output in detail.get("outputs") or []:
            name = output.get("file_name")
            if isinstance(name, str) and name:
                return name
    return None


def _speaker_int(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _normalize(
    payload: dict,
    *,
    duration_s: float | None,
    processing_duration_s: float,
) -> ProviderTranscript:
    warnings: list[str] = []
    segments: list[TranscriptSegment] = []

    entries = (payload.get("diarized_transcript") or {}).get("entries") or []
    for entry in entries:
        segment = _entry_segment(entry, warnings)
        if segment is not None:
            segments.append(segment)

    if not segments:
        segments = _chunk_segments(payload.get("timestamps") or {}, warnings)

    starts = [s.start for s in segments]
    if starts != sorted(starts):
        segments.sort(key=lambda s: s.start)
        warnings.append("segments reordered by start time")

    text = (payload.get("transcript") or "").strip()
    if not text and segments:
        text = " ".join(s.text for s in segments)

    if duration_s is None:
        duration_s = max((s.end for s in segments), default=0.0)

    return ProviderTranscript(
        text=text,
        language=payload.get("language_code"),
        language_probability=None,  # Sarvam batch returns no language confidence
        segments=segments,
        words_available=False,  # batch API returns no word timings
        model=MODEL,
        model_version=None,
        audio_duration_s=duration_s,
        processing_duration_s=round(processing_duration_s, 3),
        warnings=warnings,
        raw=payload,
    )


def _entry_segment(entry, warnings: list[str]) -> TranscriptSegment | None:
    if not isinstance(entry, dict):
        warnings.append("dropped malformed diarization entry")
        return None
    try:
        start = float(entry["start_time_seconds"])
        end = float(entry["end_time_seconds"])
    except (KeyError, TypeError, ValueError):
        warnings.append("dropped entry without usable timestamps")
        return None
    text = (entry.get("transcript") or "").strip()
    if not text:
        warnings.append("dropped entry with empty transcript")
        return None
    if end <= start:
        warnings.append(f"dropped degenerate entry ({start} -> {end})")
        return None
    return TranscriptSegment(
        start=round(start, 3),
        end=round(end, 3),
        text=text,
        speaker=_speaker_int(entry.get("speaker_id")),
    )


def _chunk_segments(
    timestamps: dict, warnings: list[str]
) -> list[TranscriptSegment]:
    chunks = timestamps.get("chunks") or []
    starts = timestamps.get("start_time_seconds") or []
    ends = timestamps.get("end_time_seconds") or []
    segments: list[TranscriptSegment] = []
    for index, chunk in enumerate(chunks):
        text = (chunk or "").strip()
        if not text:
            continue
        if index >= len(starts) or index >= len(ends):
            warnings.append("dropped chunk without matching timestamps")
            continue
        try:
            start = float(starts[index])
            end = float(ends[index])
        except (TypeError, ValueError):
            warnings.append("dropped chunk with unusable timestamps")
            continue
        if end <= start:
            warnings.append(f"dropped degenerate chunk ({start} -> {end})")
            continue
        segments.append(
            TranscriptSegment(start=round(start, 3), end=round(end, 3), text=text)
        )
    return segments
