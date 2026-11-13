"""Milestone 5: ``GhShipper`` against a real temp repo, a local bare ``origin`` and a fake gh.

No network and no GitHub. Failures come from real git hooks, real branch
collisions and a scripted fake gh, not mocks of the shipper's own code.
Ids (A1-A8, S1-S8) are the research note's
(``research/2026-11-13-nightloop-real-shipper.md``).
"""

from __future__ import annotations

import time
from pathlib import Path

from nightloop.cycle import Clean, GhShipper, GitWorkspace, NotShipped, RunId, Shipped, Snapshotted
from nightloop.cycle.review_record import REVIEW_MARKER

from .gh_support import FINDINGS_LINES, VERDICT, FakeGh
from .git_support import GitRepoCase, git

RUN = RunId("2026-11-11_020001")
STARTED = 1780000000
HHMMSS = time.strftime("%H%M%S", time.localtime(STARTED))
BRANCH = f"cycle/2026-11-11-{HHMMSS}-1-ship"
TITLE = "Add the demo app"
VERDICT_PATH = Path("logs/last-review.md")


class GhShipperCase(GitRepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.sleeps: list[float] = []
        (self.repo / "logs").mkdir()
        self.write_verdict(VERDICT)

    def write_verdict(self, text: str) -> None:
        (self.repo / VERDICT_PATH).write_text(text, encoding="utf-8")

    def make_work(self) -> None:
        (self.repo / "projects" / "demo").mkdir(parents=True)
        (self.repo / "projects" / "demo" / "app.py").write_text("print('hi')\n", encoding="utf-8")

    def fake(self, **state: object) -> FakeGh:
        return FakeGh(self.repo.parent / f"gh-{len(list(self.repo.parent.glob('gh-*')))}", **state)

    def ship(self, fake: FakeGh, *, title: str = TITLE, git_program: str = "git") -> object:
        shipper = GhShipper(
            self.repo, verdict_path=VERDICT_PATH, title=lambda: title, now=lambda: STARTED,
            sleep=self.sleeps.append, gh=str(fake.path), git=git_program,
        )
        return shipper.ship(RUN, 1)

    def assert_not_shipped(self, outcome: object, *phrases: str) -> None:
        self.assertIsInstance(outcome, NotShipped)
        for phrase in phrases:
            self.assertIn(phrase, outcome.reason)

    def assert_work_uncommitted_on_main(self) -> None:
        self.assertEqual(self.current_branch(), "main")
        self.assertIn("projects/", self.porcelain())
        self.assertEqual(git(self.repo, "rev-parse", "main"), git(self.origin, "rev-parse", "main"))


class ShipsTest(GhShipperCase):
    def test_a1_ships_the_work_with_the_review_record(self) -> None:
        self.make_work()
        fake = self.fake()
        self.assertEqual(self.ship(fake), Shipped(7))
        # The branch is pushed, holds the work, and is the only thing pushed.
        self.assertEqual(sorted(self.origin_branches()), sorted(["main", BRANCH]))
        self.assertEqual(git(self.origin, "show", f"{BRANCH}:projects/demo/app.py"), "print('hi')\n")
        self.assertEqual(git(self.origin, "log", "-1", "--format=%s", BRANCH).strip(), TITLE)
        # The create call: explicit head and base, the title, the body on stdin.
        [create] = fake.calls_to("pr", "create")
        argv = create["argv"]
        self.assertEqual(argv[argv.index("--head") + 1], BRANCH)
        self.assertEqual(argv[argv.index("--base") + 1], "main")
        self.assertEqual(argv[argv.index("--body-file") + 1], "-")
        self.assertIn(f"--title={TITLE}", argv)
        self.assertIn(REVIEW_MARKER, create["stdin"])
        for finding in FINDINGS_LINES:
            self.assertIn(finding, create["stdin"].splitlines())
        # S5: the body is read back.
        [view] = fake.calls_to("pr", "view")
        self.assertEqual(view["argv"], ["pr", "view", "7", "--json", "body,mergeable"])
        # S7: back on a clean main equal to origin's; recovery finds nothing.
        self.assert_clean_main_equal_to_origin()
        self.assertEqual(self.sleeps, [])
        self.assertEqual(GitWorkspace(self.repo, now=lambda: STARTED).recover(RUN, 1), Clean())

    def test_ignored_files_are_not_shipped(self) -> None:
        self.make_work()
        self.ship(self.fake())
        listed = git(self.repo, "ls-tree", "-r", "--name-only", BRANCH).split()
        self.assertNotIn("logs/last-review.md", listed)
        self.assertTrue((self.repo / VERDICT_PATH).exists())


class MergeableTest(GhShipperCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_work()

    def test_a2_conflicting_leaves_the_pr_open(self) -> None:
        outcome = self.ship(self.fake(mergeable=["CONFLICTING"]))
        self.assert_not_shipped(outcome, "PR #7", "mergeable=CONFLICTING")
        self.assertEqual(self.sleeps, [])
        self.assert_clean_main_equal_to_origin()

    def test_a2_unknown_three_times_then_mergeable_ships_after_three_sleeps(self) -> None:
        fake = self.fake(mergeable=["UNKNOWN", "UNKNOWN", "UNKNOWN", "MERGEABLE"])
        self.assertEqual(self.ship(fake), Shipped(7))
        self.assertEqual(self.sleeps, [2.0, 2.0, 2.0])
        self.assertEqual(len(fake.calls_to("pr", "view")), 4)

    def test_a2_unknown_six_times_is_not_shipped_within_the_budget(self) -> None:
        fake = self.fake(mergeable=["UNKNOWN"] * 6)
        self.assert_not_shipped(self.ship(fake), "PR #7", "mergeable=UNKNOWN")
        self.assertEqual(len(fake.calls_to("pr", "view")), 5)
        self.assertEqual(len(self.sleeps), 4)

    def test_an_unexpected_value_is_a_refusal_and_stays_one_line(self) -> None:
        self.assert_not_shipped(self.ship(self.fake(mergeable=["BLOCKED"])), "mergeable=BLOCKED")
        git(self.repo, "branch", "-D", BRANCH)  # let the next ship reuse the name
        git(self.origin, "branch", "-D", BRANCH)
        self.make_work()
        self.assert_not_shipped(self.ship(self.fake(mergeable=["two\nlines"])), "mergeable='two\\nlines'")


class FailedStepsTest(GhShipperCase):
    def test_a3_a_rejected_push_never_calls_gh_and_keeps_the_work(self) -> None:
        self.make_work()
        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\necho 'push refused for the test' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        fake = self.fake()
        outcome = self.ship(fake)
        self.assert_not_shipped(outcome, "push failed", BRANCH, "the work is on the local branch")
        self.assertEqual(fake.calls(), [])
        self.assertEqual(self.origin_branches(), ["main"])
        self.assertEqual(self.file_on(BRANCH, "projects/demo/app.py"), "print('hi')\n")
        self.assert_clean_main_equal_to_origin()

    def test_a4_each_gh_failure_is_not_shipped(self) -> None:
        cases = (
            ({"create_rc": 1, "create_stdout": "", "create_stderr": "a pull request already exists\n"},
             ("gh pr create failed", "already exists")),
            ({"create_stdout": "Created!\n"}, ("printed no PR URL", BRANCH)),
            ({"view_rc": 1}, ("PR #7", "gh pr view failed")),
            ({"view_raw": "{not json"}, ("PR #7", "unreadable gh output")),
            ({"view_raw": '{"body": 3, "mergeable": "MERGEABLE"}'}, ("PR #7", "unreadable gh output")),
            ({"strip_marker": True}, ("PR #7", "without the review record")),
        )
        for state, phrases in cases:
            with self.subTest(state=state):
                self.make_work()
                self.assert_not_shipped(self.ship(self.fake(**state)), *phrases)
                self.assert_clean_main_equal_to_origin()
                git(self.repo, "branch", "-D", BRANCH)
                git(self.origin, "branch", "-D", BRANCH)

    def test_a5_a_credential_in_the_findings_is_never_posted(self) -> None:
        self.make_work()
        self.write_verdict(VERDICT + "- blocker: key sk-ant-aaaaaaaaaaaa in app.py\n")
        fake = self.fake()
        outcome = self.ship(fake)
        self.assert_not_shipped(outcome, "credential pattern")
        self.assertNotIn("sk-ant", outcome.reason)
        self.assertEqual(fake.calls(), [])
        self.assertEqual(self.local_branches("cycle/*"), [])
        self.assert_work_uncommitted_on_main()

    def test_a6_a_verdict_without_findings_is_not_shipped(self) -> None:
        self.make_work()
        self.write_verdict("VERDICT: PASS\nIncrement: x\nRan: y\n")
        fake = self.fake()
        self.assert_not_shipped(self.ship(fake), "Findings")
        self.assertEqual(fake.calls(), [])
        self.assert_work_uncommitted_on_main()

    def test_a_missing_verdict_file_is_not_shipped(self) -> None:
        self.make_work()
        (self.repo / VERDICT_PATH).unlink()
        fake = self.fake()
        self.assert_not_shipped(self.ship(fake), "cannot read the verdict file")
        self.assertEqual(fake.calls(), [])

    def test_a_title_that_is_not_one_line_is_not_shipped(self) -> None:
        self.make_work()
        for title in ("", "   ", "two\nlines"):
            with self.subTest(title=title):
                self.assert_not_shipped(self.ship(self.fake(), title=title), "PR title")
                self.assert_work_uncommitted_on_main()

    def test_a_title_starting_with_a_dash_is_a_title_not_an_option(self) -> None:
        self.make_work()
        fake = self.fake()
        self.assertEqual(self.ship(fake, title="--amend everything"), Shipped(7))
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s", BRANCH).strip(), "--amend everything")
        self.assertIn("--title=--amend everything", fake.calls_to("pr", "create")[0]["argv"])


class MainStateTest(GhShipperCase):
    def test_a7_s3_main_ahead_of_origin_refuses_before_anything_is_made(self) -> None:
        (self.repo / "STRAY.md").write_text("a failed cycle's commit\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "stray commit on main")
        stray = git(self.repo, "rev-parse", "HEAD").strip()
        self.make_work()
        fake = self.fake()
        self.assert_not_shipped(self.ship(fake), "1 commit(s) ahead", "nothing was pushed")
        self.assertEqual(fake.calls(), [])
        self.assertEqual(self.origin_branches(), ["main"])
        self.assertEqual(self.local_branches("cycle/*"), [])
        # Recovery then moves both the commit and the work off main.
        recovery = GitWorkspace(self.repo, now=lambda: STARTED).recover(RUN, 1)
        self.assertEqual(recovery, Snapshotted(branch=f"cycle/2026-11-11-unshipped-{HHMMSS}-1", pushed=True))
        self.assert_clean_main_equal_to_origin()
        git(self.repo, "merge-base", "--is-ancestor", stray, recovery.branch)
        git(self.origin, "merge-base", "--is-ancestor", stray, recovery.branch)
        self.assertEqual(self.file_on(recovery.branch, "projects/demo/app.py"), "print('hi')\n")

    def test_s7_a_failed_commit_deletes_only_its_branch_and_leaves_the_work(self) -> None:
        self.make_work()
        self.fail_every_commit()
        fake = self.fake()
        self.assert_not_shipped(self.ship(fake), "commit on", "refusing for the test", "still uncommitted")
        self.assertEqual(fake.calls(), [])
        self.assertEqual(self.local_branches("cycle/*"), [])
        self.assert_work_uncommitted_on_main()

    def test_s8_a_name_collision_is_not_shipped_and_the_other_branch_is_untouched(self) -> None:
        git(self.repo, "branch", BRANCH)
        before = git(self.repo, "rev-parse", BRANCH)
        self.make_work()
        fake = self.fake()
        self.assert_not_shipped(self.ship(fake), "could not create", BRANCH)
        self.assertEqual(git(self.repo, "rev-parse", BRANCH), before)
        self.assertEqual(fake.calls(), [])
        self.assert_work_uncommitted_on_main()

    def test_a_clean_tree_has_nothing_to_ship(self) -> None:
        fake = self.fake()
        self.assert_not_shipped(self.ship(fake), "nothing to ship")
        self.assertEqual(fake.calls(), [])
        self.assertEqual(self.local_branches("cycle/*"), [])

    def test_head_off_main_is_not_shipped(self) -> None:
        git(self.repo, "switch", "-q", "-c", "scratch")
        self.make_work()
        self.assert_not_shipped(self.ship(self.fake()), "on scratch, not main")
        git(self.repo, "switch", "-q", "--detach", "main")
        self.assert_not_shipped(self.ship(self.fake()), "HEAD is not on a branch")

    def test_an_unreachable_origin_is_not_shipped(self) -> None:
        self.make_work()
        git(self.repo, "remote", "set-url", "origin", str(self.repo.parent / "gone.git"))
        fake = self.fake()
        self.assert_not_shipped(self.ship(fake), "could not fetch origin/main")
        self.assertEqual(fake.calls(), [])


class ConstructionTest(GhShipperCase):
    def test_bad_setups_fail_at_construction(self) -> None:
        fake = self.fake()

        def build(repo: Path, **kwargs: str) -> GhShipper:
            return GhShipper(repo, verdict_path=VERDICT_PATH, title=lambda: TITLE, now=time.time,
                             sleep=self.sleeps.append, **{"gh": str(fake.path), **kwargs})

        with self.assertRaises(ValueError):
            build(Path("relative"))
        with self.assertRaises(ValueError):
            build(self.origin.parent)
        with self.assertRaises(ValueError):
            build(self.repo, gh="/nonexistent/gh")
        with self.assertRaises(ValueError):
            build(self.repo, git="/nonexistent/git")
        with self.assertRaises(TypeError):
            GhShipper(str(self.repo), verdict_path=VERDICT_PATH, title=lambda: TITLE,  # type: ignore[arg-type]
                      now=time.time, sleep=self.sleeps.append, gh=str(fake.path))

    def test_s1_gh_vanishing_after_construction_raises_and_leaves_main_clean(self) -> None:
        self.make_work()
        fake = self.fake()
        shipper = GhShipper(self.repo, verdict_path=VERDICT_PATH, title=lambda: TITLE, now=lambda: STARTED,
                            sleep=self.sleeps.append, gh=str(fake.path))
        fake.path.unlink()
        with self.assertRaises(OSError):
            shipper.ship(RUN, 1)
        self.assert_clean_main_equal_to_origin()
        self.assertIn(BRANCH, self.origin_branches())


if __name__ == "__main__":
    import unittest

    unittest.main()
