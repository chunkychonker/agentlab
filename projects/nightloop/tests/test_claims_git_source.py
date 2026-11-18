"""Milestone 6, edge: ``GitClaimSource`` plus the reconcilers on a temp repo with a local bare ``origin``.

Real git throughout; ``gh`` is the scripted ``FakeGh`` from the shipper
tests, answering ``pr view --json mergeCommit`` with a merge commit that
exists in the temp repo. Nothing talks to GitHub.
"""

from __future__ import annotations

from pathlib import Path

from nightloop.claims import (
    BacklogFile,
    Claim,
    ClaimSourceError,
    GitClaimSource,
    NoClaim,
    PrNumber,
    ShippedOutcome,
    ShippedStatus,
    StrandedBranch,
    StrandedStatus,
    Unreadable,
    reconcile_shipped,
    reconcile_stranded,
)

from .git_support import GitRepoCase, git
from .ship_support import FakeGh

KEY = "Claim tracking (`backlog.sh` port): match `[building]` literally"
INVENTED = "An item the researcher wrote and claimed in one edit"
INVENTED_REST = "  so main never had it (`git log -S` finds nothing)."
BACKLOG = (
    "# BACKLOG\n"
    "\n"
    "## Work\n"
    f"- [ ] {KEY}\n"
    "  continuation of the claimed item\n"
    f"- [ ] {KEY} (a longer item sharing its prefix)\n"
    "- [ ] an unrelated unclaimed item\n"
)
BRANCH = "cycle/2026-11-18-unshipped-010203-1"


