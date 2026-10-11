"""Bounded exponential backoff with jitter (pure function)."""
import pytest

from stt_platform.workers.retry import compute_backoff


def _backoff(attempt: int, rand: float = 0.0, jitter: float = 0.0) -> float:
    return compute_backoff(
        attempt, base=2.0, multiplier=2.0, cap=60.0, jitter_ratio=jitter, rand=rand
    )


def test_growth_doubles_from_base_without_jitter():
    assert _backoff(1) == 2.0
    assert _backoff(2) == 4.0
    assert _backoff(3) == 8.0


def test_cap_is_respected():
    assert _backoff(10) == 60.0
    assert _backoff(50) == 60.0


def test_jitter_scales_with_rand_and_stays_within_bound():
    assert _backoff(2, rand=0.0, jitter=0.25) == 4.0
    assert _backoff(2, rand=0.5, jitter=0.25) == pytest.approx(4.5)
    assert _backoff(2, rand=1.0, jitter=0.25) == pytest.approx(5.0)


def test_attempt_must_be_positive():
    with pytest.raises(ValueError):
        _backoff(0)
