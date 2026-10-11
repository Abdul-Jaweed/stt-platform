"""Test doubles. FakeProvider is a fully in-memory provider used to test
the contract, the job service, and the API without GPU or network."""

from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import (
    ProviderTranscript,
    RemoteJobRef,
    RemoteJobState,
    TranscriptionRequest,
)
from stt_platform.schemas.providers import ProviderCapabilities, ProviderHealth
from stt_platform.schemas.transcript import TranscriptSegment, TranscriptWord


def sample_transcript(**overrides) -> ProviderTranscript:
    """Canonical transcript with word timings, reusable across tests."""
    base = dict(
        text="hello world",
        language="en",
        language_probability=0.99,
        segments=[
            TranscriptSegment(
                start=0.0,
                end=1.0,
                text="hello world",
                words=[
                    TranscriptWord(start=0.0, end=0.5, text="hello", confidence=0.9),
                    TranscriptWord(start=0.5, end=1.0, text="world", confidence=0.8),
                ],
            )
        ],
        words_available=True,
        model="fake-whisper",
        model_version="1",
        audio_duration_s=1.0,
        processing_duration_s=0.1,
    )
    base.update(overrides)
    return ProviderTranscript(**base)


class FakeProvider:
    """In-memory provider implementing the full contract.

    ``fail``: None | "transient" | "permanent".
    """

    id = "fake"
    name = "Fake Provider"
    is_local = True

    def __init__(self, fail: str | None = None):
        self.fail = fail

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            languages=["en"],
            language_detection=True,
            segment_timestamps=True,
            word_timestamps=True,
            formats=["wav", "mp3"],
            capability_sources={"word_timestamps": "verified"},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(status="ok")

    def transcribe(self, request: TranscriptionRequest) -> ProviderTranscript:
        if self.fail == "transient":
            raise ProviderError("FAKE_TRANSIENT", "temporary failure", retryable=True)
        if self.fail == "permanent":
            raise ProviderError("FAKE_PERMANENT", "permanent failure", retryable=False)
        return sample_transcript()


class FakeAsyncProvider:
    """In-memory managed-style provider with an asynchronous job API.

    ``poll_script`` is consumed one entry per poll call; the last entry
    repeats. Counters (``submits``/``polls``/``cancels``) let tests assert
    exactly-once submission and cancellation behaviour.
    """

    id = "fake-async"
    name = "Fake Async Provider"
    is_local = False

    def __init__(
        self,
        poll_script: list[str] | None = None,
        *,
        cancel_supported: bool = True,
        fail_submit: str | None = None,
    ):
        self.poll_script = poll_script or ["pending", "pending", "completed"]
        self.cancel_supported = cancel_supported
        self.fail_submit = fail_submit
        self.submits = 0
        self.polls = 0
        self.cancels = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            languages=["en", "hi"],
            language_detection=True,
            segment_timestamps=True,
            word_timestamps=True,
            batch=True,
            async_processing=True,
            formats=["wav", "mp3"],
            capability_sources={"async_processing": "documented"},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(status="ok")

    def transcribe(self, request: TranscriptionRequest) -> ProviderTranscript:
        raise ProviderError(
            "ASYNC_ONLY", "this provider requires the asynchronous path",
            retryable=False,
        )

    def submit(self, request: TranscriptionRequest) -> RemoteJobRef:
        if self.fail_submit == "transient":
            raise ProviderError("REMOTE_SUBMIT", "submit failed", retryable=True)
        if self.fail_submit == "permanent":
            raise ProviderError("REMOTE_SUBMIT", "submit rejected", retryable=False)
        self.submits += 1
        return RemoteJobRef(provider_job_id=f"remote-{self.submits}")

    def poll(self, ref: RemoteJobRef) -> RemoteJobState:
        scripted = self.poll_script[min(self.polls, len(self.poll_script) - 1)]
        self.polls += 1
        if scripted == "completed":
            return RemoteJobState(
                state="completed",
                transcript=sample_transcript(model="fake-async"),
            )
        if scripted == "failed":
            return RemoteJobState(
                state="failed",
                error_code="REMOTE_FAIL",
                error_message="remote job failed",
                retryable=True,
            )
        return RemoteJobState(state="pending")

    def cancel(self, ref: RemoteJobRef) -> bool:
        self.cancels += 1
        return self.cancel_supported
