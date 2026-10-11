"""Sync bridge to the async Temporal client (dispatcher, API best-effort).

A single dedicated event-loop thread owns the connection; sync callers
block on it with a bounded timeout. Re-dispatch of an already-started
workflow id is treated as success (the outbox row derivation makes the
start idempotent).
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError


class TemporalWorkflowStarter:
    def __init__(
        self, *, host: str, task_queue: str, retry: dict | None = None
    ) -> None:
        self._host = host
        self._task_queue = task_queue
        self._retry = retry or {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="temporal-starter", daemon=True
        )
        self._thread.start()
        self._client: Client | None = None

    # ------------------------------------------------------------------
    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(
            timeout=30
        )

    async def _client_async(self) -> Client:
        if self._client is None:
            self._client = await Client.connect(self._host)
        return self._client

    async def _start(self, payload: dict) -> None:
        client = await self._client_async()
        job_input = {
            "job_id": payload["job_id"],
            "scope": payload.get("scope", "local"),
            "retry": self._retry,
        }
        try:
            await client.start_workflow(
                "TranscriptionWorkflow",
                job_input,
                id=payload["workflow_id"],
                task_queue=self._task_queue,
            )
        except WorkflowAlreadyStartedError:
            return  # same outbox row re-dispatched: nothing to do

    async def _cancel(self, workflow_id: str) -> None:
        client = await self._client_async()
        await client.get_workflow_handle(workflow_id).cancel()

    # ------------------------------------------------------------------
    def __call__(self, payload: dict) -> None:
        self._run(self._start(payload))

    def cancel(self, workflow_id: str) -> None:
        self._run(self._cancel(workflow_id))

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
