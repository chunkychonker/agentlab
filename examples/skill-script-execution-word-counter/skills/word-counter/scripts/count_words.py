#!/usr/bin/env python3
"""Count words, lines, and characters in a text file.

Standalone script — no repo imports, meant to be run only as a subprocess
(by a shell, by a Skill's execution step, or by agent.py's tool wrapper).

Usage:
    count_words.py <path>

Output contract (the "solve, don't defer" contract from the research note):
every outcome, success or failure, is exactly one JSON object on stdout.
No outcome ever lets a Python traceback reach stdout or stderr.

    success            {"words": N, "lines": M, "chars": K}   exit 0
    missing file       {"error": "file not found: <path>"}    exit 1
    path is a directory {"error": "not a file: <path>"}       exit 2
    unreadable file    {"error": "cannot read: <path> (...)"}  exit 3
    bad usage          {"error": "usage: ..."}                exit 4
    unexpected failure {"error": "unexpected failure: ..."}    exit 5

An empty file is NOT an error: it returns {"words": 0, "lines": 0, "chars": 0}
with exit 0.
"""

from __future__ import annotations

import json
import os
import sys

# Exit codes are named constants, not magic numbers, so callers can depend on
# them without re-reading this file.
EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_NOT_A_FILE = 2
EXIT_CANNOT_READ = 3
EXIT_BAD_USAGE = 4
EXIT_UNEXPECTED = 5


def _classify(path: str) -> tuple[dict, int]:
    """Read and count `path`, or describe why it could not be read.

    Returns (result, exit_code). `result` is always a JSON-serializable dict:
    either the three counts on success, or a single "error" key on failure.
    Never raises for the documented failure modes (missing path, non-file
    path, unreadable file) — those are reported as data, not exceptions.
    """
    if not os.path.exists(path):
        return {"error": f"file not found: {path}"}, EXIT_NOT_FOUND
    if not os.path.isfile(path):
        return {"error": f"not a file: {path}"}, EXIT_NOT_A_FILE
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        return {"error": f"cannot read: {path} ({exc})"}, EXIT_CANNOT_READ

    return (
        {
            "words": len(content.split()),
            "lines": len(content.splitlines()),
            "chars": len(content),
        },
        EXIT_OK,
    )


def count(path: str) -> dict:
    """Count words/lines/chars in a text file.

    Failure modes: returns {"error": "<message>"} instead of raising for a
    missing file, a non-file path, or an unreadable file — see module
    docstring for the exact messages. Never lets an exception escape.
    """
    result, _exit_code = _classify(path)
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"error": f"usage: {os.path.basename(argv[0]) if argv else 'count_words.py'} <path>"}))
        return EXIT_BAD_USAGE

    result, exit_code = _classify(argv[1])
    print(json.dumps(result))
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # noqa: BLE001 - solve, don't defer: no traceback ever reaches stdout
        print(json.dumps({"error": f"unexpected failure: {exc}"}))
        sys.exit(EXIT_UNEXPECTED)
