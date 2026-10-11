"""Worker entrypoint: transcription activities + outbox dispatcher loop.

Run (dev):  uv run python -m stt_platform.workers.transcription_worker

The worker process also hosts the dispatcher: every
``dispatch_poll_interval_s`` it drains the transactional outbox into
Temporal workflow starts, so a crash between commit and dispatch only
delays the job by one tick.

Two subtleties this file gets right:

* ``async with Worker(...)`` **starts** the worker; calling
  ``worker.run()`` inside the block double-starts the SDK bridge and
  panics (``must only be set once: TaskLocals``). The block is kept alive
  with an event wait instead.
* The dispatcher thread starts workflows through the **main event
  loop's** client (``run_coroutine_threadsafe``) instead of a second
  Temporal client on its own loop — one bridge loop per process.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from stt_platform.core.config import Settings, get_settings
from stt_platform.observability.temporal import temporal_interceptors
from stt_platform.persistence.db import make_engine, make_session_factory
from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.services.dispatch import dispatch_pending
from stt_platform.services.temporal_starter import (
    remote_config_from_settings,
    retry_config_from_settings,
)
from stt_platform.storage.s3 import S3ObjectStore
from stt_platform.workers.activities import (
    ActivityDeps,
    make_activities,
)
from stt_platform.workflows.transcription import TranscriptionWorkflow

log = logging.getLogger(__name__)


def build_deps(settings: Settings) -> ActivityDeps:
    from stt_platform.providers.defaults import build_default_registry

    settings.apply_runtime_env()
    engine = make_engine(settings.database_url)
    registry = build_default_registry(settings)
    store = S3ObjectStore.from_settings(settings)
    try:
        store.ensure_buckets()
    except Exception as exc:
        log.warning("object storage not ready at startup: %r", exc)
    return ActivityDeps(
        settings=settings,
        session_factory=make_session_factory(engine),
        registry=registry,
        store=store,
    )


def run_dispatcher_tick(settings: Settings, starter) -> int:
    engine = make_engine(settings.database_url)
    try:
        session_factory = make_session_factory(engine)
        with session_factory() as session:
            return dispatch_pending(OutboxRepository(session), starter)
    finally:
        engine.dispose()


def _dispatcher_loop(settings: Settings, starter) -> None:
    while True:
        try:
            run_dispatcher_tick(settings, starter)
        except Exception as exc:
            log.warning("dispatcher tick failed: %r", exc)
        time.sleep(settings.dispatch_poll_interval_s)


async def main() -> None:
    from concurrent.futures import ThreadPoolExecutor

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    deps = build_deps(settings)
    client = await Client.connect(
        settings.temporal_host,
        interceptors=temporal_interceptors(settings),
    )
    loop = asyncio.get_running_loop()

    async def start_workflow(payload: dict) -> None:
        try:
            await client.start_workflow(
                "TranscriptionWorkflow",
                {
                    "job_id": payload["job_id"],
                    "scope": payload.get("scope", "local"),
                    "retry": retry_config_from_settings(settings),
                    "remote": remote_config_from_settings(settings),
                },
                id=payload["workflow_id"],
                task_queue=settings.temporal_task_queue,
            )
        except WorkflowAlreadyStartedError:
            return  # same outbox row re-dispatched: nothing to do

    def starter(payload: dict) -> None:
        """Sync bridge used by the dispatcher thread; main loop does the IO."""
        future = asyncio.run_coroutine_threadsafe(start_workflow(payload), loop)
        future.result(timeout=30)

    # Sync activities (ffmpeg, model inference, boto3, provider HTTP) run in
    # this pool. Activity slots cover GPU + managed + transition overhead;
    # the GPU semaphore inside the whisper adapter and the managed semaphore
    # inside the HTTP client do the real class-level gating, so a managed
    # call can never starve the single GPU slot.
    activity_slots = settings.gpu_concurrency + settings.managed_concurrency + 2
    executor = ThreadPoolExecutor(
        max_workers=max(4, activity_slots),
        thread_name_prefix="stt-activity",
    )
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[TranscriptionWorkflow],
        activities=make_activities(deps),
        activity_executor=executor,
        max_concurrent_activities=activity_slots,
        interceptors=temporal_interceptors(settings),
    )
    log.info(
        "transcription worker up: queue=%s gpu_concurrency=%d "
        "managed_concurrency=%d temporal=%s",
        settings.temporal_task_queue,
        settings.gpu_concurrency,
        settings.managed_concurrency,
        settings.temporal_host,
    )
    threading.Thread(
        target=_dispatcher_loop,
        args=(settings, starter),
        name="stt-dispatcher",
        daemon=True,
    ).start()
    # ``async with`` starts the worker; keep it alive until interrupted.
    async with worker:
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
