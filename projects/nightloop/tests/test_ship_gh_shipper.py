"""Milestone 5, S1-S9: ``GhShipper`` on a real temp repo with a local bare ``origin`` and a fake ``gh``.

No test talks to GitHub or runs a real ``gh``. Push failures come from a
real ``pre-push`` hook; what was pushed is read from the bare origin and
from a git wrapper that records every argv. Numbers refer to
research/2026-11-15-nightloop-real-shipper.md.
"""

from __future__ import annotations

import time
from pathlib import Path

from nightloop.cycle import Clean, GitWorkspace, NotShipped, RecoveryError, RunId, Shipped
from nightloop.ship import MARKER, GhShipper

from .git_support import GitRepoCase, git
from .ship_support import FakeGh, GitRecorder, verdict_text

RUN = RunId("2026-11-15_020003")
BRANCH = "cycle/2026-11-15-020003-1-ship"
VERDICT = Path("logs/last-review.md")
SECRET = "sk-ant-abcdefgh12345"


class ShipperCase(GitRepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.repo.parent
        self.sleeps: list[float] = []
        self.origin_main = git(self.origin, "rev-parse", "main")

    # --- set-up helpers -------------------------------------------------------

    def write_verdict(self, text: str | None = None) -> None:
        path = self.repo / VERDICT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(verdict_text() if text is None else text, encoding="utf-8")

    def build_leaves_work(self) -> None:
        """What a build phase leaves: an edited file and a new one, uncommitted."""
        (self.repo / "README.md").write_text("# fixture, edited\n", encoding="utf-8")
        (self.repo / "app.py").write_text("print('hi')\n", encoding="utf-8")

    def build_commits_on_main(self) -> str:
        """What a build phase that commits by itself leaves: one commit on local main."""
        (self.repo / "lib.py").write_text("X = 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "builder committed on main")
        return git(self.repo, "rev-parse", "main")

    def reject_pushes(self) -> None:
        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\necho 'push refused for the test' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

    def shipper(self, gh: FakeGh | str, *, git_program: str = "git", timeout_s: float = 60) -> GhShipper:
        program = gh if isinstance(gh, str) else gh.program
        return GhShipper(self.repo, VERDICT, sleep=self.sleeps.append, gh=program,
                         git=git_program, timeout_s=timeout_s)

    # --- observations ---------------------------------------------------------

    def assert_origin_main_unchanged(self) -> None:
        self.assertEqual(git(self.origin, "rev-parse", "main"), self.origin_main)

    def assert_nothing_pushed_or_posted(self, gh: FakeGh) -> None:
        self.assertEqual(gh.commands(), [])
        self.assertEqual(self.origin_branches(), ["main"])
        self.assert_origin_main_unchanged()

    def origin_file(self, path: str) -> str:
        return git(self.origin, "show", f"{BRANCH}:{path}")


class HappyPathTest(ShipperCase):
    def test_s1_ships_uncommitted_work_on_its_own_branch(self) -> None:
        self.write_verdict()
        self.build_leaves_work()
        gh = FakeGh(self.root)
        self.assertEqual(self.shipper(gh).ship(RUN, 1), Shipped(pr=7))
        self.assertIn(BRANCH, self.origin_branches())
        self.assertEqual(self.origin_file("app.py"), "print('hi')\n")
        self.assertEqual(self.origin_file("README.md"), "# fixture, edited\n")
        self.assert_origin_main_unchanged()
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.origin_main)
        self.assertEqual(self.porcelain(), "")
        (pr,) = gh.state()["prs"]
        self.assertEqual((pr["head"], pr["base"]), (BRANCH, "main"))
        self.assertEqual(pr["title"], "nightloop: cycle 1 of run 2026-11-15_020003")
        body = pr["body"]
        self.assertIn(MARKER, body)
        self.assertIn("**Verdict:** PASS", body)
        self.assertIn("- nit: the README could name the timeout", body)
        self.assertIn("242 tests OK", body)
        self.assertNotIn("logs/", git(self.origin, "ls-tree", "-r", "--name-only", BRANCH))

    def test_s1_commits_already_on_main_come_along_and_main_is_moved_back(self) -> None:
        self.write_verdict()
        model_commit = self.build_commits_on_main()
        self.build_leaves_work()
        self.assertEqual(self.shipper(FakeGh(self.root)).ship(RUN, 1), Shipped(pr=7))
        self.assertEqual(self.origin_file("lib.py"), "X = 1\n")
        self.assertEqual(self.origin_file("app.py"), "print('hi')\n")
        git(self.origin, "merge-base", "--is-ancestor", model_commit.strip(), BRANCH)  # raises if not
        self.assert_origin_main_unchanged()
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.origin_main, "local main moved back")
        # The orchestrator's recovery then has nothing to snapshot.
        self.assertEqual(GitWorkspace(self.repo, now=time.time).recover(RUN, 1), Clean())
        self.assert_clean_main_equal_to_origin()

    def test_a_builder_that_committed_everything_is_shipped_without_a_new_commit(self) -> None:
        self.write_verdict()
        model_commit = self.build_commits_on_main()
        self.assertEqual(self.shipper(FakeGh(self.root)).ship(RUN, 1), Shipped(pr=7))
        self.assertEqual(git(self.origin, "rev-parse", BRANCH), model_commit)

    def test_nothing_to_ship_is_not_shipped_and_posts_nothing(self) -> None:
        self.write_verdict()
        gh = FakeGh(self.root)
        self.assertEqual(self.shipper(gh).ship(RUN, 1), NotShipped("no changes to ship"))
        self.assert_nothing_pushed_or_posted(gh)
        self.assertEqual(self.local_branches("cycle/*"), [])


