"""Milestone 3: ``run_phase`` (A4-A10 and invariants 1-3).

Every test drives the real gate (``nightloop.gates.evaluate``) against a temp
repo through a ``FakeRunner``; no process is started and no model is called.
Numbered comments refer to research/2026-11-10-nightloop-phase-runner.md.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Callable

from nightloop.gates import GateResult, evaluate
from nightloop.model import Postcondition
from nightloop.phase import (
    FLOOR_SLACK_S,
    FakeRunner,
    PhaseOutcome,
    PhaseRequest,
    PhaseStatus,
    RawRun,
    cli_stdout,
    cli_success,
    run_phase,
)

from .phase_support import request

STARTED = 1780000000  # what the fake clock says when the phase starts
VERDICT_FILE = Path("logs/last-review.md")
METRICS_KEYS = [
    "phase", "model", "rc", "outcome", "postcondition_state", "duration_ms", "num_turns",
    "tokens", "total_cost_usd", "session_id", "is_error", "parse_error", "denials",
]


def write(path: Path, text: str, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def writes_note(repo: Path, mtime: float, then: RawRun) -> Callable[[PhaseRequest], RawRun]:
    """A fake phase that leaves a research note with the given mtime, then exits as ``then``."""

    def step(_req: PhaseRequest) -> RawRun:
        write(repo / "research" / "2026-11-10-note.md", "# note\n", mtime)
        return then

    return step


class GateSpy:
    """The real gate, plus a log of the calls it received."""

    def __init__(self) -> None:
        self.calls: list[tuple[Postcondition, Path, int, Path]] = []

    def __call__(self, post: Postcondition, repo: Path, started: int, verdict: Path) -> GateResult:
        self.calls.append((post, repo, started, verdict))
        return evaluate(post, repo, started, verdict)


class RunPhaseTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        self.lines: list[str] = []
        self.gate = GateSpy()

    def run_one(self, req: PhaseRequest, *steps, now: Callable[[], float] = lambda: STARTED) -> PhaseOutcome:
        return run_phase(
            req,
            FakeRunner(list(steps)),
            now=now,
            gate=self.gate,
            repo=self.repo,
            verdict_path=VERDICT_FILE,
            metrics_sink=self.lines.append,
        )

    def only_line(self) -> dict:
        self.assertEqual(len(self.lines), 1, "exactly one metrics line per call")
        self.assertNotIn("\n", self.lines[0])
        return json.loads(self.lines[0])

    # A4. rc 0 with is_error true is a CLI error; the gate never runs.
    def test_is_error_with_rc_0_is_cli_error_and_skips_the_gate(self) -> None:
        req = request("research", self.repo)
        fresh = STARTED + 5
        run = RawRun(0, cli_stdout("Failed to authenticate", is_error=True), "", False)
        out = self.run_one(req, writes_note(self.repo, fresh, run))
        self.assertIs(out.status, PhaseStatus.CLI_ERROR)
        self.assertIsNone(out.gate)
        self.assertEqual(self.gate.calls, [])
        line = self.only_line()
        self.assertEqual((line["outcome"], line["is_error"], line["rc"]), ("CLI_ERROR", True, 0))

    # A5 + invariant 3: a non-zero exit is CLI_ERROR even with a fresh artifact.
    def test_nonzero_rc_is_cli_error_never_gate_failed(self) -> None:
        req = request("research", self.repo)
        out = self.run_one(req, writes_note(self.repo, STARTED + 5, RawRun(1, cli_stdout("boom"), "err", False)))
        self.assertIs(out.status, PhaseStatus.CLI_ERROR)
        self.assertEqual(self.gate.calls, [])
        self.assertEqual(self.only_line()["rc"], 1)

    # A5. A timeout is its own status and still writes one line.
    def test_timeout_is_timed_out_with_one_line(self) -> None:
        out = self.run_one(request("build", self.repo), RawRun(-15, "", "", True))
        self.assertIs(out.status, PhaseStatus.TIMED_OUT)
        self.assertEqual(self.gate.calls, [])
        line = self.only_line()
        self.assertEqual((line["outcome"], line["rc"], line["parse_error"]), ("TIMED_OUT", -15, True))

    def test_timeout_wins_over_a_clean_exit(self) -> None:
        out = self.run_one(request("build", self.repo), RawRun(0, cli_stdout(), "", True))
        self.assertIs(out.status, PhaseStatus.TIMED_OUT)

    def test_rc_0_with_unparseable_stdout_is_unreadable(self) -> None:
        out = self.run_one(request("build", self.repo), RawRun(0, "Traceback: KeyError door\n", "", False))
        self.assertIs(out.status, PhaseStatus.UNREADABLE)
        self.assertEqual(self.gate.calls, [])
        line = self.only_line()
        self.assertIs(line["parse_error"], True)
        self.assertIsNone(line["postcondition_state"])

    # A6. A note written after the floor passes; the same note, older, does not.
    def test_research_note_after_the_floor_passes(self) -> None:
        out = self.run_one(request("research", self.repo), writes_note(self.repo, STARTED + 30, cli_success()))
        self.assertIs(out.status, PhaseStatus.GATED_PASS)
        self.assertTrue(out.gate.advance)
        self.assertEqual(self.only_line()["postcondition_state"], "FRESH")

    def test_research_note_older_than_the_floor_fails_the_gate(self) -> None:
        out = self.run_one(request("research", self.repo), writes_note(self.repo, STARTED - 3600, cli_success()))
        self.assertIs(out.status, PhaseStatus.GATE_FAILED)
        self.assertFalse(out.gate.advance)
        line = self.only_line()
        self.assertEqual((line["outcome"], line["postcondition_state"]), ("GATE_FAILED", "STALE"))

    # A7. The slack is exactly one second.
    def test_floor_slack_is_one_second(self) -> None:
        self.assertEqual(FLOOR_SLACK_S, 1)
        cases = {STARTED - 1: PhaseStatus.GATED_PASS, STARTED - 2: PhaseStatus.GATE_FAILED}
        for mtime, expected in cases.items():
            with self.subTest(mtime_offset=mtime - STARTED):
                self.setUp()
                out = self.run_one(request("research", self.repo), writes_note(self.repo, mtime, cli_success()))
                self.assertIs(out.status, expected)
                self.assertEqual(out.floor, STARTED - 1)

    def test_floor_truncates_a_fractional_clock(self) -> None:
        out = self.run_one(
            request("research", self.repo),
            writes_note(self.repo, STARTED - 1, cli_success()),
            now=lambda: STARTED + 0.999,
        )
        self.assertEqual(out.floor, STARTED - 1)
        self.assertEqual(self.gate.calls[0][2], STARTED - 1)

    # Invariant 2: the clock is read once, before the runner starts.
    def test_clock_read_once_before_the_run(self) -> None:
        events: list[str] = []

        def clock() -> float:
            events.append("now")
            return STARTED

        def step(_req: PhaseRequest) -> RawRun:
            events.append("run")
            return cli_success()

        self.run_one(request("build", self.repo), step, now=clock)
        self.assertEqual(events, ["now", "run"])

    # A8. Review: the verdict file decides.
    def test_review_verdict_pass_and_fail(self) -> None:
        cases = {"VERDICT: PASS\n": PhaseStatus.GATED_PASS, "VERDICT: FAIL\n": PhaseStatus.GATE_FAILED}
        for body, expected in cases.items():
            with self.subTest(verdict=body.strip()):
                self.setUp()
                write(self.repo / VERDICT_FILE, body, STARTED + 10)
                out = self.run_one(request("review", self.repo), cli_success())
                self.assertIs(out.status, expected)
                self.assertEqual(
                    self.gate.calls, [(Postcondition.REVIEW_VERDICT, self.repo, STARTED - 1, VERDICT_FILE)]
                )

    def test_build_increment_and_ungated_phase(self) -> None:
        def builds(_req: PhaseRequest) -> RawRun:
            write(self.repo / "projects" / "x" / "main.py", "print(1)\n", STARTED + 2)
            return cli_success()

        self.assertIs(self.run_one(request("build", self.repo), builds).status, PhaseStatus.GATED_PASS)

        self.setUp()
        base = request("build", self.repo)
        ungated = replace(base, phase=replace(base.phase, postcondition=Postcondition.NONE))
        out = self.run_one(ungated, cli_success())
        self.assertIs(out.status, PhaseStatus.GATED_PASS)
        self.assertEqual(self.only_line()["postcondition_state"], "NONE")

    # A9. The line's keys, in order, and null for what the CLI omitted.
    def test_metrics_line_keys_and_nulls(self) -> None:
        stdout = json.dumps({"result": "ok", "is_error": False, "num_turns": 4, "usage": {"output_tokens": 9}})
        self.run_one(request("build", self.repo), RawRun(0, stdout, "", False))
        line = self.only_line()
        self.assertEqual(list(line), METRICS_KEYS)
        self.assertEqual(
            (line["phase"], line["model"], line["num_turns"], line["parse_error"]), ("build", "opus", 4, False)
        )
        self.assertEqual(line["tokens"], {"input": None, "output": 9, "cache_read": None, "cache_creation": None})
        for key in ("duration_ms", "total_cost_usd", "session_id", "denials"):
            self.assertIsNone(line[key], key)

    # A10. Two refused tool calls give denials: 2.
    def test_two_permission_denials(self) -> None:
        denied = [{"tool_name": "Bash", "tool_input": {"command": "git push"}}] * 2
        self.run_one(request("build", self.repo), cli_success(permission_denials=denied))
        self.assertEqual(self.only_line()["denials"], 2)

    # Failure modes from run_phase's docstring.
    def test_bad_clock_fails_before_running(self) -> None:
        runner = FakeRunner([cli_success()])
        with self.assertRaises(ValueError):
            run_phase(
                request("build", self.repo), runner, now=lambda: 0, gate=self.gate,
                repo=self.repo, verdict_path=VERDICT_FILE, metrics_sink=self.lines.append,
            )
        self.assertEqual(runner.requests, [])
        self.assertEqual(self.lines, [])

    def test_runner_that_cannot_start_propagates_without_a_line(self) -> None:
        def cannot_start(_req: PhaseRequest) -> RawRun:
            raise FileNotFoundError("claude")

        with self.assertRaises(FileNotFoundError):
            self.run_one(request("build", self.repo), cannot_start)
        self.assertEqual(self.lines, [])

    def test_sink_failure_propagates(self) -> None:
        def broken_sink(_line: str) -> None:
            raise OSError("disk full")

        with self.assertRaises(OSError):
            run_phase(
                request("build", self.repo), FakeRunner([cli_success()]), now=lambda: STARTED,
                gate=self.gate, repo=self.repo, verdict_path=VERDICT_FILE, metrics_sink=broken_sink,
            )


if __name__ == "__main__":
    unittest.main()
