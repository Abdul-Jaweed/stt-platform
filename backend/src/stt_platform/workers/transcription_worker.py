"""Worker entrypoint: transcription activities + outbox dispatcher loop.

Run (dev):  uv run python -m stt_platform.workers.transcription_worker

The worker process also hosts the dispatcher: every
``dispatch_poll_interval_s`` it drains the transactional outbox into
Temporal workflow starts, so a crash between commit and dispatch only
delays the job by one tick.
"""
from __future__ import annotations

import asyncio
import logging
import time

from temporalio.client import Client
from temporalio.worker import Worker

from stt_platform.core.config import Settings, get_settings
from stt_platform.persistence.db import make_engine, make_session_factory
from stt_platform.persistence.outbox import OutboxRepository
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.services.dispatch import dispatch_pending
from stt_platform.services.temporal_starter import (
    TemporalWorkflowStarter,
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
    from stt_platform.providers.faster_whisper import FasterWhisperProvider

    settings.apply_runtime_env()
    engine = make_engine(settings.database_url)
    registry = ProviderRegistry()
    registry.register(FasterWhisperProvider(), default=True)
    store = S3ObjectStore.from_settings(settings)
    try:
        store.ensure_buckets()
    except Exception as exc:  # noqa: BLE001 - storage may be down; op fails later
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
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
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
    client = await Client.connect(settings.temporal_host)
    starter = TemporalWorkflowStarter(
        host=settings.temporal_host,
        task_queue=settings.temporal_task_queue,
        retry=retry_config_from_settings(settings),
    )
    # Sync activities (ffmpeg, model inference, boto3) run in this pool;
    # max_concurrent_activities is what gates real GPU concurrency.
    executor = ThreadPoolExecutor(
        max_workers=max(4, settings.gpu_concurrency),
        thread_name_prefix="stt-activity",
    )
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[TranscriptionWorkflow],
        activities=make_activities(deps),
        activity_executor=executor,
        max_concurrent_activities=settings.gpu_concurrency,
    )
    log.info(
        "transcription worker up: queue=%s gpu_concurrency=%d temporal=%s",
        settings.temporal_task_queue,
        settings.gpu_concurrency,
        settings.temporal_host,
    )
    dispatcher = asyncio.create_task(
        asyncio.to_thread(_dispatcher_loop, settings, starter)
    )
    async with worker:
        await asyncio.gather(worker.run(), dispatcher)


if __name__ == "__main__":
    asyncio.run(main())
