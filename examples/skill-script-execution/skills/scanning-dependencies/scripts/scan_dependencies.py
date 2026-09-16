"""Scan a directory for dependency manifests and report unpinned deps.

Walks a directory tree looking for `requirements.txt`, `package.json` and
`Cargo.toml` manifests, and flags any dependency entry that is not pinned to
an exact version. "Pinned" for `requirements.txt` means an `==` exact
specifier; for `package.json` it means a version string that is not a
caret/tilde/wildcard range and not the literal string "latest"; for
`Cargo.toml` it means Cargo's exact-requirement form, a leading `=` (a bare
`"1.2.3"` is an *implicit caret range* in Cargo, not a pin).

Requires Python >= 3.11 for the stdlib `tomllib` parser. Nothing else here
needs 3.11, and there is still no third-party dependency.

Intended to be invoked by the `scanning-dependencies` Claude Code skill via
`python3 ${CLAUDE_SKILL_DIR}/scripts/scan_dependencies.py <directory>`, but is
a plain stdlib-only CLI script and runs the same way standalone.

Output contract: exactly one JSON object on stdout, always valid JSON,
regardless of outcome.

Failure modes:
    - Nonexistent root directory: `scan_directory` raises FileNotFoundError;
      `main` catches it, prints `{"error": "..."}` JSON to stdout, and
      returns 2. This is the only case that is a script failure rather than
      scan data.
    - Malformed package.json (invalid JSON): caught per-file inside
      `scan_package_json`, recorded as one Finding with a parse-error
      reason. Does not abort the rest of the scan — other manifests already
      found keep their findings.
    - Malformed Cargo.toml (invalid TOML): same contract, caught per-file
      inside `scan_cargo_toml`.
    - No manifests found anywhere under root: not an error. Returns valid
      JSON with an empty findings list and count 0, exit 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

# Same ignore set as the docs' codebase-visualizer example, for consistency.
IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}

# requirements.txt line shape: optional extras in [...], then a version
# specifier (any of pip's comparison operators), then an optional
# environment marker after ';'. We only need the package name and whatever
# specifier text follows it.
_REQ_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[^\]]*\])?"
    r"\s*(?P<spec>.*)$"
)

# Version-specifier operators that mean "not an exact pin" when present
# without an accompanying "==".
_RANGE_OPERATORS = (">=", "<=", "~=", "!=", ">", "<")

# package.json version prefixes/values that mean "not an exact pin".
_NPM_RANGE_PREFIXES = ("^", "~", "*")
_NPM_LATEST = "latest"

# The three Cargo tables that hold dependency entries. Any other top-level
# table (`[package]`, `[features]`, `[workspace.dependencies]`, ...) is not
# a dependency list this scanner evaluates.
CARGO_DEPENDENCY_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")

# Cargo's one exact-requirement operator. Every other requirement form is a
# range: `^1.2` / bare `1.2` (caret is the default), `~1.2`, `1.*`, `>=1.2`.
_CARGO_EXACT_PREFIX = "="

# Leading characters that make a Cargo requirement visibly a range. A version
# with none of these and no `*` is the implicit-caret trap: it *looks* exact.
_CARGO_RANGE_PREFIXES = ("^", "~", ">", "<", "*")
_CARGO_WILDCARD = "*"


@dataclass(frozen=True)
class Finding:
    file: str
    package: str
    version_spec: str
    reason: str


def _strip_marker(spec: str) -> str:
    """Drop a trailing PEP 508 environment marker (after ';'), if present."""
    return spec.split(";", 1)[0].strip()


def scan_requirements_txt(path: Path, display_path: str) -> list[Finding]:
    """Parse one requirements.txt and return findings for unpinned entries.

    Comments (lines starting with '#') and option/include lines (starting
    with '-', e.g. "-r other.txt") are skipped, not flagged.
    """
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = _REQ_LINE_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        spec = _strip_marker(match.group("spec"))
        if not spec:
            findings.append(
                Finding(
                    file=display_path,
                    package=name,
                    version_spec="",
                    reason="no version pin (bare package name)",
                )
            )
        elif "==" not in spec:
            findings.append(
                Finding(
                    file=display_path,
                    package=name,
                    version_spec=spec,
                    reason="range specifier, not an exact pin",
                )
            )
    return findings


def _npm_deps_unpinned(deps: dict, display_path: str) -> list[Finding]:
    findings: list[Finding] = []
    for name, version in deps.items():
        version_str = str(version)
        if version_str == _NPM_LATEST or version_str.startswith(_NPM_RANGE_PREFIXES):
            findings.append(
                Finding(
                    file=display_path,
                    package=name,
                    version_spec=version_str,
                    reason="range or floating version, not an exact pin",
                )
            )
    return findings


def scan_package_json(path: Path, display_path: str) -> list[Finding]:
    """Parse one package.json and return findings for unpinned entries.

    Malformed JSON is caught here (not propagated) and reported as one
    Finding with a parse-error reason, so one bad file cannot abort a scan
    of an otherwise-healthy tree.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [
            Finding(
                file=display_path,
                package="",
                version_spec="",
                reason=f"could not parse package.json: {exc}",
            )
        ]

    findings: list[Finding] = []
    for key in ("dependencies", "devDependencies"):
        deps = data.get(key)
        if isinstance(deps, dict):
            findings.extend(_npm_deps_unpinned(deps, display_path))
    return findings


