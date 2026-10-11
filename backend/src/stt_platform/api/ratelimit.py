"""Per-tenant request quotas (sliding window, in-process).

Scope of the guarantee: this limiter is per API process. A single-node
deployment gets exactly the behaviour documented in ``docs/operations.md``;
a fleet would move the same counters to the Redis the spec reserves for
ephemeral coordination. It is off by default (``STT_MAX_REQUESTS_PER_MINUTE=0``)
so the developer flow and the e2e drivers are unaffected.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import Depends, Request

from stt_platform.api.security import get_scope
from stt_platform.domain.errors import RateLimited


class SlidingWindowLimiter:
    """Allow ``limit`` calls per ``window_s`` per key; 0 disables it."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = max(0, limit)
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> None:
        if self.limit <= 0:
            return
        moment = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= moment - self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, int(hits[0] + self.window_s - moment) + 1)
                raise RateLimited(
                    f"rate limit exceeded: {self.limit} requests per "
                    f"{int(self.window_s)}s for this tenant",
                    retry_after_s=retry_after,
                )
            hits.append(moment)


def enforce_rate_limit(
    request: Request, scope: str = Depends(get_scope)
) -> None:
    """FastAPI dependency: charge this request to the caller's tenant."""
    limiter: SlidingWindowLimiter | None = getattr(
        request.app.state, "rate_limiter", None
    )
    if limiter is not None:
        limiter.check(scope)
