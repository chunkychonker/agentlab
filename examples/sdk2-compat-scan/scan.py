"""Command-line shell: walk a tree, read files, print 2026-10-21 / SDK 2.x findings.

Usage:
    python3 scan.py ROOT [--json]

Scans every ``*.py`` and ``requirements*.txt`` under ROOT, skipping
``.git``, ``.venv``, ``venv``, ``node_modules`` and ``__pycache__``.

Exit codes:
    0  no finding (which means "no known pattern found", not "safe")
    1  at least one finding
    2  bad usage, ROOT is not a readable directory, or a file could not be read
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

# Explicit, so `python3 -I scan.py` (isolated mode, no script dir on sys.path) works too.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rules import Finding, Rule, Severity, scan_requirement, scan_source  # noqa: E402

SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__"})
REQUIREMENTS_GLOB = "requirements*.txt"

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


class ScanError(Exception):
    """The scan could not complete; the message says which path and why."""


def _candidate_files(root: Path) -> Iterator[Path]:
    """Every file to scan under ``root``, in a stable (sorted) order.

    Raises ``ScanError`` if a directory under ``root`` cannot be listed: a
    partially walked tree must not be reported as a complete scan.
    """

    def on_error(exc: OSError) -> None:
        raise ScanError(f"cannot list {exc.filename}: {exc.strerror}") from exc

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py") or fnmatch.fnmatch(name, REQUIREMENTS_GLOB):
                yield Path(dirpath) / name


def scan_tree(root: Path) -> tuple[int, list[Finding], list[Finding]]:
    """Scan ``root``; return (files scanned, findings, suppressed findings).

    Paths in findings are relative to ``root``, POSIX-style. A file that is not
    UTF-8 becomes a PARSE-ERROR finding (its content is the problem). Raises
    ``ScanError`` if ``root`` is not a directory or any file or directory
    cannot be read; no partial result is returned.
    """
    if not root.is_dir():
        raise ScanError(f"not a directory: {root}")
    findings: list[Finding] = []
    suppressed: list[Finding] = []
    count = 0
    for file in _candidate_files(root):
        rel = file.relative_to(root).as_posix()
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            findings.append(Finding(Rule.PARSE_ERROR, rel, 1, f"not UTF-8: {exc.reason}"))
            count += 1
            continue
        except OSError as exc:
            raise ScanError(f"cannot read {file}: {exc.strerror}") from exc
        count += 1
        if file.name.endswith(".py"):
            report = scan_source(rel, text)
            findings.extend(report.findings)
            suppressed.extend(report.suppressed)
        else:
            for number, line in enumerate(text.splitlines(), start=1):
                finding = scan_requirement(rel, number, line)
                if finding is not None:
                    findings.append(finding)
    return count, findings, suppressed


def _as_text(count: int, findings: list[Finding], suppressed: list[Finding]) -> str:
    lines = [
        f"{f.path}:{f.line}: {f.rule.severity.value:<5} {f.rule.rule_id}  {f.message}"
        for f in findings
    ]
    errors = sum(1 for f in findings if f.rule.severity is Severity.ERROR)
    lines.append(
        f"{len(findings)} finding(s) ({errors} error, {len(findings) - errors} warn) "
        f"in {count} file(s); {len(suppressed)} suppressed by pragma"
    )
    if not findings:
        lines.append("No known 2026-10-21 / SDK 2.x pattern found. This is not a guarantee.")
    return "\n".join(lines)


def _as_json(count: int, findings: list[Finding], suppressed: list[Finding]) -> str:
    def row(f: Finding) -> dict[str, object]:
        return {
            "rule": f.rule.rule_id,
            "severity": f.rule.severity.value,
            "path": f.path,
            "line": f.line,
            "message": f.message,
        }

    return json.dumps(
        {
            "files_scanned": count,
            "findings": [row(f) for f in findings],
            "suppressed": [row(f) for f in suppressed],
        },
        indent=2,
    )


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Static scan for Anthropic API 2026-10-21 / Python SDK 2.x breaking patterns."
    )
    parser.add_argument("root", type=Path, help="directory to scan")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on bad usage; keep that code explicit
        return EXIT_USAGE if exc.code else EXIT_CLEAN

    try:
        count, findings, suppressed = scan_tree(args.root)
    except ScanError as exc:
        print(f"scan.py: {exc}", file=sys.stderr)
        return EXIT_USAGE

    render = _as_json if args.json else _as_text
    print(render(count, findings, suppressed))
    return EXIT_FINDINGS if findings else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
