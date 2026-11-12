"""Milestone 5, R5: ``run_night`` with the real ``GitWorkspace``, the real ``GitHubShipper`` and a fake ``gh``.

Two cycles. Cycle 1 passes review and is shipped for real; then the
shipper's wrapper leaves a commit on local main, the leak #89's review
found. Cycle 2 must start without it. No model is called: phases are
``FakeRunner`` steps that write what a phase would.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

from nightloop.cycle import GitWorkspace, ReviewRefused, RunId, Shipped, Snapshotted, run_night
from nightloop.gates import Verdict, evaluate
from nightloop.phase import FakeRunner, PhaseRequest, RawRun, cli_success
from nightloop.ship import GhCli, GitBranchPublisher, GitHubShipper, verdict_reader

from .fake_gh import FakeGh
from .git_support import GitRepoCase, git
from .phase_support import example_config

RUN = RunId("2026-11-12_020001")
STARTED = 1780000000
HHMMSS = time.strftime("%H%M%S", time.localtime(STARTED))
VERDICT = Path("logs/last-review.md")
PASSING = "VERDICT: PASS\nRan:\n- unit tests\nFindings:\n- none blocking\n"


class LeakyShipper:
    """The real shipper, then a commit on local main: a shipper bug the recovery must contain."""

    def __init__(self, inner: GitHubShipper, repo: Path) -> None:
        self._inner = inner
        self._repo = repo

    def ship(self, run_id: RunId, cycle: int):
        outcome = self._inner.ship(run_id, cycle)
        (self._repo / "LEAK.md").write_text("committed on main by mistake\n", encoding="utf-8")
        git(self._repo, "add", "LEAK.md")
        git(self._repo, "commit", "-q", "-m", "leaked onto main")
        return outcome


class ShipNightTest(GitRepoCase):
    def write(self, rel: str, text: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (STARTED, STARTED))

    def step(self, name: str, writes: dict[str, str], check=None):
        def run(req: PhaseRequest) -> RawRun:
            assert req.name == name, f"expected {name}, got {req.name}"
            if check is not None:
                check()
            for rel, text in writes.items():
                self.write(rel, text)
            return cli_success()

        return run

    def test_r5_a_commit_left_on_main_by_the_shipper_does_not_reach_cycle_2(self) -> None:
        def starts_without_the_leak() -> None:
            self.assertFalse((self.repo / "LEAK.md").exists())
            self.assertFalse((self.repo / "projects" / "demo" / "app.py").exists())
            self.assertEqual(self.porcelain(), "")
            self.assertEqual(git(self.repo, "rev-parse", "main"), git(self.origin, "rev-parse", "main"))

        steps = [
            self.step("research", {"research/2026-11-12-note.md": "# note\n"}),
            self.step("build", {"projects/demo/app.py": "print('hi')\n"}),
            self.step("review", {str(VERDICT): PASSING}),
            self.step("research", {"research/2026-11-12-note.md": "# note 2\n"}, check=starts_without_the_leak),
            self.step("build", {"projects/demo/other.py": "print('two')\n"}),
            self.step("review", {str(VERDICT): "VERDICT: FAIL\nFindings:\n- broken\n"}),
        ]
        gh = FakeGh(self.repo.parent / "fake-gh")
        shipper = LeakyShipper(
            GitHubShipper(
                read_verdict=verdict_reader(self.repo / VERDICT),
                git=GitBranchPublisher(self.repo),
                gh=GhCli(self.repo, gh=str(gh.program)),
                sleep=lambda _s: None,
            ),
            self.repo,
        )
        runner = FakeRunner(steps)
        night = run_night(
            replace(example_config(), cycles=2), run_id=RUN, repo=self.repo, verdict_path=VERDICT,
            timeout_s=60, runner=runner, gate=evaluate,
            workspace=GitWorkspace(self.repo, now=lambda: STARTED), shipper=shipper,
            now=lambda: STARTED, metrics_sink=lambda _line: None,
        )

        self.assertEqual(len(runner.requests), 6, "cycle 2 ran in full")
        first, second = night.reports
        self.assertEqual(first.result, Shipped(pr=7))
        self.assertEqual(first.recovery, Snapshotted(branch=f"cycle/2026-11-12-unshipped-{HHMMSS}-1", pushed=True))
        self.assertEqual(self.file_on(first.recovery.branch, "LEAK.md"), "committed on main by mistake\n")
        self.assertEqual(git(self.origin, "show", "nightloop/2026-11-12_020001-1:projects/demo/app.py"),
                         "print('hi')\n")
        self.assertEqual(second.result, ReviewRefused(Verdict.FAIL))
        self.assertIsInstance(second.recovery, Snapshotted)
        self.assertNotIn("LEAK.md", git(self.repo, "ls-tree", "-r", "--name-only", second.recovery.branch))
        self.assertEqual(gh.keys(), ["list", "create", "view_body", "view_mergeable"], "only cycle 1 shipped")
        self.assert_clean_main_equal_to_origin()
