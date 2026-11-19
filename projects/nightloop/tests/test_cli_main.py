"""Milestone 7, W2, D1, D2, R6, E1, E2 and the exit codes: ``main`` end to end.

``main`` is driven with injected factories: a ``FakeRunner``, a workspace
that records its calls, a ``FakeShipper`` and the real gate
(``nightloop.gates.evaluate``) unless noted. Nothing calls a model, a git
remote or ``gh``. Numbers refer to research/2026-11-19-nightloop-cli.md.
"""

from __future__ import annotations

import contextlib
import io
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nightloop.cli import ExitCode, main
from nightloop.cli.plan import plan_night
from nightloop.config import load_config
from nightloop.cycle import Clean, FakeShipper, NotShipped, RunId, Shipped
from nightloop.phase import FakeRunner, PhaseRequest, PhaseStatus, RawRun, cli_success
from nightloop.phase.argv import build_argv

from .cli_support import at_local_hour, metric_line, passing_gate, tree_digest, write_config
from .phase_support import EXAMPLE_CONFIG

PROJECT = Path(__file__).resolve().parent.parent
LIMIT_MESSAGE = "You've hit your session limit · resets 4:10pm (America/Chicago)"
INSIDE = at_local_hour(2)  # the test config's window is [01:00, 06:00)
OUTSIDE = at_local_hour(14)


class Called(AssertionError):
    """A factory that must not be used was used."""


def never(*_args: object) -> object:
    raise Called("this factory must not be called")


class RecordingWorkspace:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def clear_handoff(self, verdict_path: Path) -> None:
        self.calls.append(f"clear_handoff {verdict_path}")

    def recover(self, run_id: RunId, cycle: int) -> Clean:
        self.calls.append(f"recover {run_id.value} {cycle}")
        return Clean()


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()  # deliberately not a git repo
        self.config = write_config(self.repo / "nightloop.toml")
        self.metrics = self.repo / "logs" / "metrics.jsonl"
        self.workspace = RecordingWorkspace()
        self.built: list[str] = []

    def call(self, *argv: str, **factories) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv), **factories)
        return code, out.getvalue(), err.getvalue()

    def run_args(self, *extra: str) -> list[str]:
        return ["run", "--repo", str(self.repo), *extra]

    def wiring(self, runner: FakeRunner, shipper: FakeShipper, *, clock: float = INSIDE, gate=None):
        def runner_factory():
            self.built.append("runner")
            return runner

        def workspace_factory(repo: Path):
            self.built.append(f"workspace {repo}")
            return self.workspace

        def shipper_factory(repo: Path, verdict: Path):
            self.built.append(f"shipper {repo} {verdict}")
            return shipper

        wired = dict(clock=lambda: clock, runner_factory=runner_factory,
                     workspace_factory=workspace_factory, shipper_factory=shipper_factory)
        if gate is not None:
            wired["gate"] = gate
        return wired

    def no_effects(self, clock: float) -> dict:
        return dict(clock=lambda: clock, runner_factory=never, workspace_factory=never,
                    shipper_factory=never)

    def write_verdict(self, text: str):
        def step(req: PhaseRequest) -> RawRun:
            path = self.repo / "logs" / "last-review.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return cli_success()

        return step


class WindowRefusalTest(CliCase):
    """W2."""

    def test_outside_the_window_run_exits_3_names_the_hours_and_calls_nothing(self) -> None:
        before = tree_digest(self.repo)
        runner = FakeRunner([])
        code, out, err = self.call(*self.run_args(), **self.wiring(runner, FakeShipper([]), clock=OUTSIDE))
        self.assertEqual(code, ExitCode.REFUSED)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        for text in ("14", "01:00", "06:00"):
            self.assertIn(text, err)
        self.assertEqual(runner.requests, [])
        self.assertEqual(self.built, [])
        self.assertEqual(tree_digest(self.repo), before)

    def test_the_end_hour_refuses_and_the_start_hour_runs(self) -> None:
        code, _, _ = self.call(*self.run_args(), **self.no_effects(at_local_hour(6, 0)))
        self.assertEqual(code, ExitCode.REFUSED)
        runner = FakeRunner([cli_success(), cli_success(), self.write_verdict("VERDICT: PASS\n")])
        code, _, _ = self.call(*self.run_args(),
                               **self.wiring(runner, FakeShipper([Shipped(1)]), clock=at_local_hour(1, 0)))
        self.assertEqual(code, ExitCode.OK)

    def test_ignore_window_runs_outside_the_window(self) -> None:
        runner = FakeRunner([cli_success(), cli_success(), self.write_verdict("VERDICT: PASS\n")])
        code, out, _ = self.call(*self.run_args("--ignore-window"),
                                 **self.wiring(runner, FakeShipper([Shipped(3)]), clock=OUTSIDE))
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("shipped PR #3", out)

    def test_an_unreadable_clock_refuses(self) -> None:
        code, _, err = self.call(*self.run_args("--ignore-window"), **self.no_effects(float("nan")))
        self.assertEqual(code, ExitCode.REFUSED)
        self.assertIn("not an hour", err)


