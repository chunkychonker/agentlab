"""Decides what one benchmark trial measured.

Pure: no I/O. The verdict comes from the real gate (``check_verdict`` ->
``parse_verdict``), so "what counts as a verdict" is the same as in a night.
"""

from __future__ import annotations

import enum
import re

from ..gates.outcome import Verdict
from .fixture import Kind


class Outcome(enum.Enum):
    """How one trial ended, as the benchmark counts it."""

    CAUGHT = "CAUGHT"  # seeded, FAIL, every pattern found in the verdict text
    MISDIAGNOSED = "MISDIAGNOSED"  # seeded, FAIL, but not for the seeded bug
    MISSED = "MISSED"  # seeded, PASS
    CLEAN_PASSED = "CLEAN_PASSED"  # clean, PASS
    FALSE_POSITIVE = "FALSE_POSITIVE"  # clean, FAIL
    UNGRADED = "UNGRADED"  # no gradable verdict: counted in neither rate


# Which outcomes each kind can have, apart from UNGRADED. The catch rate's
# denominator is SEEDED_GRADED; the false-positive rate's is CLEAN_GRADED.
SEEDED_GRADED = (Outcome.CAUGHT, Outcome.MISDIAGNOSED, Outcome.MISSED)
CLEAN_GRADED = (Outcome.CLEAN_PASSED, Outcome.FALSE_POSITIVE)
GRADED_BY_KIND = {Kind.SEEDED: SEEDED_GRADED, Kind.CLEAN: CLEAN_GRADED}


def classify(kind: Kind, verdict: Verdict | None, findings: str, patterns: tuple[str, ...]) -> Outcome:
    """The ``Outcome`` of one trial.

    ``verdict`` is the gate's reading of the verdict file, or ``None`` when
    the phase ended without the gate running (CLI error, timeout,
    unreadable output). ``findings`` is the whole verdict file's text:
    patterns are searched anywhere in it, case-insensitively, not only
    under ``Findings:``. ``patterns`` are ignored for a clean fixture.

    ``None``, ``MISSING`` and ``MALFORMED`` are ``UNGRADED`` for either
    kind; they are never read as a PASS or a FAIL. Never raises for valid
    input (a ``Fixture``'s patterns already compile).
    """
    if verdict is None or verdict in (Verdict.MISSING, Verdict.MALFORMED):
        return Outcome.UNGRADED
    if kind is Kind.CLEAN:
        return Outcome.CLEAN_PASSED if verdict is Verdict.PASS else Outcome.FALSE_POSITIVE
    if verdict is Verdict.PASS:
        return Outcome.MISSED
    if all(re.search(p, findings, re.IGNORECASE) for p in patterns):
        return Outcome.CAUGHT
    return Outcome.MISDIAGNOSED
