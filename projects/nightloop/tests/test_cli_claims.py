"""Milestone 7b, W7 and W9-W12 at the CLI: ``nightloop run`` runs the claim steps, ``--dry-run`` only lists them.

``main`` is driven as in ``test_cli_main.py`` (``FakeRunner``, a recording
workspace, ``FakeShipper``), plus an injected claims factory whose
publisher logs into the workspace's call list, so one list shows the
interleaving. The last test runs the production claim and workspace
wiring on a temp repo with a local bare ``origin``. Numbers refer to
research/2026-11-20-nightloop-claim-wiring.md.
"""

from __future__ import annotations

import contextlib
import io
import re

from nightloop.claims import (
    Claim,
    ClaimPorts,
    ClaimSourceError,
    PublishFailed,
    StrandedBranch,
    Unreadable,
    UnshippedBranch,
)
from nightloop.cli import ExitCode, main
from nightloop.cycle import FakeShipper, NotShipped, Shipped
from nightloop.phase import FakeRunner, RawRun, cli_success

from .claims_support import MemoryBacklog, RecordingPublisher, ScriptedSource
from .cli_support import passing_gate, tree_digest, write_config
from .git_support import GitRepoCase, git
from .test_cli_main import INSIDE, CliCase, never

KEY = "Claim wiring at the CLI"
BACKLOG = f"# BACKLOG\n- [ ] {KEY}\n- [building] {KEY} (shipped)\n"
BRANCH = StrandedBranch("cycle/2026-11-19-unshipped-020000-1")
UNMERGED = "gh reports no merge commit for PR #1: got ''"
CLAIM_LINE = re.compile(r"^nightloop: claims (before cycle 1|after cycle \d+|after the night): ")


class SourceFailingOn(ScriptedSource):
    """Raises ``ClaimSourceError`` on the listed calls (1-based) and lists no branch on the others."""

    def __init__(self, *failing_calls: int) -> None:
        super().__init__([])
        self.calls = 0
        self._failing = set(failing_calls)

    def unshipped_claims(self):
        self.calls += 1
        if self.calls in self._failing:
            raise ClaimSourceError("git for-each-ref failed: fatal: not a git repository")
        return []


class ClaimsCliCase(CliCase):
    def claims(self, source: ScriptedSource, *, text: str = BACKLOG, **publisher) -> dict:
        self.backlog = MemoryBacklog(text)
        self.publisher = RecordingPublisher(self.workspace.calls, **publisher)
        self.claim_repos: list[tuple] = []

        def factory(repo, backlog):
            self.claim_repos.append((repo, backlog))
            return ClaimPorts(source, self.backlog, self.publisher)

        return {"claims_factory": factory}

    def passing_night(self, cycles: int, ships: list, **claims) -> tuple[int, str, str]:
        write_config(self.config, cycles=cycles)
        runner = FakeRunner([cli_success()] * (3 * cycles))
        return self.call(*self.run_args(), **self.wiring(runner, FakeShipper(ships), gate=passing_gate), **claims)


def claim_lines(err: str) -> list[str]:
    return [line for line in err.splitlines() if CLAIM_LINE.match(line)]


