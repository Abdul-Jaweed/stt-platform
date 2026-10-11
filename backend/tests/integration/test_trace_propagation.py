"""Trace context crosses the API -> workflow -> activity boundary.

Real workflow on the compose Temporal server, real worker in-process, and
an in-memory exporter: one trace id must cover the span that started the
workflow, the workflow span, and the activity spans (including the domain
``stt.*`` spans the activity opens). Skips when 127.0.0.1:7233 is down.
"""
from __future__ import annotations

import asyncio
import socket
import uuid

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.core.config import Settings
from stt_platform.observability import tracing
from stt_platform.observability.temporal import temporal_interceptors
from stt_platform.persistence.models import Base
from stt_platform.workers.activities import make_activities
from stt_platform.workflows.transcription import TranscriptionWorkflow
from tests.fakes import FakeProvider
from tests.integration.harness import (
    build_deps,
    build_store,
    make_settings,
    seed_job,
)
from tests.rustfs_container import RustFSContainer

TEMPORAL_HOST = "127.0.0.1:7233"

RETRY = {
    "max_attempts": 3,
    "base": 0.2,
    "multiplier": 2.0,
    "cap": 1.0,
    "jitter_ratio": 0.1,
}


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


def _obs_settings() -> Settings:
    return Settings(
        otel_enabled=True,
        otel_exporter="none",
        database_url="postgresql+psycopg://stt:stt@127.0.0.1:25432/stt",
    )


@pytest.fixture()
def span_exporter():
    exporter = InMemorySpanExporter()
    tracing.setup_tracing(_obs_settings(), span_exporter=exporter)
    yield exporter
    tracing.shutdown_tracing()


async def _run_job(
    session_factory,
    store,
    settings,
    job_id: str,
    *,
    parent_trace: bool,
):
    """Run the real workflow; return (result, starting span context or None)."""
    from concurrent.futures import ThreadPoolExecutor

    from temporalio.client import Client
    from temporalio.worker import Worker

    obs_settings = _obs_settings()
    interceptors = temporal_interceptors(obs_settings)
    assert interceptors, "tracing is enabled in this test"

    client = await Client.connect(TEMPORAL_HOST, interceptors=interceptors)
    activities = make_activities(
        build_deps(session_factory, FakeProvider(), store, settings)
    )
    task_queue = f"trace-q-{uuid.uuid4().hex[:8]}"
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[TranscriptionWorkflow],
            activities=activities,
            activity_executor=executor,
            max_concurrent_activities=1,
            interceptors=interceptors,
        ):
            if parent_trace:
                with tracing.get_tracer().start_as_current_span(
                    "api.request"
                ) as api_span:
                    handle = await client.start_workflow(
                        "TranscriptionWorkflow",
                        {"job_id": job_id, "scope": "local", "retry": RETRY},
                        id=f"trace-{job_id}",
                        task_queue=task_queue,
                    )
                    start_context = api_span.get_span_context()
            else:
                handle = await client.start_workflow(
                    "TranscriptionWorkflow",
                    {"job_id": job_id, "scope": "local", "retry": RETRY},
                    id=f"trace-{job_id}",
                    task_queue=task_queue,
                )
                start_context = None
            result = await handle.result()
    return result, start_context


def test_one_trace_covers_workflow_and_activities(
    session_factory, store, settings, span_exporter
):
    job_id = seed_job(session_factory, store)

    result, start_context = asyncio.run(
        _run_job(session_factory, store, settings, job_id, parent_trace=True)
    )

    assert result == "SUCCEEDED"
    spans = span_exporter.get_finished_spans()
    workflow_spans = [s for s in spans if "Workflow" in s.name]
    activity_spans = [
        s
        for s in spans
        if "Activity" in s.name or s.name.startswith("stt.")
    ]
    assert workflow_spans, [s.name for s in spans]
    assert activity_spans, [s.name for s in spans]

    # The whole run lives in the trace the caller started.
    assert start_context is not None
    assert start_context.trace_id
    for span in workflow_spans + activity_spans:
        assert span.context.trace_id == start_context.trace_id, span.name
    api_span = next(s for s in spans if s.name == "api.request")
    assert api_span.context.trace_id == start_context.trace_id


def test_workflow_started_without_a_parent_still_gets_a_trace(
    session_factory, store, settings, span_exporter
):
    """The outbox dispatcher starts workflows with no request context."""
    job_id = seed_job(session_factory, store)

    result, start_context = asyncio.run(
        _run_job(session_factory, store, settings, job_id, parent_trace=False)
    )

    assert result == "SUCCEEDED"
    assert start_context is None
    spans = span_exporter.get_finished_spans()
    workflow_spans = [s for s in spans if "Workflow" in s.name]
    assert workflow_spans, [s.name for s in spans]
    trace_ids = {s.context.trace_id for s in workflow_spans}
    assert len(trace_ids) == 1
    # ... and the activities of that run share it.
    for span in spans:
        if "Activity" in span.name or span.name.startswith("stt."):
            assert span.context.trace_id in trace_ids, span.name
