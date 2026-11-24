# An hour-of-day run window (research note)

## Question

A nightly job may only start inside a window of local hours, say 1 to 6.
What is the smallest rule that has no off-by-one at either end and refuses
a window it cannot represent?

## Findings

- Half-open intervals, start inclusive and end exclusive, compose without
  gaps or overlaps: `[1, 6)` and `[6, 9)` share no hour. Then `end = 24`
  means "until midnight".
- A window that wraps midnight (22 to 2) needs a second rule. Refusing it
  is simpler and honest; callers can configure two windows later.

## Build proposal

`window.py` with `in_window(hour, start, end) -> bool`, a README, and tests.

Acceptance criteria:

1. `in_window(hour, start, end)` is `True` exactly when
   `start <= hour < end`: the start hour is in the window, the end hour is
   not.
2. `ValueError` (naming the argument) if `hour` is not an int in 0..23,
   `start` not an int in 0..23, `end` not an int in 1..24, or
   `end <= start` (no wrapping windows). A bool is not an int.
3. Tests: `python3 -m unittest discover -v` from the repo root passes, and
   cover both boundaries.

Out of scope: windows that wrap midnight, minutes, time zones (the caller
passes a local hour).
