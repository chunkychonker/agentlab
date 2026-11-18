"""Recovers claims from git and gh: the only claims module that runs a process.

Implements ``ClaimSource``. A stranded claim is the backlog diff from
``merge-base <main> <ref>`` to ``<ref>``. A shipped claim is the backlog diff
of the PR's merge commit against its FIRST parent: once a PR is merged its
head is an ancestor of main, so the merge base is the head itself and that
diff would be empty (bash learned this on PRs #37, #38 and #41). The first
parent also works for a squash merge.

It reads only; it never fetches, commits, pushes or deletes. The caller
fetches first if the merge commit was made on the server.
"""

from __future__ import annotations

import re
import subprocess

from ..git_runner import GIT_PROGRAM, GIT_TIMEOUT_S, GitRunner
from ..process_tail import last_line
from ..ship.gh import GH_PROGRAM, Gh, GhError
from .backlog_file import BacklogFile
from .claim_diff import claim_from_diff
from .ports import ClaimLookup, ClaimSourceError, NoClaim, Unreadable, UnshippedBranch
from .values import PrNumber, StrandedBranch

UNSHIPPED_GLOB = "cycle/*-unshipped-*"
_HEADS = "refs/heads/"
_REMOTES = "refs/remotes/"
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_QUOTE_CHARS = 80
# Diff exactly what is committed: no user diff drivers, filters or colour.
_DIFF = ("diff", "--no-color", "--no-ext-diff", "--no-textconv")


class GitClaimSource:
    """Claims in the checkout ``backlog.repo``, read from the file ``backlog.relative``.

    ``main`` is the branch recovery branches are diffed against and
    ``remote`` the remote whose copies of them are also listed. ``git`` and
    ``gh`` are program names (stand-ins in tests). Raises
    ``TypeError``/``ValueError`` for a non-``BacklogFile``, a blank or
    spaced ``main``/``remote``, or a non-positive ``timeout_s``.
    """

    def __init__(
        self,
        backlog: BacklogFile,
        *,
        main: str = "main",
        remote: str = "origin",
        git: str = GIT_PROGRAM,
        gh: str = GH_PROGRAM,
        timeout_s: float = GIT_TIMEOUT_S,
    ) -> None:
        if not isinstance(backlog, BacklogFile):
            raise TypeError(f"backlog must be a BacklogFile, got {type(backlog).__name__}")
        for field, value in (("main", main), ("remote", remote)):
            if not isinstance(value, str) or value == "" or any(ch.isspace() for ch in value):
                raise ValueError(f"{field} must be a non-empty name with no whitespace, got {value!r}")
        self._path = backlog.relative
        self._main = main
        self._remote = remote
        self._git = GitRunner(backlog.repo, program=git, timeout_s=timeout_s)
        self._gh = Gh(backlog.repo, program=gh, timeout_s=timeout_s)

    def unshipped_claims(self) -> list[UnshippedBranch]:
        """Every local and ``<remote>`` branch matching ``cycle/*-unshipped-*``, in refname order.

        Local branches sort first, so a branch whose push succeeded is named
        by its local name. Each one's ``lookup`` is its claim, ``NoClaim``,
        or ``Unreadable`` (no merge base, a failed diff, a timeout). Raises
        ``ClaimSourceError`` if the refs cannot be listed and ``OSError`` if
        git cannot start.
        """
        patterns = (f"{_HEADS}{UNSHIPPED_GLOB}", f"{_REMOTES}{self._remote}/{UNSHIPPED_GLOB}")
        try:
            listed = self._git.run("for-each-ref", "--format=%(refname)", *patterns)
        except subprocess.TimeoutExpired as exc:
            raise ClaimSourceError(f"git for-each-ref timed out after {self._git.timeout_s:g}s") from exc
        if listed.returncode != 0:
            raise ClaimSourceError(f"git for-each-ref failed: {last_line(listed.stderr, listed.stdout, listed.returncode)}")
        return [
            UnshippedBranch(StrandedBranch(_short_name(refname)), self._branch_claim(refname))
            for refname in listed.stdout.splitlines()
            if refname
        ]

    def merged_claim(self, pr: PrNumber) -> ClaimLookup:
        """The claim PR ``pr``'s merge commit added to the backlog, relative to its first parent.

        ``Unreadable`` when gh fails or reports no merge commit (an unmerged
        PR), when that commit is not in this clone, or when the diff fails.
        Raises ``TypeError`` for a non-``PrNumber`` and ``OSError`` if git or
        gh cannot start.
        """
        if not isinstance(pr, PrNumber):
            raise TypeError(f"pr must be a PrNumber, got {type(pr).__name__}")
        try:
            oid = self._gh.merge_commit_of(pr.value)
        except GhError as exc:
            return Unreadable(str(exc))
        if _OID.fullmatch(oid) is None:
            return Unreadable(f"gh reports no merge commit for PR #{pr}: got {oid[:_QUOTE_CHARS]!r}")
        present = self._git_step(f"merge commit {oid} is not in this clone (fetch first)",
                                 "cat-file", "-e", f"{oid}^{{commit}}")
        if isinstance(present, Unreadable):
            return present
        return self._diff_claim(f"{oid}^1", oid)

    def _branch_claim(self, refname: str) -> ClaimLookup:
        base = self._git_step(f"no merge base between {self._main} and {refname}", "merge-base", self._main, refname)
        if isinstance(base, Unreadable):
            return base
        return self._diff_claim(base.strip(), refname)

    def _diff_claim(self, old: str, new: str) -> ClaimLookup:
        diff = self._git_step(f"cannot diff {self._path} from {old} to {new}", *_DIFF, old, new, "--", self._path)
        if isinstance(diff, Unreadable):
            return diff
        claim = claim_from_diff(diff)
        return NoClaim() if claim is None else claim

    def _git_step(self, what: str, *args: str) -> str | Unreadable:
        """stdout of one git call, or ``Unreadable("<what>: <git's last line>")``. ``OSError`` propagates."""
        try:
            done = self._git.run(*args)
        except subprocess.TimeoutExpired:
            return Unreadable(f"{what}: git {args[0]} timed out after {self._git.timeout_s:g}s")
        if done.returncode != 0:
            return Unreadable(f"{what}: {last_line(done.stderr, done.stdout, done.returncode)}")
        return done.stdout


def _short_name(refname: str) -> str:
    """``refs/heads/cycle/x`` -> ``cycle/x``; ``refs/remotes/origin/cycle/x`` -> ``origin/cycle/x``."""
    for prefix in (_HEADS, _REMOTES):
        if refname.startswith(prefix):
            return refname[len(prefix):]
    raise ClaimSourceError(f"git for-each-ref listed a ref outside the patterns: {refname!r}")
