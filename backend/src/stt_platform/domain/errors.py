"""Domain exception hierarchy.

Every exception carries a stable machine-readable ``code``; providers and
the API layer normalize instead of leaking native errors.
"""


class DomainError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class AudioValidationError(DomainError):
    """Audio content is missing, malformed, or unsupported.

    Codes: UNDECODABLE, EMPTY, NO_AUDIO_STREAM, ZERO_DURATION.
    """


class UploadTooLarge(DomainError):
    def __init__(self, message: str):
        super().__init__("UPLOAD_TOO_LARGE", message)


class AudioTooLong(DomainError):
    def __init__(self, message: str):
        super().__init__("AUDIO_TOO_LONG", message)


class ProviderError(DomainError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(code, message)
        self.retryable = retryable


class LivePreviewUnsupported(DomainError):
    """The live preview only runs on the local provider.

    A preview is sent every few seconds while someone is still speaking; it
    must never spend a managed vendor's quota, and local-only audio must never
    leave the machine without an explicit choice.
    """

    def __init__(self, message: str):
        super().__init__("LIVE_PREVIEW_LOCAL_ONLY", message)


class ProviderNotFound(DomainError):
    def __init__(self, provider_id: str):
        super().__init__("PROVIDER_NOT_FOUND", f"unknown provider: {provider_id!r}")


class JobNotFound(DomainError):
    def __init__(self, job_id: str):
        super().__init__("JOB_NOT_FOUND", f"job not found: {job_id!r}")


class JobNotReady(DomainError):
    def __init__(self, job_id: str, status: str):
        super().__init__(
            "JOB_NOT_READY", f"job {job_id!r} has no result (status={status})"
        )


class IdempotencyConflict(DomainError):
    def __init__(self, key: str):
        super().__init__(
            "IDEMPOTENCY_CONFLICT",
            f"idempotency key {key!r} was already used with different parameters",
        )


class InvalidTransition(DomainError):
    def __init__(self, from_status: str, to_status: str):
        super().__init__(
            "INVALID_TRANSITION", f"transition {from_status} -> {to_status} not allowed"
        )


class StorageNotConfigured(DomainError):
    """Asynchronous (Temporal-backed) jobs require object storage."""

    def __init__(self, message: str = "asynchronous jobs require object storage"):
        super().__init__("STORAGE_NOT_CONFIGURED", message)


class InvalidObjectKey(DomainError):
    """A client-supplied object key is malformed or out of scope."""

    def __init__(self) -> None:
        super().__init__(
            "INVALID_OBJECT_KEY",
            "object_key is not a valid upload key for this scope",
        )


class InvalidRequest(DomainError):
    """Semantically invalid request the schema alone cannot reject."""

    def __init__(self, message: str):
        super().__init__("VALIDATION_ERROR", message)


class DatasetNotFound(DomainError):
    """No such reference dataset manifest."""

    def __init__(self, dataset_id: str, available: list[str] | None = None) -> None:
        hint = f"; available: {', '.join(sorted(available))}" if available else ""
        super().__init__("DATASET_NOT_FOUND", f"unknown dataset: {dataset_id}{hint}")


class DatasetNotReady(DomainError):
    """The manifest exists but its audio has not been generated."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            "DATASET_NOT_READY",
            "audio missing for "
            f"{len(missing)} clip(s): {', '.join(sorted(missing)[:5])}"
            "; run scripts/make_eval_corpus.py",
        )


class EvaluationTooLarge(DomainError):
    """A synchronous evaluation has a hard size cap."""

    def __init__(self, clips: int, limit: int) -> None:
        super().__init__(
            "EVALUATION_TOO_LARGE",
            f"dataset has {clips} scored clips; the limit is {limit} "
            "(raise STT_EVALUATION_MAX_CLIPS to run more)",
        )


class Unauthorized(DomainError):
    """Missing or invalid credentials (fail closed, never open)."""

    def __init__(self, message: str = "a valid X-API-Key header is required") -> None:
        super().__init__("UNAUTHORIZED", message)


class RateLimited(DomainError):
    """A caller exceeded its request quota."""

    def __init__(self, message: str, retry_after_s: int = 60) -> None:
        super().__init__("RATE_LIMITED", message)
        self.retry_after_s = retry_after_s
