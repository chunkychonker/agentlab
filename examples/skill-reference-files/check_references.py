"""Validate a skill's Level-3 reference-file structure against documented
Agent Skills authoring rules (broken links, nesting depth, TOC presence,
path style, naming).

Reference detection is a heuristic: a local file is considered "referenced"
by a markdown body if it appears as a markdown link [text](path), or if its
filename appears as a bare token in the body text. The latter can false-
positive on an unrelated prose mention of a filename; this is a stated,
accepted limitation, not a silent assumption. See the module's
extract_local_references for exactly what counts as a "mention".

Checks (each producing zero or more Findings):
  - broken-link (error): a markdown-link path that doesn't resolve to a
    real file under the skill directory.
  - path-escapes-skill-dir (error): a link path that resolves (via `..`)
    outside the skill directory root. The checker computes this with pure
    string manipulation (os.path.normpath) and never calls open()/read_text()
    on a path once it is known to fall outside the skill directory.
  - nested-reference (error): a file referenced from SKILL.md that itself
    contains a local reference to a *different* .md file under the skill
    directory (violates the documented "keep references one level deep"
    rule).
  - missing-toc (warning): a referenced file exceeding 100 lines with no
    line matching `^#+\\s*(table of )?contents` (case-insensitive) in its
    first 15 lines.
  - windows-path (error): a markdown-link path containing a backslash.
  - generic-filename (warning): a bundled file named `doc<N>.md`,
    `file<N>.md`, `notes.md`, or `misc.md`.

Exit code (main / CLI): 0 iff there are zero error-level findings.

Failure modes:
  - Missing SKILL.md at the given skill directory: FileNotFoundError
    propagates unchanged (boundary failure, never swallowed).
  - A link path that resolves outside the skill directory: reported as a
    path-escapes-skill-dir Finding and never opened/read — this is the one
    filesystem boundary the checker itself must not cross, since a skill's
    own markdown is untrusted input.

Usage:
    python3 check_references.py path/to/skill_dir
"""
from __future__ import annotations

import dataclasses
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SKILL_MD_FILENAME = "SKILL.md"

TOC_LINE_THRESHOLD = 100
TOC_SCAN_WINDOW = 15
TOC_HEADING_PATTERN = re.compile(r"^#+\s*(table of )?contents", re.IGNORECASE)

GENERIC_FILENAME_PATTERN = re.compile(r"^(doc\d+\.md|file\d+\.md|notes\.md|misc\.md)$")

_MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning"
    kind: str   # "broken-link" | "nested-reference" | "missing-toc"
                # | "windows-path" | "generic-filename"
                # | "path-escapes-skill-dir"
    file: str   # path relative to the skill directory
    message: str


def _local_link_targets(markdown_text: str) -> list[str]:
    """Raw path strings from markdown links [text](path), excluding any
    target that starts with http:// or https://.
    """
    targets = []
    for match in _MARKDOWN_LINK_PATTERN.finditer(markdown_text):
        raw = match.group(1).strip()
        if raw.lower().startswith(("http://", "https://")):
            continue
        targets.append(raw)
    return targets


def _resolve_no_io(skill_dir_norm: Path, raw_path: str) -> Path:
    """Normalize raw_path against skill_dir_norm using pure string
    manipulation (os.path.normpath / os.path.join). Never touches the
    filesystem, so it is safe to call on untrusted, possibly-escaping paths.
    """
    return Path(os.path.normpath(os.path.join(str(skill_dir_norm), raw_path)))


def _is_within(skill_dir_norm: Path, candidate: Path) -> bool:
    skill_dir_str = str(skill_dir_norm)
    candidate_str = str(candidate)
    return candidate_str == skill_dir_str or candidate_str.startswith(skill_dir_str + os.sep)


def extract_local_references(markdown_text: str, skill_dir: Path) -> list[Path]:
    """Local files referenced by markdown_text: markdown links (excluding
    http(s) targets) plus bare filename mentions matching a real file under
    skill_dir. Returns resolved, skill_dir-relative Paths, deduplicated in
    first-seen order; never returns a path that has been read from outside
    skill_dir (escaping link targets are silently excluded here — see
    check_path_escapes for reporting them).
    """
    skill_dir_norm = Path(os.path.abspath(str(skill_dir)))
    found: list[Path] = []
    seen: set[Path] = set()

    def _add(rel: Path) -> None:
        if rel not in seen:
            seen.add(rel)
            found.append(rel)

    for raw in _local_link_targets(markdown_text):
        if "\\" in raw:
            continue  # windows-style paths are reported by check_windows_paths, not resolved here
        candidate = _resolve_no_io(skill_dir_norm, raw)
        if not _is_within(skill_dir_norm, candidate):
            continue  # escaping paths are reported by check_path_escapes, never touched here
        if candidate.is_file():
            _add(candidate.relative_to(skill_dir_norm))

    if skill_dir_norm.is_dir():
        for path in sorted(skill_dir_norm.rglob("*")):
            if not path.is_file():
                continue
            name = path.name
            if name == SKILL_MD_FILENAME:
                continue  # a mention of "SKILL.md" is never a reference to another file
            if re.search(r"\b" + re.escape(name) + r"\b", markdown_text):
                _add(path.relative_to(skill_dir_norm))

    return found


