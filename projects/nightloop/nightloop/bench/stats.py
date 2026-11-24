"""The Wilson score interval for a binomial proportion.

Pure: no I/O. Chosen over the normal approximation because the benchmark's
n is small and its rates sit near 0 or 1, where the normal interval
undercovers (Agresti & Coull 1998; Brown, Cai & DasGupta 2001).
"""

from __future__ import annotations

import math

# Two-sided 95%.
Z_95 = 1.96


def wilson(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """The Wilson score interval ``(low, high)`` for ``successes`` out of ``n``.

    Both bounds are in ``[0, 1]`` and contain ``successes / n``; ``low`` is
    exactly 0.0 when ``successes == 0`` and ``high`` exactly 1.0 when
    ``successes == n``.

    Raises ``TypeError`` if ``successes`` or ``n`` is not an int (a bool is
    not one) or ``z`` is not a number; ``ValueError`` if ``n <= 0``,
    ``successes`` is outside ``0..n``, or ``z`` is not a positive finite
    number. A rate with no trials has no interval; the caller reports n/a.
    """
    for name, value in (("successes", successes), ("n", n)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if isinstance(z, bool) or not isinstance(z, (int, float)):
        raise TypeError(f"z must be a number, got {type(z).__name__}")
    if n <= 0:
        raise ValueError(f"n must be > 0, got {n}")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be in 0..{n}, got {successes}")
    if not (math.isfinite(z) and z > 0):
        raise ValueError(f"z must be a positive finite number, got {z}")
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    low = 0.0 if successes == 0 else max(0.0, min(p, centre - half))
    high = 1.0 if successes == n else min(1.0, max(p, centre + half))
    return low, high
