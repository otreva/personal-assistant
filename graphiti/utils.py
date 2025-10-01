"""Common helper utilities used across Graphiti modules."""
from __future__ import annotations

import random
import time


def sleep_with_jitter(base: float = 0.5, jitter: float = 0.25) -> float:
    """Sleep for a duration around *base* seconds with +/- *jitter* randomness."""

    jitter = max(jitter, 0.0)
    lower = max(base - jitter, 0.0)
    upper = max(base + jitter, 0.0)
    delay = random.uniform(lower, upper)
    if delay > 0:
        time.sleep(delay)
    return delay


__all__ = ["sleep_with_jitter"]
