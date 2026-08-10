"""Pure decision layer for failing tool calls: which of three dispositions an
exception maps to, and how long to wait before a local retry.

This module imports nothing from ``anthropic``, performs no I/O, reads no
environment, and never sleeps. Every function is a pure function of its
arguments, so the entire failure policy is auditable and testable offline.

The three dispositions come from the taxonomy in
``knowledge/tool-failure-taxonomy.md``:

    Retry   - transient, the model cannot help; handle it locally with backoff.
    Report  - recoverable by the model; hand it back as ``is_error: true``.
    Abort   - terminal; neither retrying nor re-prompting can succeed.

See the research note this came from:
    research/2026-08-10-tool-error-handling-retries.md
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence

# --------------------------------------------------------------------------- #
# Tool-side exception types
# --------------------------------------------------------------------------- #


class TransientToolError(Exception):
    """Raised by a tool when the failure is worth retrying locally.

    Connection reset, upstream 503, upstream rate limit - anything where the
    same call with the same arguments could plausibly succeed a moment later.
    """


class FatalToolError(Exception):
    """Raised by a tool when no retry and no re-prompt can succeed.

    Auth/permission denied, budget exhausted, a disabled account. Continuing
    the run only spends money.
    """


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
#
# A tagged union rather than one dataclass with a `disposition` enum and a
# `message` that is meaningless for RETRY: a Retry decision carrying an error
# message is an illegal state, so it should not be representable at all.


@dataclasses.dataclass(frozen=True)
class Retry:
    """Call the same tool again, after ``backoff_delay(attempt, ...)`` seconds.

    Carries no message: nothing is reported to the model on a retry, which is
    the entire point of handling this class locally.
    """


@dataclasses.dataclass(frozen=True)
class Report:
    """Hand the failure back to the model as a ``tool_result`` with
    ``is_error: true``.

    ``message`` becomes the tool_result content verbatim, so it must say what
    went wrong *and* what to try next - never a bare "failed".
    """

    message: str


@dataclasses.dataclass(frozen=True)
class Abort:
    """Stop the run and raise. ``reason`` is the exception message."""

    reason: str


Decision = Retry | Report | Abort


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def classify(exc: BaseException, *, attempt: int, max_attempts: int) -> Decision:
    """Map an exception raised by a tool to its disposition.

    ``attempt`` is 1-based and counts invocations of this one tool call;
    ``max_attempts`` is the local retry budget for it.

    Rules:
      * ``FatalToolError``    -> ``Abort``, immediately, with zero retries.
      * ``TransientToolError``-> ``Retry`` while ``attempt < max_attempts``,
        then degrades to ``Report`` naming the number of attempts made.
      * anything else         -> ``Report``. Unknown is not the same as safe to
        retry, so the conservative disposition is to let the model decide.

    Failure modes: raises ``ValueError`` if ``max_attempts < 1`` or if
    ``attempt`` is outside ``1..max_attempts``. It never returns a fallback
    decision for a bad budget - that would hide a caller bug inside a
    retry loop, which is exactly where it would be most expensive.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    if not 1 <= attempt <= max_attempts:
        raise ValueError(f"attempt must be in 1..{max_attempts}, got {attempt}")

    if isinstance(exc, FatalToolError):
        return Abort(f"terminal tool failure, aborting run: {exc}")

    if isinstance(exc, TransientToolError):
        if attempt < max_attempts:
            return Retry()
        return Report(
            f"The tool failed {max_attempts} times in a row with a transient error "
            f"({_describe(exc)}). It appears to be down. Do not request it again - "
            f"use a different tool or tell the user this data is unavailable."
        )

    return Report(
        f"{_describe(exc)}. Check the arguments against the tool's input schema "
        f"and try a corrected call, or use a different tool."
    )


def _describe(exc: BaseException) -> str:
    """Render an exception as ``TypeName: message``.

    Keeps the exception type, like the SDK's ``repr``-based
    ``tool_error_content()``, but without the quoting noise of ``repr`` - the
    string is going into a prompt, not a log.
    """
    return f"{type(exc).__name__}: {exc}"