class ReviewRecordTest(ShipperCase):
    def test_s2_a_verdict_without_findings_stops_before_any_git_or_gh_call(self) -> None:
        self.write_verdict("VERDICT: PASS\nRan: the tests\n")
        self.build_leaves_work()
        gh, recorder = FakeGh(self.root), GitRecorder(self.root)
        shipper = self.shipper(gh, git_program=recorder.program)
        before = len(recorder.calls())  # the constructor's top-level check
        outcome = shipper.ship(RUN, 1)
        self.assertIsInstance(outcome, NotShipped)
        self.assertIn("Findings", outcome.reason)
        self.assertEqual(len(recorder.calls()), before, "no git call after the refusal")
        self.assert_nothing_pushed_or_posted(gh)
        self.assertNotEqual(self.porcelain(), "", "the work is left for recovery")

    def test_s3_a_credential_in_the_findings_is_never_pushed_or_posted(self) -> None:
        self.write_verdict(verdict_text(findings=f"- the key {SECRET} is in the log"))
        self.build_leaves_work()
        gh = FakeGh(self.root)
        outcome = self.shipper(gh).ship(RUN, 1)
        self.assertIsInstance(outcome, NotShipped)
        self.assertIn("credential", outcome.reason)
        self.assertNotIn(SECRET, outcome.reason)
        self.assert_nothing_pushed_or_posted(gh)

    def test_a_missing_verdict_is_not_shipped(self) -> None:
        self.build_leaves_work()
        gh = FakeGh(self.root)
        outcome = self.shipper(gh).ship(RUN, 1)
        self.assertIsInstance(outcome, NotShipped)
        self.assertIn("missing", outcome.reason)
        self.assert_nothing_pushed_or_posted(gh)


class PushTest(ShipperCase):
    def test_s4_a_rejected_push_keeps_the_commit_local_calls_no_gh_and_leaves_main(self) -> None:
        self.write_verdict()
        model_commit = self.build_commits_on_main()
        self.build_leaves_work()
        self.reject_pushes()
        gh = FakeGh(self.root)
        outcome = self.shipper(gh).ship(RUN, 1)
        self.assertIsInstance(outcome, NotShipped)
        self.assertIn("failed to push", outcome.reason)  # git's last line, not the hook's
        self.assertIn(BRANCH, outcome.reason)
        self.assertEqual(self.local_branches("cycle/*"), [BRANCH])
        self.assertEqual(self.file_on(BRANCH, "app.py"), "print('hi')\n")
        self.assertEqual(gh.commands(), [])
        self.assertEqual(git(self.repo, "rev-parse", "main"), model_commit, "local main not moved")
        self.assertEqual(self.origin_branches(), ["main"])
        # Recovery cannot push either, so it stops the night rather than
        # carry main's commit into the next cycle (R3).
        with self.assertRaises(RecoveryError):
            GitWorkspace(self.repo, now=time.time).recover(RUN, 1)
        self.assertEqual(git(self.repo, "rev-parse", "main"), model_commit)

    def test_s9_only_the_cycle_branch_is_ever_pushed_and_never_forced(self) -> None:
        self.write_verdict()
        self.build_commits_on_main()
        self.build_leaves_work()
        recorder = GitRecorder(self.root)
        self.assertEqual(self.shipper(FakeGh(self.root), git_program=recorder.program).ship(RUN, 1), Shipped(pr=7))
        pushes = [argv for argv in recorder.calls() if argv[:1] == ["push"]]
        ref = f"refs/heads/{BRANCH}"
        self.assertEqual(pushes, [["push", "origin", f"{ref}:{ref}"]])
        for argv in recorder.calls():
            self.assertNotIn("--force", argv)
            self.assertNotIn("--force-with-lease", argv)
            if argv[:1] == ["push"]:
                self.assertFalse(any(arg.startswith("+") or arg.endswith("main") for arg in argv[1:]))

    def test_a_branch_that_does_not_hold_mains_commits_is_not_shipped(self) -> None:
        self.write_verdict()
        model_commit = self.build_commits_on_main()
        git(self.repo, "switch", "-q", "-c", "scratch", "origin/main")  # a phase left HEAD elsewhere
        self.build_leaves_work()
        gh = FakeGh(self.root)
        outcome = self.shipper(gh).ship(RUN, 1)
        self.assertIsInstance(outcome, NotShipped)
        self.assertIn("unpushed commit", outcome.reason)
        self.assert_nothing_pushed_or_posted(gh)
        self.assertEqual(git(self.repo, "rev-parse", "main"), model_commit)


