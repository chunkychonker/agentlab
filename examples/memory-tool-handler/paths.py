"""Turns a model-supplied memory path into a validated virtual path under /memories.

Pure: no filesystem access. This is the first of two security checks; the
second (symlink containment) lives in ``store.DirStore`` because only the real
filesystem can answer it.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote

MEMORY_ROOT = "/memories"

# Guards against pathological nesting; well beyond any sane memory layout.
_MAX_DEPTH = 32


class MemoryPathError(ValueError):
    """A path the model supplied is not a safe location under /memories."""


@dataclass(frozen=True)
class MemoryPath:
    """A validated virtual path. ``parts`` are the segments below /memories.

    Construct only through ``resolve_memory_path`` (or ``child``); both
    validate, so holding a MemoryPath means the path is already safe text.
    """

    parts: tuple[str, ...]

    @property
    def virtual(self) -> str:
        return "/".join((MEMORY_ROOT, *self.parts))

    @property
    def is_root(self) -> bool:
        return not self.parts

    def child(self, name: str) -> "MemoryPath":
        """Path of a direct child. Raises MemoryPathError if ``name`` is unsafe."""
        return resolve_memory_path(f"{self.virtual}/{name}")

    def is_within(self, other: "MemoryPath") -> bool:
        """True if self equals ``other`` or lies underneath it."""
        return self.parts[: len(other.parts)] == other.parts

    def ancestors(self) -> tuple["MemoryPath", ...]:
        """Proper ancestors from the root down, excluding self."""
        return tuple(MemoryPath(self.parts[:i]) for i in range(len(self.parts)))


def resolve_memory_path(raw: str) -> MemoryPath:
    """Validate ``raw`` and return it as a MemoryPath.

    Accepts ``/memories``, ``/memories/``, and ``/memories/<seg>/...``; repeated
    slashes collapse.

    Raises MemoryPathError (never returns a fallback) when ``raw`` is not a
    string, is empty, contains NUL or other control characters, contains a
    backslash, contains any percent-encoding (rejected outright so the handler
    and storage can never disagree about what the path means), is not under the
    ``/memories`` prefix (``/memoriesX`` included), contains a ``.`` or ``..``
    segment, or nests deeper than _MAX_DEPTH.
    """
    if not isinstance(raw, str):
        raise MemoryPathError(f"path must be a string, got {type(raw).__name__}")
    if raw == "":
        raise MemoryPathError("path must not be empty")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        raise MemoryPathError(f"path {raw!r} contains a control character")
    if "\\" in raw:
        raise MemoryPathError(f"path {raw!r} contains a backslash")
    if unquote(raw) != raw:
        raise MemoryPathError(f"path {raw!r} is percent-encoded; use a plain path")
    if raw != MEMORY_ROOT and not raw.startswith(MEMORY_ROOT + "/"):
        raise MemoryPathError(f"path {raw!r} is outside {MEMORY_ROOT}")
    parts = tuple(seg for seg in raw[len(MEMORY_ROOT):].split("/") if seg)
    for seg in parts:
        if seg in (".", ".."):
            raise MemoryPathError(f"path {raw!r} contains a '{seg}' segment")
    if len(parts) > _MAX_DEPTH:
        raise MemoryPathError(f"path {raw!r} nests deeper than {_MAX_DEPTH} levels")
    return MemoryPath(parts)
