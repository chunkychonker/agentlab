"""Milestone 7b, edge: ``claim_step`` with ``GitBacklogPublisher`` on a temp repo with a local bare ``origin``.

Real git throughout (``GitClaimSource``, ``BacklogFile``,
``GitBacklogPublisher``); nothing talks to GitHub. Numbers refer to
research/2026-11-20-nightloop-claim-wiring.md: W1-W6 here, the pure half in
``test_claims_step.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from nightloop.claims import (
    BacklogFile,
    ClaimPorts,
    GitBacklogPublisher,
    GitClaimSource,
    Nothing,
    Published,
    PublishFailed,
    Skipped,
    StrandedStatus,
    claim_step,
)
from nightloop.cycle import GitWorkspace, RunId, Snapshotted

from .git_support import GitRepoCase, git
from .ship_support import GitRecorder

KEY_A = "Claim wiring: publish the backlog on a clean main"
KEY_B = "Another item `with code` and [brackets]"
BACKLOG = f"# BACKLOG\n\n## Work\n- [ ] {KEY_A}\n- [ ] {KEY_B}\n- [ ] an unclaimed item\n"
BRANCH_1 = "cycle/2026-11-20-unshipped-010203-1"
BRANCH_2 = "cycle/2026-11-20-unshipped-020304-2"
MISSING_GIT = "/nonexistent/nightloop-test/git"


class PublisherCase(GitRepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.repo.parent
        (self.repo / "BACKLOG.md").write_text(BACKLOG, encoding="utf-8")
        git(self.repo, "add", "BACKLOG.md")
        git(self.repo, "commit", "-q", "-m", "backlog")
        git(self.repo, "push", "-q", "origin", "main")
        # GitRepoCase clones an empty origin, which leaves no origin/HEAD; a clone
        # of any real repo has one. Set it so a fetch has nothing of its own to add.
        git(self.repo, "remote", "set-head", "origin", "main")
        self.backlog = BacklogFile(self.repo)

    def ports(self, *, git_program: str = "git") -> ClaimPorts:
        source = GitClaimSource(self.backlog, gh=MISSING_GIT)  # gh is never asked in a stranded step
        return ClaimPorts(source, self.backlog, GitBacklogPublisher(self.backlog, git=git_program))

    def strand(self, branch: str, key: str, *, push: bool = True) -> None:
        """A recovery branch whose cycle claimed ``key`` and did some work."""
        git(self.repo, "switch", "-q", "-c", branch, "main")
        text = (self.repo / "BACKLOG.md").read_text(encoding="utf-8")
        (self.repo / "BACKLOG.md").write_text(text.replace(f"- [ ] {key}", f"- [building] {key}"), encoding="utf-8")
        (self.repo / "work.txt").write_text(f"work on {branch}\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"wip on {branch}")
        if push:
            git(self.repo, "push", "-q", "origin", branch)
        git(self.repo, "switch", "-q", "main")

    # --- observations -------------------------------------------------------

    def refs(self) -> tuple[str, str]:
        return git(self.repo, "for-each-ref"), git(self.origin, "for-each-ref")

    def backlog_state(self) -> tuple[bytes, int]:
        path = self.repo / "BACKLOG.md"
        return path.read_bytes(), path.stat().st_mtime_ns

    def origin_main(self) -> str:
        return git(self.origin, "rev-parse", "main").strip()

    def commits_on_origin_since(self, oid: str) -> list[str]:
        return git(self.origin, "rev-list", f"{oid}..main").split()

    def files_in(self, oid: str) -> list[str]:
        return git(self.origin, "diff-tree", "--no-commit-id", "--name-only", "-r", oid).split()

    def push_from_elsewhere(self) -> None:
        """Another clone moves origin/main on, so this checkout's main is behind after a fetch."""
        other = self.root / "other"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], check=True, capture_output=True)
        (other / "README.md").write_text("# moved on elsewhere\n", encoding="utf-8")
        git(other, "commit", "-q", "-am", "elsewhere")
        git(other, "push", "-q", "origin", "main")


