"""Turns trial outcomes into the benchmark's two rates, its text report and its JSON record.

Pure: no I/O, no clock. Every collection is sorted by slug or by the
``Outcome`` declaration order before it is printed, so the same outcomes
always give the same bytes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from .classify import CLEAN_GRADED, GRADED_BY_KIND, SEEDED_GRADED, Outcome
from .fixture import Kind
from .fresh import TEMPLATE_SHA256_KEY
from .stats import wilson

RESULTS_SCHEMA_VERSION = 1
# ``exit_code`` for a run with an UNGRADED trial. ``nightloop.cli.ExitCode.UNGRADED``
# has the same value; a test keeps the two equal (this core does not import the CLI).
UNGRADED_EXIT = 7
OK_EXIT = 0

# A review model name as ``nightloop.model.Phase`` accepts it: non-empty, no whitespace.
_REVIEW_MODEL = re.compile(r"\S+")

INTERVAL_CAVEAT = (
    "95% CI: Wilson score interval over trials. Repeated trials of one fixture are "
    "not independent samples of all diffs."
)


@dataclass(frozen=True)
class TrialResult:
    """One trial: which fixture, which repeat (1-based), what it measured, and why if ungraded.

    Raises ``ValueError`` if ``outcome`` is impossible for ``kind`` (e.g. a
    clean fixture CAUGHT), ``trial < 1``, or ``detail`` holds a line break.
    """

    slug: str
    kind: Kind
    trial: int
    outcome: Outcome
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, Kind) or not isinstance(self.outcome, Outcome):
            raise TypeError("kind must be a Kind and outcome an Outcome")
        if self.outcome is not Outcome.UNGRADED and self.outcome not in GRADED_BY_KIND[self.kind]:
            raise ValueError(f"a {self.kind.value} fixture cannot be {self.outcome.name}")
        if isinstance(self.trial, bool) or not isinstance(self.trial, int) or self.trial < 1:
            raise ValueError(f"trial must be an int >= 1, got {self.trial!r}")
        if not isinstance(self.detail, str) or "\n" in self.detail or "\r" in self.detail:
            raise ValueError("detail must be one line of text")


@dataclass(frozen=True)
class FixtureTally:
    """Counts per outcome for one fixture. ``counts`` holds every ``Outcome`` (0 if none)."""

    slug: str
    kind: Kind
    counts: Mapping[Outcome, int]


@dataclass(frozen=True)
class Rate:
    """``hits`` out of ``n`` graded trials, with its Wilson interval; no interval when ``n == 0``."""

    hits: int
    n: int

    @property
    def point(self) -> float | None:
        return None if self.n == 0 else self.hits / self.n

    @property
    def interval(self) -> tuple[float, float] | None:
        return None if self.n == 0 else wilson(self.hits, self.n)


@dataclass(frozen=True)
class BenchReport:
    """A whole benchmark run: per-fixture tallies (sorted by slug), the template hash, the ungraded trials, and the review model.

    Built by ``summarise``. Raises ``ValueError`` if a tally does not sum to
    ``trials`` or counts an outcome its kind cannot have, if the tallies
    are not sorted by unique slug, if ``template_sha256`` is blank, or if
    ``review_model`` is not a non-empty name without whitespace.
    """

    template_sha256: str
    trials: int
    fixtures: tuple[FixtureTally, ...]
    ungraded: tuple[TrialResult, ...]
    # The model every trial's review phase ran on, so two runs can be told apart.
    review_model: str

    def __post_init__(self) -> None:
        if not isinstance(self.template_sha256, str) or self.template_sha256.strip() == "":
            raise ValueError("template_sha256 must be non-empty")
        if not isinstance(self.review_model, str) or not _REVIEW_MODEL.fullmatch(self.review_model):
            raise ValueError(f"review_model must be a non-empty name without whitespace, got {self.review_model!r}")
        slugs = [t.slug for t in self.fixtures]
        if slugs != sorted(set(slugs)):
            raise ValueError("fixtures must be sorted by unique slug")
        for tally in self.fixtures:
            allowed = set(GRADED_BY_KIND[tally.kind]) | {Outcome.UNGRADED}
            if set(tally.counts) != set(Outcome):
                raise ValueError(f"{tally.slug}: counts must hold every outcome")
            if any(n and o not in allowed for o, n in tally.counts.items()):
                raise ValueError(f"{tally.slug}: counts an outcome a {tally.kind.value} fixture cannot have")
            if sum(tally.counts.values()) != self.trials:
                raise ValueError(f"{tally.slug}: {sum(tally.counts.values())} trials counted, expected {self.trials}")

    def total(self, outcome: Outcome) -> int:
        return sum(t.counts[outcome] for t in self.fixtures)

    @property
    def catch(self) -> Rate:
        """CAUGHT over CAUGHT + MISDIAGNOSED + MISSED. UNGRADED is in neither part."""
        return Rate(self.total(Outcome.CAUGHT), sum(self.total(o) for o in SEEDED_GRADED))

    @property
    def false_positive(self) -> Rate:
        """FALSE_POSITIVE over FALSE_POSITIVE + CLEAN_PASSED. UNGRADED is in neither part."""
        return Rate(self.total(Outcome.FALSE_POSITIVE), sum(self.total(o) for o in CLEAN_GRADED))

    @property
    def ungraded_count(self) -> int:
        return self.total(Outcome.UNGRADED)


def summarise(
    results: Sequence[TrialResult], *, template_sha256: str, trials: int, review_model: str
) -> BenchReport:
    """Tally ``results`` into a ``BenchReport`` for a run whose reviews ran on ``review_model``.

    Raises ``ValueError`` if ``results`` is empty, a slug appears with two
    kinds, a fixture does not have exactly one result for each trial
    ``1..trials`` (a partial run is not summarised as if it were whole), or
    ``review_model`` is blank or holds whitespace.
    """
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ValueError(f"trials must be an int >= 1, got {trials!r}")
    if not results:
        raise ValueError("no trial results to summarise")
    by_slug: dict[str, list[TrialResult]] = {}
    for result in results:
        by_slug.setdefault(result.slug, []).append(result)
    tallies = []
    for slug in sorted(by_slug):
        group = by_slug[slug]
        kinds = {r.kind for r in group}
        if len(kinds) != 1:
            raise ValueError(f"{slug}: results disagree about the fixture's kind")
        if sorted(r.trial for r in group) != list(range(1, trials + 1)):
            raise ValueError(f"{slug}: expected one result for each trial 1..{trials}")
        counts = {o: 0 for o in Outcome}
        for r in group:
            counts[r.outcome] += 1
        tallies.append(FixtureTally(slug, kinds.pop(), MappingProxyType(counts)))
    ungraded = tuple(
        sorted((r for r in results if r.outcome is Outcome.UNGRADED), key=lambda r: (r.slug, r.trial))
    )
    return BenchReport(template_sha256, trials, tuple(tallies), ungraded, review_model)


def exit_code(report: BenchReport) -> int:
    """``UNGRADED_EXIT`` if any trial is UNGRADED, else ``OK_EXIT``. The report is still printed either way."""
    return UNGRADED_EXIT if report.ungraded_count else OK_EXIT


def render_report(report: BenchReport) -> str:
    """The text report. Line 1 is the catch rate, line 2 the false-positive rate, each with its interval."""
    lines = [
        _rate_line("catch rate", report.catch, "seeded"),
        _rate_line("false-positive rate", report.false_positive, "clean"),
        f"ungraded {report.ungraded_count} trial(s), counted in neither rate",
        f"template sha256 {report.template_sha256}; {report.trials} trial(s) per fixture",
        f"review model {report.review_model}",
        INTERVAL_CAVEAT,
        "",
    ]
    header = ("fixture", "kind", *(o.name for o in Outcome))
    rows = [header] + [
        (t.slug, t.kind.value, *(str(t.counts[o]) for o in Outcome)) for t in report.fixtures
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
    for r in report.ungraded:
        lines.append(f"UNGRADED {r.slug} trial {r.trial}: {r.detail}")
    return "\n".join(lines)


def to_json(report: BenchReport) -> str:
    """The results file's text: sorted keys, two-space indent, one trailing newline."""
    doc = {
        "schema_version": RESULTS_SCHEMA_VERSION,
        TEMPLATE_SHA256_KEY: report.template_sha256,
        "trials": report.trials,
        "review_model": report.review_model,
        "catch": _rate_doc(report.catch),
        "false_positive": _rate_doc(report.false_positive),
        "ungraded": report.ungraded_count,
        "fixtures": [
            {"slug": t.slug, "kind": t.kind.value, "counts": {o.name: t.counts[o] for o in Outcome}}
            for t in report.fixtures
        ],
        "ungraded_trials": [{"slug": r.slug, "trial": r.trial, "detail": r.detail} for r in report.ungraded],
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _percent(x: float) -> str:
    return f"{x * 100:.0f}%"


def _rate_line(label: str, rate: Rate, kind: str) -> str:
    interval = rate.interval
    point = rate.point
    if interval is None or point is None:
        return f"{label} n/a (no graded {kind} trial)"
    low, high = interval
    return (
        f"{label} {rate.hits}/{rate.n} = {_percent(point)} "
        f"(95% CI {low * 100:.0f}-{_percent(high)})"
    )


def _rate_doc(rate: Rate) -> dict[str, object]:
    interval = rate.interval
    point = rate.point
    return {
        "hits": rate.hits,
        "n": rate.n,
        "rate": None if point is None else round(point, 4),
        "ci_low": None if interval is None else round(interval[0], 4),
        "ci_high": None if interval is None else round(interval[1], 4),
    }
