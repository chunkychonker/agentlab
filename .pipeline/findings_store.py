#!/usr/bin/env python3
"""findings_store.py: keeps the private findings file, one line per finding.

Spec #7. Health and pipeline findings used to go only into BACKLOG.md, which
is public and is also the queue the nightly cycles pick work from. They now
go to logs/findings.md on the runner (git-ignored, never pushed), where they
stay until the repo owner reads them. The owner marks a finding fixed by
changing `[ ]` to `[x]`; the pipeline never deletes or closes one itself.

A line the pipeline writes looks like this:

  - [ ] [pipeline] NETWORK UNREACHABLE — seen 4× (first 2026-08-02, last 2026-11-09) — <latest detail>

A finding is identified by its label plus its subject (the text before the
first " — " of what the observer reported), the same rule BACKLOG.md uses.
Every other line in the file is the owner's and is kept byte for byte.

`record` is the pure core: old file text in, new file text out. `main` is the
command-line edge that run.sh calls through .pipeline/findings.sh; it is the
only part that touches the disk. Python 3.9 stdlib only: it runs under
/usr/bin/python3.

  findings_store.py record --file F --label L --date D --subject=S --detail=T

Exit codes (EXIT_* below): 0 a new line was added, 3 an open line was seen
again, 4 a fixed line came back and was reopened, 1 nothing was written (a bad
date or subject, or the file could not be read or written), 2 bad usage.

Failure modes: on any failure the file is left exactly as it was. The write
goes through a temp file in the same directory and a rename, so a crash never
leaves it half-written; the temp file is removed if the write fails. Not safe
against a second writer at the same moment: the owner editing the file while
a night writes to it can lose one of the two edits. Nights write at about
02:00, one finding at a time.
"""

import argparse
import datetime
import enum
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

EXIT_ADDED = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_SEEN_AGAIN = 3
EXIT_REOPENED = 4

# Details longer than this are cut and end in "...", like BACKLOG_HEALTH_DETAIL_MAX
# in backlog.sh. A finding's detail is a pointer to the night's log, not the log.
DETAIL_MAX = 200
SEP = " — "