class OrderTest(ClaimsCliCase):
    def test_w7_steps_run_before_cycle_1_after_each_recovery_and_after_the_night(self) -> None:
        merged = {1: Unreadable(UNMERGED), 2: Unreadable("not merged")}
        code, out, err = self.passing_night(2, [Shipped(1), Shipped(2)],
                                            **self.claims(ScriptedSource([], merged=merged)))
        self.assertEqual(code, ExitCode.OK, err)
        run_ts = "2026-11-19_023000"
        self.assertEqual(self.workspace.calls, [
            "ready",
            "clear_handoff logs/last-review.md", f"recover {run_ts} 1", "ready",
            "clear_handoff logs/last-review.md", f"recover {run_ts} 2", "ready",
            "ready",
        ])
        self.assertEqual(claim_lines(err), [
            "nightloop: claims before cycle 1: nothing to change",
            "nightloop: claims after cycle 1: nothing to change",
            "nightloop: claims after cycle 2: nothing to change",
            "nightloop: claims after the night: nothing to change",
        ])
        self.assertIn(f"  PR #1: pending, not marked ({UNMERGED})", err)
        self.assertEqual(out.strip().splitlines(), ["cycle 1: shipped PR #1; tree clean",
                                                    "cycle 2: shipped PR #2; tree clean"],
                         "stdout keeps m7's end-of-night summary only")
        self.assertEqual(self.claim_repos, [(self.repo, "BACKLOG.md")])

    def test_w7_w11_the_dry_run_lists_exactly_the_steps_a_run_takes(self) -> None:
        merged = {1: Unreadable(UNMERGED), 2: Unreadable(UNMERGED)}
        _, _, err = self.passing_night(2, [Shipped(1), Shipped(2)], **self.claims(ScriptedSource([], merged=merged)))
        ran = [CLAIM_LINE.match(line).group(1) for line in claim_lines(err)]
        _, dry, _ = self.call(*self.run_args("--dry-run"), **self.no_effects(INSIDE), claims_factory=never)
        planned = re.findall(r"^  (before cycle 1|after cycle \d+|after the night): ", dry, re.MULTILINE)
        self.assertEqual(planned, ran)

    def test_w7_the_step_after_a_usage_limited_cycle_runs(self) -> None:
        limited = RawRun(returncode=1, stdout="", stderr="You've hit your session limit · resets 4pm", timed_out=False)
        write_config(self.config, cycles=3)
        code, _, err = self.call(*self.run_args(), **self.wiring(FakeRunner([limited]), FakeShipper([])),
                                 **self.claims(ScriptedSource([])))
        self.assertEqual(code, ExitCode.USAGE_LIMITED)
        self.assertEqual([CLAIM_LINE.match(l).group(1) for l in claim_lines(err)],
                         ["before cycle 1", "after cycle 1", "after the night"])

    def test_a_stranded_claim_is_published_before_the_first_cycle(self) -> None:
        source = ScriptedSource([UnshippedBranch(BRANCH, Claim(KEY, KEY))])
        code, _, err = self.passing_night(1, [NotShipped("x")], **self.claims(source))
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual(self.workspace.calls[:3], ["ready", "publish", "clear_handoff logs/last-review.md"])
        self.assertIn("nightloop: claims before cycle 1: published 1 backlog edit(s) to main", err)
        self.assertIn(f"  {BRANCH}: marked", err)
        self.assertIn("nightloop: claims after cycle 1: nothing to change", err)
        self.assertEqual(self.publisher.messages,
                         ["chore(backlog): reconcile 1 claim(s) stranded on unshipped branches"])


class ShippedTest(ClaimsCliCase):
    def test_w9_a_merged_pr_is_marked_done_and_published_after_the_night(self) -> None:
        source = ScriptedSource([], merged={7: Claim(f"{KEY} (shipped)", f"{KEY} (shipped)")})
        code, _, err = self.passing_night(1, [Shipped(7)], **self.claims(source))
        self.assertEqual(code, ExitCode.OK, err)
        self.assertIn("nightloop: claims after the night: published 1 backlog edit(s) to main", err)
        self.assertIn("  PR #7: marked done", err)
        self.assertIn(f"- [done #7] {KEY} (shipped)\n", self.backlog.text)
        self.assertEqual(self.publisher.messages, ["chore(backlog): mark 1 claim(s) done for merged PR(s) #7"])

    def test_no_shipped_pr_means_no_post_night_ready(self) -> None:
        code, _, err = self.passing_night(1, [NotShipped("x")], **self.claims(ScriptedSource([])))
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual(self.workspace.calls.count("ready"), 2)
        self.assertIn("nightloop: claims after the night: nothing to change", err)

    def test_a_failed_publish_is_reported_and_does_not_change_the_exit_code(self) -> None:
        source = ScriptedSource([UnshippedBranch(BRANCH, Claim(KEY, KEY))])
        code, _, err = self.passing_night(
            1, [NotShipped("x")], **self.claims(source, failure=PublishFailed("git push failed: rejected")))
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("nightloop: claims before cycle 1: publish FAILED: git push failed: rejected", err)