class FixtureTest(PublisherCase):
    def test_the_checkout_has_origin_head_like_a_real_clone(self) -> None:
        # Without it, git >= 2.48's fetch creates origin/HEAD on the first step
        # (remote.<name>.followRemoteHEAD), and W1 sees a ref nightloop never made.
        self.assertEqual(git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD").strip(), "refs/remotes/origin/main")


class NothingToDoTest(PublisherCase):
    def test_w1_no_recovery_branches_changes_no_ref_tree_or_byte(self) -> None:
        refs, state, porcelain = self.refs(), self.backlog_state(), self.porcelain()
        result, outcomes = claim_step(self.ports())
        self.assertEqual((result, outcomes), (Nothing(), ()))
        self.assertEqual(self.refs(), refs)
        self.assertEqual(self.backlog_state(), state)
        self.assertEqual(self.porcelain(), porcelain)


class PublishTest(PublisherCase):
    def test_w2_one_commit_on_origin_main_touching_only_the_backlog(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        self.strand(BRANCH_2, KEY_B, push=False)
        before = self.origin_main()
        result, outcomes = claim_step(self.ports())
        self.assertEqual(result, Published(2))
        self.assertEqual(
            [(str(o.ref), o.status) for o in outcomes],
            [(BRANCH_1, StrandedStatus.MARKED), (BRANCH_2, StrandedStatus.MARKED),
             (f"origin/{BRANCH_1}", StrandedStatus.ALREADY_HANDLED)],
        )
        new = self.commits_on_origin_since(before)
        self.assertEqual(len(new), 1)
        self.assertEqual(self.files_in(new[0]), ["BACKLOG.md"])
        self.assertEqual(git(self.origin, "log", "-1", "--format=%s", "main").strip(),
                         "chore(backlog): reconcile 2 claim(s) stranded on unshipped branches")
        published = git(self.origin, "show", "main:BACKLOG.md")
        self.assertIn(f"- [stranded {BRANCH_1}] {KEY_A}\n", published)
        self.assertIn(f"- [stranded {BRANCH_2}] {KEY_B}\n", published)
        self.assert_clean_main_equal_to_origin()
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), git(self.repo, "rev-parse", "main"),
                         "the push updated the tracking ref, so recovery does not see main as ahead")

    def test_w3_unrelated_dirt_stays_out_of_the_commit_and_byte_identical(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        (self.repo / "scratch.txt").write_bytes(b"untracked \x00 bytes\n")
        (self.repo / "README.md").write_text("# edited, not committed\n", encoding="utf-8")
        git(self.repo, "add", "README.md")  # staged too: still not part of the backlog commit
        (self.repo / "README.md").write_text("# edited again, unstaged\n", encoding="utf-8")
        before = self.origin_main()
        result, _ = claim_step(self.ports())
        self.assertEqual(result, Published(1))
        new = self.commits_on_origin_since(before)
        self.assertEqual([self.files_in(c) for c in new], [["BACKLOG.md"]])
        self.assertEqual((self.repo / "scratch.txt").read_bytes(), b"untracked \x00 bytes\n")
        self.assertEqual((self.repo / "README.md").read_text(encoding="utf-8"), "# edited again, unstaged\n")
        self.assertEqual(git(self.repo, "show", ":README.md"), "# edited, not committed\n", "the index entry is kept")
        self.assertEqual(git(self.origin, "show", "main:README.md"), "# fixture\n")
        self.assertEqual(sorted(self.porcelain().splitlines()), ["?? scratch.txt", "MM README.md"])

    def test_w6_a_second_step_after_a_publish_is_nothing(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        self.assertEqual(claim_step(self.ports())[0], Published(1))
        refs, state = self.refs(), self.backlog_state()
        self.assertEqual(claim_step(self.ports())[0], Nothing())
        self.assertEqual(self.refs(), refs)
        self.assertEqual(self.backlog_state(), state)

    def test_w5_git_is_asked_for_exactly_main_to_main_and_never_a_force(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        recorder = GitRecorder(self.root)
        self.assertEqual(claim_step(self.ports(git_program=recorder.program))[0], Published(1))
        calls = recorder.calls()
        pushes = [c for c in calls if "push" in c]
        self.assertEqual(pushes, [["push", "--quiet", "origin", "refs/heads/main:refs/heads/main"]])
        flat = [arg for call in calls for arg in call]
        for forbidden in ("--force", "-f", "--force-with-lease", "reset", "-A", "--all", "--prune"):
            self.assertNotIn(forbidden, flat)
        self.assertIn(["--literal-pathspecs", "add", "--", "BACKLOG.md"], calls)
        self.assertIn("fetch", [c[0] for c in calls], "ready() fetches before comparing with origin/main")


class NotReadyTest(PublisherCase):
    """W4: every way of not being ready is Skipped, and writes nothing."""

    def assert_skipped_writing_nothing(self, expected_reason: str, *, ports: ClaimPorts | None = None) -> None:
        refs_local = git(self.repo, "for-each-ref", "refs/heads")
        state, origin = self.backlog_state(), git(self.origin, "for-each-ref")
        result, outcomes = claim_step(ports or self.ports())
        self.assertIsInstance(result, Skipped)
        self.assertIn(expected_reason, result.reason)
        self.assertEqual(outcomes, ())
        self.assertEqual(self.backlog_state(), state)
        self.assertEqual(git(self.repo, "for-each-ref", "refs/heads"), refs_local)
        self.assertEqual(git(self.origin, "for-each-ref"), origin)

    def test_on_another_branch(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        git(self.repo, "switch", "-q", "-c", "dev")
        self.assert_skipped_writing_nothing("the checkout is on dev, not main")

    def test_detached_head(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        git(self.repo, "switch", "-q", "--detach", "main")
        self.assert_skipped_writing_nothing("detached")

    def test_behind_origin_after_the_fetch(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        self.push_from_elsewhere()
        # Without the fetch the tracking ref still says "not behind": this catches a skipped fetch.
        self.assertEqual(git(self.repo, "rev-list", "--count", "main..origin/main").strip(), "0")
        self.assert_skipped_writing_nothing("1 commit(s) behind origin/main")

    def test_ahead_of_origin(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        (self.repo / "notes.txt").write_text("a local commit\n", encoding="utf-8")
        git(self.repo, "add", "notes.txt")
        git(self.repo, "commit", "-q", "-m", "local, unpushed")
        self.assert_skipped_writing_nothing("1 commit(s) ahead of origin/main")

    def test_fetch_fails(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        git(self.repo, "remote", "set-url", "origin", str(self.root / "no-such-remote.git"))
        self.assert_skipped_writing_nothing("git fetch failed")

    def test_backlog_has_uncommitted_changes(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        with (self.repo / "BACKLOG.md").open("a", encoding="utf-8") as handle:
            handle.write("- [ ] a human's edit, not yet committed\n")
        self.assert_skipped_writing_nothing("BACKLOG.md has uncommitted changes")

    def test_untracked_backlog(self) -> None:
        git(self.repo, "rm", "-q", "--cached", "BACKLOG.md")
        git(self.repo, "commit", "-q", "-m", "untrack the backlog")
        git(self.repo, "push", "-q", "origin", "main")
        self.assert_skipped_writing_nothing("BACKLOG.md has uncommitted changes")

    def test_w12_no_backlog_file_is_skipped_before_any_git_runs(self) -> None:
        absent = BacklogFile(self.repo, "docs/TODO.md")
        # A git that cannot start would raise OSError if ready() ran it.
        ports = ClaimPorts(GitClaimSource(absent, git=MISSING_GIT, gh=MISSING_GIT), absent,
                           GitBacklogPublisher(absent, git=MISSING_GIT))
        self.assert_skipped_writing_nothing("no backlog file docs/TODO.md", ports=ports)

    def test_git_that_cannot_start_raises(self) -> None:
        with self.assertRaises(OSError):
            claim_step(self.ports(git_program=MISSING_GIT))


class PushFailsTest(PublisherCase):
    def reject_every_push(self) -> Path:
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'origin says no' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        return hook

    def test_w5_a_rejected_push_keeps_the_local_commit_for_recovery(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        hook = self.reject_every_push()
        origin_refs = git(self.origin, "for-each-ref")
        result, _ = claim_step(self.ports())
        self.assertIsInstance(result, PublishFailed)
        self.assertIn("git push failed", result.reason)
        self.assertIn("stays on local main", result.reason)
        self.assertEqual(git(self.origin, "for-each-ref"), origin_refs, "origin is unchanged")
        self.assertEqual(git(self.repo, "rev-list", "--count", "origin/main..main").strip(), "1",
                         "the commit is kept on local main, not reset")
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s", "main").strip(),
                         "chore(backlog): reconcile 1 claim(s) stranded on unshipped branches")
        self.assertEqual(self.porcelain(), "")

        # The m5 path: the next recovery snapshots main's extra commit to a branch and pushes it.
        hook.unlink()
        recovery = GitWorkspace(self.repo, now=lambda: 1780000000).recover(RunId("2026-11-20_020001"), 1)
        self.assertIsInstance(recovery, Snapshotted)
        self.assertTrue(recovery.pushed)
        self.assert_clean_main_equal_to_origin()
        self.assertIn(f"- [stranded {BRANCH_1}] {KEY_A}\n", git(self.origin, "show", f"{recovery.branch}:BACKLOG.md"))

    def test_a_failed_commit_leaves_the_edit_uncommitted(self) -> None:
        self.strand(BRANCH_1, KEY_A)
        self.fail_every_commit()
        before = git(self.repo, "rev-parse", "main")
        result, _ = claim_step(self.ports())
        self.assertIsInstance(result, PublishFailed)
        self.assertIn("git commit failed", result.reason)
        self.assertEqual(git(self.repo, "rev-parse", "main"), before)
        self.assertEqual(self.porcelain(), "M  BACKLOG.md\n")
