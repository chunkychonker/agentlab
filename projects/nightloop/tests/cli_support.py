"""Shared builders for the milestone-7 CLI tests (not a test module).

Metrics lines are built with the real writer (``PhaseRecord`` ->
``to_json_line`` -> ``tag_line``), so the summary is tested against what
``run_night`` actually writes, not against a hand-copied shape.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from nightloop.cycle.metrics_tag import tag_line
from nightloop.cycle.run_id import RunId
from nightloop.gates.outcome import Freshness, GateResult, Ungated, Verdict
from nightloop.model import Postcondition
from nightloop.phase.metrics import PhaseRecord, to_json_line
from nightloop.phase.result import CliResult, Usage
from nightloop.phase.status import PhaseStatus

# research/build ungated, review gated on the verdict file: the real gate
# (nightloop.gates.evaluate) can then run with no artifact but the verdict.
CONFIG_TEMPLATE = """\
schema_version = 1
[repo]
backlog = "BACKLOG.md"
[run]
cycles = {cycles}
window_start_hour = {start}
window_end_hour = {end}
[phase.research]
agent = "test-researcher"
model = "sonnet"
postcondition = "none"
allowed_tools = ["Read", "Write"]
[phase.build]
agent = "test-builder"
model = "opus"
postcondition = "none"
allowed_tools = ["Read", "Edit"]
[phase.review]
agent = "test-reviewer"
model = "opus"
postcondition = "review_verdict"
allowed_tools = ["Read", "Write"]
"""


def write_config(path: Path, *, cycles: int = 1, start: int = 1, end: int = 6) -> Path:
    path.write_text(CONFIG_TEMPLATE.format(cycles=cycles, start=start, end=end), encoding="utf-8")
    return path


def at_local_hour(hour: int, minute: int = 30) -> float:
    """An epoch second whose local hour is ``hour`` on 2026-11-19, in whatever TZ the tests run."""
    return time.mktime((2026, 11, 19, hour, minute, 0, 0, 0, -1))


def passing_gate(post: Postcondition, repo: Path, started: int, verdict: Path) -> GateResult:
    """A gate that always advances, for tests about what ran rather than what passed."""
    outcome = {
        Postcondition.RESEARCH_NOTE_FRESH: Freshness.FRESH,
        Postcondition.INCREMENT_BUILT: Freshness.FRESH,
        Postcondition.REVIEW_VERDICT: Verdict.PASS,
        Postcondition.NONE: Ungated.NONE,
    }[post]
    return GateResult(post, outcome, "stub gate: always advances")


def metric_line(
    run_ts: str,
    phase: str,
    outcome: PhaseStatus,
    state: str | None,
    *,
    cycle: int = 1,
    rc: int = 0,
    duration_ms: int | None = 60_000,
    num_turns: int | None = 10,
    output_tokens: int | None = 1000,
    cost: float | None = 0.5,
    parse_error: bool = False,
) -> str:
    """One metrics line exactly as ``run_night`` writes it."""
    cli = None if parse_error else CliResult(
        result="done", is_error=False, duration_ms=duration_ms, num_turns=num_turns,
        session_id="s", total_cost_usd=cost,
        usage=Usage(input_tokens=1, output_tokens=output_tokens,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0),
        denials=0,
    )
    record = PhaseRecord(phase=phase, model="opus", rc=rc, outcome=outcome,
                         postcondition_state=state, cli=cli)
    return tag_line(to_json_line(record), RunId(run_ts), cycle)


def bash_line(run_ts: str, phase: str, state: str | None, *, rc: int = 0) -> str:
    """A line in bash ``metrics.sh``'s format: no ``outcome`` field."""
    return json.dumps({
        "run_ts": run_ts, "cycle": 1, "phase": phase, "model": "opus", "rc": rc,
        "postcondition_state": state, "duration_ms": 60000, "num_turns": 5,
        "tokens": {"input": 1, "output": 10, "cache_read": 0, "cache_creation": 0},
        "total_cost_usd": 0.1, "session_id": "s", "is_error": False, "parse_error": False,
        "denials": 0,
    })


def tree_digest(root: Path) -> str:
    """A digest of every path, mode and file content under ``root``: equal means nothing was written."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        stat = path.lstat()
        digest.update(str((stat.st_mode, stat.st_size, stat.st_mtime_ns)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()
