"""Summarises metrics lines per phase, and per gate how many nights it ran and blocked.

Pure: no I/O. A port of ``.pipeline/metrics.sh``'s ``metrics_summary`` (the
last N distinct ``run_ts``; per phase ``n``, ``ok`` and medians that skip
nulls; a torn line is skipped, never fatal), plus the gate counts from the
2026-11-16 decision. Those counts need no new field. A gate *ran* on a line
whose ``postcondition_state`` is set and is not ``NONE``. It *blocked* when
such a line's ``outcome`` is ``GATE_FAILED``, or, for a line with no
``outcome`` (bash's format), when its state is one that refuses to advance.
A night is one distinct ``run_ts``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Iterable

from ..cycle.cycle import PHASE_ORDER
from ..gates.outcome import Freshness, Ungated, Verdict
from ..phase.status import PhaseStatus

_UNGATED_STATE = Ungated.NONE.name
_BLOCKED = PhaseStatus.GATE_FAILED.value
# For lines with no ``outcome`` (bash's ``metrics.sh`` writes none): the gate
# states that refuse to advance. Every Verdict/Freshness name except the one
# that advances; tests check this against ``GateResult.advance``.
REFUSING_STATES: frozenset[str] = frozenset(
    {o.name for o in Verdict if o is not Verdict.PASS}
    | {o.name for o in Freshness if o is not Freshness.FRESH}
)
_MS_PER_MIN = 60_000
MISSING = "-"  # what the table shows for a median with no values, as in bash


def _check_count(field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{field} must be >= 0, got {value}")


@dataclass(frozen=True)
class GateStats:
    """One phase's gate over the window.

    ``ran``: lines whose gate ran and was a real gate (state set, not
    ``NONE``). ``blocked``: those of them with outcome ``GATE_FAILED``.
    ``not_gated``: lines where the gate never ran (state null: CLI error,
    timeout, unreadable output). ``ungated``: lines with state ``NONE``.
    ``nights_ran`` / ``nights_blocked``: distinct ``run_ts`` among the ran /
    blocked lines.

    Raises ``TypeError``/``ValueError`` for a non-int or negative count, or
    if ``blocked > ran``, ``nights_blocked > nights_ran`` or
    ``nights_ran > ran``.
    """

    ran: int
    blocked: int
    not_gated: int
    ungated: int
    nights_ran: int
    nights_blocked: int

    def __post_init__(self) -> None:
        for field in ("ran", "blocked", "not_gated", "ungated", "nights_ran", "nights_blocked"):
            _check_count(field, getattr(self, field))
        if self.blocked > self.ran:
            raise ValueError(f"blocked ({self.blocked}) cannot exceed ran ({self.ran})")
        if self.nights_blocked > self.nights_ran:
            raise ValueError("nights_blocked cannot exceed nights_ran")
        if self.nights_ran > self.ran:
            raise ValueError("nights_ran cannot exceed ran")


@dataclass(frozen=True)
class PhaseRow:
    """One phase over the window: ``n`` records, ``ok`` with rc 0, its gate, and medians.

    Each median is ``None`` when no record had a usable value. The duration
    median is in minutes and is not rounded; ``render_report`` rounds.
    """

    phase: str
    n: int
    ok: int
    gate: GateStats
    median_min: float | None
    median_turns: float | None
    median_out_tokens: float | None
    median_cost_usd: float | None


@dataclass(frozen=True)
class Report:
    """The summary: how many nights were asked for and found, lines skipped, one row per phase.

    Raises ``TypeError``/``ValueError`` for non-int or out-of-range counts
    (``nights_asked >= 1``, ``nights_found <= nights_asked``).
    """

    nights_asked: int
    nights_found: int
    skipped_lines: int
    rows: tuple[PhaseRow, ...]

    def __post_init__(self) -> None:
        for field in ("nights_asked", "nights_found", "skipped_lines"):
            _check_count(field, getattr(self, field))
        if self.nights_asked < 1:
            raise ValueError("nights_asked must be >= 1")
        if self.nights_found > self.nights_asked:
            raise ValueError("nights_found cannot exceed nights_asked")


@dataclass(frozen=True)
class _Record:
    """The fields of one usable metrics line that the summary reads."""

    run_ts: str
    phase: str
    rc: int | None
    outcome: str | None
    state: str | None
    duration_ms: float | None
    num_turns: float | None
    out_tokens: float | None
    cost_usd: float | None


def summarise(lines: Iterable[str], nights: int) -> Report:
    """Summarise the metrics ``lines`` over the last ``nights`` distinct ``run_ts`` values.

    ``run_ts`` sorts by time (``YYYY-MM-DD_HHMMSS``), so "last" is the
    greatest. A line is skipped, and counted in ``skipped_lines``, if it is
    not JSON (including NaN/Infinity and nesting too deep to parse), is not
    an object, lacks a non-empty string ``run_ts`` or ``phase``, or has a
    ``postcondition_state`` that is neither a string nor null. Lines of
    whitespace only are ignored and not counted. Any other field of the
    wrong type is read as null, as in bash: it drops out of its median and
    out of ``ok``. A ``bool`` rc is not 0.

    Rows come in ``PHASE_ORDER``, then any other phase name sorted.
    Raises ``TypeError`` if ``nights`` is not an int and ``ValueError`` if
    it is below 1. Never raises for the content of a line.
    """
    if isinstance(nights, bool) or not isinstance(nights, int):
        raise TypeError(f"nights must be an int, got {type(nights).__name__}")
    if nights < 1:
        raise ValueError(f"nights must be >= 1, got {nights}")
    records: list[_Record] = []
    skipped = 0
    for line in lines:
        if line.strip() == "":
            continue
        record = _parse(line)
        if record is None:
            skipped += 1
        else:
            records.append(record)
    keep = set(sorted({r.run_ts for r in records})[-nights:])
    kept = [r for r in records if r.run_ts in keep]
    by_phase: dict[str, list[_Record]] = {}
    for record in kept:
        by_phase.setdefault(record.phase, []).append(record)
    order = [p for p in PHASE_ORDER if p in by_phase] + sorted(set(by_phase) - set(PHASE_ORDER))
    return Report(
        nights_asked=nights,
        nights_found=len(keep),
        skipped_lines=skipped,
        rows=tuple(_row(phase, by_phase[phase]) for phase in order),
    )


def render_report(report: Report) -> str:
    """The report as text: a header, the phase table and the gate table, both tab-separated.

    A gate's note is ``never blocked in N night(s)`` when it ran on N > 0
    nights and blocked on none (a candidate to retire), ``ungated`` when
    only ``NONE`` lines were seen, otherwise ``-``. Never raises.
    """
    header = f"metrics: {report.nights_found} night(s) of data"
    if report.nights_found < report.nights_asked:
        header += f" (asked for {report.nights_asked})"
    lines = [header]
    if report.skipped_lines:
        lines.append(f"skipped {report.skipped_lines} unreadable line(s)")
    lines += ["", "\t".join(("phase", "n", "ok", "median_min", "median_turns",
                             "median_out_tokens", "median_cost_usd"))]
    for row in report.rows:
        lines.append("\t".join((
            row.phase, str(row.n), str(row.ok),
            _show(None if row.median_min is None else _round_half_up(row.median_min, 1)),
            _show(row.median_turns),
            _show(None if row.median_out_tokens is None else _round_half_up(row.median_out_tokens, 0)),
            _show(None if row.median_cost_usd is None else _round_half_up(row.median_cost_usd, 2)),
        )))
    lines += ["", "\t".join(("gate", "ran", "blocked", "not_gated", "ungated",
                             "nights_ran", "nights_blocked", "note"))]
    for row in report.rows:
        g = row.gate
        lines.append("\t".join((
            row.phase, str(g.ran), str(g.blocked), str(g.not_gated), str(g.ungated),
            str(g.nights_ran), str(g.nights_blocked), _note(g),
        )))
    return "\n".join(lines)


def _row(phase: str, records: list[_Record]) -> PhaseRow:
    ran = [r for r in records if r.state is not None and r.state != _UNGATED_STATE]
    blocked = [r for r in ran if _blocked(r)]
    durations = _median([r.duration_ms for r in records])
    return PhaseRow(
        phase=phase,
        n=len(records),
        ok=sum(1 for r in records if r.rc == 0),
        gate=GateStats(
            ran=len(ran),
            blocked=len(blocked),
            not_gated=sum(1 for r in records if r.state is None),
            ungated=sum(1 for r in records if r.state == _UNGATED_STATE),
            nights_ran=len({r.run_ts for r in ran}),
            nights_blocked=len({r.run_ts for r in blocked}),
        ),
        median_min=None if durations is None else durations / _MS_PER_MIN,
        median_turns=_median([r.num_turns for r in records]),
        median_out_tokens=_median([r.out_tokens for r in records]),
        median_cost_usd=_median([r.cost_usd for r in records]),
    )


def _blocked(record: _Record) -> bool:
    """Whether a line whose gate ran records a block.

    A nightloop line has ``outcome``, and blocked means ``GATE_FAILED``. A
    line without one (bash's format) is blocked when its state is one that
    refuses to advance (``STALE``, ``MISSING``, ``FAIL``, ``MALFORMED``).
    Without this, bash data could never show a block, and every gate in it
    would read "never blocked".
    """
    if record.outcome is not None:
        return record.outcome == _BLOCKED
    return record.state in REFUSING_STATES


def _note(gate: GateStats) -> str:
    if gate.nights_ran > 0 and gate.nights_blocked == 0:
        return f"never blocked in {gate.nights_ran} night(s)"
    if gate.ran == 0 and gate.ungated > 0:
        return "ungated"
    return MISSING


def _parse(line: str) -> _Record | None:
    """The usable fields of ``line``, or ``None`` if it cannot be placed in a night and phase."""
    try:
        doc = json.loads(line, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None
    if not isinstance(doc, dict):
        return None
    run_ts, phase, state = doc.get("run_ts"), doc.get("phase"), doc.get("postcondition_state")
    if not (isinstance(run_ts, str) and run_ts and isinstance(phase, str) and phase):
        return None
    if state is not None and not isinstance(state, str):
        return None
    rc = doc.get("rc")
    outcome = doc.get("outcome")
    tokens = doc.get("tokens")
    return _Record(
        run_ts=run_ts,
        phase=phase,
        rc=rc if isinstance(rc, int) and not isinstance(rc, bool) else None,
        outcome=outcome if isinstance(outcome, str) else None,
        state=state,
        duration_ms=_number(doc.get("duration_ms")),
        num_turns=_number(doc.get("num_turns")),
        out_tokens=_number(tokens.get("output")) if isinstance(tokens, dict) else None,
        cost_usd=_number(doc.get("total_cost_usd")),
    )


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not JSON")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:  # an int too large for a float is not a usable measurement
        return None
    return number if math.isfinite(number) else None


def _median(values: list[float | None]) -> float | None:
    present = sorted(v for v in values if v is not None)
    if not present:
        return None
    mid = len(present) // 2
    if len(present) % 2 == 1:
        return present[mid]
    return (present[mid - 1] + present[mid]) / 2


def _round_half_up(value: float, places: int) -> float:
    """Round as jq's ``round`` does for the non-negative values here: halves go up."""
    scale = 10**places
    return math.floor(value * scale + 0.5) / scale


def _show(value: float | None) -> str:
    if value is None:
        return MISSING
    if value == int(value):
        return str(int(value))
    return repr(value)
