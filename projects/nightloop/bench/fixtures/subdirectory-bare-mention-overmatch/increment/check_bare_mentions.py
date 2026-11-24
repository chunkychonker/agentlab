"""Toy skill reference-file checker.

Rules checked:
  - one-level-deep: any file referenced from SKILL.md must not itself
    reference another local .md file.
  - broken-link: any referenced .md file must exist under the skill dir.

Detection scans SKILL.md's raw text for two signal types: markdown links
[text](path) and bare filename mentions matching [\\w-]+\\.md anywhere in
the text (so a plain-prose mention of a filename also counts as a
reference). Bare mentions are resolved against the skill directory root.

Usage:
    python3 check_bare_mentions.py path/to/skill_dir/SKILL.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_BARE_MD_RE = re.compile(r"\b[\w-]+\.md\b")


def find_references(markdown_text: str) -> set[str]:
    """Bare .md filenames this text references, from markdown-link targets
    and bare filename mentions."""
    refs: set[str] = set()
    for m in _MD_LINK_RE.finditer(markdown_text):
        target = m.group(1).strip()
        refs.add(Path(target).name)
    for m in _BARE_MD_RE.finditer(markdown_text):
        refs.add(m.group(0))
    return refs


def check_skill(skill_dir: Path) -> list[str]:
    """Return a list of human-readable error strings; empty means clean."""
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text()
    errors: list[str] = []

    for name in sorted(find_references(text)):
        resolved = skill_dir / name
        if not resolved.is_file():
            errors.append(f"referenced file does not exist: {resolved}")
            continue
        if resolved != skill_md:
            nested_text = resolved.read_text()
            for nested_name in sorted(find_references(nested_text)):
                errors.append(
                    f"one-level-deep violation: {name} itself references "
                    f"'{nested_name}' — reference files must link "
                    f"directly from SKILL.md only"
                )
        else:
            for nested_name in sorted(find_references(text)):
                errors.append(
                    f"one-level-deep violation: SKILL.md itself references "
                    f"'{nested_name}' — reference files must link "
                    f"directly from SKILL.md only"
                )
    return errors


def main() -> int:
    target = Path(sys.argv[1])
    skill_dir = target.parent if target.name == "SKILL.md" else target
    errors = check_skill(skill_dir)
    for e in errors:
        print(f"error\t{e}")
    print(f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
