"""POST /v1/transcriptions and the job lifecycle endpoints.

Phase 1 is a synchronous short-audio path: by the time POST returns, the
job is terminal (SUCCEEDED or FAILED). The URL contract does not change
when Phase 2 moves execution behind Temporal.
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
from fastapi.responses import JSONResponse

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

    # Pre-flight: reject bad media before a job exists (sync client UX);
    # the service re-validates inside the VALIDATING state regardless.
    try:
        validate_upload(dest, settings)
    except (AudioValidationError, UploadTooLarge, AudioTooLong):
        dest.unlink(missing_ok=True)
        raise

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
    return service.retry(job_id)


@router.post("/{job_id}/cancel")
def cancel_transcription(job_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "code": "NOT_IMPLEMENTED",
                "message": (
                    "cancellation arrives with the asynchronous pipeline "
                    "(Phase 2); synchronous jobs are terminal when POST returns"
                ),
            }
        },
    )
