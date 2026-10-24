"""Command-line shell that reads a plugin directory and prints `plugin_rules` findings.

Usage:
    python3 check_plugin.py PLUGIN_DIR [--strict] [--original COPY=ORIGINAL ...]

`COPY` is a path relative to PLUGIN_DIR; `ORIGINAL` is a path relative to the
current directory (or absolute). Each pair must be byte-identical.

Exit codes: 0 = no errors (and, with --strict, no warnings); 1 = findings or
an unreadable input; 2 = bad command-line usage (argparse).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from plugin_rules import (
    Finding,
    Severity,
    check_drift,
    check_manifest,
    check_mcp,
    has_failures,
    user_config_keys,
)

MANIFEST_REL = ".claude-plugin/plugin.json"
MCP_REL = ".mcp.json"


class InputError(Exception):
    """An input file is missing, unreadable, or not valid JSON."""


def _listing(root: Path) -> frozenset[str]:
    """Every file and directory under `root`, as relative POSIX paths."""
    out: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath).relative_to(root)
        for name in (*dirnames, *filenames):
            out.add((base / name).as_posix())
    return frozenset(out)


def _read_json(path: Path) -> object:
    """Raises InputError if `path` is missing, unreadable, or not valid JSON."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}") from exc


def _read_bytes(path: Path) -> bytes:
    """Raises InputError if `path` is missing or unreadable."""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc


def _parse_pair(raw: str) -> tuple[str, str]:
    copy, sep, original = raw.partition("=")
    if not sep or not copy or not original:
        raise argparse.ArgumentTypeError(f"expected COPY=ORIGINAL, got {raw!r}")
    return copy, original


def _collect(root: Path, pairs: list[tuple[str, str]]) -> list[Finding]:
    """All findings for the plugin at `root`. Raises InputError on unreadable input."""
    if not root.is_dir():
        raise InputError(f"{root} is not a directory")
    listing = _listing(root)
    manifest = _read_json(root / MANIFEST_REL)
    findings = check_manifest(manifest, listing)
    if MCP_REL in listing:
        findings += check_mcp(_read_json(root / MCP_REL), listing, user_config_keys(manifest))
    for copy_rel, original in pairs:
        findings += check_drift(
            _read_bytes(root / copy_rel), _read_bytes(Path(original)), f"{copy_rel} and {original}"
        )
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Offline check of a Claude Code plugin directory.")
    parser.add_argument("plugin_dir", type=Path)
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument(
        "--original",
        dest="pairs",
        action="append",
        default=[],
        type=_parse_pair,
        metavar="COPY=ORIGINAL",
        help="assert a bundled copy is byte-identical to its original (repeatable)",
    )
    args = parser.parse_args(argv)

    try:
        findings = _collect(args.plugin_dir, args.pairs)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for f in findings:
        print(f"{f.severity.value}: {f.message}")
    errors = sum(f.severity is Severity.ERROR for f in findings)
    warnings = len(findings) - errors
    failed = has_failures(findings, args.strict)
    print(f"{'FAIL' if failed else 'OK'}: {errors} error(s), {warnings} warning(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
