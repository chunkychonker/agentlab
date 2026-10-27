"""The storage contract the command dispatcher depends on.

The core (``commands.py``) declares what it needs from storage; the edges
(``memory_store.py``, ``dir_store.py``) implement it. Every method takes a
``MemoryPath``, which is already traversal-free by construction.

Preconditions are the caller's job: ``commands.py`` checks them and turns a
violation into an ``is_error`` result for the model. A store that receives a
call violating its preconditions raises -- that is a bug in the caller, not a
model mistake, and it must be loud.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from paths import MemoryPath


class NodeKind(Enum):
    FILE = "file"
    DIR = "dir"
    MISSING = "missing"


class Store(Protocol):
    """Hierarchical text storage rooted at ``/memories``. The root always exists."""

    def kind(self, path: MemoryPath) -> NodeKind:
        """What is at ``path``. Never raises for a missing path (that is ``MISSING``).

        Implementations may raise ``paths.MemoryPathError`` if ``path`` maps to
        something outside the store (e.g. a symlink escape).
        """
        ...

    def read_text(self, path: MemoryPath) -> str:
        """Contents of a file. Precondition: ``kind(path) is FILE``."""
        ...

    def write_text(self, path: MemoryPath, text: str) -> None:
        """Create or overwrite a file, creating missing parent directories.

        Precondition: ``kind(path)`` is ``FILE`` or ``MISSING`` and no ancestor
        is a ``FILE``.
        """
        ...

    def children(self, path: MemoryPath) -> list[str]:
        """Names directly inside a directory, sorted. Precondition: ``kind(path) is DIR``."""
        ...

    def size(self, path: MemoryPath) -> int:
        """UTF-8 byte length of a file, or the recursive sum of a directory's files.

        Precondition: ``kind(path)`` is not ``MISSING``.
        """
        ...

    def delete(self, path: MemoryPath) -> None:
        """Remove a file, or a directory and everything under it.

        Precondition: ``path`` is not the root and ``kind(path)`` is not ``MISSING``.
        """
        ...

    def rename(self, old: MemoryPath, new: MemoryPath) -> None:
        """Move a file or directory, creating missing parents of ``new``.

        Precondition: neither is the root, ``old`` exists, ``new`` is
        ``MISSING``, ``new`` is not inside ``old``, and no ancestor of ``new``
        is a ``FILE``.
        """
        ...
