"""The benchmark fixture value type and the ``expect.toml`` parse.

Pure: no I/O (``tomllib.loads`` parses a string it is handed). A ``Fixture``
that exists is valid however it was built, so the loader, the workspace and
the classifier never re-check it.

A fixture on disk is ``<fixtures>/<slug>/`` holding exactly ``expect.toml``,
``note.md`` and ``increment/``; ``nightloop.bench.load`` reads it.
"""

from __future__ import annotations

import enum
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Iterable, Mapping

EXPECT_NAME = "expect.toml"
NOTE_NAME = "note.md"
INCREMENT_DIR = "increment"

# The temp repo's own layout (see ``nightloop.bench.workspace``). An increment
# that wrote any of these would overwrite the note, the agent template, the
# ignore rules or the verdict, so such a path is not representable.
RESERVED_TOP_LEVEL = frozenset({".git", ".claude", ".gitignore", "logs", "research"})

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
_EXPECT_KEYS = frozenset({"kind", "patterns"})


class Kind(enum.Enum):
    """What a fixture's increment is: a diff with a planted bug, or a correct one."""

    SEEDED = "seeded"
    CLEAN = "clean"


class FixtureError(ValueError):
    """One fixture is invalid. ``slug`` and ``field`` name where; ``str(err)`` says both."""

    def __init__(self, slug: str, field: str, reason: str) -> None:
        super().__init__(f"fixture {slug!r}: {field}: {reason}")
        self.slug = slug
        self.field = field
        self.reason = reason


class MatrixError(ValueError):
    """The set of fixtures cannot be benchmarked (no seeded or no clean one, a repeated slug, no fixtures dir)."""


@dataclass(frozen=True)
class Fixture:
    """One benchmark case: the research note the reviewer reads and the increment it reviews.

    ``patterns`` are regexes that must all appear (case-insensitive) in a
    FAIL verdict for a seeded fixture to count as caught. ``files`` maps
    repo-relative paths to bytes; it is copied into a read-only mapping.

    Raises ``FixtureError`` (with this fixture's slug) if: ``slug`` does not
    match ``[a-z0-9][a-z0-9-]*``; ``kind`` is not a ``Kind``; a seeded
    fixture has no pattern or a clean one has any; a pattern is not a
    string, is blank, or does not compile; ``note`` is blank; ``files`` is
    empty; or a path is absolute, empty, holds a ``.`` / ``..`` / blank
    segment, or starts with a name in ``RESERVED_TOP_LEVEL``; or a content
    is not ``bytes``.
    """

    slug: str
    kind: Kind
    patterns: tuple[str, ...]
    note: str
    files: Mapping[PurePosixPath, bytes]

    def __post_init__(self) -> None:
        slug = self.slug
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise FixtureError(str(slug), "slug", f"must match {_SLUG.pattern}")
        if not isinstance(self.kind, Kind):
            raise FixtureError(slug, "kind", f"must be a Kind, got {type(self.kind).__name__}")
        if not isinstance(self.patterns, tuple):
            raise FixtureError(slug, "patterns", "must be a tuple of regexes")
        if self.kind is Kind.SEEDED and not self.patterns:
            raise FixtureError(slug, "patterns", "a seeded fixture needs at least one pattern")
        if self.kind is Kind.CLEAN and self.patterns:
            raise FixtureError(slug, "patterns", "a clean fixture must have no patterns")
        for i, pattern in enumerate(self.patterns):
            _check_pattern(slug, f"patterns[{i}]", pattern)
        if not isinstance(self.note, str) or self.note.strip() == "":
            raise FixtureError(slug, NOTE_NAME, "must be non-empty text")
        if not isinstance(self.files, Mapping) or not self.files:
            raise FixtureError(slug, INCREMENT_DIR, "must hold at least one file")
        for path, content in self.files.items():
            _check_file(slug, path, content)
        object.__setattr__(self, "files", MappingProxyType(dict(sorted(self.files.items()))))


def parse_expect(slug: str, text: str) -> tuple[Kind, tuple[str, ...]]:
    """Parse ``expect.toml`` text into ``(kind, patterns)``.

    The document has ``kind = "seeded" | "clean"`` and, for seeded only,
    ``patterns = [regex, ...]``. Whether a seeded fixture has at least one
    pattern, and whether each compiles, is checked by ``Fixture``.

    Raises ``FixtureError(slug, field, reason)``: field ``expect.toml`` for
    text that is not TOML; ``expect.toml.<key>`` for an unknown key;
    ``kind`` if it is missing, not a string, or not one of the two values;
    ``patterns`` if it is not a list of strings, or is present on a clean
    fixture.
    """
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise FixtureError(slug, EXPECT_NAME, f"not valid TOML: {exc}") from exc
    for key in sorted(set(doc) - _EXPECT_KEYS):
        raise FixtureError(slug, f"{EXPECT_NAME}.{key}", "unknown key")
    if "kind" not in doc:
        raise FixtureError(slug, "kind", "missing")
    raw_kind = doc["kind"]
    values = {k.value: k for k in Kind}
    if not isinstance(raw_kind, str) or raw_kind not in values:
        raise FixtureError(slug, "kind", f"must be one of {', '.join(sorted(values))}, got {raw_kind!r}")
    kind = values[raw_kind]
    if kind is Kind.CLEAN and "patterns" in doc:
        raise FixtureError(slug, "patterns", "a clean fixture must have no patterns key")
    raw = doc.get("patterns", [])
    if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
        raise FixtureError(slug, "patterns", "must be a list of strings")
    return kind, tuple(raw)


def require_both_kinds(fixtures: Iterable[Fixture]) -> None:
    """Refuse a fixture set that would report one axis alone, or names a fixture twice.

    Raises ``MatrixError`` if there is no seeded fixture, no clean fixture,
    or two fixtures share a slug.
    """
    seen: set[str] = set()
    kinds: set[Kind] = set()
    for fixture in fixtures:
        if fixture.slug in seen:
            raise MatrixError(f"fixture slug {fixture.slug!r} appears twice")
        seen.add(fixture.slug)
        kinds.add(fixture.kind)
    for kind in Kind:
        if kind not in kinds:
            raise MatrixError(
                f"no {kind.value} fixture: the catch rate and the false-positive rate "
                "are only reported together"
            )


def _check_pattern(slug: str, field: str, pattern: object) -> None:
    if not isinstance(pattern, str):
        raise FixtureError(slug, field, f"must be a string, got {type(pattern).__name__}")
    if pattern.strip() == "":
        # An empty regex matches every verdict, so every FAIL would count as caught.
        raise FixtureError(slug, field, "must not be blank")
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise FixtureError(slug, field, f"invalid regex: {exc}") from exc


def _check_file(slug: str, path: object, content: object) -> None:
    if not isinstance(path, PurePosixPath):
        raise FixtureError(slug, INCREMENT_DIR, f"paths must be PurePosixPath, got {type(path).__name__}")
    field = f"{INCREMENT_DIR}/{path}"
    if path.is_absolute():
        raise FixtureError(slug, field, "must be relative")
    # PurePosixPath drops "." segments and keeps "..", so check the raw text too.
    segments = str(path).split("/")
    if not path.parts or any(s in ("", ".", "..") or s.strip() == "" for s in segments):
        raise FixtureError(slug, field, "must not escape the increment (no '.', '..' or empty segment)")
    if path.parts[0] in RESERVED_TOP_LEVEL:
        raise FixtureError(slug, field, f"{path.parts[0]!r} is reserved for the temp repo's own files")
    if not isinstance(content, bytes):
        raise FixtureError(slug, field, f"content must be bytes, got {type(content).__name__}")