def check_broken_links(skill_dir: Path, skill_md_text: str) -> list[Finding]:
    skill_dir_norm = Path(os.path.abspath(str(skill_dir)))
    findings: list[Finding] = []
    for raw in _local_link_targets(skill_md_text):
        if "\\" in raw:
            continue  # reported by check_windows_paths instead
        candidate = _resolve_no_io(skill_dir_norm, raw)
        if not _is_within(skill_dir_norm, candidate):
            continue  # reported by check_path_escapes instead, never touched here
        if not candidate.is_file():
            findings.append(
                Finding(
                    "error",
                    "broken-link",
                    SKILL_MD_FILENAME,
                    f"link path '{raw}' does not resolve to a real file under the skill directory",
                )
            )
    return findings


def check_path_escapes(skill_dir: Path, skill_md_text: str) -> list[Finding]:
    skill_dir_norm = Path(os.path.abspath(str(skill_dir)))
    findings: list[Finding] = []
    for raw in _local_link_targets(skill_md_text):
        if "\\" in raw:
            continue  # backslash paths never traverse via '..' on POSIX; reported separately
        candidate = _resolve_no_io(skill_dir_norm, raw)
        if not _is_within(skill_dir_norm, candidate):
            findings.append(
                Finding(
                    "error",
                    "path-escapes-skill-dir",
                    SKILL_MD_FILENAME,
                    f"link path '{raw}' resolves outside the skill directory (would be {candidate})",
                )
            )
    return findings


def check_one_level_deep(skill_dir: Path, skill_md_text: str) -> list[Finding]:
    skill_dir_norm = Path(os.path.abspath(str(skill_dir)))
    findings: list[Finding] = []
    for rel in extract_local_references(skill_md_text, skill_dir):
        if rel.suffix.lower() != ".md":
            continue
        nested_text = (skill_dir_norm / rel).read_text(encoding="utf-8")
        for nested_rel in extract_local_references(nested_text, skill_dir):
            if nested_rel.suffix.lower() != ".md" or nested_rel == rel:
                continue
            findings.append(
                Finding(
                    "error",
                    "nested-reference",
                    str(rel),
                    f"references '{nested_rel}', a second-level reference not linked "
                    f"directly from {SKILL_MD_FILENAME} (keep references one level deep)",
                )
            )
    return findings


def check_table_of_contents(referenced_file: Path) -> list[Finding]:
    lines = referenced_file.read_text(encoding="utf-8").splitlines()
    if len(lines) <= TOC_LINE_THRESHOLD:
        return []
    header_window = lines[:TOC_SCAN_WINDOW]
    if any(TOC_HEADING_PATTERN.match(line.strip()) for line in header_window):
        return []
    return [
        Finding(
            "warning",
            "missing-toc",
            str(referenced_file),
            f"{len(lines)} lines but no table-of-contents heading in the first "
            f"{TOC_SCAN_WINDOW} lines",
        )
    ]


def check_windows_paths(skill_md_text: str) -> list[Finding]:
    findings: list[Finding] = []
    for raw in _local_link_targets(skill_md_text):
        if "\\" in raw:
            findings.append(
                Finding(
                    "error",
                    "windows-path",
                    SKILL_MD_FILENAME,
                    f"link path '{raw}' contains a backslash; use forward slashes",
                )
            )
    return findings


def check_generic_filenames(skill_dir: Path) -> list[Finding]:
    skill_dir_norm = Path(os.path.abspath(str(skill_dir)))
    findings: list[Finding] = []
    for path in sorted(skill_dir_norm.rglob("*")):
        if path.is_file() and GENERIC_FILENAME_PATTERN.match(path.name):
            rel = path.relative_to(skill_dir_norm)
            findings.append(
                Finding(
                    "warning",
                    "generic-filename",
                    str(rel),
                    f"filename '{path.name}' is generic; name files descriptively "
                    "by domain/feature instead",
                )
            )
    return findings


def check_skill(skill_dir: Path) -> list[Finding]:
    """Run all checks against skill_dir (must contain SKILL.md).

    Failure modes: FileNotFoundError if skill_dir/SKILL.md is missing —
    propagates unchanged, since a caller pointing this at a nonexistent
    skill is a boundary error, not something to paper over.
    """
    skill_dir_norm = Path(os.path.abspath(str(skill_dir)))
    skill_md_text = (skill_dir_norm / SKILL_MD_FILENAME).read_text(encoding="utf-8")

    findings: list[Finding] = []
    findings.extend(check_broken_links(skill_dir_norm, skill_md_text))
    findings.extend(check_path_escapes(skill_dir_norm, skill_md_text))
    findings.extend(check_windows_paths(skill_md_text))
    findings.extend(check_one_level_deep(skill_dir_norm, skill_md_text))
    findings.extend(check_generic_filenames(skill_dir_norm))

    for rel in extract_local_references(skill_md_text, skill_dir_norm):
        if rel.suffix.lower() != ".md":
            continue
        for finding in check_table_of_contents(skill_dir_norm / rel):
            findings.append(dataclasses.replace(finding, file=str(rel)))

    return findings


def main(argv: list[str]) -> int:
    """CLI: check_references.py <skill_dir>. Returns 0 iff no error findings."""
    if len(argv) != 1:
        print("usage: python3 check_references.py path/to/skill_dir", file=sys.stderr)
        return 2

    skill_dir = Path(argv[0])
    findings = check_skill(skill_dir)

    for finding in findings:
        print(f"{finding.level:7} {finding.kind:22} {finding.file:40} {finding.message}")

    error_count = sum(1 for f in findings if f.level == "error")
    warning_count = len(findings) - error_count
    if not findings:
        print("ok  no findings")
    print(f"\n{error_count} error(s), {warning_count} warning(s)")
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
