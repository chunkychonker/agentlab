"""Mechanical, fail-closed gates that decide whether a phase may hand off.

Public API::

    from nightloop.gates import evaluate, GateResult, Verdict, Freshness, Ungated

``evaluate`` is what the phase runner calls; the pure ``parse_verdict`` and
``classify_freshness`` and the filesystem ``check_*`` functions are exported
for callers that need one gate on its own.
"""

from __future__ import annotations

from .dispatch import evaluate
from .freshness import classify_freshness
from .fs import check_freshness, check_increment, check_verdict
from .outcome import Freshness, GateResult, Outcome, Ungated, Verdict
from .verdict import parse_verdict

__all__ = [
    "Freshness",
    "GateResult",
    "Outcome",
    "Ungated",
    "Verdict",
    "check_freshness",
    "check_increment",
    "check_verdict",
    "classify_freshness",
    "evaluate",
    "parse_verdict",
]
