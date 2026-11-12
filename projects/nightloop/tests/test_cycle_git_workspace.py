"""Milestone 4: ``GitWorkspace`` against a real temp git repo with a local bare ``origin``.

No network and no ``gh``. Failures are produced with real git hooks
(``pre-commit``, ``pre-push``) and real name collisions, not mocks.
"""

from __future__ import annotations

import subprocess
import time
import unittest
from pathlib import Path

from nightloop.cycle import Clean, GitWorkspace, RecoveryError, RunId, Snapshotted

from .git_support import GitRepoCase, git

RUN = RunId("2026-11-11_020001")
STARTED = 1780000000
HHMMSS = time.strftime("%H%M%S", time.localtime(STARTED))


class GitWorkspaceTest(GitRepoCase):
    def workspace(self) -> GitWorkspace:
        return GitWorkspace(self.repo, now=lambda: STARTED)

    def make_dirty(self) -> None:
        (self.repo / "README.md").write_text("# fixture, edited\n", encoding="utf-8")
        (self.repo / "projects" / "demo").mkdir(parents=True)
        (self.repo / "projects" / "demo" / "app.py").write_text("print('hi')\n", encoding="utf-8")
        (self.repo / "build").mkdir()
        (self.repo / "build" / "out.bin").write_text("ignored\n", encoding="utf-8")

    # --- construction --------------------------------------------------------

    def test_rejects_a_path_that_is_not_a_checkout_top_level(self) -> None:
        with self.assertRaises(ValueError):
            GitWorkspace(Path("relative"), now=time.time)
        (self.repo / "sub").mkdir()
        with self.assertRaises(ValueError):
            GitWorkspace(self.repo / "sub", now=time.time)
        with self.assertRaises(ValueError):
            GitWorkspace(self.origin.parent, now=time.time)

    # --- clear_handoff -------------------------------------------------------

    def test_clear_handoff_removes_the_verdict_and_tolerates_its_absence(self) -> None:
        verdict = self.repo / "logs" / "last-review.md"
        verdict.parent.mkdir()
        verdict.write_text("VERDICT: PASS\n", encoding="utf-8")
        ws = self.workspace()
        ws.clear_handoff(Path("logs/last-review.md"))
        self.assertFalse(verdict.exists())
        ws.clear_handoff(Path("logs/last-review.md"))  # absent: no error

    def test_clear_handoff_raises_when_the_verdict_cannot_be_removed(self) -> None:
        (self.repo / "logs" / "last-review.md").mkdir(parents=True)
        with self.assertRaises(OSError):
            self.workspace().clear_handoff(Path("logs/last-review.md"))

    # --- recover: clean ------------------------------------------------------

    def test_clean_tree_makes_no_branch(self) -> None:  # B6 at the edge
        self.assertEqual(self.workspace().recover(RUN, 1), Clean())
        self.assertEqual(self.local_branches("cycle/*"), [])
        self.assert_clean_main_equal_to_origin()

    def test_recover_fast_forwards_main_from_origin(self) -> None:
        other = self.repo.parent / "other"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], check=True, capture_output=True)
        (other / "NEW.md").write_text("from elsewhere\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "upstream change")
        git(other, "push", "-q", "origin", "main")
        self.workspace().recover(RUN, 1)
        self.assertTrue((self.repo / "NEW.md").exists())
        self.assert_clean_main_equal_to_origin()

    def test_recover_returns_to_main_if_a_phase_left_head_elsewhere(self) -> None:
        git(self.repo, "switch", "-q", "-c", "scratch")
        self.assertEqual(self.workspace().recover(RUN, 1), Clean())
        self.assertEqual(self.current_branch(), "main")

    # --- recover: dirty ------------------------------------------------------

    def test_dirty_tree_goes_to_one_pushed_branch_and_main_is_clean(self) -> None:  # B5 at the edge
        self.make_dirty()
        recovery = self.workspace().recover(RUN, 2)
        branch = f"cycle/2026-11-11-unshipped-{HHMMSS}-2"
        self.assertEqual(recovery, Snapshotted(branch=branch, pushed=True))
        self.assertEqual(self.local_branches("cycle/*"), [branch])
        self.assertIn(branch, self.origin_branches())
        self.assertEqual(self.file_on(branch, "projects/demo/app.py"), "print('hi')\n")
        self.assertEqual(self.file_on(branch, "README.md"), "# fixture, edited\n")
        self.assert_clean_main_equal_to_origin()
        self.assertEqual(self.file_on("main", "README.md"), "# fixture\n")

    def test_ignored_files_are_not_committed_and_are_not_deleted(self) -> None:
        self.make_dirty()
        recovery = self.workspace().recover(RUN, 1)
        listed = git(self.repo, "ls-tree", "-r", "--name-only", recovery.branch).split()
        self.assertNotIn("build/out.bin", listed)
        self.assertTrue((self.repo / "build" / "out.bin").exists())

    def test_a_failed_push_is_recorded_not_raised(self) -> None:
        self.make_dirty()
        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        recovery = self.workspace().recover(RUN, 1)
        self.assertIsInstance(recovery, Snapshotted)
        self.assertFalse(recovery.pushed)
        self.assertEqual(self.local_branches("cycle/*"), [recovery.branch])
        self.assertNotIn(recovery.branch, self.origin_branches())
        self.assert_clean_main_equal_to_origin()

    # --- recover: failures ---------------------------------------------------

    def test_a_failed_commit_raises_and_leaves_main_dirty_with_nothing_lost(self) -> None:
        self.make_dirty()
        self.fail_every_commit()
        with self.assertRaises(RecoveryError) as caught:
            self.workspace().recover(RUN, 1)
        self.assertIn("refusing for the test", str(caught.exception))
        self.assertEqual(self.current_branch(), "main")
        self.assertNotEqual(self.porcelain(), "")
        self.assertTrue((self.repo / "projects" / "demo" / "app.py").exists())
        self.assertEqual((self.repo / "README.md").read_text(encoding="utf-8"), "# fixture, edited\n")
        # The empty branch this call made is gone.
        self.assertEqual(self.local_branches("cycle/*"), [])

    def test_a_name_collision_raises_and_never_touches_the_other_branch(self) -> None:
        branch = f"cycle/2026-11-11-unshipped-{HHMMSS}-1"
        git(self.repo, "branch", branch)
        before = git(self.repo, "rev-parse", branch)
        self.make_dirty()
        with self.assertRaises(RecoveryError):
            self.workspace().recover(RUN, 1)
        self.assertEqual(git(self.repo, "rev-parse", branch), before)
        self.assertEqual(self.current_branch(), "main")
        self.assertTrue((self.repo / "projects" / "demo" / "app.py").exists())

    def test_an_unreachable_origin_on_a_clean_tree_raises(self) -> None:
        git(self.repo, "remote", "set-url", "origin", str(self.repo.parent / "gone.git"))
        with self.assertRaises(RecoveryError):
            self.workspace().recover(RUN, 1)

    # --- recover: commits on main ahead of origin (milestone 5, R1-R3) --------

    def commit_on_main(self, name: str = "LEAK.md") -> str:
        """Commit a file on local main without pushing; returns the commit's sha."""
        (self.repo / name).write_text("left on main by a failed shipper\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "leaked commit")
        return git(self.repo, "rev-parse", "HEAD").strip()

    def reachable_from_main(self, sha: str) -> bool:
        done = subprocess.run(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", sha, "main"])
        return done.returncode == 0

    def test_r1_commits_ahead_of_origin_move_to_a_branch_and_main_is_reset(self) -> None:
        leaked = self.commit_on_main()
        recovery = self.workspace().recover(RUN, 3)
        branch = f"cycle/2026-11-11-unshipped-{HHMMSS}-3"
        self.assertEqual(recovery, Snapshotted(branch=branch, pushed=True))
        self.assertEqual(git(self.repo, "rev-parse", branch).strip(), leaked)
        self.assertIn(branch, self.origin_branches())
        self.assert_clean_main_equal_to_origin()
        self.assertFalse(self.reachable_from_main(leaked))
        self.assertFalse((self.repo / "LEAK.md").exists())

    def test_r1_ahead_commits_are_caught_when_head_was_left_elsewhere(self) -> None:
        leaked = self.commit_on_main()
        git(self.repo, "switch", "-q", "-c", "scratch")
        recovery = self.workspace().recover(RUN, 1)
        self.assertIsInstance(recovery, Snapshotted)
        self.assertEqual(git(self.repo, "rev-parse", recovery.branch).strip(), leaked)
        self.assert_clean_main_equal_to_origin()

    def test_r2_ahead_commits_and_a_dirty_tree_go_to_one_branch(self) -> None:
        leaked = self.commit_on_main()
        self.make_dirty()
        recovery = self.workspace().recover(RUN, 1)
        self.assertIsInstance(recovery, Snapshotted)
        self.assertEqual(self.local_branches("cycle/*"), [recovery.branch])
        self.assertEqual(git(self.repo, "rev-parse", f"{recovery.branch}~1").strip(), leaked)
        self.assertEqual(self.file_on(recovery.branch, "LEAK.md"), "left on main by a failed shipper\n")
        self.assertEqual(self.file_on(recovery.branch, "projects/demo/app.py"), "print('hi')\n")
        self.assert_clean_main_equal_to_origin()

    def test_r2_ahead_commits_with_a_failed_push_are_kept_locally(self) -> None:
        leaked = self.commit_on_main()
        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        recovery = self.workspace().recover(RUN, 1)
        self.assertEqual(recovery, Snapshotted(branch=recovery.branch, pushed=False))
        self.assertEqual(git(self.repo, "rev-parse", recovery.branch).strip(), leaked)
        self.assert_clean_main_equal_to_origin()

    def test_r3_a_failed_fetch_raises_and_main_is_not_reset(self) -> None:
        leaked = self.commit_on_main()
        git(self.repo, "remote", "set-url", "origin", str(self.repo.parent / "gone.git"))
        with self.assertRaises(RecoveryError):
            self.workspace().recover(RUN, 1)
        self.assertEqual(git(self.repo, "rev-parse", "main").strip(), leaked)
        self.assertEqual(self.local_branches("cycle/*"), [])

    def test_r3_a_failed_fetch_with_a_dirty_tree_still_snapshots_first(self) -> None:
        self.make_dirty()
        git(self.repo, "remote", "set-url", "origin", str(self.repo.parent / "gone.git"))
        with self.assertRaises(RecoveryError):
            self.workspace().recover(RUN, 1)
        [branch] = self.local_branches("cycle/*")
        self.assertEqual(self.file_on(branch, "projects/demo/app.py"), "print('hi')\n")

    def test_ahead_commits_with_a_dirty_tree_on_another_branch_raise_without_reset(self) -> None:
        # The dirty work is on a branch that does not contain main's leaked
        # commit, so one recovery branch cannot hold both: refuse to reset.
        leaked = self.commit_on_main()
        git(self.repo, "switch", "-q", "-c", "scratch", "HEAD~1")
        self.make_dirty()
        with self.assertRaises(RecoveryError) as caught:
            self.workspace().recover(RUN, 1)
        self.assertIn("ahead", str(caught.exception))
        self.assertEqual(git(self.repo, "rev-parse", "main").strip(), leaked)
        [branch] = self.local_branches("cycle/*")
        self.assertEqual(self.file_on(branch, "projects/demo/app.py"), "print('hi')\n")

    def test_git_that_cannot_start_fails_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            GitWorkspace(self.repo, now=time.time, git="/nonexistent/git")


if __name__ == "__main__":
    unittest.main()
