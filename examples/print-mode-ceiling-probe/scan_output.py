"""Scan captured `claude -p` output files for the ceiling-kill signature.

A run killed by the background-wait ceiling exits 0, so its exit code says
"success". This reads the text instead.

CLI::

    python3 scan_output.py <file> [file...]

Exit 0 if no file carries the signature, 1 if any does, 2 if a file cannot be
read (an unreadable file is never reported as clean).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from verdict import find_truncation

EXIT_CLEAN = 0
EXIT_TRUNCATED = 1
EXIT_UNREADABLE = 2


def main(paths: Sequence[str]) -> int:
    if not paths:
        print("usage: python3 scan_output.py <file> [file...]", file=sys.stderr)
        return EXIT_UNREADABLE
    truncated = False
    for name in paths:
        try:
            text = Path(name).read_text(errors="replace")
        except OSError as exc:
            print(f"UNREADABLE  {name}: {exc}", file=sys.stderr)
            return EXIT_UNREADABLE
        finding = find_truncation(text)
        if finding is None:
            print(f"CLEAN      {name}")
        else:
            truncated = True
            print(f"TRUNCATED  {name}:{finding.line_number}  killed after {finding.waited_seconds}s idle wait")
    return EXIT_TRUNCATED if truncated else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
