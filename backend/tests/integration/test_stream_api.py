"""WS /v1/transcriptions/stream -- true streaming, local-first (T4).

Two things are being pinned here: the wire contract (ready / partial /
final / error / done), and the red lines -- a managed provider is refused
before any audio is accepted, a stream never creates a job, an object or a
row, and a session that stops hearing anything ends itself.
"""
from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from starlette.websockets import WebSocketDisconnect

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.persistence.models import Base
from stt_platform.providers.base import ProviderTranscript
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.schemas.transcript import TranscriptSegment
from tests.fakes import FakeProvider

SR = 16000
STREAM_URL = "/v1/transcriptions/stream"


def tone(seconds: float, freq: float = 220.0, amplitude: float = 0.4) -> bytes:
    t = np.arange(int(seconds * SR), dtype=np.float64) / SR
    return (amplitude * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2").tobytes()


def silence(seconds: float) -> bytes:
    return np.zeros(int(seconds * SR), dtype="<i2").tobytes()


def read_until(ws, kind: str, limit: int = 6) -> dict:
    """The next frame of ``kind``, skipping the ones the cadence may add."""
    seen: list[str] = []
    for _ in range(limit):
        frame = ws.receive_json()
        seen.append(str(frame.get("type")))
        if frame.get("type") == kind:
            return frame
    raise AssertionError(f"no {kind!r} frame among {seen}")


class StreamingFakeProvider(FakeProvider):
    """FakeProvider plus the in-memory window API the stream path calls."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.windows: list[int] = []
        self.languages: list[str | None] = []

    def transcribe_pcm(self, samples, language=None) -> ProviderTranscript:
        size = int(np.asarray(samples).size)
        self.windows.append(size)
        self.languages.append(language)
        return ProviderTranscript(
            text="hello world",
            language="en",
            segments=[TranscriptSegment(start=0.0, end=1.0, text="hello world")],
            words_available=False,
            model="fake-window",
            audio_duration_s=size / SR,
            processing_duration_s=0.01,
        )


class FakeManagedProvider(StreamingFakeProvider):
    id = "managed-fake"
    name = "Fake Managed Provider"
    is_local = False


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


def make_client(tmp_path, database_url, providers, **overrides):
    registry = ProviderRegistry()
    for index, provider in enumerate(providers):
        registry.register(provider, default=(index == 0))
    settings = Settings(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
        database_url=database_url,
        **overrides,
    )
    app = create_app(settings=settings, registry=registry)
    return app, TestClient(app)


def job_count(app) -> int:
    with app.state.engine.connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM jobs")).scalar_one())


def test_a_stream_yields_ready_partial_final_and_done(tmp_path, pg_engine, pg_url):
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    provider = StreamingFakeProvider()
    app, client = make_client(tmp_path, pg_url, [provider])
    jobs_before = job_count(app)

    with client.websocket_connect(STREAM_URL) as ws:
        ws.send_json({"type": "start", "language": "en", "input_format": "pcm16"})
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["provider"] == "fake"
        assert ready["model"]
        assert ready["sample_rate"] == SR
        assert ready["max_duration_s"] > 0
        assert ready["session_id"]

        ws.send_bytes(tone(1.0))
        time.sleep(1.2)  # let the partial cadence tick
        partial = read_until(ws, "partial")
        assert partial["is_final"] is False
        assert partial["window"]["start_s"] == 0.0
        assert partial["segments"][0]["start"] == 0.0
        assert provider.languages[0] == "en"

        ws.send_bytes(silence(0.8))  # closes the utterance
        final = read_until(ws, "final")
        assert final["text"] == "hello world"
        assert final["segments"][0]["start"] == 0.0
        assert final["window"]["end_s"] == pytest.approx(1.02, abs=0.05)

        ws.send_bytes(tone(1.2))
        ws.send_json({"type": "stop"})
        flushed = read_until(ws, "final")
        done = read_until(ws, "done")
        # The second utterance is stamped where it actually happened.
        assert flushed["segments"][0]["start"] == pytest.approx(1.8, abs=0.05)
        assert done["reason"] == "stop"
        assert done["duration_s"] == pytest.approx(3.0, abs=0.05)
        assert done["text"] == "hello world hello world"

    assert provider.windows[0] == pytest.approx(SR, rel=0.05)
    assert job_count(app) == jobs_before, "streaming must not create jobs"


def test_a_managed_provider_is_refused_before_any_audio(tmp_path, pg_engine, pg_url):
    local, managed = StreamingFakeProvider(), FakeManagedProvider()
    _app, client = make_client(tmp_path, pg_url, [local, managed])

    with client.websocket_connect(STREAM_URL) as ws:
        ws.send_json({"type": "start", "provider": "managed-fake"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "STREAM_LOCAL_ONLY"
        assert error["fatal"] is True
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert managed.windows == [], "a managed provider was dialled by the stream path"


@pytest.mark.parametrize(
    "start_frame",
    [
        {"type": "start", "provider": "no-such-provider"},
        {"type": "start", "input_format": "mp3-maybe"},
    ],
)
def test_a_start_frame_that_cannot_be_served_is_a_bad_request(
    tmp_path, pg_engine, pg_url, start_frame
):
    _app, client = make_client(tmp_path, pg_url, [StreamingFakeProvider()])
    with client.websocket_connect(STREAM_URL) as ws:
        ws.send_json(start_frame)
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "STREAM_BAD_REQUEST"
        assert error["fatal"] is True
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_audio_that_is_not_audio_reports_a_decode_failure(tmp_path, pg_engine, pg_url):
    provider = StreamingFakeProvider()
    _app, client = make_client(tmp_path, pg_url, [provider])

    with client.websocket_connect(STREAM_URL) as ws:
        ws.send_json({"type": "start", "input_format": "container"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(b"this is not a container, it is a tantrum " * 1000)
        ws.send_json({"type": "stop"})
        error = read_until(ws, "error")
        assert error["code"] == "STREAM_DECODE_FAILED"
        done = read_until(ws, "done")
        assert done["text"] == ""

    assert provider.windows == [], "undecodable input reached the model"


def test_an_idle_stream_ends_itself(tmp_path, pg_engine, pg_url):
    _app, client = make_client(
        tmp_path, pg_url, [StreamingFakeProvider()], stream_idle_timeout_s=1.0
    )
    with client.websocket_connect(STREAM_URL) as ws:
        ws.send_json({"type": "start", "input_format": "pcm16"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(tone(0.5))
        done = read_until(ws, "done", limit=8)
        assert done["reason"] == "idle"
        assert done["duration_s"] == pytest.approx(0.5, abs=0.05)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_a_second_stream_is_refused_while_the_first_is_running(
    tmp_path, pg_engine, pg_url
):
    _app, client = make_client(
        tmp_path, pg_url, [StreamingFakeProvider()], stream_max_sessions=1
    )
    with client.websocket_connect(STREAM_URL) as first:
        first.send_json({"type": "start", "input_format": "pcm16"})
        assert first.receive_json()["type"] == "ready"

        with client.websocket_connect(STREAM_URL) as second:
            second.send_json({"type": "start", "input_format": "pcm16"})
            error = second.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "STREAM_BUSY"
            with pytest.raises(WebSocketDisconnect):
                second.receive_json()

        # ...and the first stream is untouched by the refusal.
        first.send_bytes(tone(1.0))
        first.send_json({"type": "stop"})
        done = read_until(first, "done")
        assert done["reason"] == "stop"
