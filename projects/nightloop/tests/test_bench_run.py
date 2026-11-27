"""Milestone 11, criteria 5 and 6 (I1, I2, I3, I4): the matrix through FakeRunner, run_phase and the real gate.

Every trial runs in a real ``TempGitWorkspace`` repo under the test's temp
dir; the fake reviewer writes the verdict file there, and the production
``evaluate`` gate and ``parse_verdict`` read it.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from nightloop.bench.classify import Outcome
from nightloop.bench.fixture import Kind, MatrixError
from nightloop.bench.report import exit_code, render_report, summarise
from nightloop.bench.run import run_bench
from nightloop.bench.workspace import NOTE_PATH, TempGitWorkspace, WorkspaceError
from nightloop.cycle.night import phase_requests
from nightloop.cycle.prompts import phase_prompt
from nightloop.gates.dispatch import evaluate
from nightloop.phase.argv import build_argv
from nightloop.phase.fake_runner import FakeRunner, cli_success
from nightloop.phase.request import PhaseRequest
from nightloop.phase.runner import RawRun

from .bench_support import (
    FAIL,
    PASS,
    PROJECT,
    TEMPLATE,
    VERDICT,
    IsolatedGitCase,
    exits_nonzero,
    fixture,
    plugin_config,
    tree_digest,
    writes_verdict,
)

TIMEOUT_S = 600
PATTERNS = ("prefix", "(escape|outside)")
RIGHT = FAIL.format("the startswith prefix check lets a sibling dir escape the root")
WRONG = FAIL.format("the README is missing a usage section")
MALFORMED = "verdict: probably fine\nFindings:\n- prefix escape\n"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return done.stdout


class BenchCase(IsolatedGitCase):
    def bench(self, fixtures, steps, *, trials: int):
        self.runner = FakeRunner(steps)
        self.seen: list = []
        results = run_bench(
            fixtures, plugin_config(),
            runner=self.runner, workspace=TempGitWorkspace(TEMPLATE, "reviewer", parent=self.tmp),
            trials=trials, timeout_s=TIMEOUT_S, verdict_path=VERDICT,
            now=time.time, gate=evaluate, on_trial=self.seen.append,
        )
        return results


class FullMatrixTest(BenchCase):
    """Criterion 5: 3 seeded x 2 trials + 2 clean x 2 trials, hand-counted."""

    def setUp(self) -> None:
        super().setUp()
        fixtures = [
            fixture("seed-a", Kind.SEEDED, PATTERNS),
            fixture("seed-b", Kind.SEEDED, PATTERNS),
            fixture("seed-c", Kind.SEEDED, PATTERNS),
            fixture("clean-a", Kind.CLEAN),
            fixture("clean-b", Kind.CLEAN),
        ]
        steps = [
            writes_verdict(RIGHT), writes_verdict(PASS),        # seed-a: CAUGHT, MISSED
            writes_verdict(WRONG), writes_verdict(MALFORMED),   # seed-b: MISDIAGNOSED, UNGRADED
            writes_verdict(RIGHT), exits_nonzero,               # seed-c: CAUGHT, UNGRADED
            writes_verdict(PASS), writes_verdict(PASS),         # clean-a: CLEAN_PASSED x2
            writes_verdict(WRONG), writes_verdict(PASS),        # clean-b: FALSE_POSITIVE, CLEAN_PASSED
        ]
        self.results = self.bench(fixtures, steps, trials=2)
        self.report = summarise(self.results, template_sha256="b" * 64, trials=2, review_model="opus")

    def test_each_trial_gets_the_hand_computed_outcome(self) -> None:
        got = [(r.slug, r.trial, r.outcome) for r in self.results]
        self.assertEqual(got, [
            ("seed-a", 1, Outcome.CAUGHT), ("seed-a", 2, Outcome.MISSED),
            ("seed-b", 1, Outcome.MISDIAGNOSED), ("seed-b", 2, Outcome.UNGRADED),
            ("seed-c", 1, Outcome.CAUGHT), ("seed-c", 2, Outcome.UNGRADED),
            ("clean-a", 1, Outcome.CLEAN_PASSED), ("clean-a", 2, Outcome.CLEAN_PASSED),
            ("clean-b", 1, Outcome.FALSE_POSITIVE), ("clean-b", 2, Outcome.CLEAN_PASSED),
        ])
        self.assertEqual(self.seen, list(self.results))

    def test_rates_leave_ungraded_out_of_both_denominators(self) -> None:
        self.assertEqual(Counter(r.outcome for r in self.results)[Outcome.UNGRADED], 2)
        self.assertEqual((self.report.catch.hits, self.report.catch.n), (2, 4))
        self.assertEqual((self.report.false_positive.hits, self.report.false_positive.n), (1, 4))
        lines = render_report(self.report).splitlines()
        self.assertTrue(lines[0].startswith("catch rate 2/4 = 50%"), lines[0])
        self.assertTrue(lines[1].startswith("false-positive rate 1/4 = 25%"), lines[1])

    def test_ungraded_trials_say_why_and_make_the_exit_code_non_zero(self) -> None:
        details = {(r.slug, r.trial): r.detail for r in self.results if r.outcome is Outcome.UNGRADED}
        self.assertEqual(details[("seed-b", 2)], "verdict MALFORMED")
        self.assertTrue(details[("seed-c", 2)].startswith("phase "), details)
        self.assertIn("rc 1", details[("seed-c", 2)])
        self.assertNotEqual(exit_code(self.report), 0)

    def test_every_trial_repo_is_removed(self) -> None:
        self.assertEqual(list(self.tmp.iterdir()), [])


class VerdictParsingTest(BenchCase):
    """I3: the verdict comes from ``parse_verdict`` via the gate, not from comparing text."""

    def test_verdict_forms_the_gate_accepts_or_rejects(self) -> None:
        fixtures = [fixture("seed", Kind.SEEDED, ("prefix",)), fixture("clean", Kind.CLEAN)]
        steps = [
            writes_verdict("Notes first.\nVERDICT:   FAIL\nprefix bug\n"),      # CAUGHT
            writes_verdict("VERDICT: FAIL\nVERDICT: PASS\nprefix\n"),         # two lines: MALFORMED
            writes_verdict(" VERDICT: FAIL prefix\n"),                         # not at column 0
            _exit_zero,                                                         # no file: MISSING
        ]
        results = self.bench(fixtures, steps, trials=2)
        self.assertEqual([r.outcome for r in results],
                         [Outcome.CAUGHT, Outcome.UNGRADED, Outcome.UNGRADED, Outcome.UNGRADED])
        self.assertEqual(results[3].detail, "verdict MISSING")


def _exit_zero(req: PhaseRequest) -> RawRun:
    """The CLI succeeded but the reviewer wrote no verdict file."""
    return cli_success("wrote nothing")


class TrialRepoTest(BenchCase):
    """Criterion 6 (I1, I2): what the reviewer is sent, and the repo it is sent into."""

    def setUp(self) -> None:
        super().setUp()
        self.observed: list[dict] = []
        self.seeded = fixture("path-prefix-escape", Kind.SEEDED, ("prefix",),
                              files={"containment.py": b"def f(): ...\n", "pkg/test_c.py": b"# t\n"},
                              note="# Containment\n\n## Build proposal\nCheck paths.\n")
        self.clean = fixture("slugify-ascii", Kind.CLEAN, files={"slug.py": b"x = 1\n"})
        self.live_before = tree_digest(PROJECT)
        self.results = self.bench([self.seeded, self.clean],
                                  [self.inspect(PASS), self.inspect(PASS)], trials=1)

    def inspect(self, verdict: str):
        def step(req: PhaseRequest) -> RawRun:
            repo = req.cwd
            self.observed.append({
                "req": req,
                "repo": repo,
                "template": (repo / ".claude" / "agents" / "reviewer.md").read_bytes(),
                "tracked": git(repo, "ls-files").split(),
                "untracked": git(repo, "ls-files", "--others", "--exclude-standard").split(),
                "remotes": git(repo, "remote").split(),
                "log": git(repo, "log", "--format=%s").splitlines(),
                "note": (repo / NOTE_PATH).read_text(encoding="utf-8"),
            })
            return writes_verdict(verdict)(req)
        return step

    def test_the_prompt_and_request_are_the_production_review_phase(self) -> None:
        cfg = plugin_config()
        for seen in self.observed:
            req, repo = seen["req"], seen["repo"]
            self.assertEqual(req.prompt, phase_prompt("review", repo=repo, verdict_path=VERDICT))
            (production,) = [r for r in phase_requests(cfg, repo=repo, verdict_path=VERDICT, timeout_s=TIMEOUT_S)
                             if r.name == "review"]
            self.assertEqual(req, production)
            self.assertEqual(build_argv(req), build_argv(production))
            self.assertEqual(req.phase, cfg.review)
            self.assertEqual(req.cwd, repo)

    def test_the_trial_repo_is_a_fresh_repo_outside_the_live_checkout(self) -> None:
        for seen in self.observed:
            repo = seen["repo"]
            self.assertEqual(repo.parent, self.tmp)
            self.assertFalse(repo.is_relative_to(PROJECT.parent.parent))
        self.assertNotEqual(self.observed[0]["repo"], self.observed[1]["repo"])
        self.assertEqual(tree_digest(PROJECT), self.live_before)

    def test_template_note_increment_and_remote(self) -> None:
        seeded = self.observed[0]
        self.assertEqual(seeded["template"], TEMPLATE)
        self.assertEqual(sorted(seeded["tracked"]), [".claude/agents/reviewer.md", ".gitignore", str(NOTE_PATH)])
        self.assertEqual(seeded["note"], self.seeded.note)
        self.assertEqual(sorted(seeded["untracked"]), ["containment.py", "pkg/test_c.py"])
        self.assertEqual(seeded["remotes"], [])
        self.assertEqual(len(seeded["log"]), 1)

    def test_nothing_the_reviewer_sees_names_the_fixture(self) -> None:
        for seen, f in zip(self.observed, (self.seeded, self.clean)):
            for text in (str(seen["repo"]), seen["req"].prompt, *seen["log"], *seen["tracked"]):
                self.assertNotIn(f.slug, text)
                self.assertNotIn("prefix", text)

    def test_repos_are_removed_after_the_run(self) -> None:
        for seen in self.observed:
            self.assertFalse(seen["repo"].exists())


class DefaultParentTest(IsolatedGitCase):
    def test_default_trial_repos_live_in_the_system_temp_dir(self) -> None:
        ws = TempGitWorkspace(TEMPLATE, "reviewer")
        with ws.materialize(fixture("one", Kind.CLEAN)) as repo:
            self.assertTrue(repo.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            self.assertFalse(repo.is_relative_to(PROJECT.parent.parent))
        self.assertFalse(repo.exists())


class FailureTest(BenchCase):
    def test_one_kind_only_runs_nothing(self) -> None:
        runner = FakeRunner([])
        with self.assertRaises(MatrixError):
            run_bench([fixture("s", Kind.SEEDED, ("x",))], plugin_config(), runner=runner,
                      workspace=TempGitWorkspace(TEMPLATE, "reviewer", parent=self.tmp),
                      trials=1, timeout_s=TIMEOUT_S, verdict_path=VERDICT,
                      now=time.time, gate=evaluate, on_trial=lambda r: None)
        self.assertEqual(runner.requests, [])

    def test_git_that_cannot_start_raises_and_leaves_no_repo(self) -> None:
        ws = TempGitWorkspace(TEMPLATE, "reviewer", parent=self.tmp, git=str(self.tmp / "no-such-git"))
        with self.assertRaises(OSError):
            run_bench([fixture("s", Kind.SEEDED, ("x",)), fixture("c", Kind.CLEAN)], plugin_config(),
                      runner=FakeRunner([]), workspace=ws, trials=1, timeout_s=TIMEOUT_S,
                      verdict_path=VERDICT, now=time.time, gate=evaluate, on_trial=lambda r: None)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_a_failing_git_step_is_a_workspace_error(self) -> None:
        ws = TempGitWorkspace(TEMPLATE, "reviewer", parent=self.tmp, git="false")
        with self.assertRaises(WorkspaceError), ws.materialize(fixture("c", Kind.CLEAN)):
            pass
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_bad_trials_raise(self) -> None:
        for trials in (0, -1, True):
            with self.subTest(trials=trials), self.assertRaises(ValueError):
                self.bench([fixture("s", Kind.SEEDED, ("x",)), fixture("c", Kind.CLEAN)], [], trials=trials)
