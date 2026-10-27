"""Virtual memory paths: the only way a model-supplied string becomes a path.

A ``MemoryPath`` is a tuple of validated name segments under the fixed virtual
root ``/memories``. It cannot hold ``..``, ``.``, an empty segment, a slash, a
backslash or a NUL, because ``__post_init__`` rejects them -- so every store
that receives a ``MemoryPath`` can join its segments without re-checking for
traversal. ``resolve_memory_path`` is the boundary parser that produces one.

Pure: no filesystem, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote

MEMORY_ROOT = "/memories"

# Characters that may never appear in a segment. "/" is the separator; "\\" is a
# separator on Windows and a classic traversal smuggle ("..\\..\\x"); NUL truncates
# C-level paths.
_FORBIDDEN_CHARS = ("/", "\\", "\x00")
_FORBIDDEN_SEGMENTS = (".", "..")


class MemoryPathError(ValueError):
    """A path is not a valid location under /memories."""


@dataclass(frozen=True)
class MemoryPath:
    """A validated location under ``/memories``. ``parts == ()`` is the root.

    Raises:
        MemoryPathError: if any segment is empty, ``.``/``..``, or contains a
            forbidden character. Construction is the validation; there is no
            way to hold an unvalidated instance.
    """

    parts: tuple[str, ...]

    def __post_init__(self) -> None:
        for seg in self.parts:
            if not isinstance(seg, str) or seg == "":
                raise MemoryPathError("empty path segment")
            if seg in _FORBIDDEN_SEGMENTS:
                raise MemoryPathError(f"path segment {seg!r} is not allowed")
            for ch in _FORBIDDEN_CHARS:
                if ch in seg:
                    raise MemoryPathError(f"path segment contains forbidden character {ch!r}")

    @property
    def is_root(self) -> bool:
        return self.parts == ()

    @property
    def name(self) -> str:
        """Last segment. Raises MemoryPathError on the root, which has no name."""
        if self.is_root:
            raise MemoryPathError("the root has no name")
        return self.parts[-1]

    @property
    def parent(self) -> MemoryPath:
        """Containing directory. Raises MemoryPathError on the root."""
        if self.is_root:
            raise MemoryPathError("the root has no parent")
        return MemoryPath(self.parts[:-1])

    def ancestors(self) -> tuple[MemoryPath, ...]:
        """Every proper ancestor, root first. Empty for the root."""
        return tuple(MemoryPath(self.parts[:i]) for i in range(len(self.parts)))

    def child(self, name: str) -> MemoryPath:
        """``self / name``. Raises MemoryPathError if ``name`` is not a valid segment."""
        return MemoryPath(self.parts + (name,))

    def is_within(self, other: MemoryPath) -> bool:
        """True if ``self`` equals ``other`` or lies underneath it."""
        return self.parts[: len(other.parts)] == other.parts

    def __str__(self) -> str:
        return MEMORY_ROOT if self.is_root else MEMORY_ROOT + "/" + "/".join(self.parts)


ROOT = MemoryPath(())


def resolve_memory_path(raw: object) -> MemoryPath:
    """Parse a model-supplied path string into a ``MemoryPath``.

    Accepts ``/memories``, ``/memories/``, and ``/memories/<seg>[/<seg>...]`` with
    an optional single trailing slash. Nothing is normalized: a path containing
    ``..`` is rejected, not collapsed, so a caller can never be surprised by
    where a path "really" points.

    Raises:
        MemoryPathError: if ``raw`` is not a string, is empty, contains a NUL or
            backslash, contains any valid percent-escape (``%2e%2e%2f`` and
            friends are rejected rather than decoded), does not start with the
            ``/memories`` segment (``/memoriesX`` is rejected), or contains an
            empty / ``.`` / ``..`` segment.
    """
    if not isinstance(raw, str):
        raise MemoryPathError(f"path must be a string, got {type(raw).__name__}")
    if raw == "":
        raise MemoryPathError("path is empty")
    if "\x00" in raw:
        raise MemoryPathError("path contains a NUL byte")
    if "\\" in raw:
        raise MemoryPathError("path contains a backslash")
    # Reject rather than decode: one decode pass invites double-encoding tricks,
    # and a legitimate memory file name has no reason to contain an escape.
    # unquote() leaves invalid escapes alone, so "50%.txt" is still allowed.
    if unquote(raw) != raw:
        raise MemoryPathError("path contains percent-encoding")
    if raw != MEMORY_ROOT and not raw.startswith(MEMORY_ROOT + "/"):
        raise MemoryPathError(f"path must be {MEMORY_ROOT} or start with {MEMORY_ROOT}/")

    rest = raw[len(MEMORY_ROOT):]
    if rest.endswith("/"):
        rest = rest[:-1]
    if rest == "":
        return ROOT
    # rest starts with "/" here; split off the leading empty element.
    return MemoryPath(tuple(rest.split("/")[1:]))
