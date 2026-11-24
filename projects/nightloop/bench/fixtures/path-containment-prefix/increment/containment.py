"""Resolve a user-supplied path under a root directory, refusing any path that leaves it."""

from __future__ import annotations

from pathlib import Path


class EscapeError(ValueError):
    """The user path resolves outside the root (or names nothing)."""


def resolve_under(root: Path, user_path: str) -> Path:
    """``root / user_path`` resolved (symlinks followed); ``EscapeError`` if it is outside ``root``."""
    if user_path == "":
        raise EscapeError("empty path names no file")
    base = root.resolve()
    candidate = (base / user_path).resolve()
    if not str(candidate).startswith(str(base)):
        raise EscapeError(f"{user_path!r} resolves outside {base}")
    return candidate
