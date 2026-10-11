"""Sarvam async adapter through the real workflow (live Temporal, respx).

A job requesting ``sarvam`` must run submit → poll → download inside the
workflow, land SUCCEEDED with a segment-granularity transcript, and leave
exactly one provider_attempts row (no duplicate submissions).

Skips when 127.0.0.1:7233 is unreachable. All Sarvam HTTP is mocked —
no network, no paid usage.
"""
import asyncio
import json
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import respx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.domain.status import JobStatus
from stt_platform.persistence.models import Base
from stt_platform.persistence.provider_attempts import ProviderAttemptRepository
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.managed.sarvam import SarvamProvider
from stt_platform.workers.activities import make_activities
from stt_platform.workflows.transcription import TranscriptionWorkflow
from tests.integration.harness import build_deps, build_store, make_settings, seed_job
from tests.rustfs_container import RustFSContainer

TEMPORAL_HOST = "127.0.0.1:7233"

RETRY = {
    "max_attempts": 2,
    "base": 0.2,
    "multiplier": 2.0,
    "cap": 1.0,
    "jitter_ratio": 0.1,
}
REMOTE = {"poll_interval_s": 0.3, "max_wait_s": 60.0}

BASE = "https://api.sarvam.ai"
INIT_URL = f"{BASE}/speech-to-text/job/v1"
UPLOAD_URL = f"{INIT_URL}/upload-files"
START_URL = f"{INIT_URL}/job-1/start"
STATUS_URL = f"{INIT_URL}/job-1/status"
DOWNLOAD_URL = f"{INIT_URL}/download-files"
PRESIGNED_GET = "https://storage.example/get/0.json"


def _temporal_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 7233), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _temporal_reachable(),
    reason=(
        "compose Temporal not reachable on 127.0.0.1:7233 "
        "(run: cd infrastructure && docker compose up -d)"
    ),
)


@pytest.fixture(scope="module")
def rustfs():
    with RustFSContainer() as rc:
        yield rc


@pytest.fixture(scope="module")
def store(rustfs):
    return build_store(rustfs)


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(pg_engine):
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    return sessionmaker(bind=pg_engine, future=True)


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


def _result_payload() -> dict:
    # Timings fit inside the seeded 1.0 s fixture wav.
    return {
        "request_id": "20260130_d8d2c0e6",
        "transcript": "Hello world. This is a test.",
        "language_code": "en-IN",
        "timestamps": {
            "chunks": ["Hello world.", "This is a test."],
            "start_time_seconds": [0.01, 0.45],
            "end_time_seconds": [0.4, 0.9],
        },
        "diarized_transcript": {
            "entries": [
                {
                    "transcript": "Hello world.",
                    "start_time_seconds": 0.01,
                    "end_time_seconds": 0.4,
                    "speaker_id": "0",
                },
                {
                    "transcript": "This is a test.",
                    "start_time_seconds": 0.45,
                    "end_time_seconds": 0.9,
                    "speaker_id": "1",
                },
            ]
        },
    }


def _mock_sarvam(poll_states: list[str]):
    """Mock the full Sarvam surface; ``poll_states`` drives status calls."""
    respx.post(INIT_URL).mock(
        return_value=httpx.Response(
            202, json={"job_id": "job-1", "job_state": "Accepted"}
        )
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

    respx.post(UPLOAD_URL).mock(side_effect=_uploads)
    respx.put(url__regex=r"https://storage\.example/put/.*").mock(
        return_value=httpx.Response(201)
    )
    respx.post(START_URL).mock(
        return_value=httpx.Response(200, json={"job_id": "job-1", "job_state": "Running"})
    )

    calls = {"count": 0}

    def _status(request):
        index = min(calls["count"], len(poll_states) - 1)
        calls["count"] += 1
        state = poll_states[index]
        payload = {"job_id": "job-1", "job_state": state}
        if state == "Completed":
            payload["job_details"] = [
                {
                    "file_name": "sample.wav",
                    "status": "success",
                    "outputs": [{"file_name": "0.json"}],
                }
            ]
        return httpx.Response(200, json=payload)

    respx.get(STATUS_URL).mock(side_effect=_status)
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
    respx.get(PRESIGNED_GET).mock(
        return_value=httpx.Response(200, json=_result_payload())
    )


async def _run_job(session_factory, store, settings, provider, job_id):
    from temporalio.client import Client
    from temporalio.worker import Worker

    client = await Client.connect(TEMPORAL_HOST)
    activities = make_activities(
        build_deps(session_factory, provider, store, settings)
    )
    task_queue = f"test-sarvam-{uuid.uuid4().hex[:8]}"
    executor = ThreadPoolExecutor(max_workers=4)
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[TranscriptionWorkflow],
        activities=activities,
        activity_executor=executor,
        max_concurrent_activities=2,
    )
    handle = await client.start_workflow(
        "TranscriptionWorkflow",
        {
            "job_id": job_id,
            "scope": "local",
            "retry": RETRY,
            "remote": REMOTE,
        },
        id=f"test-sarvam-{job_id}",
        task_queue=task_queue,
    )
    async with worker:
        try:
            result = await asyncio.wait_for(handle.result(), timeout=90)
        except Exception as err:  # noqa: BLE001 - returned for assertions
            result = err
    executor.shutdown(wait=False)
    return result


@respx.mock
def test_sarvam_job_submits_polls_and_succeeds(session_factory, store, settings):
    provider = SarvamProvider(
        settings=settings.model_copy(update={"sarvam_api_key": "srv-test-key"})
    )
    job_id = seed_job(session_factory, store, provider="sarvam")
    _mock_sarvam(["Pending", "Completed"])

    result = asyncio.run(
        _run_job(session_factory, store, settings, provider, job_id)
    )

    assert result == "SUCCEEDED", result

    with session_factory() as session:
        record = JobRepository(session).get(job_id, "local")
        saved = JobRepository(session).load_result(job_id, "local")
        attempts = ProviderAttemptRepository(session).list_for_job(job_id, "local")
        trail = JobRepository(session).transitions(job_id, "local")

    assert record.status is JobStatus.SUCCEEDED
    assert record.actual_provider == "sarvam"
    assert ("QUEUED", "PROCESSING") in trail
    assert ("PROCESSING", "SUCCEEDED") in trail

    # Exactly one submission, completed with the provider-side job id.
    assert len(attempts) == 1
    assert attempts[0].state == "COMPLETED"
    assert attempts[0].provider_job_id == "job-1"

    assert saved is not None
    assert saved.provider == "sarvam"
    assert saved.model == "saaras:v4"
    assert saved.timestamp_granularity == "segment"  # batch API has no words
    assert [s.speaker for s in saved.segments] == [0, 1]
    assert saved.text == "Hello world. This is a test."
