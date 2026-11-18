"""Reads and atomically replaces the backlog file: the only claims module that touches the filesystem.

Implements ``BacklogStore``. Text is read and written as UTF-8 with no
newline translation, so a file is handed back byte for byte. A write goes
to a temp file in the same directory and is moved over the original with
``os.replace``, so a reader never sees half a file and a failed write
leaves the original as it was.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path, PurePosixPath

DEFAULT_BACKLOG = "BACKLOG.md"


class BacklogFile:
    """The backlog at ``repo / relative``.

    ``relative`` is also the git pathspec ``GitClaimSource`` diffs, so the
    file that is edited and the file that is read from git are one path.
    Raises ``TypeError``/``ValueError`` if ``repo`` is not an absolute
    ``Path`` or ``relative`` is not a plain relative path inside it
    (no ``..``, not absolute, not empty).
    """

    def __init__(self, repo: Path, relative: str = DEFAULT_BACKLOG) -> None:
        if not isinstance(repo, Path):
            raise TypeError(f"repo must be a Path, got {type(repo).__name__}")
        if not repo.is_absolute():
            raise ValueError(f"repo must be absolute, got {str(repo)!r}")
        if not isinstance(relative, str):
            raise TypeError(f"relative must be a str, got {type(relative).__name__}")
        parts = PurePosixPath(relative).parts
        if relative.strip() == "" or relative.startswith("/") or ".." in parts or "\\" in relative:
            raise ValueError(f"relative must be a path inside the repo, got {relative!r}")
        self._repo = repo
        self._relative = str(PurePosixPath(relative))
        self._path = repo.joinpath(*parts)

    @property
    def repo(self) -> Path:
        return self._repo

    @property
    def relative(self) -> str:
        """The path from the repo's top level, with ``/`` separators."""
        return self._relative

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> str:
        """The whole file. Raises ``OSError`` if it is missing or unreadable, ``UnicodeDecodeError`` if not UTF-8."""
        with self._path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()

    def write(self, text: str) -> None:
        """Replace the file with ``text``, keeping its permission bits.

        The file must already exist. Raises ``TypeError`` for a non-str and
        ``OSError`` if the file is missing or the temp file cannot be
        written or moved into place; the temp file is removed on every
        failure path, and the original is untouched.
        """
        if not isinstance(text, str):
            raise TypeError(f"text must be a str, got {type(text).__name__}")
        mode = stat.S_IMODE(os.stat(self._path).st_mode)
        fd, temp = tempfile.mkstemp(dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.chmod(temp, mode)
            os.replace(temp, self._path)
        except BaseException:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
            raise
