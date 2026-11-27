"""The text ``nightloop bench`` prints for ``--dry-run``, ``--check-fresh`` and each trial.

Pure: no I/O. ``bench_cmd`` passes in every value it shows.
"""

from __future__ import annotations

import shlex
from typing import Sequence

from ..bench.classify import Outcome
from ..bench.fixture import Fixture, Kind
from ..bench.fresh import TemplateFreshness
from ..bench.report import TrialResult


def render_matrix(
    fixtures: Sequence[Fixture],
    *,
    trials: int,
    template: str,
    template_sha256: str,
    review_model: str,
    agent_file: str,
    argv: Sequence[str],
    placeholder_repo: str,
    path_prefix: str,
) -> str:
    """The dry-run plan: what would run, how many billed review calls, and one trial's argv."""
    seeded = sum(1 for f in fixtures if f.kind is Kind.SEEDED)
    clean = len(fixtures) - seeded
    width = max(len(f.slug) for f in fixtures)
    lines = [
        "nightloop bench --dry-run: nothing is run and nothing is written",
        f"template {template} (sha256 {template_sha256})",
        f"review model {review_model}",
        f"each trial: a fresh git repo with the template at {agent_file}, the fixture's note "
        "committed, the increment untracked, no remote",
        f"fixtures: {len(fixtures)} ({seeded} seeded, {clean} clean) x {trials} trial(s) = "
        f"{len(fixtures) * trials} review call(s), each billed",
    ]
    for f in fixtures:
        extra = f"  patterns: {len(f.patterns)}" if f.kind is Kind.SEEDED else ""
        lines.append(f"  {f.kind.value:<6}  {f.slug:<{width}}{extra}".rstrip())
    lines.append(f"argv of one trial ({placeholder_repo} stands for that trial's temp repo):")
    lines.append(f"  {shlex.join(argv)}")
    lines.append(f"PATH for the review phase starts with {path_prefix}")
    return "\n".join(lines)


def render_freshness(state: TemplateFreshness, *, results: str, template: str) -> str:
    """One line for ``--check-fresh``."""
    if state is TemplateFreshness.FRESH:
        return f"FRESH: {results} was recorded for the current {template}"
    if state is TemplateFreshness.STALE:
        return f"STALE: {results} was recorded for a different {template}; re-run nightloop bench"
    return f"NEVER_RUN: no results file at {results}; run nightloop bench"


def render_trial(result: TrialResult, *, trials: int) -> str:
    """One progress line after a trial."""
    tail = f" ({result.detail})" if result.outcome is Outcome.UNGRADED else ""
    return f"bench {result.slug} trial {result.trial}/{trials}: {result.outcome.name}{tail}"
