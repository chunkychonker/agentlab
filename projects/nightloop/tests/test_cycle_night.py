"""Milestone 4, B1-B8, B10, B11: ``run_night`` end to end.

Every test drives the real orchestrator, the real gate
(``nightloop.gates.evaluate``) and the real ``GitWorkspace`` on a temp git
repo with a local bare ``origin``, through a ``FakeRunner`` whose steps
write the files a phase would. No process but git is started, and no model
is called. Numbers refer to research/2026-11-11-nightloop-cycle-orchestrator.md.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Callable

from nightloop.cycle import (
    Clean,
    FakeShipper,
    GitWorkspace,
    NightResult,
    NotShipped,
    PhaseFailed,
    RecoveryError,
    ReviewRefused,
    RunId,
    Shipped,
    Snapshotted,
    UsageLimited,
    run_night,
)
from nightloop.gates import Verdict, evaluate
from nightloop.phase import FakeRunner, PhaseRequest, PhaseStatus, RawRun, cli_stdout, cli_success

from .git_support import GitRepoCase, git
from .phase_support import example_config

RUN = RunId("2026-11-11_020001")
STARTED = 1780000000  # every clock in these tests says this second
VERDICT = Path("logs/last-review.md")
LIMIT_MESSAGE = "You've hit your session limit · resets 4:10pm (America/Chicago)"
M3_KEYS = [
    "phase", "model", "rc", "outcome", "postcondition_state", "duration_ms", "num_turns",
    "tokens", "total_cost_usd", "session_id", "is_error", "parse_error", "denials",
]

Step = Callable[[PhaseRequest], RawRun]


class SpyWorkspace:
    """The real ``GitWorkspace``, plus a shared log of when its methods were called."""

    def __init__(self, inner: GitWorkspace, events: list[str]) -> None:
        self._inner = inner
        self._events = events

    def clear_handoff(self, verdict_path: Path) -> None:
        self._events.append("clear_handoff")
        self._inner.clear_handoff(verdict_path)

    def recover(self, run_id: RunId, cycle: int):
        self._events.append(f"recover {cycle}")
        return self._inner.recover(run_id, cycle)


class NightTest(GitRepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.events: list[str] = []
        self.lines: list[str] = []

    # --- phase stand-ins -----------------------------------------------------

    def write(self, rel: str, text: str, mtime: float = STARTED) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def phase(self, name: str, *, writes: dict[str, str] | None = None, then: RawRun | None = None,
              mtime: float = STARTED, check: Callable[[], None] | None = None) -> Step:
        """A fake phase: log itself, optionally check the tree, write files, then exit as ``then``."""

        def step(req: PhaseRequest) -> RawRun:
            assert req.name == name, f"expected the {name} phase, got {req.name}"
            self.events.append(name)
            if check is not None:
                check()
            for rel, text in (writes or {}).items():
                self.write(rel, text, mtime)
            return then if then is not None else cli_success()

        return step

    def research(self, **kw) -> Step:
        return self.phase("research", writes={"research/2026-11-11-note.md": "# note\n"}, **kw)

    def build(self, **kw) -> Step:
        return self.phase("build", writes={"projects/demo/app.py": "print('hi')\n"}, **kw)

    def review(self, verdict: str | None, **kw) -> Step:
        return self.phase("review", writes=None if verdict is None else {str(VERDICT): verdict}, **kw)

    def cycle(self, verdict: str | None = "VERDICT: PASS\n") -> list[Step]:
        return [self.research(), self.build(), self.review(verdict)]

    def ship_for_real(self, run_id: RunId, cycle: int) -> Shipped:
        """Stands in for the real shipper: commit the work to a branch, push it, go back to main."""
        self.events.append(f"ship {cycle}")
        branch = f"ship/cycle-{cycle}"
        git(self.repo, "switch", "-q", "-c", branch)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"cycle {cycle}")
        git(self.repo, "push", "-q", "-u", "origin", branch)
        git(self.repo, "switch", "-q", "main")
        return Shipped(pr=40 + cycle)

    # --- driving -------------------------------------------------------------

    def night(self, steps: list, *, cycles: int = 1, shipper: FakeShipper | None = None,
              workspace=None, run_id: RunId = RUN) -> tuple[NightResult, FakeRunner, FakeShipper]:
        runner = FakeRunner(steps)
        shipper = shipper if shipper is not None else FakeShipper([])
        workspace = workspace or SpyWorkspace(GitWorkspace(self.repo, now=lambda: STARTED), self.events)
        result = run_night(
            replace(example_config(), cycles=cycles),
            run_id=run_id,
            repo=self.repo,
            verdict_path=VERDICT,
            timeout_s=60,
            runner=runner,
            gate=evaluate,
            workspace=workspace,
            shipper=shipper,
            now=lambda: STARTED,
            metrics_sink=self.lines.append,
        )
        return result, runner, shipper

    def results(self, night: NightResult) -> list:
        return [r.result for r in night.reports]

    def phases_run(self, runner: FakeRunner) -> list[str]:
        return [r.name for r in runner.requests]

    # --- B1 --------------------------------------------------------------------

    def test_b1_a_passing_cycle_ships_once_and_makes_no_recovery_branch(self) -> None:
        shipper = FakeShipper([self.ship_for_real])
        night, runner, _ = self.night(self.cycle(), shipper=shipper)
        self.assertEqual(self.results(night), [Shipped(pr=41)])
        self.assertEqual(night.reports[0].recovery, Clean())
        self.assertEqual(shipper.calls, [(RUN, 1)])
        self.assertEqual(self.local_branches("cycle/*"), [])
        self.assertEqual(self.file_on("ship/cycle-1", "projects/demo/app.py"), "print('hi')\n")
        self.assert_clean_main_equal_to_origin()
        # Invariant 1 and 2: handoff cleared first, phases in order, ship, then recover.
        self.assertEqual(self.events, ["clear_handoff", "research", "build", "review", "ship 1", "recover 1"])

    # --- B2 --------------------------------------------------------------------

    def test_b2_a_stale_research_note_ends_the_cycle_before_build(self) -> None:
        stale = self.research(mtime=STARTED - 100)
        night, runner, shipper = self.night([stale])
        self.assertEqual(self.results(night), [PhaseFailed("research", PhaseStatus.GATE_FAILED)])
        self.assertEqual(self.phases_run(runner), ["research"])
        self.assertEqual(shipper.calls, [])

    def test_a_failing_build_ends_the_cycle_before_review(self) -> None:
        broken = self.build(then=RawRun(2, cli_stdout("tests failed"), "", False))
        night, runner, _ = self.night([self.research(), broken])
        self.assertEqual(self.results(night), [PhaseFailed("build", PhaseStatus.CLI_ERROR)])
        self.assertEqual(self.phases_run(runner), ["research", "build"])

    def test_a_timed_out_review_is_a_phase_failure_not_a_refusal(self) -> None:
        night, _, shipper = self.night([self.research(), self.build(), self.review(None, then=RawRun(-15, "", "", True))])
        self.assertEqual(self.results(night), [PhaseFailed("review", PhaseStatus.TIMED_OUT)])
        self.assertEqual(shipper.calls, [])

    # --- B3 --------------------------------------------------------------------

    def test_b3_fail_missing_and_malformed_verdicts_never_reach_the_shipper(self) -> None:
        cases = {
            Verdict.FAIL: "VERDICT: FAIL\nthe tests do not run\n",
            Verdict.MISSING: None,  # the review wrote no file at all
            Verdict.MALFORMED: "Looks fine to me.\n",
        }
        for index, (verdict, text) in enumerate(cases.items()):
            with self.subTest(verdict=verdict):
                self.events.clear()
                # A run id per subtest: they share one repo, and recovery
                # branch names must not collide across nights.
                night, _, shipper = self.night(self.cycle(text), run_id=RunId(f"2026-11-1{index}_020001"))
                self.assertEqual(self.results(night), [ReviewRefused(verdict)])
                self.assertEqual(shipper.calls, [])
                self.assert_clean_main_equal_to_origin()

    # --- B4 --------------------------------------------------------------------

    def test_b4_a_stale_pass_does_not_authorise_the_next_cycle(self) -> None:
        # Cycle 1 passes review but is not shipped, so its PASS stays on disk
        # (logs/ is ignored, recovery does not touch it). Cycle 2's review
        # writes nothing.
        shipper = FakeShipper([NotShipped("gh is not authenticated")])
        steps = self.cycle("VERDICT: PASS\n") + self.cycle(None)
        night, runner, _ = self.night(steps, cycles=2, shipper=shipper)
        self.assertEqual(self.results(night), [NotShipped("gh is not authenticated"), ReviewRefused(Verdict.MISSING)])
        self.assertEqual(shipper.calls, [(RUN, 1)])
        self.assertFalse((self.repo / VERDICT).exists())

    def test_a_stale_pass_left_before_the_night_is_cleared_too(self) -> None:
        self.write(str(VERDICT), "VERDICT: PASS\n")
        night, _, shipper = self.night(self.cycle(None))
        self.assertEqual(self.results(night), [ReviewRefused(Verdict.MISSING)])
        self.assertEqual(shipper.calls, [])

    # --- B5 / B6 ---------------------------------------------------------------

    def test_b5_failed_cycles_each_go_to_their_own_branch_and_the_next_starts_clean(self) -> None:
        def starts_clean() -> None:
            self.assertEqual(self.porcelain(), "")
            self.assertFalse((self.repo / "projects" / "demo" / "app.py").exists())

        steps = [self.research(), self.build(), self.review("VERDICT: FAIL\n"),
                 self.research(check=starts_clean), self.build(), self.review("VERDICT: FAIL\n")]
        night, _, _ = self.night(steps, cycles=2)
        self.assertEqual(self.results(night), [ReviewRefused(Verdict.FAIL)] * 2)
        branches = [r.recovery.branch for r in night.reports]
        self.assertEqual(len(set(branches)), 2, "two failures in the same second, two names")
        for report in night.reports:
            self.assertIsInstance(report.recovery, Snapshotted)
            self.assertTrue(report.recovery.pushed)
            self.assertEqual(self.file_on(report.recovery.branch, "projects/demo/app.py"), "print('hi')\n")
        self.assertEqual(sorted(self.local_branches("cycle/*")), sorted(branches))
        self.assert_clean_main_equal_to_origin()

    def test_b6_a_failure_that_left_the_tree_clean_makes_no_branch(self) -> None:
        crashed = self.phase("research", then=RawRun(1, "", "Error: something broke", False))
        night, _, _ = self.night([crashed])
        self.assertEqual(night.reports[0].result, PhaseFailed("research", PhaseStatus.CLI_ERROR))
        self.assertEqual(night.reports[0].recovery, Clean())
        self.assertEqual(self.local_branches("cycle/*"), [])

    # --- B7 --------------------------------------------------------------------

    def test_b7_a_recovery_failure_stops_the_night(self) -> None:
        self.fail_every_commit()
        steps = self.cycle("VERDICT: FAIL\n") + self.cycle()
        runner = FakeRunner(steps)
        with self.assertRaises(RecoveryError):
            run_night(
                replace(example_config(), cycles=2), run_id=RUN, repo=self.repo, verdict_path=VERDICT,
                timeout_s=60, runner=runner, gate=evaluate,
                workspace=GitWorkspace(self.repo, now=lambda: STARTED), shipper=FakeShipper([]),
                now=lambda: STARTED, metrics_sink=self.lines.append,
            )
        self.assertEqual(len(runner.requests), 3, "cycle 2 never started")
        # Nothing discarded: the work is still on main, and no empty branch is left.
        self.assertTrue((self.repo / "projects" / "demo" / "app.py").exists())
        self.assertEqual(self.current_branch(), "main")
        self.assertEqual(self.local_branches("cycle/*"), [])

    # --- B8 --------------------------------------------------------------------

    def test_b8_a_usage_limit_stops_the_night_after_recovery(self) -> None:
        limited_runs = {
            "non-zero exit, plain stdout": (RawRun(1, LIMIT_MESSAGE + "\n", "", False), "4:10pm (America/Chicago)"),
            "rc 0 with is_error true": (RawRun(0, cli_stdout("You've hit your usage limit · resets 9pm", is_error=True), "", False), "9pm"),
            "non-zero exit, JSON result": (RawRun(1, cli_stdout(LIMIT_MESSAGE, is_error=True), "", False), "4:10pm (America/Chicago)"),
            "message on stderr only": (RawRun(1, "", "Claude usage limit reached.\n", False), "unknown"),
        }
        for index, (label, (run, reset)) in enumerate(limited_runs.items()):
            with self.subTest(label):
                self.events.clear()
                night, runner, shipper = self.night([self.research(), self.build(then=run)], cycles=2,
                                                    run_id=RunId(f"2026-11-1{index}_020001"))
                self.assertEqual(self.results(night), [UsageLimited("build", reset)])
                self.assertTrue(night.usage_limited)
                self.assertEqual(self.phases_run(runner), ["research", "build"], "cycle 2 never ran")
                self.assertEqual(self.events[-1], "recover 1", "recovery ran before the night stopped")
                self.assertIsInstance(night.reports[0].recovery, Snapshotted)
                self.assert_clean_main_equal_to_origin()

    def test_b8_the_limit_text_in_a_passing_phase_does_not_stop_the_night(self) -> None:
        quoting = self.phase("research", writes={"research/2026-11-11-note.md": f"# note\nquote: {LIMIT_MESSAGE}\n"},
                             then=cli_success(f"Summarised the bug: {LIMIT_MESSAGE}"))
        steps = [quoting, self.build(), self.review("VERDICT: FAIL\n")] + self.cycle("VERDICT: FAIL\n")
        night, runner, _ = self.night(steps, cycles=2)
        self.assertEqual(self.results(night), [ReviewRefused(Verdict.FAIL)] * 2)
        self.assertFalse(night.usage_limited)
        self.assertEqual(len(runner.requests), 6)

    def test_unreadable_output_is_not_scanned_for_the_limit(self) -> None:
        # Pins the spec: only CLI_ERROR is scanned. rc 0 with non-JSON stdout
        # is UNREADABLE, an ordinary failure, and the night goes on.
        unreadable = self.build(then=RawRun(0, LIMIT_MESSAGE, "", False))
        night, runner, _ = self.night([self.research(), unreadable] + self.cycle("VERDICT: FAIL\n"), cycles=2)
        self.assertEqual(self.results(night), [PhaseFailed("build", PhaseStatus.UNREADABLE), ReviewRefused(Verdict.FAIL)])

    # --- B10 -------------------------------------------------------------------

    def test_b10_one_tagged_metrics_line_per_phase_run(self) -> None:
        shipper = FakeShipper([NotShipped("no gh in tests")])
        failing_research = self.phase("research", then=RawRun(1, "", "boom", False))
        night, _, _ = self.night(self.cycle() + [failing_research], cycles=2, shipper=shipper)
        self.assertEqual(len(night.reports), 2)
        docs = [json.loads(line) for line in self.lines]
        self.assertEqual([(d["cycle"], d["phase"]) for d in docs],
                         [(1, "research"), (1, "build"), (1, "review"), (2, "research")])
        for doc in docs:
            self.assertEqual(list(doc), ["run_ts", "cycle", *M3_KEYS])
            self.assertEqual(doc["run_ts"], RUN.value)
        self.assertEqual([d["outcome"] for d in docs], ["GATED_PASS"] * 3 + ["CLI_ERROR"])
        self.assertEqual(docs[2]["postcondition_state"], "PASS")

    # --- B11 -------------------------------------------------------------------

    def test_b11_a_crashing_phase_is_recovered_then_propagates(self) -> None:
        def cannot_start(_req: PhaseRequest) -> RawRun:
            self.events.append("build")
            raise FileNotFoundError("claude: not on PATH")

        with self.assertRaises(FileNotFoundError):
            self.night([self.research(), cannot_start], cycles=2)
        self.assertEqual(self.events, ["clear_handoff", "research", "build", "recover 1"])
        [branch] = self.local_branches("cycle/*")
        self.assertEqual(self.file_on(branch, "research/2026-11-11-note.md"), "# note\n")
        self.assert_clean_main_equal_to_origin()

    def test_b11_a_failed_recovery_after_a_crash_is_raised_from_the_crash(self) -> None:
        self.fail_every_commit()

        def cannot_start(_req: PhaseRequest) -> RawRun:
            raise FileNotFoundError("claude: not on PATH")

        with self.assertRaises(RecoveryError) as caught:
            self.night([self.research(), cannot_start])
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertTrue((self.repo / "research" / "2026-11-11-note.md").exists())

    def test_a_raising_shipper_is_recovered_then_propagates(self) -> None:
        def broken_shipper(_run: RunId, _cycle: int) -> Shipped:
            raise ConnectionError("github is down")

        with self.assertRaises(ConnectionError):
            self.night(self.cycle(), shipper=FakeShipper([broken_shipper]))
        [branch] = self.local_branches("cycle/*")
        self.assertEqual(self.file_on(branch, "projects/demo/app.py"), "print('hi')\n")
        self.assert_clean_main_equal_to_origin()

    # --- invariant 9 and input checks -------------------------------------------

    def test_never_more_cycles_than_configured(self) -> None:
        steps = self.cycle("VERDICT: FAIL\n") * 1 + self.cycle("VERDICT: FAIL\n")
        night, runner, _ = self.night(steps, cycles=2)
        self.assertEqual(len(night.reports), 2)
        self.assertEqual(len(runner.requests), 6)

    def test_bad_inputs_fail_before_any_cycle(self) -> None:
        cases = {
            "relative repo": dict(repo=Path("repo")),
            "zero timeout": dict(timeout_s=0),
            "str run id": dict(run_id="2026-11-11_020001"),
            "str verdict path": dict(verdict_path="logs/last-review.md"),
        }
        for label, override in cases.items():
            with self.subTest(label):
                self.events.clear()
                kwargs = dict(
                    run_id=RUN, repo=self.repo, verdict_path=VERDICT, timeout_s=60,
                    runner=FakeRunner([]), gate=evaluate,
                    workspace=SpyWorkspace(GitWorkspace(self.repo, now=lambda: STARTED), self.events),
                    shipper=FakeShipper([]), now=lambda: STARTED, metrics_sink=self.lines.append,
                )
                kwargs.update(override)
                with self.assertRaises((TypeError, ValueError)):
                    run_night(example_config(), **kwargs)
                self.assertEqual(self.events, [], "nothing ran")


if __name__ == "__main__":
    unittest.main()
