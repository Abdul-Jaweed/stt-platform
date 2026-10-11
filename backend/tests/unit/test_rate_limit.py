"""Per-tenant request quotas: a sliding window, honest about its scope.

This limiter lives in the API process. That is a deliberate, documented
shortcut for a single-node deployment: the spec's Redis is the production
path for a fleet. What matters here is that the *behaviour* is right —
per-tenant, refill-as-time-passes, and never applied when disabled.
"""
from __future__ import annotations

import pytest

from stt_platform.api.ratelimit import SlidingWindowLimiter
from stt_platform.domain.errors import RateLimited


def test_allows_up_to_the_limit_inside_the_window():
    limiter = SlidingWindowLimiter(limit=3, window_s=60.0)
    for _ in range(3):
        limiter.check("acme", now=100.0)


def test_rejects_the_request_after_the_limit():
    limiter = SlidingWindowLimiter(limit=2, window_s=60.0)
    limiter.check("acme", now=100.0)
    limiter.check("acme", now=100.5)
    with pytest.raises(RateLimited):
        limiter.check("acme", now=101.0)


def test_the_window_slides_and_the_budget_refills():
    limiter = SlidingWindowLimiter(limit=2, window_s=60.0)
    limiter.check("acme", now=100.0)
    limiter.check("acme", now=100.0)
    with pytest.raises(RateLimited):
        limiter.check("acme", now=100.0)
    # 61 s later the first two calls have left the window.
    limiter.check("acme", now=161.5)
    limiter.check("acme", now=161.5)


def test_scopes_have_independent_budgets():
    limiter = SlidingWindowLimiter(limit=1, window_s=60.0)
    limiter.check("acme", now=100.0)
    with pytest.raises(RateLimited):
        limiter.check("acme", now=100.0)
    limiter.check("globex", now=100.0)  # unaffected


def test_a_zero_limit_disables_the_limiter():
    limiter = SlidingWindowLimiter(limit=0, window_s=60.0)
    for _ in range(50):
        limiter.check("acme", now=100.0)


def test_the_error_says_when_to_come_back():
    limiter = SlidingWindowLimiter(limit=1, window_s=60.0)
    limiter.check("acme", now=100.0)
    with pytest.raises(RateLimited) as excinfo:
        limiter.check("acme", now=100.0)
    assert excinfo.value.retry_after_s >= 1
    assert "rate" in str(excinfo.value).lower()
