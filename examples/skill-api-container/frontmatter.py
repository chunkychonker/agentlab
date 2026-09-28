"""Parses SKILL.md text into a `SkillMd` that satisfies the API's upload constraints.

Pure: takes a string, returns a value or raises. No filesystem, no network.

The constraints are the ones the Claude API enforces server-side on
`POST /v1/skills` (Agent Skills overview, "Skill structure", fetched
2026-09-28):

- `name`: required, <= 64 chars, lowercase letters / digits / hyphens only
  (so no XML tags are possible), must not contain "anthropic" or "claude".
- `description`: required, non-empty, <= 1024 chars, no XML tags.

Checking them locally means a bad fixture fails before any upload attempt,
with a message naming the exact rule, instead of a generic HTTP 400.

Deliberate limitation: this is a minimal frontmatter reader, not a YAML
parser. It accepts `key: value` lines with plain, single-quoted, or
double-quoted scalars. Block scalars (`|`, `>`) and nested mappings raise
`SkillValidationError` ("unsupported by this validator") rather than being
guessed at, so a valid-YAML-but-unusual SKILL.md fails loudly here rather
than passing with a misread value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NAME_MAX_LEN = 64
DESCRIPTION_MAX_LEN = 1024
RESERVED_NAME_WORDS = ("anthropic", "claude")

_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_XML_TAG_PATTERN = re.compile(r"</?[A-Za-z][^<>]*>")
_FENCE = "---"


class SkillValidationError(ValueError):
    """A SKILL.md violates one specific upload constraint (named in the message)."""


@dataclass(frozen=True)
class SkillMd:
    """A SKILL.md known to satisfy the upload constraints.

    Validation runs in `__post_init__`, so an invalid instance cannot exist:
    constructing one with a bad field raises `SkillValidationError`.
    """

    name: str
    description: str
    body: str

    def __post_init__(self) -> None:
        _check_name(self.name)
        _check_description(self.description)


def parse_skill_md(text: str) -> SkillMd:
    """Split SKILL.md text into frontmatter fields plus body, then validate.

    Raises:
        SkillValidationError: missing/unterminated `---` frontmatter fence; a
            frontmatter line that is not a simple `key: value`; a block scalar
            or nested value (unsupported here); a duplicate key; missing
            `name` or `description`; or any rule in `_check_name` /
            `_check_description`. Nothing is partially returned.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise SkillValidationError("SKILL.md must start with a '---' frontmatter fence")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FENCE)
    except StopIteration:
        raise SkillValidationError("SKILL.md frontmatter has no closing '---' fence") from None

    fields = _parse_fields(lines[1:end])
    for required in ("name", "description"):
        if required not in fields:
            raise SkillValidationError(f"frontmatter is missing required field '{required}'")

    body = "\n".join(lines[end + 1 :])
    return SkillMd(name=fields["name"], description=fields["description"], body=body)


def _parse_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for lineno, raw in enumerate(lines, start=2):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0].isspace():
            raise SkillValidationError(
                f"frontmatter line {lineno}: indented/nested values are unsupported by this validator"
            )
        key, sep, value = raw.partition(":")
        if not sep or not key.strip():
            raise SkillValidationError(f"frontmatter line {lineno}: expected 'key: value', got {raw!r}")
        key = key.strip()
        if key in fields:
            raise SkillValidationError(f"frontmatter line {lineno}: duplicate key '{key}'")
        fields[key] = _unquote(value.strip(), lineno)
    return fields


def _unquote(value: str, lineno: int) -> str:
    if value[:1] in ("|", ">") and value[1:2] in ("", "-", "+"):
        raise SkillValidationError(
            f"frontmatter line {lineno}: block scalars ('|', '>') are unsupported by this validator"
        )
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _check_name(name: str) -> None:
    if not name:
        raise SkillValidationError("name must be non-empty")
    if len(name) > NAME_MAX_LEN:
        raise SkillValidationError(f"name is {len(name)} chars; max is {NAME_MAX_LEN}")
    if not _NAME_PATTERN.match(name):
        raise SkillValidationError(
            f"name {name!r} may contain only lowercase letters, digits, and hyphens"
        )
    for word in RESERVED_NAME_WORDS:
        if word in name:
            raise SkillValidationError(f"name {name!r} must not contain reserved word {word!r}")


def _check_description(description: str) -> None:
    if not description.strip():
        raise SkillValidationError("description must be non-empty")
    if len(description) > DESCRIPTION_MAX_LEN:
        raise SkillValidationError(
            f"description is {len(description)} chars; max is {DESCRIPTION_MAX_LEN}"
        )
    if _XML_TAG_PATTERN.search(description):
        raise SkillValidationError("description must not contain XML tags")
