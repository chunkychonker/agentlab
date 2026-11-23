"""Milestone 7b, W7 and W8: ``run_night``'s ``between_cycles`` hook runs at the right moments.

A ``FakeRunner``, a workspace that logs its calls, ``FakeShipper`` and a
gate that always advances; the hook logs into the same event list, so the
tests assert the interleaving. No git, no model. Numbers refer to
research/2026-11-20-nightloop-claim-wiring.md.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from nightloop.cycle import Clean, FakeShipper, RunId, Shipped, UsageLimited, run_night
from nightloop.phase import FakeRunner, PhaseRequest, RawRun, cli_success

from .cli_support import passing_gate
from .phase_support import example_config

RUN = RunId("2026-11-20_020001")
VERDICT = Path("logs/last-review.md")
LIMIT_MESSAGE = "You've hit your session limit · resets 4:10pm (America/Chicago)"


class EventWorkspace:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def clear_handoff(self, verdict_path: Path) -> None:
        self.events.append("clear_handoff")

    def recover(self, run_id: RunId, cycle: int) -> Clean:
        self.events.append(f"recover {cycle}")
        return Clean()


class HookOrderTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        self.events: list[str] = []

    def night(self, runner, cycles: int, shipper: FakeShipper, **hook):
        return run_night(
            replace(example_config(), cycles=cycles), run_id=RUN, repo=self.repo, verdict_path=VERDICT,
            timeout_s=60, runner=runner, gate=passing_gate, workspace=EventWorkspace(self.events),
            shipper=shipper, now=lambda: 1780000000.0, metrics_sink=lambda line: None, **hook,
        )

    def hook(self, after_cycle: int) -> None:
        self.events.append(f"claims {after_cycle}")

    def test_w7_before_the_first_cycle_and_after_each_recovery(self) -> None:
        night = self.night(FakeRunner([cli_success()] * 6), 2, FakeShipper([Shipped(1), Shipped(2)]),
                           between_cycles=self.hook)
        self.assertEqual(len(night.reports), 2)
        self.assertEqual(self.events, [
            "claims 0",
            "clear_handoff", "recover 1", "claims 1",
            "clear_handoff", "recover 2", "claims 2",
        ])

    def test_w7_the_step_after_a_usage_limited_cycle_runs_then_the_night_stops(self) -> None:
        limited = RawRun(returncode=1, stdout="", stderr=LIMIT_MESSAGE, timed_out=False)
        night = self.night(FakeRunner([limited]), 3, FakeShipper([]), between_cycles=self.hook)
        self.assertIsInstance(night.reports[-1].result, UsageLimited)
        self.assertEqual(self.events, ["claims 0", "clear_handoff", "recover 1", "claims 1"])

    def test_not_called_after_a_cycle_that_raised(self) -> None:
        class NoClaude:
            def run(self, request: PhaseRequest) -> RawRun:
                raise FileNotFoundError(2, "No such file or directory", "claude")

        with self.assertRaises(FileNotFoundError):
            self.night(NoClaude(), 2, FakeShipper([]), between_cycles=self.hook)
        self.assertEqual(self.events, ["claims 0", "clear_handoff", "recover 1"])

    def test_what_the_hook_raises_stops_the_night_at_once(self) -> None:
        def hook(after_cycle: int) -> None:
            self.events.append(f"claims {after_cycle}")
            if after_cycle == 1:
                raise RuntimeError("claim source broke")

        runner = FakeRunner([cli_success()] * 6)
        with self.assertRaisesRegex(RuntimeError, "claim source broke"):
            self.night(runner, 2, FakeShipper([Shipped(1), Shipped(2)]), between_cycles=hook)
        self.assertEqual(self.events, ["claims 0", "clear_handoff", "recover 1", "claims 1"])
        self.assertEqual(len(runner.requests), 3, "cycle 2 never started")

    def test_bad_inputs_fail_before_the_pre_night_step(self) -> None:
        with self.assertRaises(ValueError):
            run_night(example_config(), run_id=RUN, repo=self.repo, verdict_path=VERDICT, timeout_s=0,
                      runner=FakeRunner([]), gate=passing_gate, workspace=EventWorkspace(self.events),
                      shipper=FakeShipper([]), now=lambda: 0.0, metrics_sink=lambda line: None,
                      between_cycles=self.hook)
        self.assertEqual(self.events, [])

    def test_w8_without_the_hook_the_night_is_unchanged(self) -> None:
        night = self.night(FakeRunner([cli_success()] * 6), 2, FakeShipper([Shipped(1), Shipped(2)]))
        self.assertEqual([r.result for r in night.reports], [Shipped(1), Shipped(2)])
        self.assertEqual(self.events, ["clear_handoff", "recover 1", "clear_handoff", "recover 2"])


if __name__ == "__main__":
    unittest.main()
