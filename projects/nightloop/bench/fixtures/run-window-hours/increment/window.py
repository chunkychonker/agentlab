"""A half-open window of local hours, [start, end), that does not wrap midnight."""

from __future__ import annotations


def _check(name: str, value: object, lo: int, hi: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be in {lo}..{hi}, got {value}")


def in_window(hour: int, start: int, end: int) -> bool:
    """``True`` when ``start <= hour < end``; ``ValueError`` naming the argument for a bad value."""
    _check("hour", hour, 0, 23)
    _check("start", start, 0, 23)
    _check("end", end, 1, 24)
    if end <= start:
        raise ValueError(f"end must be greater than start ({start}); windows do not wrap, got {end}")
    return start <= hour < end
