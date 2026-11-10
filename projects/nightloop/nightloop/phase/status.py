"""The closed set of ways one phase run can end.

Pure: no I/O. Named ``PhaseStatus`` (not ``Outcome``) so it cannot be
confused with ``nightloop.gates.Outcome``, the gate's own result type.
"""

from __future__ import annotations

import enum


class PhaseStatus(enum.Enum):
    """How a phase run ended. Only ``GATED_PASS`` lets the night advance."""

    GATED_PASS = "GATED_PASS"  # rc 0, is_error not true, gate advanced
    GATE_FAILED = "GATE_FAILED"  # rc 0, is_error not true, gate refused
    CLI_ERROR = "CLI_ERROR"  # rc != 0, or the CLI reported is_error: true
    TIMED_OUT = "TIMED_OUT"  # the runner hit the phase's wall-clock limit
    UNREADABLE = "UNREADABLE"  # rc 0 but stdout is not one CLI result object


# The statuses for which the gate ran, so a GateResult exists.
GATED: frozenset[PhaseStatus] = frozenset({PhaseStatus.GATED_PASS, PhaseStatus.GATE_FAILED})