class FailureTest(ClaimsCliCase):
    """W10."""

    def test_a_source_error_before_the_night_is_skipped_and_the_night_runs(self) -> None:
        code, out, err = self.passing_night(1, [NotShipped("no changes to ship")], **self.claims(SourceFailingOn(1)))
        self.assertEqual(code, ExitCode.OK, err)
        self.assertIn("nightloop: claims before cycle 1: skipped: cannot list recovery branches: "
                      "git for-each-ref failed: fatal: not a git repository", err)
        self.assertIn("nightloop: claims after cycle 1: nothing to change", err)
        self.assertIn("cycle 1: review passed but not shipped: no changes to ship", out)

    def test_a_source_error_after_a_cycle_propagates(self) -> None:
        with self.assertRaises(ClaimSourceError):
            self.passing_night(2, [Shipped(1), Shipped(2)], **self.claims(SourceFailingOn(2)))
        self.assertEqual(self.workspace.calls.count("clear_handoff logs/last-review.md"), 1, "cycle 2 never started")

    def test_an_os_error_in_the_pre_night_step_exits_1_before_any_phase(self) -> None:
        class NoGit(RecordingPublisher):
            def ready(self):
                raise FileNotFoundError(2, "No such file or directory", "git")

        runner = FakeRunner([])
        source = ScriptedSource([])
        code, _, err = self.call(*self.run_args(), **self.wiring(runner, FakeShipper([])),
                                 claims_factory=lambda repo, b: ClaimPorts(source, MemoryBacklog(BACKLOG), NoGit()))
        self.assertEqual(code, ExitCode.ENVIRONMENT)
        self.assertIn("No such file or directory", err)
        self.assertEqual(runner.requests, [])

    def test_an_os_error_after_the_night_exits_1_after_the_summary(self) -> None:
        class NoGh(ScriptedSource):
            def merged_claim(self, pr):
                raise FileNotFoundError(2, "No such file or directory", "gh")

        code, out, err = self.passing_night(1, [Shipped(5)], **self.claims(NoGh([])))
        self.assertEqual(code, ExitCode.ENVIRONMENT)
        self.assertIn("cycle 1: shipped PR #5", out)
        self.assertIn("environment error in the claim step after the night", err)

    def test_a_claims_factory_that_rejects_the_repo_exits_2_before_any_runner(self) -> None:
        def rejecting(repo, backlog):
            raise ValueError("relative must be a path inside the repo")

        code, _, err = self.call(*self.run_args(), **self.wiring(FakeRunner([]), FakeShipper([])),
                                 claims_factory=rejecting)
        self.assertEqual(code, ExitCode.USAGE)
        self.assertIn("relative must be a path inside the repo", err)
        self.assertNotIn("runner", self.built)


