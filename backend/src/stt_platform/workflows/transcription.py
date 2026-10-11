"""The transcription workflow: bounded retries, every attempt visible.

Retries are implemented HERE rather than via Temporal's activity
RetryPolicy, because each failure must write a RETRY_WAIT transition
into PostgreSQL — the trail is part of the product, not a side channel.
Workflow code stays deterministic: delays are computed with the
deterministic ``workflow.random()`` RNG and slept with ``workflow.sleep``.
"""
import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

from stt_platform.workers.retry import compute_backoff

# Default when the dispatcher passes no bound (older payloads). The starter
# always passes ``attempt_timeout_s`` from settings (Phase 7).
_DEFAULT_ATTEMPT_TIMEOUT = timedelta(hours=4)
_POLL_TIMEOUT = timedelta(seconds=120)
_TRANSITION_TIMEOUT = timedelta(seconds=30)
_ONE_SHOT = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="TranscriptionWorkflow")
class TranscriptionWorkflow:
    @workflow.run
    async def run(self, job: dict) -> str:
        job_id = job["job_id"]
        scope = job.get("scope", "local")
        retry = job.get("retry", {})
        remote = job.get("remote", {})
        poll_interval = float(remote.get("poll_interval_s", 5.0))
        max_wait = float(remote.get("max_wait_s", 7200.0))
        max_attempts = int(retry.get("max_attempts", 3))
        attempt_timeout = timedelta(
            seconds=float(
                retry.get("attempt_timeout_s", _DEFAULT_ATTEMPT_TIMEOUT.total_seconds())
            )
        )
        attempt = 0
        try:
            while True:
                attempt += 1
                try:
                    outcome = await workflow.execute_activity(
                        "execute_attempt",
                        args=[job_id, scope, attempt],
                        start_to_close_timeout=attempt_timeout,
                        retry_policy=_ONE_SHOT,
                    )
                except ActivityError as err:
                    if workflow.cancellation_reason() is not None:
                        # An external cancellation delivered while the
                        # activity was pending must end the workflow,
                        # not start a retry.
                        raise CancelledError("workflow cancelled") from err
                    # Unexpected failure inside the activity: treat as a
                    # transient attempt so the loop (and trail) still apply.
                    outcome = {
                        "outcome": "transient",
                        "code": "ACTIVITY_ERROR",
                        "message": str(err.cause or err)[:300],
                    }

                verdict = outcome.get("outcome")
                if verdict == "pending":
                    # Asynchronous provider: the submission is durable in
                    # provider_attempts; polling happens here so a worker
                    # crash resumes instead of resubmitting.
                    outcome = await self._await_remote(
                        job_id, scope, attempt, poll_interval, max_wait
                    )
                    verdict = outcome.get("outcome")
                if verdict == "ok":
                    await self._transition(
                        job_id,
                        scope,
                        "SUCCEEDED",
                        fields={
                            "actual_provider": outcome.get("provider"),
                            "model": outcome.get("model"),
                            "model_version": outcome.get("model_version"),
                            "language_detected": outcome.get("language"),
                        },
                    )
                    return "SUCCEEDED"

                permanent = verdict == "permanent"
                if permanent or attempt >= max_attempts:
                    await self._transition(
                        job_id,
                        scope,
                        "FAILED",
                        detail={"attempt": attempt},
                        failure={
                            "code": outcome.get("code", "UNKNOWN"),
                            "message": outcome.get("message", ""),
                            "retryable": not permanent,
                        },
                    )
                    return "FAILED"

                await self._transition(
                    job_id,
                    scope,
                    "RETRY_WAIT",
                    detail={
                        "attempt": attempt,
                        "code": outcome.get("code"),
                        "message": outcome.get("message"),
                    },
                )
                delay = compute_backoff(
                    attempt,
                    base=float(retry.get("base", 2.0)),
                    multiplier=float(retry.get("multiplier", 2.0)),
                    cap=float(retry.get("cap", 60.0)),
                    jitter_ratio=float(retry.get("jitter_ratio", 0.25)),
                    rand=workflow.random().random(),
                )
                await workflow.sleep(timedelta(seconds=delay))
                await self._transition(job_id, scope, "QUEUED")
        except (asyncio.CancelledError, CancelledError):
            # A cancel delivered while sleeping arrives as
            # asyncio.CancelledError; one delivered at an activity await
            # arrives as the SDK's CancelledError. Both must run the same
            # cleanup: cancel the provider-side job where supported (best
            # effort), record CANCELLED, then propagate. Failures here
            # must never mask the cancellation itself.
            try:
                await workflow.execute_activity(
                    "cancel_remote",
                    args=[job_id, scope],
                    start_to_close_timeout=_TRANSITION_TIMEOUT,
                    retry_policy=_ONE_SHOT,
                )
            except Exception as exc:
                workflow.logger.warning(
                    "provider-side cancellation was not possible: %r", exc
                )
            try:
                await self._transition(job_id, scope, "CANCELLED")
            except Exception as exc:
                workflow.logger.warning(
                    "recording CANCELLED lost the race: %r", exc
                )
            raise

    async def _await_remote(
        self,
        job_id: str,
        scope: str,
        attempt: int,
        poll_interval: float,
        max_wait: float,
    ) -> dict:
        """Poll the provider-side job until done, failed, or out of time.

        Transport-level poll errors keep polling (the remote job may be
        healthy); a provider-side failure verdict ends the attempt with
        the provider's own retryability guidance.
        """
        deadline = workflow.now() + timedelta(seconds=max_wait)
        while True:
            await workflow.sleep(timedelta(seconds=poll_interval))
            try:
                result = await workflow.execute_activity(
                    "poll_remote",
                    args=[job_id, scope, attempt],
                    start_to_close_timeout=_POLL_TIMEOUT,
                    retry_policy=_ONE_SHOT,
                )
            except ActivityError as err:
                if workflow.cancellation_reason() is not None:
                    raise CancelledError("workflow cancelled") from err
                result = {
                    "state": "error",
                    "code": "POLL_ACTIVITY_ERROR",
                    "message": str(err.cause or err)[:300],
                }
            state = result.get("state")
            if state == "ok":
                return {
                    "outcome": "ok",
                    **{
                        key: value
                        for key, value in result.items()
                        if key != "state"
                    },
                }
            if state == "failed":
                return {
                    "outcome": (
                        "transient" if result.get("retryable") else "permanent"
                    ),
                    "code": result.get("code", "REMOTE_FAILED"),
                    "message": result.get("message", ""),
                }
            if workflow.now() >= deadline:
                return {
                    "outcome": "transient",
                    "code": "REMOTE_TIMEOUT",
                    "message": (
                        "provider-side job did not finish within "
                        f"{int(max_wait)}s"
                    ),
                }
            # "pending" and transport "error" both keep polling.

    async def _transition(
        self,
        job_id: str,
        scope: str,
        to_status: str,
        *,
        detail: dict | None = None,
        failure: dict | None = None,
        fields: dict | None = None,
    ) -> None:
        await workflow.execute_activity(
            "transition",
            args=[job_id, scope, to_status, detail, failure, fields],
            start_to_close_timeout=_TRANSITION_TIMEOUT,
        )
