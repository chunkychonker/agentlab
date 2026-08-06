"""Scan a directory for requirements.txt/package.json and report unpinned deps.

Walks a directory tree looking for `requirements.txt` and `package.json`
manifests, and flags any dependency entry that is not pinned to an exact
version. "Pinned" for `requirements.txt` means an `==` exact specifier; for
`package.json` it means a version string that is not a caret/tilde/wildcard
range and not the literal string "latest".

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
    - No manifests found anywhere under root: not an error. Returns valid
      JSON with an empty findings list and count 0, exit 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
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
        for filename in ("requirements.txt", "package.json"):
            if filename in filenames:
                file_path = dir_path / filename
                display_path = str(file_path.relative_to(root))
                scanned.append(display_path)
                if filename == "requirements.txt":
                    findings.extend(scan_requirements_txt(file_path, display_path))
                else:
                    findings.extend(scan_package_json(file_path, display_path))

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