class DryRunAndOptOutTest(ClaimsCliCase):
    """W11 and W12."""

    def test_w11_the_dry_run_lists_the_claim_steps_and_runs_none(self) -> None:
        write_config(self.config, cycles=2)
        before = tree_digest(self.repo)
        code, out, err = self.call(*self.run_args("--dry-run"), **self.no_effects(INSIDE), claims_factory=never)
        self.assertEqual(code, ExitCode.OK, err)
        self.assertIn("claims (backlog BACKLOG.md): each step checks main is current after a fetch", out)
        for line in ("  before cycle 1: mark claims on recovery branches stranded",
                     "  after cycle 2: mark claims on recovery branches stranded",
                     "  after the night: mark tonight's shipped PRs done if already merged"):
            self.assertIn(line, out)
        self.assertEqual(err, "")
        self.assertEqual(tree_digest(self.repo), before)

    def test_w12_no_claims_runs_no_step_and_the_dry_run_says_so(self) -> None:
        code, out, err = self.passing_night(1, [Shipped(1)])  # warm-up: default wiring prints claim lines
        self.assertTrue(claim_lines(err))
        write_config(self.config, cycles=1)
        runner = FakeRunner([cli_success()] * 3)
        code, out, err = self.call(*self.run_args("--no-claims"),
                                   **self.wiring(runner, FakeShipper([Shipped(1)]), gate=passing_gate),
                                   claims_factory=never)
        self.assertEqual(code, ExitCode.OK, err)
        self.assertEqual(claim_lines(err), [])
        _, dry, _ = self.call(*self.run_args("--dry-run", "--no-claims"), **self.no_effects(INSIDE),
                              claims_factory=never)
        self.assertIn("claims: off (--no-claims); no backlog step runs", dry)
        self.assertNotIn("before cycle 1", dry)

    def test_w12_a_missing_backlog_with_the_production_wiring_is_skipped_every_step(self) -> None:
        # self.repo is a temp dir with no BACKLOG.md and no .git: the real publisher skips before any git.
        code, _, err = self.passing_night(1, [Shipped(4)])
        self.assertEqual(code, ExitCode.OK, err)
        lines = claim_lines(err)
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("skipped: no backlog file BACKLOG.md", line)

    def test_the_backlog_path_comes_from_the_config(self) -> None:
        write_config(self.config)
        self.config.write_text(self.config.read_text().replace('backlog = "BACKLOG.md"', 'backlog = "docs/TODO.md"'))
        runner = FakeRunner([cli_success()] * 3)
        self.call(*self.run_args(), **self.wiring(runner, FakeShipper([NotShipped("x")]), gate=passing_gate),
                  **self.claims(ScriptedSource([])))
        self.assertEqual(self.claim_repos, [(self.repo, "docs/TODO.md")])
        _, dry, _ = self.call(*self.run_args("--dry-run"), **self.no_effects(INSIDE), claims_factory=never)
        self.assertIn("claims (backlog docs/TODO.md)", dry)


class ProductionWiringTest(GitRepoCase):
    """The default claims and workspace factories on a real checkout: a stranded claim reaches origin/main."""

    def test_a_night_publishes_a_stranded_claim_to_origin_before_its_first_cycle(self) -> None:
        root = self.repo.parent
        (self.repo / "BACKLOG.md").write_text(BACKLOG, encoding="utf-8")
        git(self.repo, "add", "BACKLOG.md")
        git(self.repo, "commit", "-q", "-m", "backlog")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "switch", "-q", "-c", str(BRANCH))
        (self.repo / "BACKLOG.md").write_text(BACKLOG.replace(f"- [ ] {KEY}", f"- [building] {KEY}"), encoding="utf-8")
        git(self.repo, "commit", "-q", "-am", "claimed, then the cycle failed")
        git(self.repo, "push", "-q", "origin", str(BRANCH))
        git(self.repo, "switch", "-q", "main")
        before = git(self.origin, "rev-parse", "main").strip()

        config = write_config(root / "nightloop.toml")  # outside the checkout, so it is not dirt
        failing = RawRun(returncode=1, stdout="", stderr="boom", timed_out=False)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["run", "--repo", str(self.repo), "--config", str(config)],
                        clock=lambda: INSIDE, runner_factory=lambda: FakeRunner([failing]),
                        shipper_factory=lambda repo, verdict: FakeShipper([]))
        self.assertEqual(code, ExitCode.OK, err.getvalue())
        self.assertIn("nightloop: claims before cycle 1: published 1 backlog edit(s) to main", err.getvalue())
        self.assertIn("nightloop: claims after cycle 1: nothing to change", err.getvalue())
        self.assertIn("cycle 1: research failed (CLI_ERROR); tree clean", out.getvalue())
        new = git(self.origin, "rev-list", f"{before}..main").split()
        self.assertEqual(len(new), 1)
        self.assertEqual(git(self.origin, "diff-tree", "--no-commit-id", "--name-only", "-r", new[0]).split(),
                         ["BACKLOG.md"])
        self.assertIn(f"- [stranded {BRANCH}] {KEY}\n", git(self.origin, "show", "main:BACKLOG.md"))
        self.assert_clean_main_equal_to_origin()
        self.assertEqual(self.local_branches("cycle/*"), [str(BRANCH)], "a failed research phase leaves no branch")


if __name__ == "__main__":
    import unittest

    unittest.main()