class RunTest(CliCase):
    def test_a_night_inside_the_window_runs_ships_and_appends_tagged_metrics(self) -> None:
        runner = FakeRunner([cli_success(), cli_success(), self.write_verdict("VERDICT: PASS\n")])
        shipper = FakeShipper([Shipped(7)])
        code, out, err = self.call(*self.run_args(), **self.wiring(runner, shipper))
        self.assertEqual(code, ExitCode.OK, err)
        self.assertEqual(out.strip(), "cycle 1: shipped PR #7; tree clean")
        run_ts = "2026-11-19_023000"
        self.assertEqual(shipper.calls, [(RunId(run_ts), 1)])
        self.assertEqual(self.workspace.calls,
                         ["clear_handoff logs/last-review.md", f"recover {run_ts} 1"])
        lines = self.metrics.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(l.startswith(f'{{"run_ts":"{run_ts}","cycle":1,') for l in lines))

    def test_the_metrics_file_is_appended_not_replaced(self) -> None:
        self.metrics.parent.mkdir()
        old = metric_line("2026-11-18_020001", "research", PhaseStatus.CLI_ERROR,
                          None, rc=1)
        self.metrics.write_text(old + "\n", encoding="utf-8")
        runner = FakeRunner([cli_success(), cli_success(), self.write_verdict("VERDICT: FAIL\n")])
        code, out, _ = self.call(*self.run_args(), **self.wiring(runner, FakeShipper([])))
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("review refused (FAIL)", out)
        lines = self.metrics.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], old)
        self.assertEqual(len(lines), 4)

    def test_a_usage_limited_night_exits_4(self) -> None:
        limited = RawRun(returncode=1, stdout="", stderr=LIMIT_MESSAGE, timed_out=False)
        code, out, _ = self.call(*self.run_args(), **self.wiring(FakeRunner([limited]), FakeShipper([])))
        self.assertEqual(code, ExitCode.USAGE_LIMITED)
        self.assertIn("usage limit in research, resets 4:10pm", out)

    def test_timeout_s_reaches_every_phase(self) -> None:
        runner = FakeRunner([cli_success(), cli_success(), self.write_verdict("VERDICT: PASS\n")])
        self.call(*self.run_args("--timeout-s", "42"), **self.wiring(runner, FakeShipper([NotShipped("x")])))
        self.assertEqual([r.timeout_s for r in runner.requests], [42, 42, 42])


class DryRunTest(CliCase):
    """D1 and D2."""

    def test_dry_run_prints_the_plan_inside_and_outside_the_window_and_builds_nothing(self) -> None:
        for clock, verb in ((INSIDE, "would run"), (OUTSIDE, "would refuse")):
            with self.subTest(verb=verb):
                before = tree_digest(self.repo)
                code, out, err = self.call(*self.run_args("--dry-run"), **self.no_effects(clock))
                self.assertEqual(code, ExitCode.OK, err)
                self.assertTrue(out.startswith(f"dry run: {verb}: "), out)
                for name, model in (("research", "sonnet"), ("build", "opus"), ("review", "opus")):
                    self.assertIn(f"cycle 1 / {name}", out)
                    self.assertIn(f"model:   {model}", out)
                self.assertIn("gate:    review_verdict", out)
                self.assertIn("timeout: 3600s", out)
                self.assertIn(f"cwd:     {self.repo}", out)
                self.assertIn("--permission-mode dontAsk --agent test-reviewer", out)
                self.assertEqual(tree_digest(self.repo), before)
                self.assertFalse((self.repo / ".git").exists())

    def test_dry_run_argv_equals_what_a_real_run_hands_the_runner(self) -> None:
        write_config(self.config, cycles=2)
        runner = FakeRunner([cli_success()] * 6)
        code, _, err = self.call(*self.run_args("--timeout-s", "77"),
                                 **self.wiring(runner, FakeShipper([Shipped(1), Shipped(2)]), gate=passing_gate))
        self.assertEqual(code, ExitCode.OK, err)
        plan = plan_night(load_config(self.config), repo=self.repo,
                          verdict_path=Path("logs/last-review.md"), timeout_s=77)
        self.assertEqual([build_argv(r) for r in runner.requests], [p.argv for p in plan])
        self.assertEqual([r.cwd for r in runner.requests], [p.cwd for p in plan])

        _, dry, _ = self.call(*self.run_args("--timeout-s", "77", "--dry-run"), **self.no_effects(INSIDE))
        for request in runner.requests:
            self.assertIn(f"argv:    {shlex.join(build_argv(request))}", dry)


