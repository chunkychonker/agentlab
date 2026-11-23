"""Puts the workflow file in place: the generator's only module that writes to disk.

Idempotent: if the file already holds the same bytes it is not touched (its
mtime stays). Otherwise the text goes to a temp file in the same directory,
which is then ``os.replace``d over the target, so a reader never sees half
a file. The temp file is removed on every failure path.
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

_NEW_FILE_MODE = 0o644
_ENCODING = "utf-8"


@dataclass(frozen=True)
class Written:
    """The file was created or replaced."""

    path: Path


@dataclass(frozen=True)
class Unchanged:
    """The file already held exactly these bytes; nothing was written."""

    path: Path


def install_workflow(path: Path, text: str) -> Written | Unchanged:
    """Make ``path`` hold ``text`` (UTF-8, no newline translation).

    Creates missing parent directories. Keeps an existing file's permission
    bits; a new file gets 0644. Raises ``OSError`` if the existing file
    cannot be read (other than not existing), or a directory, the temp file
    or the replace fails; then the original file is untouched and no temp
    file is left behind (a parent directory made before the failure stays).
    """
    data = text.encode(_ENCODING)
    try:
        if path.read_bytes() == data:
            return Unchanged(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mode = _NEW_FILE_MODE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise
    return Written(path)