class ClaimSourceCase(GitRepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.repo.parent
        (self.repo / "BACKLOG.md").write_text(BACKLOG, encoding="utf-8")
        git(self.repo, "add", "BACKLOG.md")
        git(self.repo, "commit", "-q", "-m", "backlog")
        git(self.repo, "push", "-q", "origin", "main")
        self.backlog = BacklogFile(self.repo)
        self.gh = FakeGh(self.root)

    def source(self, **kwargs: object) -> GitClaimSource:
        return GitClaimSource(self.backlog, gh=self.gh.program, **kwargs)  # type: ignore[arg-type]

    def commit_on(self, branch: str, backlog: str | None, *, push: bool, start: str = "main") -> str:
        """A commit on ``branch`` (from ``start``) setting BACKLOG.md to ``backlog`` plus one work file."""
        git(self.repo, "switch", "-q", "-c", branch, start)
        if backlog is not None:
            (self.repo / "BACKLOG.md").write_text(backlog, encoding="utf-8")
        (self.repo / "work.txt").write_text(f"work on {branch}\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"work on {branch}")
        if push:
            git(self.repo, "push", "-q", "origin", branch)
        oid = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "switch", "-q", "main")
        return oid

    def claimed(self, marker: str = "building") -> str:
        return BACKLOG.replace(f"- [ ] {KEY}\n  continuation", f"- [{marker}] {KEY}\n  continuation")

    def main_text(self) -> str:
        return (self.repo / "BACKLOG.md").read_text(encoding="utf-8")


class StrandedOnGitTest(ClaimSourceCase):
    def test_a_pushed_branch_marks_main_once_and_a_second_run_changes_nothing(self) -> None:
        self.commit_on(BRANCH, self.claimed(), push=True)
        outcomes = reconcile_stranded(self.source(), self.backlog)
        self.assertEqual(
            [(str(o.ref), o.status) for o in outcomes],
            [(BRANCH, StrandedStatus.MARKED), (f"origin/{BRANCH}", StrandedStatus.ALREADY_HANDLED)],
        )
        self.assertEqual(self.main_text(), BACKLOG.replace(f"- [ ] {KEY}\n  cont", f"- [stranded {BRANCH}] {KEY}\n  cont"))
        self.assertEqual(self.porcelain(), " M BACKLOG.md\n", "only the backlog changed; no temp file")
        self.assertEqual(self.current_branch(), "main")
        self.assertEqual(self.local_branches("cycle/*"), [BRANCH], "no branch is made or deleted")

        stamp = (self.repo / "BACKLOG.md").stat().st_mtime_ns
        before = self.main_text()
        again = reconcile_stranded(self.source(), self.backlog)
        self.assertEqual({o.status for o in again}, {StrandedStatus.ALREADY_HANDLED})
        self.assertEqual(self.main_text(), before)
        self.assertEqual((self.repo / "BACKLOG.md").stat().st_mtime_ns, stamp, "a no-op does not write")

    def test_a_local_only_claim_never_on_main_is_filed_with_its_continuation(self) -> None:
        invented = BACKLOG + f"- [researching] {INVENTED}\n{INVENTED_REST}\n"
        self.commit_on(BRANCH, invented, push=False)
        outcomes = reconcile_stranded(self.source(), self.backlog)
        self.assertEqual([(str(o.ref), o.status) for o in outcomes], [(BRANCH, StrandedStatus.FILED)])
        self.assertTrue(self.main_text().startswith(BACKLOG))
        self.assertIn(f"- [stranded {BRANCH}] {INVENTED}\n{INVENTED_REST}\n", self.main_text())

    def test_main_moving_on_does_not_hide_the_claim(self) -> None:
        self.commit_on(BRANCH, self.claimed("researching"), push=True)
        (self.repo / "README.md").write_text("# moved on\n", encoding="utf-8")
        git(self.repo, "commit", "-q", "-am", "main moves on")
        source = self.source()
        claims = {str(b.ref): b.lookup for b in source.unshipped_claims()}
        # The continuation line is diff context (unchanged), so it is not part of the claim.
        self.assertEqual(claims[BRANCH], Claim(KEY, KEY))

    def test_no_claim_no_merge_base_and_other_branches(self) -> None:
        self.commit_on("cycle/2026-11-18-unshipped-020000-2", None, push=False)
        self.commit_on("cycle/2026-11-18-020003-1-ship", self.claimed(), push=False)  # not a recovery branch
        git(self.repo, "switch", "-q", "--orphan", "cycle/2026-11-18-unshipped-030000-3")
        # `switch --orphan` starts from an empty index and tree.
        (self.repo / "BACKLOG.md").write_text(f"- [building] {KEY}\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "unrelated history")
        git(self.repo, "switch", "-q", "main")

        branches = {str(b.ref): b.lookup for b in self.source().unshipped_claims()}
        self.assertEqual(set(branches), {"cycle/2026-11-18-unshipped-020000-2", "cycle/2026-11-18-unshipped-030000-3"})
        self.assertEqual(branches["cycle/2026-11-18-unshipped-020000-2"], NoClaim())
        orphan = branches["cycle/2026-11-18-unshipped-030000-3"]
        self.assertIsInstance(orphan, Unreadable)
        assert isinstance(orphan, Unreadable)
        self.assertIn("no merge base", orphan.reason)

        outcomes = reconcile_stranded(self.source(), self.backlog)
        self.assertEqual([o.status for o in outcomes], [StrandedStatus.NO_CLAIM, StrandedStatus.UNREADABLE])
        self.assertEqual(self.main_text(), BACKLOG)

    def test_no_recovery_branches_is_an_empty_list(self) -> None:
        self.assertEqual(reconcile_stranded(self.source(), self.backlog), [])
        self.assertEqual(self.porcelain(), "")

    def test_listing_failures_raise(self) -> None:
        not_a_repo = self.root / "plain"
        not_a_repo.mkdir()
        (not_a_repo / "BACKLOG.md").write_text(BACKLOG, encoding="utf-8")
        with self.assertRaises(ClaimSourceError):
            GitClaimSource(BacklogFile(not_a_repo)).unshipped_claims()
        with self.assertRaises(OSError):
            GitClaimSource(self.backlog, git=str(self.root / "no-such-git")).unshipped_claims()


class ShippedOnGitTest(ClaimSourceCase):
    PR = PrNumber(41)

    def merge_no_ff(self, backlog: str | None) -> str:
        self.commit_on("cycle/2026-11-18-010203-1-ship", backlog, push=False)
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge PR #41", "cycle/2026-11-18-010203-1-ship")
        git(self.repo, "push", "-q", "origin", "main")
        return git(self.repo, "rev-parse", "HEAD").strip()

    def use_merge_commit(self, oid: str) -> None:
        self.gh.update(prs=[{"number": 41, "url": "u", "head": "h", "open": False, "merge_commit": oid}])

    def test_a_merge_commit_marks_the_claim_done_from_its_first_parent(self) -> None:
        # The merge base of a merged PR's head and main is the head itself, so
        # a merge-base diff would be empty; only the first-parent diff sees it.
        self.use_merge_commit(self.merge_no_ff(self.claimed()))
        self.assertIn(f"- [building] {KEY}\n", self.main_text())
        outcome = reconcile_shipped(self.source(), self.backlog, self.PR)
        self.assertEqual(outcome, ShippedOutcome(self.PR, ShippedStatus.MARKED_DONE))
        self.assertEqual(self.main_text(), self.claimed().replace(f"- [building] {KEY}\n", f"- [done #41] {KEY}\n"))
        self.assertEqual(self.porcelain(), " M BACKLOG.md\n")
        again = reconcile_shipped(self.source(), self.backlog, self.PR)
        self.assertEqual(again.status, ShippedStatus.ALREADY_HANDLED)
        self.assertEqual(self.gh.commands(), ["pr view", "pr view"], "gh is only read")

    def test_a_squash_merge_works_too(self) -> None:
        (self.repo / "BACKLOG.md").write_text(self.claimed(), encoding="utf-8")
        git(self.repo, "commit", "-q", "-am", "squashed PR #41")
        self.use_merge_commit(git(self.repo, "rev-parse", "HEAD").strip())
        self.assertEqual(reconcile_shipped(self.source(), self.backlog, self.PR).status, ShippedStatus.MARKED_DONE)

    def test_a_merge_without_a_claim_is_no_claim(self) -> None:
        self.use_merge_commit(self.merge_no_ff(None))
        self.assertEqual(self.source().merged_claim(self.PR), NoClaim())

    def test_unreadable_merges(self) -> None:
        cases = {
            "unmerged (empty)": "",
            "unmerged (null)": "null",
            "not in this clone": "a" * 40,
        }
        for name, oid in cases.items():
            with self.subTest(name=name):
                self.use_merge_commit(oid)
                outcome = reconcile_shipped(self.source(), self.backlog, self.PR)
                self.assertEqual(outcome.status, ShippedStatus.UNREADABLE)
                assert outcome.reason is not None
                self.assertIn("not in this clone" if name == "not in this clone" else "no merge commit", outcome.reason)
        self.gh.update(fail={"pr view": [1, "HTTP 404: Not Found"]})
        lookup = self.source().merged_claim(self.PR)
        self.assertEqual(lookup, Unreadable("gh pr view failed: HTTP 404: Not Found"))
        self.assertEqual(self.main_text(), BACKLOG)

    def test_the_gh_call_is_the_merge_commit_view(self) -> None:
        self.use_merge_commit("")
        self.source().merged_claim(self.PR)
        argv = self.gh.state()["calls"][-1]["argv"]
        self.assertEqual(argv, ["pr", "view", "41", "--json", "mergeCommit", "-q", ".mergeCommit.oid"])


class ConstructionTest(ClaimSourceCase):
    def test_bad_arguments_are_refused(self) -> None:
        with self.assertRaises(TypeError):
            GitClaimSource(Path(self.repo))  # type: ignore[arg-type]
        for field in ("main", "remote"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                GitClaimSource(self.backlog, **{field: "a b"})  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            GitClaimSource(self.backlog, timeout_s=0)
        with self.assertRaises(TypeError):
            self.source().merged_claim(41)  # type: ignore[arg-type]

    def test_stranded_branch_names_come_from_git(self) -> None:
        self.commit_on(BRANCH, self.claimed(), push=True)
        refs = [b.ref for b in self.source().unshipped_claims()]
        self.assertEqual(refs, [StrandedBranch(BRANCH), StrandedBranch(f"origin/{BRANCH}")])
