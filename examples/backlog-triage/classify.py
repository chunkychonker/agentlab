"""Classifies one unclaimed BACKLOG.md line as a historical observation or actionable work.

Pure: no I/O, no clock. ``today`` is always a parameter.

The rules (from research/2026-10-25-backlog-stale-health-triage.md):

* Only lines of the form ``- [ ] fix (health YYYY-MM-DD): <body>`` are
  candidates. Every other line is actionable, always.
* The body's *kind* is decided by its leading shape, first match wins:

  1. the first token is a path (contains ``/`` or names a ``.md/.py/.sh/.json``
     file, optionally ``:line``)      -> OTHER  (subject is a file someone must fix)
  2. ``run-YYYY-MM-DD_HHMMSS`` prefix  -> RUN_OUTCOME
  3. ``no run log for`` prefix         -> GAP_NIGHT
  4. ``phase '...' exited non-zero``   -> PHASE_EXIT
  5. an ``Nx`` / ``N×`` count anywhere -> REPEATED_MESSAGE
  6. anything else                     -> OTHER

* The newest date is the max ``YYYY-MM-DD`` in the body (the filing date in the
  ``(health ...)`` prefix is excluded). Run ids count: ``run-2026-10-22_020001``
  contributes 2026-10-22.
* Historical iff kind is not OTHER, a body date exists, and
  ``today - newest > max_age_days``. A kind with no body date is actionable:
  when in doubt, keep the item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum


class Kind(Enum):
    """What a health finding observes. The value is the label written into the closing marker."""

    GAP_NIGHT = "gap-night"
    RUN_OUTCOME = "run-outcome"
    PHASE_EXIT = "phase-exit"
    REPEATED_MESSAGE = "repeated-message"
    OTHER = "other"


UNCLAIMED_PREFIX = "- [ ] "

_HEALTH = re.compile(r"^- \[ \] fix \(health \d{4}-\d{2}-\d{2}\): (?P<body>.*)$")
_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_PATH_TOKEN = re.compile(r"/|\.(?:md|py|sh|json)(?::\d+)?$")
_RUN_ID = re.compile(r"^run-\d{4}-\d{2}-\d{2}_\d{6}(?!\d)")
_GAP = re.compile(r"^no run log for ")
_PHASE_EXIT = re.compile(r"^phase '[^']*' exited non-zero")
_COUNT = re.compile(r"(?<![\w.])\d+[x×](?!\w)")


@dataclass(frozen=True)
class Actionable:
    """An unclaimed line that stays queued. ``newest`` is None when the body has no date."""

    line_no: int
    kind: Kind
    newest: date | None
    reason: str
    text: str


@dataclass(frozen=True)
class Historical:
    """An unclaimed health finding old enough to close. Always has a kind and a date.

    Raises ValueError on construction with ``Kind.OTHER``: OTHER items are never
    auto-closed, so a Historical OTHER is an illegal state.
    """

    line_no: int
    kind: Kind
    newest: date
    reason: str
    text: str

    def __post_init__(self) -> None:
        if self.kind is Kind.OTHER:
            raise ValueError("Kind.OTHER is never historical")


Verdict = Actionable | Historical


def _kind_of(body: str) -> Kind:
    first_token = body.split(maxsplit=1)[0] if body.strip() else ""
    if _PATH_TOKEN.search(first_token):
        return Kind.OTHER
    if _RUN_ID.match(body):
        return Kind.RUN_OUTCOME
    if _GAP.match(body):
        return Kind.GAP_NIGHT
    if _PHASE_EXIT.match(body):
        return Kind.PHASE_EXIT
    if _COUNT.search(body):
        return Kind.REPEATED_MESSAGE
    return Kind.OTHER


def _newest_date(body: str) -> date | None:
    """Max valid calendar date in ``body``. Tokens shaped like dates but not
    real dates (``2026-13-40``) are not dates and are skipped."""
    found: list[date] = []
    for y, m, d in _DATE.findall(body):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return max(found) if found else None


def classify(line: str, line_no: int, today: date, max_age_days: int = 7) -> Verdict:
    """Verdict for one BACKLOG.md line (without its trailing newline).

    Raises ValueError if ``max_age_days`` is negative. Never raises on any
    ``line`` text: an unrecognised line is Actionable.
    """
    if max_age_days < 0:
        raise ValueError(f"max_age_days must be >= 0, got {max_age_days}")

    match = _HEALTH.match(line)
    if match is None:
        return Actionable(line_no, Kind.OTHER, None, "not a health finding", line)

    body = match.group("body")
    kind = _kind_of(body)
    newest = _newest_date(body)

    if kind is Kind.OTHER:
        return Actionable(line_no, kind, newest, "kind other is never auto-closed", line)
    if newest is None:
        return Actionable(line_no, kind, None, "no date in body, kept to be safe", line)

    age = (today - newest).days
    if age > max_age_days:
        return Historical(line_no, kind, newest, f"newest {newest} is {age}d old (> {max_age_days})", line)
    return Actionable(line_no, kind, newest, f"newest {newest} is {age}d old (<= {max_age_days})", line)
