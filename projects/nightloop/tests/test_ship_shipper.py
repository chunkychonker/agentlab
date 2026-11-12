"""Milestone 5, S1 and S3-S10: ``GitHubShipper`` on a real clone with a local bare ``origin`` and a fake ``gh``.

The git side is real (``GitBranchPublisher``, real hooks for failures); the
GitHub side is ``tests/fake_gh.py``, a stand-in executable run through the
real ``GhCli``. No test reaches GitHub. Numbers refer to
research/2026-11-12-nightloop-real-shipper.md.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from nightloop.cycle import NotShipped, RunId, Shipped
from nightloop.ship import REVIEW_MARKER, GhCli, GitBranchPublisher, GitHubShipper, verdict_reader

from .fake_gh import FakeGh
from .git_support import GitRepoCase, git

RUN = RunId("2026-11-12_020001")
BRANCH = "nightloop/2026-11-12_020001-1"
SHA = "3f2a9c41d0e8b7a6c5f4e3d2c1b0a9f8e7d6c5b4"
PASSING = f"""\
VERDICT: PASS
Ran:
- python3 -m unittest: 12 passed
Findings:
- nit: README typo at line 3 (introduced in {SHA})
"""
ANTHROPIC_KEY = "sk-ant-" + "Zq9Xw8Vu7Ts6Rq5Po4Nm"
GITHUB_TOKEN = "ghp_" + "Ab1" * 12


class ShipperTest(GitRepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.sleeps: list[float] = []
        self.verdict = self.repo / "logs" / "last-review.md"
        self.verdict.parent.mkdir()
        self.verdict.write_text(PASSING, encoding="utf-8")
        self.gh = FakeGh(self.repo.parent / "fake-gh")

    # --- helpers ---------------------------------------------------------------

    def build_output(self) -> None:
        """What a build phase leaves: uncommitted work on main."""
        (self.repo / "projects" / "demo").mkdir(parents=True, exist_ok=True)
        (self.repo / "projects" / "demo" / "app.py").write_text("print('hi')\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# fixture, edited\n", encoding="utf-8")

    def shipper(self, *, gh: Path | None = None, git_program: str = "git", attempts: int = 5) -> GitHubShipper:
        return GitHubShipper(
            read_verdict=verdict_reader(self.verdict),
            git=GitBranchPublisher(self.repo, git=git_program),
            gh=GhCli(self.repo, gh=str(gh or self.gh.program)),
            sleep=self.sleeps.append,
            mergeable_attempts=attempts,
        )

    def ship(self, cycle: int = 1, **kw) -> Shipped | NotShipped:
        return self.shipper(**kw).ship(RUN, cycle)

    def hook(self, name: str) -> None:
        path = self.repo / ".git" / "hooks" / name
        path.write_text("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n", encoding="utf-8")
        path.chmod(0o755)

    def assert_one_line(self, outcome: NotShipped) -> None:
        self.assertIsInstance(outcome, NotShipped)
        self.assertNotIn("\n", outcome.reason)

    def assert_work_uncommitted_and_nothing_pushed(self) -> None:
        self.assertEqual(self.current_branch(), "main")
        self.assertIn("projects/", self.porcelain())
        self.assertEqual(self.origin_branches(), ["main"])
        self.assertEqual(self.local_branches("nightloop/*"), [])
        self.assertEqual(git(self.repo, "rev-parse", "main"), git(self.origin, "rev-parse", "main"))

    # --- S1 --------------------------------------------------------------------

    def test_s1_happy_path_pushes_the_branch_and_opens_one_reviewed_pr(self) -> None:
        self.build_output()
        self.assertEqual(self.ship(), Shipped(pr=7))
        self.assertIn(BRANCH, self.origin_branches())
        self.assertEqual(git(self.origin, "show", f"{BRANCH}:projects/demo/app.py"), "print('hi')\n")
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s", BRANCH).strip(),
                         "nightloop: cycle 1 of run 2026-11-12_020001")
        self.assert_clean_main_equal_to_origin()
        self.assertFalse((self.repo / "projects" / "demo" / "app.py").exists())
        self.assertTrue(self.verdict.exists(), "the git-ignored verdict is left alone")

        self.assertEqual(self.gh.keys(), ["list", "create", "view_body", "view_mergeable"])
        listed, created, *_ = self.gh.calls()
        self.assertEqual(listed["argv"], ["pr", "list", "--head", BRANCH, "--state", "all", "--json", "number"])
        self.assertEqual(created["argv"], [
            "pr", "create", "--head", BRANCH, "--base", "main",
            "--title", "nightloop: cycle 1 of run 2026-11-12_020001", "--body-file", "-",
        ])
        self.assertIn(REVIEW_MARKER, created["stdin"])
        self.assertIn(f"- nit: README typo at line 3 (introduced in {SHA})", created["stdin"])
        self.assertIn("**Verdict:** PASS", created["stdin"])
        self.assertTrue(all(Path(c["cwd"]).resolve() == self.repo for c in self.gh.calls()))
        self.assertEqual(self.sleeps, [])

    # --- S3 / S4: the record is checked before anything is pushed --------------

    def test_s3_a_verdict_without_findings_ships_nothing_and_calls_no_gh(self) -> None:
        self.build_output()
        self.verdict.write_text("VERDICT: PASS\nRan: tests\nLooks good.\n", encoding="utf-8")
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertIn("Findings", outcome.reason)
        self.assert_work_uncommitted_and_nothing_pushed()
        self.assertEqual(self.gh.calls(), [])

    def test_s4_a_secret_in_the_record_ships_nothing_and_is_never_echoed(self) -> None:
        for secret in (ANTHROPIC_KEY, GITHUB_TOKEN):
            with self.subTest(secret=secret[:6]):
                self.build_output()
                self.verdict.write_text(PASSING + f"- the log printed {secret}\n", encoding="utf-8")
                outcome = self.ship()
                self.assert_one_line(outcome)
                self.assertIn("credential", outcome.reason)
                self.assertNotIn(secret, outcome.reason)
                self.assert_work_uncommitted_and_nothing_pushed()
                self.assertEqual(self.gh.calls(), [])

    # --- S5: push failures --------------------------------------------------------

    def test_s5_a_rejected_push_keeps_the_commit_off_main_and_opens_no_pr(self) -> None:
        self.build_output()
        self.hook("pre-push")
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertTrue(outcome.reason.startswith(f"push failed for {BRANCH}"), outcome.reason)
        self.assertEqual(self.local_branches("nightloop/*"), [BRANCH])
        self.assertEqual(self.file_on(BRANCH, "projects/demo/app.py"), "print('hi')\n")
        self.assertNotIn(BRANCH, self.origin_branches())
        self.assert_clean_main_equal_to_origin()
        self.assertNotIn("create", self.gh.keys())

    def test_a_rejected_commit_leaves_the_work_uncommitted_on_main(self) -> None:
        self.build_output()
        self.hook("pre-commit")
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertIn("hook says no", outcome.reason)
        self.assert_work_uncommitted_and_nothing_pushed()
        self.assertNotIn("create", self.gh.keys())

    def test_nothing_to_commit_is_not_shipped_and_leaves_no_branch(self) -> None:
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertIn("commit", outcome.reason)
        self.assertEqual(self.local_branches("nightloop/*"), [])
        self.assert_clean_main_equal_to_origin()

    def test_an_existing_local_branch_is_never_touched(self) -> None:
        git(self.repo, "branch", BRANCH)
        before = git(self.repo, "rev-parse", BRANCH)
        self.build_output()
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertEqual(git(self.repo, "rev-parse", BRANCH), before)
        self.assertEqual(self.current_branch(), "main")
        self.assertIn("projects/", self.porcelain())

    # --- S6: idempotence ----------------------------------------------------------

    def test_s6_an_existing_pr_for_the_branch_is_not_duplicated_or_pushed_to(self) -> None:
        self.gh = FakeGh(self.repo.parent / "gh-existing", list=[{"stdout": '[{"number": 5}]\n'}])
        self.build_output()
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertIn("#5", outcome.reason)
        self.assertEqual(self.gh.keys(), ["list"])
        self.assert_work_uncommitted_and_nothing_pushed()

    def test_a_failed_or_unreadable_pr_list_ships_nothing(self) -> None:
        replies = {
            "exit 1": [{"rc": 1, "stderr": "To get started with GitHub CLI, please run:  gh auth login\n"}],
            "not JSON": [{"stdout": "number\n5\n"}],
            "row without a number": [{"stdout": '[{"title": "x"}]\n'}],
        }
        for index, (label, reply) in enumerate(replies.items()):
            with self.subTest(label):
                self.gh = FakeGh(self.repo.parent / f"gh-list-{index}", list=reply)
                self.build_output()
                outcome = self.ship()
                self.assert_one_line(outcome)
                self.assertIn("could not list PRs", outcome.reason)
                self.assert_work_uncommitted_and_nothing_pushed()

    # --- S7 / S8: creation and the read-back ------------------------------------

    def test_s7_an_unreadable_or_failed_create_is_not_shipped(self) -> None:
        replies = {
            "garbage stdout": [{"stdout": "Creating pull request...\nall done!\n"}],
            "URL with no number": [{"stdout": "https://github.com/acme/widgets/pull/\n"}],
            "exit 1 even with a URL": [{"rc": 1, "stdout": "https://github.com/acme/widgets/pull/7\n",
                                        "stderr": "GraphQL: something went wrong\n"}],
        }
        for cycle, (label, reply) in enumerate(replies.items(), start=1):
            with self.subTest(label):
                self.gh = FakeGh(self.repo.parent / f"gh-create-{cycle}", create=reply)
                self.build_output()
                outcome = self.ship(cycle)
                self.assert_one_line(outcome)
                self.assertIn("check", outcome.reason)
                self.assertEqual(self.gh.keys(), ["list", "create"])
                self.assertIn(f"nightloop/2026-11-12_020001-{cycle}", self.origin_branches())
                self.assert_clean_main_equal_to_origin()

    def test_s8_a_body_without_the_marker_is_not_shipped(self) -> None:
        self.gh = FakeGh(self.repo.parent / "gh-body", view_body=[{"stdout": "someone replaced the body\n"}])
        self.build_output()
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertIn("PR #7", outcome.reason)
        self.assertNotIn("view_mergeable", self.gh.keys())

    def test_a_body_that_cannot_be_read_back_is_not_shipped(self) -> None:
        self.gh = FakeGh(self.repo.parent / "gh-body-fail", view_body=[{"rc": 1, "stderr": "HTTP 502\n"}])
        self.build_output()
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertIn("PR #7", outcome.reason)
        self.assertIn("HTTP 502", outcome.reason)

    # --- S9: the mergeable check --------------------------------------------------

    def test_s9_unknown_is_waited_out_then_mergeable_ships(self) -> None:
        self.gh = FakeGh(self.repo.parent / "gh-wait", view_mergeable=[
            {"stdout": "UNKNOWN\n"}, {"stdout": "UNKNOWN\n"}, {"stdout": "MERGEABLE\n"}])
        self.build_output()
        self.assertEqual(self.ship(), Shipped(pr=7))
        self.assertEqual(self.sleeps, [2.0, 2.0])
        self.assertEqual(self.gh.keys().count("view_mergeable"), 3)

    def test_s9_unknown_every_time_gives_up_after_the_configured_reads(self) -> None:
        self.gh = FakeGh(self.repo.parent / "gh-unknown", view_mergeable=[{"stdout": "UNKNOWN\n"}])
        self.build_output()
        outcome = self.ship(attempts=4)
        self.assert_one_line(outcome)
        self.assertIn("PR #7 opened but mergeable=UNKNOWN", outcome.reason)
        self.assertEqual(self.gh.keys().count("view_mergeable"), 4)
        self.assertEqual(self.sleeps, [2.0] * 3, "no sleep after the last read")

    def test_s9_conflicting_is_not_shipped_on_the_first_read(self) -> None:
        self.gh = FakeGh(self.repo.parent / "gh-conflict", view_mergeable=[{"stdout": "CONFLICTING\n"}])
        self.build_output()
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertIn("mergeable=CONFLICTING", outcome.reason)
        self.assertEqual(self.gh.keys().count("view_mergeable"), 1)
        self.assertEqual(self.sleeps, [])
        self.assert_clean_main_equal_to_origin()

    def test_a_multiline_mergeable_value_still_gives_a_one_line_reason(self) -> None:
        self.gh = FakeGh(self.repo.parent / "gh-odd", view_mergeable=[{"stdout": "WHAT\nIS\nTHIS\n"}])
        self.build_output()
        self.assert_one_line(self.ship())

    def test_a_failed_mergeable_read_is_not_shipped(self) -> None:
        self.gh = FakeGh(self.repo.parent / "gh-m-fail", view_mergeable=[{"rc": 1, "stderr": "rate limited\n"}])
        self.build_output()
        outcome = self.ship()
        self.assert_one_line(outcome)
        self.assertIn("rate limited", outcome.reason)

    # --- S10: environment errors raise ------------------------------------------

    def test_s10_a_missing_gh_raises_oserror(self) -> None:
        self.build_output()
        with self.assertRaises(OSError):
            self.ship(gh=self.repo.parent / "no-such-dir" / "gh")
        self.assert_work_uncommitted_and_nothing_pushed()

    def test_s10_a_missing_git_raises_oserror(self) -> None:
        self.build_output()
        with self.assertRaises(OSError):
            self.ship(git_program=str(self.repo.parent / "no-such-dir" / "git"))

    def test_a_missing_verdict_file_raises_oserror(self) -> None:
        self.verdict.unlink()
        with self.assertRaises(OSError):
            self.ship()
        self.assertEqual(self.gh.calls(), [])

    # --- construction ---------------------------------------------------------------

    def test_bad_settings_fail_at_construction(self) -> None:
        cases = {"zero attempts": dict(mergeable_attempts=0), "bool attempts": dict(mergeable_attempts=True),
                 "negative wait": dict(mergeable_wait_s=-1.0), "nan wait": dict(mergeable_wait_s=float("nan"))}
        for label, override in cases.items():
            with self.subTest(label), self.assertRaises((TypeError, ValueError)):
                GitHubShipper(read_verdict=lambda: PASSING, git=GitBranchPublisher(self.repo),
                              gh=GhCli(self.repo), sleep=self.sleeps.append, **override)

    def test_the_publisher_refuses_to_publish_main(self) -> None:
        with self.assertRaises(ValueError):
            GitBranchPublisher(self.repo).publish("main", "would push main")


if __name__ == "__main__":
    unittest.main()
