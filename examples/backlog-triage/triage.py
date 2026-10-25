"""Command-line entry point that wires the pure triage core to one BACKLOG.md file.

Usage:
    python3 triage.py BACKLOG.md --today 2026-10-25 [--max-age-days 7] [--apply]

Default is a dry run: print the report, write nothing. ``--apply`` closes the
historical items in place (write-temp-then-rename), and only if there is at
least one. Re-running ``--apply`` is a no-op: closed lines no longer start
``- [ ] ``.

Exit codes:
    0  report printed (and, with --apply, the file written or left untouched)
    2  bad arguments, unreadable/unwritable file, or file not UTF-8; message on
       stderr, nothing written
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from backlog_lines import apply, parse_unclaimed
from classify import Historical, Verdict, classify
from report import render_report

EXIT_OK = 0
EXIT_ERROR = 2
DEFAULT_MAX_AGE_DAYS = 7


def triage(text: str, today: date, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> list[Verdict]:
    """One verdict per unclaimed line of ``text``, in file order.

    Pure. Raises ValueError if ``max_age_days`` is negative.
    """
    return [classify(line, number, today, max_age_days) for number, line in parse_unclaimed(text)]


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {value!r}") from exc


def _non_negative(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from exc
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0: {number}")
    return number


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="triage.py", description="Separate stale health findings from actionable ones."
    )
    parser.add_argument("backlog", type=Path, help="path to a BACKLOG.md")
    parser.add_argument("--today", type=_iso_date, required=True, help="YYYY-MM-DD; never read from the clock")
    parser.add_argument("--max-age-days", type=_non_negative, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--apply", action="store_true", help="close historical items in place")
    return parser.parse_args(argv)


def _replace_atomically(path: Path, text: str) -> None:
    """Write ``text`` to a sibling temp file, keep ``path``'s mode, rename over ``path``.

    Raises OSError on failure; the temp file is removed and ``path`` is untouched.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copymode(path, tmp_name)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def main(argv: list[str]) -> int:
    """Run the CLI. Returns an exit code; argparse itself exits 2 on bad usage."""
    args = _parse_args(argv)
    path: Path = args.backlog
    try:
        # newline="" keeps CRLF bytes intact so --apply changes only the marker.
        with path.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"triage.py: cannot read {path}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    verdicts = triage(text, args.today, args.max_age_days)
    sys.stdout.write(render_report(verdicts, args.today, args.max_age_days))

    closable = sum(isinstance(v, Historical) for v in verdicts)
    if not args.apply:
        print(f"\ndry run: pass --apply to close {closable} historical item(s)")
        return EXIT_OK
    if closable == 0:
        print("\nnothing to apply")
        return EXIT_OK
    try:
        _replace_atomically(path, apply(text, verdicts, args.today))
    except OSError as exc:
        print(f"triage.py: cannot write {path}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"\napplied: closed {closable} item(s) in {path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
