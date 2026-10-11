"""Error mapping: domain exceptions -> structured HTTP responses.

Mapping pinned in the Phase 1 plan (T8):
UNDECODABLE->415 UNSUPPORTED_MEDIA, UploadTooLarge->413, AudioTooLong->422,
ProviderNotFound->422, JobNotFound->404, JobNotReady->409,
IdempotencyConflict->409, InvalidTransition->409, ProviderError->502,
request validation->422 VALIDATION_ERROR, anything else->500 INTERNAL_ERROR.
Every error body is ``{"error": {"code", "message"}}``.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from stt_platform.domain.errors import (
    AudioTooLong,
    AudioValidationError,
    DatasetNotFound,
    DatasetNotReady,
    EvaluationTooLarge,
    IdempotencyConflict,
    InvalidObjectKey,
    InvalidRequest,
    InvalidTransition,
    JobNotFound,
    JobNotReady,
    ProviderError,
    ProviderNotFound,
    StorageNotConfigured,
    UploadTooLarge,
)
from stt_platform.persistence.evaluations import EvaluationNotFound

ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    AudioValidationError: (415, "UNSUPPORTED_MEDIA"),
    DatasetNotFound: (404, "DATASET_NOT_FOUND"),
    DatasetNotReady: (409, "DATASET_NOT_READY"),
    UploadTooLarge: (413, "UPLOAD_TOO_LARGE"),
    AudioTooLong: (422, "AUDIO_TOO_LONG"),
    ProviderNotFound: (422, "PROVIDER_NOT_FOUND"),
    JobNotFound: (404, "JOB_NOT_FOUND"),
    JobNotReady: (409, "JOB_NOT_READY"),
    IdempotencyConflict: (409, "IDEMPOTENCY_CONFLICT"),
    InvalidTransition: (409, "INVALID_TRANSITION"),
    ProviderError: (502, "PROVIDER_ERROR"),
    StorageNotConfigured: (503, "STORAGE_NOT_CONFIGURED"),
    InvalidObjectKey: (422, "INVALID_OBJECT_KEY"),
    InvalidRequest: (422, "VALIDATION_ERROR"),
    EvaluationTooLarge: (422, "EVALUATION_TOO_LARGE"),
    EvaluationNotFound: (404, "EVALUATION_NOT_FOUND"),
}


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _handler(status_code: int, code: str):
    def handle(request: Request, exc: Exception) -> JSONResponse:
        message = getattr(exc, "message", None) or str(exc)
        return _error_response(status_code, code, message)

    return handle


def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = "; ".join(str(e.get("msg", "invalid")) for e in exc.errors()[:3])
    return _error_response(
        422, "VALIDATION_ERROR", f"request validation failed: {details}"
    )


def _internal_handler(request: Request, exc: Exception) -> JSONResponse:
    # Deliberately opaque: internals are never leaked to clients.
    return _error_response(500, "INTERNAL_ERROR", "internal server error")


def register_exception_handlers(app: FastAPI) -> None:
    for exc_type, (status_code, code) in ERROR_MAP.items():
        app.add_exception_handler(exc_type, _handler(status_code, code))
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(Exception, _internal_handler)