def unknown_tool_message(name: str, available: Sequence[str]) -> str:
    """Build the tool_result content for a tool name that is not registered.

    Names every registered tool, because "unknown tool" alone gives the model
    nothing to correct towards.

    Failure modes: raises ``ValueError`` if ``available`` is empty - an agent
    advertising no tools that is nonetheless dispatching one is a caller bug,
    not a model error.
    """
    if not available:
        raise ValueError("no tools are registered; cannot build a recovery message")
    listed = ", ".join(available)
    return (
        f"Unknown tool {name!r}. The available tools are: {listed}. "
        f"Call one of those instead."
    )


# --------------------------------------------------------------------------- #
# Backoff
# --------------------------------------------------------------------------- #

# random.random() -> [0, 1) is mapped to a multiplicative factor in
# [JITTER_FLOOR, 1.0]. This mirrors the Anthropic SDK's own
# `1 - 0.25 * random()` in `_base_client.py`: jitter only ever *shortens* the
# computed delay, so the cap is a true ceiling.
JITTER_FLOOR = 0.75

# 2 ** 32 is already astronomically past any sane cap; clamping the exponent
# keeps `base * 2 ** (attempt - 1)` from overflowing to inf on a runaway attempt
# counter, so the function stays total.
_MAX_DOUBLINGS = 32


def jitter_factor(raw: float) -> float:
    """Map a uniform random draw to a multiplicative backoff factor.

    ``raw`` is ``random.random()``'s output. Returns a value in
    ``[JITTER_FLOOR, 1.0]``, so jittered delays are never *longer* than the
    computed delay.

    Failure modes: raises ``ValueError`` unless ``0.0 <= raw <= 1.0``.
    """
    if not 0.0 <= raw <= 1.0:
        raise ValueError(f"raw jitter draw must be in [0.0, 1.0], got {raw}")
    return 1.0 - (1.0 - JITTER_FLOOR) * raw


def backoff_delay(attempt: int, *, base: float, cap: float, jitter: float) -> float:
    """Seconds to wait before retry number ``attempt`` (1-based).

    ``min(base * 2 ** (attempt - 1), cap) * jitter``. Because ``jitter <= 1``,
    the result never exceeds ``cap``.

    Failure modes: raises ``ValueError`` if ``attempt < 1``, ``base <= 0``,
    ``cap < base``, or ``jitter`` is outside ``(0.0, 1.0]``.
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    if base <= 0:
        raise ValueError(f"base must be > 0, got {base}")
    if cap < base:
        raise ValueError(f"cap must be >= base ({base}), got {cap}")
    if not 0.0 < jitter <= 1.0:
        raise ValueError(f"jitter must be in (0.0, 1.0], got {jitter}")

    uncapped = base * (2 ** min(attempt - 1, _MAX_DOUBLINGS))
    return min(uncapped, cap) * jitter


# --------------------------------------------------------------------------- #
# Repeat guard (the "terminal" row of the taxonomy that everyone skips)
# --------------------------------------------------------------------------- #


def call_key(name: str, tool_input: object) -> str:
    """A stable identity for one ``(tool name, arguments)`` pair.

    Used to notice the model re-issuing a call that has already failed. Keys
    are order-insensitive: ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` are the
    same call.

    Failure modes: raises ``TypeError`` if ``tool_input`` is not
    JSON-serializable. Tool input arriving from the Messages API always is, so
    a failure here means the caller invented the input and should be fixed.
    """
    return f"{name}:{json.dumps(tool_input, sort_keys=True)}"


def is_repeat_exhausted(report_count: int, *, repeat_limit: int) -> bool:
    """Whether an identical failing call has now been reported too many times.

    ``report_count`` is how many times this exact call has been reported to the
    model as an error, *including* the report about to be sent. Returns ``True``
    once that exceeds ``repeat_limit``: the model has had ``repeat_limit``
    chances to correct and used none of them, so another turn buys nothing.

    Failure modes: raises ``ValueError`` if ``report_count < 1`` or
    ``repeat_limit < 1``.
    """
    if report_count < 1:
        raise ValueError(f"report_count must be >= 1, got {report_count}")
    if repeat_limit < 1:
        raise ValueError(f"repeat_limit must be >= 1, got {repeat_limit}")
    return report_count > repeat_limit


def repeat_abort_message(name: str, report_count: int) -> str:
    """The abort reason for a model stuck re-issuing one failing call."""
    return (
        f"terminal: the model re-issued the same failing call to {name!r} "
        f"{report_count} times without correcting it; aborting instead of "
        f"spending another turn"
    )