class PullRequestTest(ShipperCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_verdict()
        self.build_leaves_work()

    def test_s5_a_conflicting_pr_is_not_shipped_and_names_the_pr(self) -> None:
        outcome = self.shipper(FakeGh(self.root, mergeable=["CONFLICTING"])).ship(RUN, 1)
        self.assertEqual(outcome, NotShipped("PR #7 opened but mergeable=CONFLICTING"))

    def test_s5_unknown_is_polled_until_github_decides(self) -> None:
        gh = FakeGh(self.root, mergeable=["UNKNOWN", "UNKNOWN", "MERGEABLE"])
        self.assertEqual(self.shipper(gh).ship(RUN, 1), Shipped(pr=7))
        self.assertEqual(self.sleeps, [2.0, 2.0])
        self.assertEqual(gh.commands().count("pr view"), 1 + 3)  # body, then three mergeable reads

    def test_s5_still_unknown_after_five_reads_is_not_shipped(self) -> None:
        gh = FakeGh(self.root, mergeable=["UNKNOWN"])
        self.assertEqual(self.shipper(gh).ship(RUN, 1), NotShipped("PR #7 opened but mergeable=UNKNOWN"))
        self.assertEqual(len(self.sleeps), 4)
        self.assertEqual(gh.commands().count("pr view"), 1 + 5)

    def test_s6_a_retry_reuses_the_branch_and_the_open_pr(self) -> None:
        gh = FakeGh(self.root, mergeable=["CONFLICTING"])
        self.assertIsInstance(self.shipper(gh).ship(RUN, 1), NotShipped)
        self.assertEqual(GitWorkspace(self.repo, now=time.time).recover(RUN, 1), Clean())
        gh.update(mergeable=["MERGEABLE"])
        self.assertEqual(self.shipper(gh).ship(RUN, 1), Shipped(pr=7))
        self.assertEqual(gh.commands().count("pr create"), 1)
        self.assertEqual(len(gh.state()["prs"]), 1)

    def test_s6_an_open_pr_found_for_the_branch_is_reused_not_recreated(self) -> None:
        url = "https://github.com/acme/widgets/pull/31"
        body = f"earlier\n\n{MARKER}\n## Independent review\n"
        gh = FakeGh(self.root, prs=[{"number": 31, "url": url, "head": BRANCH, "body": body, "open": True}])
        self.assertEqual(self.shipper(gh).ship(RUN, 1), Shipped(pr=31))
        self.assertNotIn("pr create", gh.commands())

    def test_s7_no_url_from_pr_create_is_not_shipped(self) -> None:
        gh = FakeGh(self.root, create_stdout="Creating pull request for cycle/... into main")
        outcome = self.shipper(gh).ship(RUN, 1)
        self.assertIsInstance(outcome, NotShipped)
        self.assertIn("no PR URL", outcome.reason)
        self.assertIn(BRANCH, self.origin_branches(), "the work is safe on the pushed branch")

    def test_s7_a_body_read_back_without_the_marker_is_not_shipped(self) -> None:
        gh = FakeGh(self.root, body_override="someone replaced the body")
        outcome = self.shipper(gh).ship(RUN, 1)
        self.assertEqual(outcome, NotShipped("PR #7 opened but its body does not show the review record"))
        self.assertNotIn("mergeable", " ".join(" ".join(c["argv"]) for c in gh.state()["calls"]))

    def test_a_failing_gh_is_not_shipped_and_only_its_last_line_is_quoted(self) -> None:
        gh = FakeGh(self.root, fail={"pr create": [1, "GraphQL: Resource not accessible"]})
        outcome = self.shipper(gh).ship(RUN, 1)
        self.assertIsInstance(outcome, NotShipped)
        self.assertIn("GraphQL: Resource not accessible", outcome.reason)
        self.assertNotIn("some detail", outcome.reason)

    def test_a_gh_that_hangs_times_out_into_not_shipped(self) -> None:
        gh = FakeGh(self.root, sleep={"pr view": 10})
        outcome = self.shipper(gh, timeout_s=2).ship(RUN, 1)
        self.assertIsInstance(outcome, NotShipped)
        self.assertIn("timed out", outcome.reason)

    def test_s8_a_missing_gh_raises_oserror(self) -> None:
        with self.assertRaises(OSError):
            self.shipper(str(self.root / "no-such-gh")).ship(RUN, 1)


class ConstructionTest(ShipperCase):
    def test_rejects_a_path_that_is_not_a_checkout_top_level(self) -> None:
        with self.assertRaises(ValueError):
            GhShipper(Path("relative"), VERDICT, sleep=time.sleep)
        (self.repo / "sub").mkdir()
        with self.assertRaises(ValueError):
            GhShipper(self.repo / "sub", VERDICT, sleep=time.sleep)
        with self.assertRaises(TypeError):
            GhShipper(self.repo, "logs/last-review.md", sleep=time.sleep)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            GhShipper(self.repo, VERDICT, sleep=time.sleep, git=str(self.root / "no-such-git"))
