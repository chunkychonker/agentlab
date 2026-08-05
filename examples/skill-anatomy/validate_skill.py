"""Offline validator for a Claude Code `SKILL.md` file's frontmatter.

Checks the documented, checkable rules for `name` and `description` from the
Agent Skills docs (see the research note this ships with for citations), plus
one soft best-practice check on body length. Does not call the model and does
not check whether a skill actually *triggers* on a given prompt — that is a
live, model-dependent behavior the docs themselves say is only checkable by
running Claude and comparing skill-on vs. skill-off (see README).

Only flat `key: value` frontmatter is supported (no lists/nested maps) since
that is all a Claude Code `SKILL.md`'s frontmatter needs for `name` and
`description`. No external YAML dependency.

Usage:
    python validate_skill.py path/to/SKILL.md

Exit code 0 iff there are zero "error"-level findings (warnings do not fail
the run).

Failure modes:
    - A missing file: `FileNotFoundError` propagates unchanged from
      `validate_skill` (a legitimate boundary failure — the caller asked for
      a path that does not exist, this is not something to paper over).
    - A file that does not start with a `---`-delimited frontmatter block:
      never raises. `validate_skill` catches this and returns a single
      `error` Finding naming the problem, because a skill with malformed
      frontmatter is a real, common authoring mistake this tool exists to
      catch, not a programmer error in the caller.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MAX_BODY_LINES = 500
RESERVED_NAME_SUBSTRINGS = ("anthropic", "claude")
ALLOWED_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning"
    field: str  # "name" | "description" | "body"
    message: str


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split SKILL.md into (frontmatter dict, body).

    The frontmatter is the flat `key: value` block between the first two
    lines that are exactly `---`. Everything after the closing `---` is the
    body, verbatim.

    Failure modes: raises ValueError if the file does not start with a
    '---'-delimited block (never silently returns an empty dict).
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("file does not start with a '---' frontmatter delimiter")

    closing_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_index = i
            break
    if closing_index is None:
        raise ValueError("frontmatter block is missing its closing '---' delimiter")

    frontmatter: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"frontmatter line is not 'key: value': {line!r}")
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = value.strip()

    body = "\n".join(lines[closing_index + 1 :])
    return frontmatter, body


def validate_name(name: str | None) -> list[Finding]:
    if name is None:
        return [Finding("error", "name", "missing 'name' field")]

    findings: list[Finding] = []
    if len(name) > MAX_NAME_LEN:
        findings.append(
            Finding(
                "error",
                "name",
                f"'{name}' is {len(name)} chars, exceeds the {MAX_NAME_LEN}-char limit",
            )
        )
    if any(ch not in ALLOWED_NAME_CHARS for ch in name):
        findings.append(
            Finding(
                "error",
                "name",
                f"'{name}' contains characters outside [a-z0-9-]",
            )
        )
    if "<" in name or ">" in name:
        findings.append(Finding("error", "name", f"'{name}' contains an XML tag character"))
    lowered = name.lower()
    for reserved in RESERVED_NAME_SUBSTRINGS:
        if reserved in lowered:
            findings.append(
                Finding(
                    "error",
                    "name",
                    f"'{name}' contains the reserved word '{reserved}'",
                )
            )
    return findings


def validate_description(description: str | None) -> list[Finding]:
    if description is None or description == "":
        return [Finding("error", "description", "missing or empty 'description' field")]

    findings: list[Finding] = []
    if len(description) > MAX_DESCRIPTION_LEN:
        findings.append(
            Finding(
                "error",
                "description",
                f"description is {len(description)} chars, exceeds the "
                f"{MAX_DESCRIPTION_LEN}-char limit",
            )
        )
    if "<" in description or ">" in description:
        findings.append(Finding("error", "description", "description contains an XML tag character"))
    if description.startswith("I ") or "You can" in description:
        findings.append(
            Finding(
                "warning",
                "description",
                "description may not be third-person (starts with 'I ' or contains 'You can') "
                "— this is a heuristic, not a hard rule",
            )
        )
    return findings


def validate_body(body: str) -> list[Finding]:
    line_count = len(body.split("\n"))
    if line_count > MAX_BODY_LINES:
        return [
            Finding(
                "warning",
                "body",
                f"body is {line_count} lines, exceeds the recommended {MAX_BODY_LINES}-line soft cap",
            )
        ]
    return []


def validate_skill(path: Path) -> list[Finding]:
    """Read, parse, and validate a SKILL.md file.

    Failure modes: FileNotFoundError propagates unchanged (boundary
    failure). A malformed frontmatter block produces one error Finding,
    never an exception.
    """
    text = path.read_text()

    try:
        frontmatter, body = parse_frontmatter(text)
    except ValueError as exc:
        return [Finding("error", "body", f"malformed frontmatter: {exc}")]

    findings: list[Finding] = []
    findings.extend(validate_name(frontmatter.get("name")))
    findings.extend(validate_description(frontmatter.get("description")))
    findings.extend(validate_body(body))
    return findings


def main(argv: list[str]) -> int:
    """CLI entry point. Returns 0 iff no error-level findings."""
    if len(argv) != 1:
        print("usage: python validate_skill.py path/to/SKILL.md", file=sys.stderr)
        return 2

    path = Path(argv[0])
    findings = validate_skill(path)

    for finding in findings:
        print(f"{finding.level:7} {finding.field:12} {finding.message}")

    error_count = sum(1 for f in findings if f.level == "error")
    if not findings:
        print("ok  no findings")
    print(f"\n{error_count} error(s), {len(findings) - error_count} warning(s)")
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