HEADER = (
    "# Findings\n"
    "\n"
    "Health and pipeline findings, written by `.pipeline/run.sh` (spec #7). One line\n"
    "per finding; a finding seen again updates its line. Mark one fixed by changing\n"
    "`[ ]` to `[x]`: if it comes back, the pipeline reopens it. Nothing here is ever\n"
    "deleted by the pipeline. Lines not in its format are yours and are left alone.\n"
    "\n"
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LINE_RE = re.compile(
    r"- \[(?P<mark>[ xX])\] \[(?P<label>health|pipeline)\] (?P<subject>.+?)"
    r" — seen (?P<count>[1-9][0-9]*)× \(first (?P<first>\d{4}-\d{2}-\d{2}),"
    r" last (?P<last>\d{4}-\d{2}-\d{2})\)(?: — (?P<detail>.*))?"
)


class Label(enum.Enum):
    HEALTH = "health"
    PIPELINE = "pipeline"


class Outcome(enum.Enum):
    ADDED = "added"
    SEEN_AGAIN = "seen_again"
    REOPENED = "reopened"


_EXIT_FOR = {
    Outcome.ADDED: EXIT_ADDED,
    Outcome.SEEN_AGAIN: EXIT_SEEN_AGAIN,
    Outcome.REOPENED: EXIT_REOPENED,
}


@dataclass(frozen=True)
class Finding:
    """One night's report of one finding. Build it with make_finding."""

    label: Label
    date: str
    subject: str
    detail: str


@dataclass(frozen=True)
class _Entry:
    fixed: bool
    label: Label
    subject: str
    count: int
    first: str
    last: str
    detail: str


def _one_line(text: str) -> str:
    return " ".join(text.split())


def make_finding(label: Label, date: str, subject: str, detail: str) -> Finding:
    """Checks and normalises a finding at the boundary.

    Whitespace runs (newlines and tabs too) become one space, and a detail
    over DETAIL_MAX is cut. Raises ValueError if the date is not a real
    YYYY-MM-DD date, or the subject is empty or contains " — ".
    """
    if not _DATE_RE.fullmatch(date):
        raise ValueError(f"date {date!r} is not YYYY-MM-DD")
    datetime.date.fromisoformat(date)  # ValueError for 2026-02-30
    subject = _one_line(subject)
    if not subject:
        raise ValueError("empty subject")
    if SEP in subject:
        raise ValueError(f"subject contains {SEP!r}: {subject!r}")
    detail = _one_line(detail)
    if len(detail) > DETAIL_MAX:
        detail = detail[:DETAIL_MAX] + "..."
    return Finding(label, date, subject, detail)


def _parse(line: str) -> Optional[_Entry]:
    m = _LINE_RE.fullmatch(line.rstrip("\r\n"))
    if m is None:
        return None
    return _Entry(
        fixed=m["mark"] != " ",
        label=Label(m["label"]),
        subject=m["subject"],
        count=int(m["count"]),
        first=m["first"],
        last=m["last"],
        detail=m["detail"] or "",
    )


def _render(e: _Entry) -> str:
    line = (
        f"- [{'x' if e.fixed else ' '}] [{e.label.value}] {e.subject}"
        f" — seen {e.count}× (first {e.first}, last {e.last})"
    )
    return line + (SEP + e.detail if e.detail else "")


def _update(e: _Entry, f: Finding) -> Tuple[_Entry, Outcome]:
    if f.date < e.last:
        # An older night than the line already records: nothing new to say.
        return e, Outcome.SEEN_AGAIN
    if f.date == e.last:
        # The same night again (a re-run): refresh the detail of an open line,
        # never the count. A line the owner fixed after tonight stays fixed.
        if e.fixed:
            return e, Outcome.SEEN_AGAIN
        return _Entry(False, e.label, e.subject, e.count, e.first, e.last, f.detail), Outcome.SEEN_AGAIN
    if e.fixed:
        detail = f"came back {f.date}: {f.detail}" if f.detail else f"came back {f.date}"
        return _Entry(False, e.label, e.subject, e.count + 1, e.first, f.date, detail), Outcome.REOPENED
    return _Entry(False, e.label, e.subject, e.count + 1, e.first, f.date, f.detail), Outcome.SEEN_AGAIN


def record(text: str, finding: Finding) -> Tuple[str, Outcome]:
    """Returns the file text with <finding> recorded, and what happened.

    Pure. The first line with the same label and subject is updated; if there
    is none, a new line is appended (after HEADER when <text> is empty).
    Every other line, and every line ending, is returned unchanged.
    """
    lines: List[str] = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        e = _parse(line)
        if e is None or e.label is not finding.label or e.subject != finding.subject:
            continue
        new, outcome = _update(e, finding)
        ending = line[len(line.rstrip("\r\n")):]
        lines[i] = _render(new) + ending
        return "".join(lines), outcome
    if not lines:
        lines = [HEADER]
    elif not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    new = _Entry(False, finding.label, finding.subject, 1, finding.date, finding.date, finding.detail)
    lines.append(_render(new) + "\n")
    return "".join(lines), Outcome.ADDED


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _write_atomic(path: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".findings.", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="findings_store.py")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="record one finding")
    rec.add_argument("--file", required=True)
    rec.add_argument("--label", required=True, choices=[label.value for label in Label])
    rec.add_argument("--date", required=True)
    rec.add_argument("--subject", required=True)
    rec.add_argument("--detail", default="")
    args = parser.parse_args(argv)  # exits EXIT_USAGE on bad usage

    try:
        finding = make_finding(Label(args.label), args.date, args.subject, args.detail)
        old = _read(args.file)
        new, outcome = record(old, finding)
        if new != old:
            _write_atomic(args.file, new)
    except (ValueError, OSError) as exc:
        print(f"findings_store.py: {exc}", file=sys.stderr)
        return EXIT_FAILED
    return _EXIT_FOR[outcome]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
