"""Sarvam batch STT adapter tests (asynchronous provider contract).

Every HTTP call is intercepted by respx — no network, no paid usage.
Vendor shapes are modelled on the documented batch flow:
https://docs.sarvam.ai/api-reference/speech-to-text/stt/job/{initiate,upload,start,status,download}
"""
import json

import httpx
import pytest
import respx

from stt_platform.core.config import Settings
from stt_platform.domain.errors import ProviderError
from stt_platform.providers.base import RemoteJobRef, TranscriptionRequest
from stt_platform.providers.managed.sarvam import SarvamProvider
from tests.audio_fixtures import make_wav

BASE = "https://api.sarvam.ai"
INIT_URL = f"{BASE}/speech-to-text/job/v1"
UPLOAD_URL = f"{INIT_URL}/upload-files"
STATUS_URL = f"{INIT_URL}/job-1/status"
START_URL = f"{INIT_URL}/job-1/start"
DOWNLOAD_URL = f"{INIT_URL}/download-files"
PRESIGNED_PUT = "https://storage.example/put/sample.wav"
PRESIGNED_GET = "https://storage.example/get/0.json"


def make_provider(**overrides) -> SarvamProvider:
    settings = Settings(sarvam_api_key="srv-test-key", **overrides)
    return SarvamProvider(settings=settings)


def _request(tmp_path) -> TranscriptionRequest:
    wav = make_wav(tmp_path / "sample.wav", seconds=1.0)
    return TranscriptionRequest(audio_path=wav, language="en")


def _result_payload() -> dict:
    return {
        "request_id": "20260130_d8d2c0e6",
        "transcript": "Hello, how can I help you today? I have a question.",
        "language_code": "en-IN",
        "timestamps": {
            "chunks": ["Hello, how can I help you today?", "I have a question."],
            "start_time_seconds": [0.01, 2.8],
            "end_time_seconds": [2.5, 4.2],
        },
        "diarized_transcript": {
            "entries": [
                {
                    "transcript": "Hello, how can I help you today?",
                    "start_time_seconds": 0.01,
                    "end_time_seconds": 2.5,
                    "speaker_id": "0",
                },
                {
                    "transcript": "I have a question.",
                    "start_time_seconds": 2.8,
                    "end_time_seconds": 4.2,
                    "speaker_id": "1",
                },
            ]
        },
    }


def _mock_submit() -> dict:
    """Mock the whole submit flow; returns the recorded routes."""
    init = respx.post(INIT_URL).mock(
        return_value=httpx.Response(202, json={"job_id": "job-1", "job_state": "Accepted"})
    )

    def _uploads(request):
        names = json.loads(request.content)["files"]
        return httpx.Response(
            200,
            json={
                "job_id": "job-1",
                "job_state": "Accepted",
                "upload_urls": {
                    name: {"file_url": f"https://storage.example/put/{name}"}
                    for name in names
                },
            },
        )

    uploads = respx.post(UPLOAD_URL).mock(side_effect=_uploads)
    put = respx.put(url__regex=r"https://storage\.example/put/.*").mock(
        return_value=httpx.Response(201)
    )
    start = respx.post(START_URL).mock(
        return_value=httpx.Response(200, json={"job_id": "job-1", "job_state": "Running"})
    )
    return {"init": init, "uploads": uploads, "put": put, "start": start}


@respx.mock
def test_submit_flow_shapes(tmp_path):
    routes = _mock_submit()
    provider = make_provider()

    ref = provider.submit(_request(tmp_path))

    assert ref.provider_job_id == "job-1"
    assert ref.raw["filename"] == "sample.wav"
    assert abs(ref.raw["duration_s"] - 1.0) < 0.2

    init_call = routes["init"].calls.last.request
    assert init_call.headers["api-subscription-key"] == "srv-test-key"
    body = json.loads(init_call.content)
    params = body["job_parameters"]
    assert params["model"] == "saaras:v4"
    assert params["with_timestamps"] is True
    assert params["with_diarization"] is True
    assert params["language_code"] == "en-IN"

    uploads_body = json.loads(routes["uploads"].calls.last.request.content)
    assert uploads_body == {"job_id": "job-1", "files": ["sample.wav"]}

    put_call = routes["put"].calls.last.request
    assert put_call.content == (tmp_path / "sample.wav").read_bytes()
    assert put_call.headers["x-ms-blob-type"] == "BlockBlob"
    # Presigned URL: no API credential may be sent to the storage host.
    assert "api-subscription-key" not in put_call.headers

    assert routes["start"].calls.last.request.headers["api-subscription-key"] == "srv-test-key"


@respx.mock
def test_submit_language_mapping(tmp_path):
    routes = _mock_submit()
    provider = make_provider()

    provider.submit(
        TranscriptionRequest(audio_path=make_wav(tmp_path / "sample.wav"), language="hi")
    )
    assert json.loads(routes["init"].calls.last.request.content)["job_parameters"][
        "language_code"
    ] == "hi-IN"

    provider.submit(
        TranscriptionRequest(
            audio_path=make_wav(tmp_path / "sample2.wav"), language="ta-IN"
        )
    )
    assert json.loads(routes["init"].calls.last.request.content)["job_parameters"][
        "language_code"
    ] == "ta-IN"


@respx.mock
def test_submit_unknown_language_requests_autodetect(tmp_path):
    routes = _mock_submit()
    provider = make_provider()

    provider.submit(
        TranscriptionRequest(audio_path=make_wav(tmp_path / "sample.wav"), language=None)
    )
    assert json.loads(routes["init"].calls.last.request.content)["job_parameters"][
        "language_code"
    ] == "unknown"


