"""Reads the filesystem on behalf of the pure gate functions.

The only gate module that does I/O, and only reads: it never writes, never
follows a symlink while walking, and never reads the clock. Every filesystem
problem becomes a fail-closed outcome (MISSING, or a file that counts as
found but not fresh); none raises.

Walk semantics mirror ``find <root> \\( -name .venv -o ... \\) -prune -o
-type f -name <pattern> -print`` from ``.pipeline/postcondition.sh``:
recursive, basename match, case-sensitive, regular files only (a symlink is
neither descended nor counted), and anything named in ``PRUNED_NAMES`` is
skipped whether it is a directory or a file.
"""

from __future__ import annotations

import os
import stat
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterator, Sequence

from .freshness import classify_freshness, validate_floor
from .outcome import Freshness, Verdict
from .verdict import parse_verdict

# Generated and vendored trees. Re-running an old example's self-test rewrites
# their mtimes, so without the prune a night that built nothing would report a
# fresh increment. `.git` is also a *file* in submodules and worktrees.
PRUNED_NAMES = frozenset({".venv", "__pycache__", "node_modules", ".git"})


def check_verdict(path: Path) -> Verdict:
    """Read the verdict file at ``path`` and parse it.

    ``MISSING`` if ``path`` is absent, unreadable, or not a regular file (a
    directory, a FIFO, a socket); a symlink to a regular file is followed.
    Opened non-blocking so a FIFO cannot hang the gate. Bytes that are not
    UTF-8 are replaced rather than raised on, so they cannot turn a verdict
    into a crash. Never raises for filesystem or content problems.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return Verdict.MISSING
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return Verdict.MISSING
        chunks = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
    except OSError:
        return Verdict.MISSING
    finally:
        os.close(fd)
    return parse_verdict(b"".join(chunks).decode("utf-8", errors="replace"))


def check_freshness(root: Path, pattern: str, floor: int) -> Freshness:
    """Is there a regular file under ``root`` matching ``pattern`` with mtime >= ``floor``?

    ``pattern`` is an ``fnmatch`` pattern applied case-sensitively to each
    basename. ``MISSING`` if ``root`` is absent, unreadable or not a
    directory, or if nothing matches. A subdirectory that cannot be listed is
    skipped (its files are simply not seen, which can never forge FRESH); a
    match whose mtime cannot be read counts as found but not fresh. ``root``
    itself may be a symlink to a directory (the caller named it); symlinks
    below it are neither descended nor counted.

    Raises ``ValueError`` only, and only if ``floor`` is invalid; that check
    runs before any I/O.
    """
    validate_floor(floor)
    return classify_freshness(list(_matching_mtimes(root, pattern)), floor)


def check_increment(roots: Sequence[Path], pattern: str, floor: int) -> Freshness:
    """``check_freshness`` over several roots: FRESH in any one is enough.

    Otherwise ``STALE`` if any root holds only pre-floor matches, else
    ``MISSING``. Only one root is live per mode, so an untouched root must not
    fail the other's fresh work. Roots after the first FRESH one are not read.

    Raises ``ValueError`` only, and only if ``floor`` is invalid; checked
    before any I/O.
    """
    validate_floor(floor)
    saw_stale = False
    for root in roots:
        state = check_freshness(root, pattern, floor)
        if state is Freshness.FRESH:
            return Freshness.FRESH
        saw_stale = saw_stale or state is Freshness.STALE
    return Freshness.STALE if saw_stale else Freshness.MISSING


def _matching_mtimes(root: Path, pattern: str) -> Iterator[float | None]:
    """Yield one mtime (or ``None`` if unreadable) per matching regular file under ``root``."""
    pending = [os.fspath(root)]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                listed = list(entries)
        except OSError:
            continue
        for entry in listed:
            if entry.name in PRUNED_NAMES:
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                pending.append(entry.path)
            elif is_file and fnmatchcase(entry.name, pattern):
                yield _mtime(entry)


def _mtime(entry: os.DirEntry[str]) -> float | None:
    try:
        return entry.stat(follow_symlinks=False).st_mtime
    except OSError:
        return None
