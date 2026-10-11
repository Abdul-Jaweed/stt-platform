"""POST /v1/transcriptions and the job lifecycle endpoints.

Short audio (<= ``max_sync_duration_s``) is processed synchronously when
object storage is configured, so the caller gets a terminal job with a
201. Longer audio (up to ``max_async_duration_s``) is preserved in object
storage and enqueued behind Temporal; the caller gets 202 and polls the
job. Without object storage the endpoint keeps the Phase 1 sync-only
contract. The URL contract is identical for both paths.
"""
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Request,
    Response,
    UploadFile,
)

from stt_platform.api.deps import get_service
from stt_platform.audio.validation import validate_upload
from stt_platform.core.config import Settings
from stt_platform.domain.errors import (
    AudioTooLong,
    AudioValidationError,
    UploadTooLarge,
)
from stt_platform.schemas.jobs import JobRecord
from stt_platform.schemas.transcript import TranscriptResult
from stt_platform.services.jobs import JobService

router = APIRouter(prefix="/transcriptions", tags=["transcriptions"])

_CHUNK_BYTES = 1024 * 1024
# Multipart framing overhead allowance for the Content-Length pre-check.
_CONTENT_LENGTH_SLACK_BYTES = 64 * 1024


@router.post("", status_code=201, response_model=JobRecord)
def create_transcription(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    provider: str | None = Form(None),
    language: str | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    service: JobService = Depends(get_service),
) -> JobRecord:
    settings: Settings = request.app.state.settings

    declared_length = request.headers.get("content-length")
    if (
        declared_length
        and declared_length.isdigit()
        and int(declared_length) > settings.max_upload_bytes + _CONTENT_LENGTH_SLACK_BYTES
    ):
        raise UploadTooLarge(
            f"upload exceeds the {settings.max_upload_bytes}-byte limit"
        )

    settings.audio_store_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix
    if not suffix[1:].isalnum():
        suffix = ""
    dest = settings.audio_store_dir / f"{uuid.uuid4().hex}{suffix}"

    size = 0
    too_large = False
    with open(dest, "wb") as out:
        while True:
            chunk = file.file.read(_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                too_large = True
                break
            out.write(chunk)
    if too_large:
        dest.unlink(missing_ok=True)
        raise UploadTooLarge(
            f"upload exceeds the {settings.max_upload_bytes}-byte limit"
        )

    # Pre-flight: reject bad media before a job exists (client UX); the
    # service re-validates inside the VALIDATING state regardless. The cap
    # applied here is the async one when storage is available, since the
    # request may legitimately route to the asynchronous path.
    store = getattr(request.app.state, "store", None)
    try:
        info = validate_upload(
            dest,
            settings,
            max_duration_s=(
                settings.max_async_duration_s if store is not None else None
            ),
        )
    except (AudioValidationError, UploadTooLarge, AudioTooLong):
        dest.unlink(missing_ok=True)
        raise

    if store is not None and info.duration_s > settings.max_sync_duration_s:
        try:
            job = service.create_async(
                audio_path=dest,
                provider_id=provider or None,
                language=language or None,
                idempotency_key=idempotency_key or None,
            )
        finally:
            # The object-storage copy is canonical for asynchronous jobs.
            dest.unlink(missing_ok=True)
        response.status_code = 202
    else:
        job = service.create_and_process(
            audio_path=dest,
            provider_id=provider or None,
            language=language or None,
            idempotency_key=idempotency_key or None,
        )
    response.headers["Location"] = f"/v1/transcriptions/{job.id}"
    return job


@router.get("/{job_id}", response_model=JobRecord)
def get_transcription(
    job_id: str, service: JobService = Depends(get_service)
) -> JobRecord:
    return service.get(job_id)


@router.get("/{job_id}/result", response_model=TranscriptResult)
def get_transcription_result(
    job_id: str, service: JobService = Depends(get_service)
) -> TranscriptResult:
    return service.get_result(job_id)


@router.post("/{job_id}/retry", response_model=JobRecord)
def retry_transcription(
    job_id: str, service: JobService = Depends(get_service)
) -> JobRecord:
    record = service.get(job_id)
    local = bool(record.audio_path) and Path(record.audio_path).is_file()
    if record.original_object_key and not local:
        return service.retry_async(job_id)
    return service.retry(job_id)


@router.post("/{job_id}/cancel", response_model=JobRecord)
def cancel_transcription(
    job_id: str, service: JobService = Depends(get_service)
) -> JobRecord:
    return service.cancel(job_id)
