"""The transcription workflow: bounded retries, every attempt visible.

Retries are implemented HERE rather than via Temporal's activity
RetryPolicy, because each failure must write a RETRY_WAIT transition
into PostgreSQL — the trail is part of the product, not a side channel.
Workflow code stays deterministic: delays are computed with the
deterministic ``workflow.random()`` RNG and slept with ``workflow.sleep``.
"""
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

from stt_platform.workers.retry import compute_backoff

_ATTEMPT_TIMEOUT = timedelta(hours=4)
_TRANSITION_TIMEOUT = timedelta(seconds=30)
_ONE_SHOT = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="TranscriptionWorkflow")
class TranscriptionWorkflow:
    @workflow.run
    async def run(self, job: dict) -> str:
        job_id = job["job_id"]
        scope = job.get("scope", "local")
        retry = job.get("retry", {})
        max_attempts = int(retry.get("max_attempts", 3))
        attempt = 0
        try:
            while True:
                attempt += 1
                try:
                    outcome = await workflow.execute_activity(
                        "execute_attempt",
                        args=[job_id, scope, attempt],
                        start_to_close_timeout=_ATTEMPT_TIMEOUT,
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
        except CancelledError:
            # Record CANCELLED best-effort, then propagate. The cancellation
            # integration test verifies this write lands; a failed write here
            # must never mask the cancellation itself.
            try:
                await self._transition(job_id, scope, "CANCELLED")
            except Exception:  # noqa: BLE001 - benign cancellation race
                pass
            raise

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
