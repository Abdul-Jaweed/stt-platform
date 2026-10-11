"""Test doubles. FakeProvider is a fully in-memory provider used to test
the contract, the job service, and the API without GPU or network."""
from pathlib import Path

from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import ProviderTranscript, TranscriptionRequest
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
