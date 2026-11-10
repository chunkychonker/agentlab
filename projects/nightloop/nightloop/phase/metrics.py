"""Turns one phase run into one metrics JSON line.

Pure: no I/O. The line's field names follow agentlab's ``.pipeline/metrics.sh``
(``tokens.input`` ... ``denials``, ``postcondition_state``), so the two
pipelines' metrics can be read by the same report. ``run_ts`` and ``cycle``
are not here: they belong to the cycle orchestrator (milestone 4), which
knows them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .result import CliResult
from .status import GATED, PhaseStatus


@dataclass(frozen=True)
class PhaseRecord:
    """The facts about one phase run that go into its metrics line.

    ``rc`` is the CLI's exit code as the runner saw it (negative signal
    number after a timeout kill), not a pipeline verdict; ``outcome`` is the
    verdict. ``postcondition_state`` is the gate outcome's name (``FRESH``,
    ``PASS``, ...) and is set exactly when the gate ran. ``cli`` is ``None``
    exactly when stdout could not be parsed, which the line reports as
    ``parse_error: true`` with every CLI field null.

    Raises ``TypeError`` if a field has the wrong type and ``ValueError`` if
    ``postcondition_state`` is set for an ungated outcome or missing for a
    gated one.
    """

    phase: str
    model: str
    rc: int
    outcome: PhaseStatus
    postcondition_state: str | None
    cli: CliResult | None

    def __post_init__(self) -> None:
        for field in ("phase", "model"):
            if not isinstance(getattr(self, field), str):
                raise TypeError(f"{field} must be a str")
        if isinstance(self.rc, bool) or not isinstance(self.rc, int):
            raise TypeError(f"rc must be an int, got {type(self.rc).__name__}")
        if not isinstance(self.outcome, PhaseStatus):
            raise TypeError(f"outcome must be a PhaseStatus, got {type(self.outcome).__name__}")
        if self.postcondition_state is not None and not isinstance(self.postcondition_state, str):
            raise TypeError("postcondition_state must be a str or None")
        if self.cli is not None and not isinstance(self.cli, CliResult):
            raise TypeError(f"cli must be a CliResult or None, got {type(self.cli).__name__}")
        gated = self.outcome in GATED
        if gated != (self.postcondition_state is not None):
            raise ValueError(
                f"postcondition_state must be set exactly when the gate ran; "
                f"outcome {self.outcome.value} with postcondition_state "
                f"{self.postcondition_state!r}"
            )


def to_json_line(record: PhaseRecord) -> str:
    """Return ``record`` as one compact JSON object, with no newline, keys in a fixed order.

    Absent CLI fields are JSON null, never 0; non-ASCII text is escaped.
    Raises ``ValueError`` only if ``record.cli`` was built by hand with a
    NaN or infinite cost, since that is not JSON (``parse_cli_json`` never
    produces one).
    """
    doc: dict[str, Any] = {
        "phase": record.phase,
        "model": record.model,
        "rc": record.rc,
        "outcome": record.outcome.value,
        "postcondition_state": record.postcondition_state,
        **_cli_fields(record.cli),
        "parse_error": record.cli is None,
        "denials": None if record.cli is None else record.cli.denials,
    }
    return json.dumps(doc, separators=(",", ":"), allow_nan=False)


def _cli_fields(cli: CliResult | None) -> dict[str, Any]:
    """The CLI-reported fields, in line order; all null when stdout was unreadable."""
    if cli is None:
        return {
            "duration_ms": None,
            "num_turns": None,
            "tokens": {"input": None, "output": None, "cache_read": None, "cache_creation": None},
            "total_cost_usd": None,
            "session_id": None,
            "is_error": None,
        }
    return {
        "duration_ms": cli.duration_ms,
        "num_turns": cli.num_turns,
        "tokens": {
            "input": cli.usage.input_tokens,
            "output": cli.usage.output_tokens,
            "cache_read": cli.usage.cache_read_input_tokens,
            "cache_creation": cli.usage.cache_creation_input_tokens,
        },
        "total_cost_usd": cli.total_cost_usd,
        "session_id": cli.session_id,
        "is_error": cli.is_error,
    }
