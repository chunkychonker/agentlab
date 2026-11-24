"""Reads benchmark fixtures from disk into ``Fixture`` values.

The only bench module that reads the fixtures directory, and it only reads.
Symlinks are never followed: a fixture's files must be its own.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath

from .fixture import EXPECT_NAME, INCREMENT_DIR, NOTE_NAME, Fixture, FixtureError, MatrixError, parse_expect

# Tool droppings skipped wherever they appear (a Finder index, a test run's
# bytecode). Anything else unexpected is an error, not skipped.
IGNORED_NAMES = frozenset({".DS_Store", "__pycache__"})
_FIXTURE_ENTRIES = frozenset({EXPECT_NAME, NOTE_NAME, INCREMENT_DIR})


def load_fixtures(root: Path) -> tuple[Fixture, ...]:
    """Every fixture under ``root``, sorted by slug (the directory name).

    Each ``<root>/<slug>/`` must hold exactly ``expect.toml``, ``note.md``
    and ``increment/`` (plus ``IGNORED_NAMES``). Whether both kinds are
    present is checked by the caller (``require_both_kinds``), so one
    fixture can be loaded on its own.

    Raises ``MatrixError`` if ``root`` is not a directory, holds a
    non-directory entry, or holds no fixture. Raises ``FixtureError(slug,
    field, reason)`` for: a fixture dir or any entry in it that is a
    symlink; a missing or unknown entry; anything in ``increment/`` that is
    a symlink or not a regular file or directory; text that is not UTF-8;
    and everything ``parse_expect`` and ``Fixture`` reject. ``OSError``
    from reading propagates.
    """
    if not root.is_dir():
        raise MatrixError(f"fixtures dir {str(root)!r} is not a directory")
    fixtures = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name in IGNORED_NAMES:
            continue
        mode = entry.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise FixtureError(entry.name, "<dir>", "is a symlink")
        if not stat.S_ISDIR(mode):
            raise MatrixError(f"{str(entry)!r} is not a fixture directory")
        fixtures.append(_load_one(entry))
    if not fixtures:
        raise MatrixError(f"no fixtures under {str(root)!r}")
    return tuple(fixtures)


def _load_one(fixture_dir: Path) -> Fixture:
    slug = fixture_dir.name
    names = {p.name for p in fixture_dir.iterdir()} - IGNORED_NAMES
    for name in sorted(names - _FIXTURE_ENTRIES):
        raise FixtureError(slug, name, "unknown entry (a fixture holds expect.toml, note.md and increment/)")
    for name in sorted(_FIXTURE_ENTRIES - names):
        raise FixtureError(slug, name, "missing")
    for name in sorted(_FIXTURE_ENTRIES):
        if stat.S_ISLNK((fixture_dir / name).lstat().st_mode):
            raise FixtureError(slug, name, "is a symlink")
    if not (fixture_dir / INCREMENT_DIR).is_dir():
        raise FixtureError(slug, INCREMENT_DIR, "must be a directory")
    for name in (EXPECT_NAME, NOTE_NAME):
        if not (fixture_dir / name).is_file():
            raise FixtureError(slug, name, "must be a regular file")
    kind, patterns = parse_expect(slug, _text(slug, EXPECT_NAME, fixture_dir / EXPECT_NAME))
    note = _text(slug, NOTE_NAME, fixture_dir / NOTE_NAME)
    files = _walk(slug, fixture_dir / INCREMENT_DIR)
    return Fixture(slug=slug, kind=kind, patterns=patterns, note=note, files=files)


def _text(slug: str, field: str, path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FixtureError(slug, field, f"is not UTF-8: {exc}") from exc


def _walk(slug: str, top: Path) -> dict[PurePosixPath, bytes]:
    """Every regular file under ``top`` by its path relative to ``top``; raises on a symlink or a special file."""
    files: dict[PurePosixPath, bytes] = {}
    pending = [top]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                if entry.name in IGNORED_NAMES:
                    continue
                path = Path(entry.path)
                rel = PurePosixPath(path.relative_to(top).as_posix())
                field = f"{INCREMENT_DIR}/{rel}"
                if entry.is_symlink():
                    raise FixtureError(slug, field, "is a symlink")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files[rel] = path.read_bytes()
                else:
                    raise FixtureError(slug, field, "is not a regular file or directory")
    return files