def _mock_status(state: str, *, job_details=None, error_message=None):
    payload = {
        "job_id": "job-1",
        "job_state": state,
        "created_at": "2026-01-30T10:00:00Z",
        "updated_at": "2026-01-30T10:01:00Z",
        "storage_container_type": "Azure_V1",
    }
    if job_details is not None:
        payload["job_details"] = job_details
    if error_message is not None:
        payload["error_message"] = error_message
    return respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json=payload))


def _output_details(filename="0.json"):
    return [
        {
            "file_name": "sample.wav",
            "status": "success",
            "outputs": [{"file_name": filename}],
        }
    ]


@respx.mock
def test_poll_pending_states():
    for state in ("Accepted", "Pending", "Running"):
        _mock_status(state)
        result = make_provider().poll(RemoteJobRef(provider_job_id="job-1"))
        assert result.state == "pending", state


@respx.mock
def test_poll_completed_downloads_and_normalizes(tmp_path):
    _mock_status("Completed", job_details=_output_details())
    downloads = respx.post(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "job-1",
                "job_state": "Completed",
                "download_urls": {"0.json": {"file_url": PRESIGNED_GET}},
            },
        )
    )
    respx.get(PRESIGNED_GET).mock(
        return_value=httpx.Response(200, json=_result_payload())
    )

    provider = make_provider()
    ref = RemoteJobRef(
        provider_job_id="job-1", raw={"filename": "sample.wav", "duration_s": 4.5}
    )
    result = provider.poll(ref)

    assert result.state == "completed"
    transcript = result.transcript
    assert transcript is not None
    assert transcript.text.startswith("Hello, how can I help you today?")
    assert transcript.language == "en-IN"  # vendor value verbatim
    assert transcript.model == "saaras:v4"
    assert transcript.words_available is False  # batch API has no word timings
    assert transcript.audio_duration_s == 4.5  # probed locally at submit time
    assert [s.speaker for s in transcript.segments] == [0, 1]
    assert transcript.segments[0].start == 0.01
    assert transcript.segments[0].end == 2.5

    assert json.loads(downloads.calls.last.request.content) == {
        "job_id": "job-1",
        "files": ["0.json"],
    }
    # Download host never receives the API credential.
    assert "api-subscription-key" not in respx.calls.last.request.headers


@respx.mock
def test_poll_completed_without_diarization_uses_timestamp_chunks():
    _mock_status("Completed", job_details=_output_details())
    respx.post(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "job-1",
                "job_state": "Completed",
                "download_urls": {"0.json": {"file_url": PRESIGNED_GET}},
            },
        )
    )
    payload = _result_payload()
    del payload["diarized_transcript"]
    respx.get(PRESIGNED_GET).mock(return_value=httpx.Response(200, json=payload))

    result = make_provider().poll(
        RemoteJobRef(provider_job_id="job-1", raw={"filename": "sample.wav", "duration_s": 4.5})
    )

    transcript = result.transcript
    assert transcript is not None
    assert [s.text for s in transcript.segments] == [
        "Hello, how can I help you today?",
        "I have a question.",
    ]
    assert all(s.speaker is None for s in transcript.segments)
    assert transcript.words_available is False


@respx.mock
def test_poll_completed_drops_degenerate_entries_with_warning():
    _mock_status("Completed", job_details=_output_details())
    respx.post(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "job-1",
                "job_state": "Completed",
                "download_urls": {"0.json": {"file_url": PRESIGNED_GET}},
            },
        )
    )
    payload = _result_payload()
    payload["diarized_transcript"]["entries"].append(
        {
            "transcript": "broken",
            "start_time_seconds": 5.0,
            "end_time_seconds": 5.0,
            "speaker_id": "1",
        }
    )
    respx.get(PRESIGNED_GET).mock(return_value=httpx.Response(200, json=payload))

    transcript = make_provider().poll(
        RemoteJobRef(provider_job_id="job-1", raw={"filename": "sample.wav", "duration_s": 6.0})
    ).transcript

    assert transcript is not None
    assert len(transcript.segments) == 2
    assert any("degenerate" in warning for warning in transcript.warnings)


@respx.mock
def test_poll_failed_job_is_retryable_failure():
    _mock_status("Failed", error_message="quota exhausted")

    result = make_provider().poll(RemoteJobRef(provider_job_id="job-1"))

    assert result.state == "failed"
    assert result.error_code == "SARVAM_JOB_FAILED"
    assert result.error_message == "quota exhausted"
    assert result.retryable is True


@respx.mock
def test_poll_completed_without_outputs_is_permanent_malformed():
    _mock_status("Completed", job_details=[])

    with pytest.raises(ProviderError) as err:
        make_provider().poll(RemoteJobRef(provider_job_id="job-1"))
    assert err.value.code == "MALFORMED_RESPONSE"
    assert err.value.retryable is False


def test_cancel_is_unsupported():
    assert make_provider().cancel(RemoteJobRef(provider_job_id="job-1")) is False


def test_capabilities_declare_async_batch_without_word_timings():
    caps = make_provider().capabilities()
    assert caps.async_processing is True
    assert caps.word_timestamps is False
    assert caps.segment_timestamps is True
    assert caps.diarization is True
    assert caps.confidence_scores is False
    assert caps.capability_sources["async_processing"] == "documented"


def test_health_without_key_is_unavailable():
    provider = SarvamProvider(settings=Settings(sarvam_api_key=None))
    assert provider.health().status == "unavailable"
