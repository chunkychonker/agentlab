"""``GitHubShipper``: the cycle's ``Shipper`` port, publishing a passed cycle as a reviewed PR.

Pure in itself: git, ``gh``, the verdict file and the clock all arrive as
parameters. The order is fixed here:

1. Compose the review record (before anything is pushed, so a PR can never
   exist without it). No record, a credential in it, or no room for it:
   ``NotShipped``, nothing committed, nothing pushed.
2. ``gh.find``: a PR already open (or closed) for this cycle's branch is
   ``NotShipped``. Checked before the push, so a retry never adds commits to
   an existing PR's branch.
3. ``git.publish``: commit on the PR branch, push it, back to main.
4. ``gh.create``, then read the PR number from the URL it prints.
5. Read the body back and check the record's marker is in it.
6. Poll ``mergeable`` while it is ``UNKNOWN``. Only ``MERGEABLE`` is
   ``Shipped``.

There is no merge step: merging is left to the repo's auto-merge or a human.
"""

from __future__ import annotations

from typing import Callable

from ..cycle.result import NotShipped, Shipped, ShipOutcome
from ..cycle.run_id import RunId
from .mergeable import Mergeable, decide
from .ports import BranchPublisher, GhFailed, PublishFailed, PullRequests
from .pr_text import branch_name, pr_base_body, pr_number_from_url, pr_title
from .review_record import REVIEW_MARKER, compose_body, has_secret, review_section

# Bash's auto-merge gate polls 5 times, 2s apart.
MERGEABLE_ATTEMPTS = 5
MERGEABLE_WAIT_S = 2.0
# Outside text (git and gh messages, a mergeable value) quoted in a reason.
_QUOTE_CHARS = 200


class GitHubShipper:
    """Opens one reviewed PR per passed cycle; see the module docstring for the steps.

    ``read_verdict`` returns the review verdict file's text. ``sleep`` is
    called between ``UNKNOWN`` mergeable readings. Raises ``TypeError`` or
    ``ValueError`` at construction for a ``mergeable_attempts`` that is not
    a positive int or a ``mergeable_wait_s`` that is not a non-negative
    number.
    """

    def __init__(
        self,
        *,
        read_verdict: Callable[[], str],
        git: BranchPublisher,
        gh: PullRequests,
        sleep: Callable[[float], None],
        mergeable_attempts: int = MERGEABLE_ATTEMPTS,
        mergeable_wait_s: float = MERGEABLE_WAIT_S,
    ) -> None:
        if isinstance(mergeable_attempts, bool) or not isinstance(mergeable_attempts, int):
            raise TypeError(f"mergeable_attempts must be an int, got {type(mergeable_attempts).__name__}")
        if mergeable_attempts < 1:
            raise ValueError(f"mergeable_attempts must be >= 1, got {mergeable_attempts}")
        if isinstance(mergeable_wait_s, bool) or not isinstance(mergeable_wait_s, (int, float)):
            raise TypeError(f"mergeable_wait_s must be a number, got {type(mergeable_wait_s).__name__}")
        if not mergeable_wait_s >= 0:
            raise ValueError(f"mergeable_wait_s must be >= 0, got {mergeable_wait_s}")
        self._read_verdict = read_verdict
        self._git = git
        self._gh = gh
        self._sleep = sleep
        self._attempts = mergeable_attempts
        self._wait_s = float(mergeable_wait_s)

    def ship(self, run_id: RunId, cycle: int) -> ShipOutcome:
        """``Shipped(pr)`` once the PR is open, carries the record, and is ``MERGEABLE``; else ``NotShipped``.

        Every ``NotShipped`` reason is one line saying what happened and
        where the work is. A PR that was opened but then failed a check is
        left open for a human, and its number is in the reason.

        Raises (environment errors only, after which the orchestrator still
        recovers): ``OSError`` if ``git`` or ``gh`` cannot be started or the
        verdict file cannot be read; ``ValueError`` for a bad ``cycle``.
        """
        branch = branch_name(run_id, cycle)
        title = pr_title(run_id, cycle)
        section = review_section(self._read_verdict())
        if section is None:
            return NotShipped("the review verdict has no Findings: block; nothing was committed or pushed")
        if has_secret(section):
            return NotShipped(
                "the review record matches a credential pattern; nothing was posted, committed or pushed, "
                "check the verdict file by hand"
            )
        body = compose_body(pr_base_body(run_id, cycle), section)
        if body is None:
            return NotShipped("the PR body has no room for the review record; nothing was committed or pushed")

        try:
            existing = self._gh.find(branch)
        except GhFailed as exc:
            return NotShipped(f"could not list PRs for {branch} ({_quote(exc)}); nothing was pushed")
        if existing:
            numbers = ", ".join(f"#{n}" for n in existing)
            return NotShipped(f"a PR already exists for {branch} ({numbers}); not pushing or opening another")

        published = self._git.publish(branch, title)
        if isinstance(published, PublishFailed):
            return NotShipped(_quote(published.reason))

        try:
            created = self._gh.create(branch, title, body)
        except GhFailed as exc:
            return NotShipped(f"gh pr create failed for {branch} ({_quote(exc)}); check whether a PR was opened")
        pr = pr_number_from_url(created)
        if pr is None:
            return NotShipped(
                f"gh pr create printed no PR URL for {branch} ({_quote(created)}); a PR may exist, check by hand"
            )

        try:
            posted = self._gh.body_of(pr)
        except GhFailed as exc:
            return NotShipped(f"PR #{pr} opened but its body could not be read back ({_quote(exc)})")
        if REVIEW_MARKER not in posted:
            return NotShipped(f"PR #{pr} opened but its body does not carry the review record; left open")

        return self._await_mergeable(pr)

    def _await_mergeable(self, pr: int) -> ShipOutcome:
        """Read ``mergeable`` up to the configured attempts, sleeping only between ``UNKNOWN`` readings."""
        value = ""
        for attempt in range(1, self._attempts + 1):
            try:
                value = self._gh.mergeable_of(pr)
            except GhFailed as exc:
                return NotShipped(f"PR #{pr} opened but its mergeable check failed ({_quote(exc)}); left open")
            decision = decide(value)
            if decision is Mergeable.READY:
                return Shipped(pr=pr)
            if decision is Mergeable.BLOCKED:
                break
            if attempt < self._attempts:
                self._sleep(self._wait_s)
        return NotShipped(f"PR #{pr} opened but mergeable={_quote(value)}; left open for a human")


def _quote(text: object) -> str:
    """``text`` collapsed to one shortened line, so a reason built from it is always valid."""
    flat = " ".join(str(text).split())
    if flat == "":
        return "(empty)"
    return flat if len(flat) <= _QUOTE_CHARS else flat[:_QUOTE_CHARS] + "..."
