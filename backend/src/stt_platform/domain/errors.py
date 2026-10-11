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
