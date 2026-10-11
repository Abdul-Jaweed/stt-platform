"""Bounded exponential backoff with jitter.

Pure function: the random source is injected by the caller (the Temporal
workflow passes ``workflow.random()`` so replays are deterministic).
"""


def compute_backoff(
    attempt: int,
    *,
    base: float,
    multiplier: float,
    cap: float,
    jitter_ratio: float,
    rand: float,
) -> float:
    """Delay for retry ``attempt`` (1-based): ``min(cap, base*m^(n-1))``,
    scaled by ``1 + jitter_ratio*rand`` where ``rand`` is in [0, 1)."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    delay = min(cap, base * (multiplier ** (attempt - 1)))
    return delay * (1.0 + jitter_ratio * rand)