class ErrorTest(CliCase):
    """E1, E2 and argument errors."""

    def test_a_bad_config_names_the_field_exits_2_and_builds_nothing(self) -> None:
        self.config.write_text(self.config.read_text().replace("cycles = 1", "cycles = 0"))
        for extra in ((), ("--dry-run",)):
            with self.subTest(extra=extra):
                code, out, err = self.call(*self.run_args(*extra), **self.no_effects(INSIDE))
                self.assertEqual(code, ExitCode.USAGE)
                self.assertIn("run.cycles", err)
                self.assertNotIn("Traceback", err)
                self.assertEqual(out, "")

    def test_a_missing_config_exits_2_naming_the_file(self) -> None:
        missing = self.repo / "absent.toml"
        code, _, err = self.call(*self.run_args("--config", str(missing)), **self.no_effects(INSIDE))
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn(str(missing), err)

    def test_a_cli_that_cannot_start_exits_1_with_the_message_after_recovery(self) -> None:
        class NoClaude:
            def run(self, request: PhaseRequest) -> RawRun:
                raise FileNotFoundError(2, "No such file or directory", "claude")

        code, out, err = self.call(*self.run_args(), **dict(self.wiring(FakeRunner([]), FakeShipper([])),
                                                             runner_factory=NoClaude))
        self.assertEqual(code, ExitCode.ENVIRONMENT)
        self.assertIn("claude", err)
        self.assertIn("No such file or directory", err)
        self.assertEqual(self.workspace.calls[-1], "recover 2026-11-19_023000 1")
        self.assertFalse(self.metrics.exists(), "a phase that never started writes no metrics line")

    def test_a_repo_that_is_not_a_checkout_exits_2_before_any_runner(self) -> None:
        # The production GitWorkspace and GhShipper factories, on a temp dir that is not a repo.
        code, _, err = self.call(*self.run_args(), clock=lambda: INSIDE, runner_factory=never)
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn("not the top level of a git checkout", err)
        self.assertFalse((self.repo / "logs").exists())

    def test_bad_arguments_exit_2(self) -> None:
        for argv in (["run", "--timeout-s", "0"], ["run", "--timeout-s", "x"], ["report", "--nights", "0"],
                     ["launch"], []):
            with self.subTest(argv=argv):
                code, out, err = self.call(*argv, **self.no_effects(INSIDE))
                self.assertEqual(code, 2)
                self.assertIn("usage:", err)

    def test_help_exits_0(self) -> None:
        code, out, _ = self.call("--help", **self.no_effects(INSIDE))
        self.assertEqual(code, 0)
        self.assertIn("run", out)


class ReportTest(CliCase):
    """R6 and the report command."""

    def test_a_missing_file_exits_5_naming_the_path(self) -> None:
        code, out, err = self.call("report", "--repo", str(self.repo))
        self.assertEqual(code, ExitCode.NO_METRICS)
        self.assertIn(str(self.metrics), err)
        self.assertEqual(out, "")

    def test_an_empty_or_unreadable_only_file_exits_5(self) -> None:
        self.metrics.parent.mkdir()
        for text, note in (("", "0 unreadable"), ("torn{\n[]\n", "2 unreadable")):
            with self.subTest(text=text):
                self.metrics.write_text(text, encoding="utf-8")
                code, _, err = self.call("report", "--metrics", str(self.metrics))
                self.assertEqual(code, ExitCode.NO_METRICS)
                self.assertIn(str(self.metrics), err)
                self.assertIn(note, err)

    def test_report_prints_the_tables_and_writes_nothing(self) -> None:
        S = PhaseStatus
        self.metrics.parent.mkdir()
        lines = [metric_line(f"2026-11-1{d}_020001", "review", S.GATE_FAILED if d == 2 else S.GATED_PASS,
                             "FAIL" if d == 2 else "PASS") for d in range(5)]
        self.metrics.write_text("\n".join(lines) + "\n", encoding="utf-8")
        before = tree_digest(self.repo)
        code, out, err = self.call("report", "--repo", str(self.repo), "--nights", "3")
        self.assertEqual(code, ExitCode.OK, err)
        self.assertIn("metrics: 3 night(s) of data\n", out)
        self.assertIn("review\t3\t1\t0\t0\t3\t1\t-", out)
        self.assertEqual(tree_digest(self.repo), before)


class EntryPointTest(unittest.TestCase):
    def test_python_dash_m_nightloop_runs_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "metrics.jsonl"
            done = subprocess.run([sys.executable, "-m", "nightloop", "report", "--metrics", str(missing)],
                                  cwd=PROJECT, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, ExitCode.NO_METRICS, done.stderr)
        self.assertIn(str(missing), done.stderr)

    def test_the_example_config_plans_through_the_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(
                [sys.executable, "-m", "nightloop", "run", "--dry-run", "--repo", tmp,
                 "--config", str(EXAMPLE_CONFIG)],
                cwd=PROJECT, capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(done.returncode, ExitCode.OK, done.stderr)
        self.assertIn("--agent agentlab-reviewer", done.stdout)


if __name__ == "__main__":
    unittest.main()
