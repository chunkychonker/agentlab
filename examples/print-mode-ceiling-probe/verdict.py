"""Pure classification of the `claude -p` background-wait ceiling.

No I/O, no environment reads, no clock. Two questions, answered from values the
caller already holds:

* ``check_ceiling(env)`` -- given the environment a ``claude`` process actually
  received, what idle-wait ceiling will it apply?
* ``find_truncation(output)`` -- does captured output carry the signature of a
  run that the ceiling killed (which exits 0, so the exit code cannot tell you)?
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Union

ENV_VAR = "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"
DEFAULT_CEILING_MS = 600_000

# Canonical decimal only: "0", or a positive integer with no leading zero.
# Anything else ("", " 0", "00", "-1", "off", "600s") is Invalid -- we do not
# know how Claude Code parses it, so we refuse to call it proven.
_CANONICAL_INT = re.compile(r"0|[1-9][0-9]*")

_TRUNCATION = re.compile(r"Background tasks still running after (\d+)s; terminating")


@dataclass(frozen=True)
class Off:
    """The variable is exactly ``"0"``: wait for background tasks indefinitely."""

    label = "OFF"


@dataclass(frozen=True)
class DefaultCeiling:
    """The variable is absent: Claude Code applies its 600s default."""

    label = "DEFAULT_600S"


@dataclass(frozen=True)
class Bounded:
    """The variable is a positive integer: a non-default, finite ceiling."""

    ms: int
    label = "BOUNDED"


@dataclass(frozen=True)
class Invalid:
    """The variable is present but not a canonical non-negative integer."""

    raw: str
    label = "INVALID"


CeilingVerdict = Union[Off, DefaultCeiling, Bounded, Invalid]


@dataclass(frozen=True)
class TruncationFinding:
    """The first ceiling-kill line in some output.

    ``line_number`` is 1-based. ``waited_seconds`` is the number Claude Code
    printed, which is 600 unless a non-default ceiling was in force.
    """

    waited_seconds: int
    line_number: int


def check_ceiling(env: Mapping[str, str]) -> CeilingVerdict:
    """Classify the ceiling a process with environment ``env`` will apply.

    Total and pure: never raises, never reads the real environment. An unset
    variable (``DefaultCeiling``) is deliberately distinct from an empty one
    (``Invalid("")``).
    """
    if ENV_VAR not in env:
        return DefaultCeiling()
    raw = env[ENV_VAR]
    if _CANONICAL_INT.fullmatch(raw) is None:
        return Invalid(raw)
    ms = int(raw)
    if ms == 0:
        return Off()
    return Bounded(ms)


def find_truncation(output: str) -> TruncationFinding | None:
    """Return the first ceiling-kill line in ``output``, or ``None`` if absent.

    Pure; never raises. ``None`` means the signature is not present -- it is
    never a stand-in for "could not tell".
    """
    for index, line in enumerate(output.splitlines(), start=1):
        match = _TRUNCATION.search(line)
        if match is not None:
            return TruncationFinding(waited_seconds=int(match.group(1)), line_number=index)
    return None
