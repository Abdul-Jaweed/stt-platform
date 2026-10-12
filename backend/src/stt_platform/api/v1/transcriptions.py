"""POST /v1/transcriptions and the job lifecycle endpoints.

Short audio (<= ``max_sync_duration_s``) is processed synchronously when
object storage is configured, so the caller gets a terminal job with a
201. Longer audio (up to ``max_async_duration_s``) is preserved in object
storage and enqueued behind Temporal; the caller gets 202 and polls the
job. Without object storage the endpoint keeps the Phase 1 sync-only
contract. The URL contract is identical for both paths.

Very large files skip the multipart hop: ``POST
/v1/transcriptions/uploads/presign`` hands out a one-shot PUT URL, and
the job is then created with ``object_key`` instead of ``file``.
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
from stt_platform.api.ratelimit import enforce_rate_limit
from stt_platform.api.security import get_scope
from stt_platform.audio.limits import check_provider_limits
from stt_platform.audio.validation import validate_upload
from stt_platform.core.config import Settings
from stt_platform.domain.errors import (
    AudioTooLong,
    AudioValidationError,
    InvalidRequest,
    ProviderNotFound,
    StorageNotConfigured,
    UploadTooLarge,
)
from stt_platform.schemas.jobs import JobRecord, PresignedUpload, PresignRequest
from stt_platform.schemas.live import LivePreviewResponse
from stt_platform.schemas.transcript import TranscriptResult
from stt_platform.services.jobs import JobService
from stt_platform.services.live import preview_transcription
from stt_platform.storage.keys import original_key

router = APIRouter(
    prefix="/transcriptions",
    tags=["transcriptions"],
    dependencies=[Depends(enforce_rate_limit)],
)

_CHUNK_BYTES = 1024 * 1024
# Multipart framing overhead allowance for the Content-Length pre-check.
_CONTENT_LENGTH_SLACK_BYTES = 64 * 1024


@router.post("/uploads/presign", response_model=PresignedUpload)
def presign_upload(
    body: PresignRequest,
    request: Request,
    scope: str = Depends(get_scope),
) -> PresignedUpload:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise StorageNotConfigured("presigned uploads require object storage")
    settings: Settings = request.app.state.settings
    key = original_key(scope, uuid.uuid4().hex, Path(body.filename or "").suffix)
    url = store.presign_put(
        store.bucket_audio, key, expires_s=settings.presign_expiry_s
    )
    return PresignedUpload(
        object_key=key,
        upload_url=url,
        expires_in_s=settings.presign_expiry_s,
    )


@router.post("/preview", response_model=LivePreviewResponse)
def preview_audio(
    request: Request,
    file: UploadFile = File(...),
    provider: str | None = Form(None),
    language: str | None = Form(None),
    scope: str = Depends(get_scope),
) -> LivePreviewResponse:
    """Transcribe the audio captured so far, transiently.

    Called every few seconds while a browser is recording. No job is created,
    no object is stored, nothing is written to the database — and only the
    local provider is allowed (``LIVE_PREVIEW_LOCAL_ONLY`` otherwise). The
    uploaded bytes are deleted before the response leaves.
    """
    settings: Settings = request.app.state.settings
    registry = request.app.state.registry

    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix
    if not suffix[1:].isalnum():
        suffix = ""
    dest = settings.temp_dir / f"preview-{uuid.uuid4().hex}{suffix}"

    size = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = file.file.read(_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise UploadTooLarge(
                        f"upload exceeds the {settings.max_upload_bytes}-byte limit"
                    )
                out.write(chunk)
        return preview_transcription(
            audio_path=dest,
            provider=registry.get(provider or None),
            language=language or None,
            settings=settings,
        )
    finally:
        dest.unlink(missing_ok=True)


@router.post("", status_code=201, response_model=JobRecord)
def create_transcription(
    request: Request,
    response: Response,
    file: UploadFile | None = File(None),
    object_key: str | None = Form(None),
    provider: str | None = Form(None),
    language: str | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    service: JobService = Depends(get_service),
    scope: str = Depends(get_scope),
) -> JobRecord:
    settings: Settings = request.app.state.settings
    store = getattr(request.app.state, "store", None)

    if (file is None) == (object_key is None):
        raise InvalidRequest("exactly one of file or object_key is required")

    if object_key is not None:
        job = service.create_async(
            object_key=object_key,
            provider_id=provider or None,
            language=language or None,
            idempotency_key=idempotency_key or None,
            scope=scope,
        )
        response.status_code = 202
        response.headers["Location"] = f"/v1/transcriptions/{job.id}"
        return job

    assert file is not None  # exclusivity checked above
    declared_length = request.headers.get("content-length")
    if (
        declared_length
        and declared_length.isdigit()
        and int(declared_length) > settings.max_upload_bytes + _CONTENT_LENGTH_SLACK_BYTES
    ):
        raise UploadTooLarge(
            f"upload exceeds the {settings.max_upload_bytes}-byte limit"
        )

    requires_async = service.provider_requires_async(provider or None)
    if requires_async and store is None:
        raise StorageNotConfigured(
            "the requested provider only has an asynchronous API, "
            "which requires object storage"
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
    try:
        info = validate_upload(
            dest,
            settings,
            max_duration_s=(
                settings.max_async_duration_s if store is not None else None
            ),
        )
        # The platform's caps are not the provider's: a file this platform
        # happily stores may be past the chosen vendor's documented limit,
        # and the refusal should name the vendor before any bytes leave.
        try:
            resolved_id, capabilities = service.provider_limits(provider or None)
        except ProviderNotFound:
            resolved_id, capabilities = None, None  # the create path owns this 422
        if capabilities is not None:
            check_provider_limits(info, capabilities, provider_id=resolved_id)
    except (AudioValidationError, UploadTooLarge, AudioTooLong):
        dest.unlink(missing_ok=True)
        raise

    if store is not None and (
        info.duration_s > settings.max_sync_duration_s or requires_async
    ):
        try:
            job = service.create_async(
                audio_path=dest,
                provider_id=provider or None,
                language=language or None,
                idempotency_key=idempotency_key or None,
                scope=scope,
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
            scope=scope,
        )
    response.headers["Location"] = f"/v1/transcriptions/{job.id}"
    return job


@router.get("/{job_id}", response_model=JobRecord)
def get_transcription(
    job_id: str,
    service: JobService = Depends(get_service),
    scope: str = Depends(get_scope),
) -> JobRecord:
    return service.get(job_id, scope)


@router.get("/{job_id}/result", response_model=TranscriptResult)
def get_transcription_result(
    job_id: str,
    service: JobService = Depends(get_service),
    scope: str = Depends(get_scope),
) -> TranscriptResult:
    return service.get_result(job_id, scope)


@router.post("/{job_id}/retry", response_model=JobRecord)
def retry_transcription(
    job_id: str,
    service: JobService = Depends(get_service),
    scope: str = Depends(get_scope),
) -> JobRecord:
    record = service.get(job_id, scope)
    local = bool(record.audio_path) and Path(record.audio_path).is_file()
    if record.original_object_key and not local:
        return service.retry_async(job_id, scope)
    return service.retry(job_id, scope)


@router.post("/{job_id}/cancel", response_model=JobRecord)
def cancel_transcription(
    job_id: str,
    service: JobService = Depends(get_service),
    scope: str = Depends(get_scope),
) -> JobRecord:
    return service.cancel(job_id, scope)


@router.delete("/{job_id}", status_code=204)
def delete_transcription(
    job_id: str,
    service: JobService = Depends(get_service),
    scope: str = Depends(get_scope),
) -> Response:
    """Delete a job and everything stored for it (objects, then rows)."""
    service.delete(job_id, scope)
    return Response(status_code=204)