def _cargo_requirement_is_pinned(version_spec: str) -> bool:
    """True only for Cargo's exact-requirement form (a leading '=').

    Cargo defaults a bare `"1.2.3"` to the caret range `^1.2.3`, so — unlike
    npm — an operator-less version string is *not* a pin here. Leading
    whitespace is tolerated (`"= 1.2.3"` is the same requirement as
    `"=1.2.3"`); `>=` / `<=` do not start with `=` and so are correctly not
    pins. Never raises.
    """
    return version_spec.lstrip().startswith(_CARGO_EXACT_PREFIX)


def _cargo_unpinned_reason(version_spec: str) -> str:
    """Explain why a Cargo requirement is not an exact pin.

    Precondition: `_cargo_requirement_is_pinned(version_spec)` is False; the
    single caller in `_cargo_deps_unpinned` checks that first. Separates the
    implicit-caret case (a bare version, which reads as exact but is not)
    from the forms that are visibly ranges, because they are different
    mistakes. Never raises.
    """
    stripped = version_spec.lstrip()
    if stripped.startswith(_CARGO_RANGE_PREFIXES) or _CARGO_WILDCARD in stripped:
        return "range or floating version requirement, not an exact pin"
    return "bare version defaults to a caret range (^), not an exact pin"


def _cargo_deps_unpinned(deps: dict, display_path: str) -> list[Finding]:
    """Findings for one Cargo dependency table.

    An entry is either a bare version string (`serde = "1.0"`) or a table
    with extras (`regex = { version = "1.10", features = [...] }`). A table
    with no `version` key expresses no semver requirement at all — a path,
    git, or workspace-inherited dependency — and is skipped, not flagged.
    A non-string version is coerced with `str()` rather than raising, so one
    oddly-shaped entry cannot sink the scan. Never raises.
    """
    findings: list[Finding] = []
    for name, entry in deps.items():
        if isinstance(entry, dict):
            if "version" not in entry:
                continue
            version = entry["version"]
        else:
            version = entry
        version_str = str(version)
        if _cargo_requirement_is_pinned(version_str):
            continue
        findings.append(
            Finding(
                file=display_path,
                package=name,
                version_spec=version_str,
                reason=_cargo_unpinned_reason(version_str),
            )
        )
    return findings


def scan_cargo_toml(path: Path, display_path: str) -> list[Finding]:
    """Parse one Cargo.toml and return findings for unpinned entries.

    Scans [dependencies], [dev-dependencies] and [build-dependencies]; any
    other table is ignored. Only a version whose value (bare string, or a
    table's "version" key) begins with "=" counts as an exact pin.

    Malformed TOML is caught here (not propagated) and reported as one
    Finding with a parse-error reason, so one bad file cannot abort a scan
    of an otherwise-healthy tree — the same contract `scan_package_json`
    holds for invalid JSON.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [
            Finding(
                file=display_path,
                package="",
                version_spec="",
                reason=f"could not parse Cargo.toml: {exc}",
            )
        ]

    findings: list[Finding] = []
    for table in CARGO_DEPENDENCY_TABLES:
        deps = data.get(table)
        if isinstance(deps, dict):
            findings.extend(_cargo_deps_unpinned(deps, display_path))
    return findings


# Manifest filename -> the scanner that owns it. One table instead of an
# if/elif chain, so adding a format is one entry and the walk below stays
# format-agnostic. Iteration order fixes the order findings are emitted in.
_SCANNERS = {
    "requirements.txt": scan_requirements_txt,
    "package.json": scan_package_json,
    "Cargo.toml": scan_cargo_toml,
}


def scan_directory(root: Path) -> dict:
    """Walk root, return {"scanned": [...], "findings": [...], "count": N}.

    Raises FileNotFoundError if root does not exist. This function's
    contract is to raise on a missing root; main()'s contract is to catch
    that and format it as the script's one documented error output.
    """
    if not root.exists():
        raise FileNotFoundError(f"directory not found: {root}")
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")

    scanned: list[str] = []
    findings: list[Finding] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        dir_path = Path(dirpath)
        for filename, scanner in _SCANNERS.items():
            if filename in filenames:
                file_path = dir_path / filename
                display_path = str(file_path.relative_to(root))
                scanned.append(display_path)
                findings.extend(scanner(file_path, display_path))

    scanned.sort()
    return {
        "scanned": scanned,
        "findings": [asdict(f) for f in findings],
        "count": len(findings),
    }


def main(argv: list[str]) -> int:
    """CLI entry point. Prints one JSON object to stdout. Returns 0 or 2."""
    directory = argv[0] if argv else "."
    root = Path(directory)
    try:
        result = scan_directory(root)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
