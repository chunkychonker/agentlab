"""Milestone 11, criteria 7 and 9 (I4, I7, I8): ``nightloop bench`` through ``main`` with fakes."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
from unittest import mock

from nightloop.bench.fresh import template_sha256
from nightloop.bench.workspace import TempGitWorkspace
from nightloop.cli.bench_args import DEFAULT_TRIALS, PLUGIN_BIN
from nightloop.cli.bench_cmd import PLACEHOLDER_REPO, run_bench_cmd
from nightloop.cli.exit_codes import ExitCode
from nightloop.cli.main import VERDICT_PATH, main
from nightloop.cycle.prompts import phase_prompt
from nightloop.gates.dispatch import evaluate
from nightloop.phase.fake_runner import FakeRunner
from nightloop.phase.request import PhaseRequest
from nightloop.phase.runner import RawRun

from .bench_support import (
    FAIL,
    PASS,
    PLUGIN_CONFIG,
    PROJECT,
    SHIPPED_FIXTURES,
    TEMPLATE,
    IsolatedGitCase,
    exits_nonzero,
    tree_digest,
    write_fixture_dir,
    writes_verdict,
)


def _no_runner():
    raise AssertionError("a runner was built")


def _no_workspace(template: bytes, agent: str):
    raise AssertionError("a workspace was built")


class BenchCliCase(IsolatedGitCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ))  # bench prepends to PATH; undo it after each test
        self.fixtures = self.tmp / "fixtures"
        write_fixture_dir(self.fixtures, "seeded-one", expect='kind = "seeded"\npatterns = ["off by one"]\n')
        write_fixture_dir(self.fixtures, "clean-one", expect='kind = "clean"\n')
        self.template = self.tmp / "reviewer.md"
        self.template.write_bytes(TEMPLATE)
        self.results = self.tmp / "out" / "last-run.json"
        self.repos = self.tmp / "repos"
        self.repos.mkdir()

    def argv(self, *extra: str) -> list[str]:
        return ["bench", "--fixtures", str(self.fixtures), "--template", str(self.template),
                "--config", str(PLUGIN_CONFIG), "--results", str(self.results), *extra]

    def call(self, argv, *, runner_factory=_no_runner, workspace_factory=None):
        if workspace_factory is None:
            workspace_factory = lambda t, a: TempGitWorkspace(t, a, parent=self.repos)  # noqa: E731
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv, runner_factory=runner_factory, bench_workspace_factory=workspace_factory,
                        gate=evaluate)
        return code, out.getvalue(), err.getvalue()

    def fake(self, steps) -> FakeRunner:
        self.runner = FakeRunner(steps)
        return self.runner


class DryRunTest(BenchCliCase):
    def test_dry_run_prints_the_matrix_builds_nothing_and_writes_nothing(self) -> None:
        before = (tree_digest(self.tmp), tree_digest(PROJECT))
        code, out, err = self.call(self.argv("--dry-run", "--trials", "4"), workspace_factory=_no_workspace)
        self.assertEqual(code, ExitCode.OK, err)
        self.assertEqual((tree_digest(self.tmp), tree_digest(PROJECT)), before)
        self.assertIn("fixtures: 2 (1 seeded, 1 clean) x 4 trial(s) = 8 review call(s), each billed", out)
        lines = out.splitlines()
        header = next(i for i, line in enumerate(lines) if line.startswith("argv of one trial"))
        argv = shlex.split(lines[header + 1])
        self.assertEqual(argv[:3], ["claude", "-p", phase_prompt("review", repo=PLACEHOLDER_REPO, verdict_path=VERDICT_PATH)])
        self.assertEqual(argv[argv.index("--agent") + 1], "reviewer")
        self.assertIn(template_sha256(TEMPLATE), out)
        self.assertIn(str(PLUGIN_BIN), out)

    def test_dry_run_on_the_shipped_defaults(self) -> None:
        before = tree_digest(PROJECT)
        code, out, err = self.call(["bench", "--dry-run"], workspace_factory=_no_workspace)
        self.assertEqual(code, ExitCode.OK, err)
        self.assertEqual(tree_digest(PROJECT), before)
        self.assertEqual(DEFAULT_TRIALS, 3)
        self.assertIn("fixtures: 7 (4 seeded, 3 clean) x 3 trial(s) = 21 review call(s), each billed", out)


class FullRunTest(BenchCliCase):
    def test_a_fully_graded_run_prints_both_rates_and_records_the_results(self) -> None:
        # Fixtures run in slug order: clean-one (passed), then seeded-one (caught).
        runner = self.fake([writes_verdict(PASS), writes_verdict(FAIL.format("an off by one in the loop"))])
        code, out, err = self.call(self.argv("--trials", "1"), runner_factory=lambda: runner)
        self.assertEqual(code, ExitCode.OK, err)
        self.assertEqual(len(runner.requests), 2)
        lines = out.splitlines()
        self.assertEqual(lines[0], "catch rate 1/1 = 100% (95% CI 21-100%)")
        self.assertEqual(lines[1], "false-positive rate 0/1 = 0% (95% CI 0-79%)")
        doc = json.loads(self.results.read_text(encoding="utf-8"))
        self.assertEqual(doc["template_sha256"], template_sha256(TEMPLATE))
        self.assertIn(f"recorded {self.results}", out)
        self.assertIn("bench clean-one trial 1/1:", err)
        self.assertEqual(list(self.repos.iterdir()), [])

    def test_the_review_phase_finds_run_silent_first_on_path(self) -> None:
        seen_path: list[str] = []

        def step(req: PhaseRequest) -> RawRun:
            seen_path.append(os.environ["PATH"].split(os.pathsep)[0])
            return writes_verdict(PASS)(req)

        code, _, err = self.call(self.argv("--trials", "1"), runner_factory=lambda: self.fake([step, step]))
        self.assertEqual(code, ExitCode.OK, err)
        self.assertEqual(seen_path, [str(PLUGIN_BIN)] * 2)

    def test_an_ungraded_trial_exits_non_zero_prints_the_report_and_records_nothing(self) -> None:
        self.results.parent.mkdir()
        self.results.write_text("previous run\n", encoding="utf-8")
        runner = self.fake([writes_verdict(PASS), exits_nonzero])
        code, out, err = self.call(self.argv("--trials", "1"), runner_factory=lambda: runner)
        self.assertEqual(code, ExitCode.UNGRADED)
        self.assertTrue(out.splitlines()[0].startswith("catch rate n/a"), out)
        self.assertIn("UNGRADED seeded-one trial 1:", out)
        self.assertIn("results not recorded", err)
        self.assertEqual(self.results.read_text(encoding="utf-8"), "previous run\n")

    def test_git_that_cannot_start_is_an_environment_error(self) -> None:
        missing_git = lambda t, a: TempGitWorkspace(t, a, parent=self.repos, git=str(self.tmp / "nogit"))  # noqa: E731
        code, _, err = self.call(self.argv("--trials", "1"), runner_factory=lambda: self.fake([]),
                                 workspace_factory=missing_git)
        self.assertEqual(code, ExitCode.ENVIRONMENT)
        self.assertIn("environment error", err)
        self.assertFalse(self.results.exists())


class CheckFreshTest(BenchCliCase):
    def record(self, sha: str) -> None:
        self.results.parent.mkdir(exist_ok=True)
        self.results.write_text(json.dumps({"template_sha256": sha}), encoding="utf-8")

    def test_no_results_file_is_never_run(self) -> None:
        code, out, _ = self.call(self.argv("--check-fresh"))
        self.assertEqual(code, ExitCode.BENCH_NEVER_RUN)
        self.assertTrue(out.startswith("NEVER_RUN"), out)

    def test_same_hash_is_fresh(self) -> None:
        self.record(template_sha256(TEMPLATE))
        code, out, _ = self.call(self.argv("--check-fresh"))
        self.assertEqual(code, ExitCode.OK)
        self.assertTrue(out.startswith("FRESH"), out)

    def test_different_hash_is_stale(self) -> None:
        self.record(template_sha256(TEMPLATE + b"edited\n"))
        code, out, _ = self.call(self.argv("--check-fresh"))
        self.assertEqual(code, ExitCode.BENCH_STALE)
        self.assertTrue(out.startswith("STALE"), out)

    def test_the_three_states_have_distinct_exit_codes(self) -> None:
        self.assertEqual(len({ExitCode.OK, ExitCode.BENCH_STALE, ExitCode.BENCH_NEVER_RUN}), 3)

    def test_a_real_run_then_an_edit_goes_fresh_then_stale(self) -> None:
        runner = self.fake([writes_verdict(PASS), writes_verdict(PASS)])
        self.assertEqual(self.call(self.argv("--trials", "1"), runner_factory=lambda: runner)[0], ExitCode.OK)
        self.assertEqual(self.call(self.argv("--check-fresh"))[0], ExitCode.OK)
        self.template.write_bytes(TEMPLATE + b"One more rule.\n")
        self.assertEqual(self.call(self.argv("--check-fresh"))[0], ExitCode.BENCH_STALE)

    def test_a_malformed_results_file_is_a_usage_error_not_never_run(self) -> None:
        self.results.parent.mkdir()
        for text in ("not json", "[]", '{"template_sha256": 3}', '{"template_sha256": "ABC"}'):
            with self.subTest(text=text):
                self.results.write_text(text, encoding="utf-8")
                code, _, err = self.call(self.argv("--check-fresh"))
                self.assertEqual(code, ExitCode.USAGE)
                self.assertIn("--results", err)


class UsageTest(BenchCliCase):
    def assertUsage(self, argv: list[str], needle: str) -> None:
        code, _, err = self.call(argv, workspace_factory=_no_workspace)
        self.assertEqual(code, ExitCode.USAGE, err)
        self.assertIn(needle, err)
        self.assertFalse(self.results.exists())

    def test_missing_template(self) -> None:
        self.template.unlink()
        self.assertUsage(self.argv(), "--template")

    def test_bad_config(self) -> None:
        bad = self.tmp / "bad.toml"
        bad.write_text("schema_version = 99\n", encoding="utf-8")
        argv = self.argv()
        argv[argv.index("--config") + 1] = str(bad)
        self.assertUsage(argv, "config")

    def test_invalid_fixture(self) -> None:
        write_fixture_dir(self.fixtures, "broken", expect='kind = "seeded"\n')
        self.assertUsage(self.argv(), "'broken'")

    def test_one_kind_only(self) -> None:
        for path in sorted((self.fixtures / "clean-one").rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        (self.fixtures / "clean-one").rmdir()
        self.assertUsage(self.argv(), "no clean fixture")

    def test_bad_trials_is_argparse_usage(self) -> None:
        code, _, err = self.call(self.argv("--trials", "0"), workspace_factory=_no_workspace)
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn("--trials", err)

    def test_dry_run_and_check_fresh_are_exclusive(self) -> None:
        code, _, err = self.call(self.argv("--dry-run", "--check-fresh"), workspace_factory=_no_workspace)
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn("not allowed with", err)

    def test_missing_run_silent_runs_nothing(self) -> None:
        empty_bin = self.tmp / "bin"
        empty_bin.mkdir()
        args = argparse.Namespace(
            fixtures=self.fixtures, template=self.template, config=PLUGIN_CONFIG, results=self.results,
            trials=1, timeout_s=60, dry_run=False, check_fresh=False,
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = run_bench_cmd(args, clock=lambda: 0.0, runner_factory=_no_runner,
                                 workspace_factory=_no_workspace, gate=evaluate,
                                 verdict_path=VERDICT_PATH, plugin_bin=empty_bin)
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn("run_silent", err.getvalue())
        self.assertNotIn(str(empty_bin), os.environ.get("PATH", ""))


class LazyImportTest(BenchCliCase):
    def test_run_and_report_do_not_load_the_benchmark(self) -> None:
        probe = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from nightloop.cli.main import _parser;"
            "p = _parser(); p.parse_args(['run', '--dry-run']); p.parse_args(['report']);"
            "p.parse_args(['bench', '--dry-run']);"
            "loaded = sorted(m for m in sys.modules if m.startswith('nightloop.bench') or m == 'nightloop.cli.bench_cmd');"
            "print(loaded); sys.exit(1 if loaded else 0)"
        )
        done = subprocess.run([sys.executable, "-I", "-c", probe, str(PROJECT)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_shipped_fixtures_dir_is_the_default(self) -> None:
        from nightloop.cli.bench_args import DEFAULT_FIXTURES
        self.assertEqual(DEFAULT_FIXTURES, SHIPPED_FIXTURES)
