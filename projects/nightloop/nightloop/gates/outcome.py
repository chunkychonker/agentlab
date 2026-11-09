"""The typed results a gate can return.

Pure: no I/O. ``GateResult.advance`` is derived from the outcome rather than
stored beside it, so a result that says PASS but refuses to advance (or the
reverse) cannot be built.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Union

from ..model import Postcondition


class Verdict(enum.Enum):
    """What a review verdict file says. Mirrors ``.pipeline/verdict.sh``."""

    PASS = "PASS"
    FAIL = "FAIL"
    MISSING = "MISSING"  # absent, unreadable, not a regular file, or zero bytes
    MALFORMED = "MALFORMED"  # non-empty without exactly one valid VERDICT line


class Freshness(enum.Enum):
    """Whether a phase left an artifact newer than its start. Mirrors ``postcondition.sh``."""

    FRESH = "FRESH"  # at least one match has mtime >= floor
    STALE = "STALE"  # matches exist, none confirmed at or after the floor
    MISSING = "MISSING"  # no match at all


class Ungated(enum.Enum):
    """The outcome of ``Postcondition.NONE``: nothing was checked, by decision."""

    NONE = "NONE"


Outcome = Union[Verdict, Freshness, Ungated]

# The only outcomes that authorise moving to the next phase. Everything else
# fails closed.
_ADVANCING: frozenset[Outcome] = frozenset({Verdict.PASS, Freshness.FRESH, Ungated.NONE})

# Which outcome type each postcondition produces.
_OUTCOME_TYPE: Mapping[Postcondition, type] = MappingProxyType(
    {
        Postcondition.REVIEW_VERDICT: Verdict,
        Postcondition.RESEARCH_NOTE_FRESH: Freshness,
        Postcondition.INCREMENT_BUILT: Freshness,
        Postcondition.NONE: Ungated,
    }
)


@dataclass(frozen=True)
class GateResult:
    """One gate evaluation: which gate ran, what it found, and a log line.

    ``advance`` is the only field an orchestrator should branch on; it is True
    exactly for ``Verdict.PASS``, ``Freshness.FRESH`` and ``Ungated.NONE``.

    Raises ``TypeError`` if ``postcondition`` is not a ``Postcondition``, if
    ``outcome`` is not the outcome type that postcondition produces (e.g. a
    ``Freshness`` for ``REVIEW_VERDICT``), or if ``detail`` is not a str.
    Raises ``ValueError`` if ``detail`` is empty or spans more than one line.
    """

    postcondition: Postcondition
    outcome: Outcome
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.postcondition, Postcondition):
            raise TypeError(
                f"postcondition must be a Postcondition, got {type(self.postcondition).__name__}"
            )
        expected = _OUTCOME_TYPE[self.postcondition]
        if not isinstance(self.outcome, expected):
            raise TypeError(
                f"{self.postcondition.name} produces a {expected.__name__} outcome, "
                f"got {self.outcome!r}"
            )
        if not isinstance(self.detail, str):
            raise TypeError(f"detail must be a str, got {type(self.detail).__name__}")
        if not self.detail or "\n" in self.detail or "\r" in self.detail:
            raise ValueError(f"detail must be one non-empty line, got {self.detail!r}")

    @property
    def advance(self) -> bool:
        """True only if this outcome authorises the next phase (fail closed)."""
        return self.outcome in _ADVANCING
