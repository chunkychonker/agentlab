"""Reads a plugin directory once into an immutable ``PluginTree`` snapshot.

The only plugin_check module that touches the filesystem. Everything the
rules need (which paths exist, which are executable, the text of the few
files they parse) is captured here, so the rules stay pure.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from .layout import wants_text
from .snapshot import PluginTree

# Any of the owner, group or other execute bits.
_ANY_EXECUTE_BIT = 0o111


def read_tree(root: Path) -> PluginTree:
    """Snapshot the directory at ``root``.

    Raises ``NotADirectoryError`` if ``root`` is not a directory, and lets any
    other ``OSError`` (a permission error, a file vanishing mid-read)
    propagate; there is no partial result.
    """
    if not root.is_dir():
        raise NotADirectoryError(f"plugin root is not a directory: {root}")
    files: set[str] = set()
    dirs: set[str] = set()
    executable: set[str] = set()
    texts: dict[str, str | None] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            rel = entry.relative_to(root).as_posix()
            if entry.is_dir():
                dirs.add(rel)
                if not entry.is_symlink():
                    pending.append(entry)
                continue
            files.add(rel)
            if entry.exists() and entry.stat().st_mode & _ANY_EXECUTE_BIT:
                executable.add(rel)
            if wants_text(rel):
                texts[rel] = _utf8_or_none(entry.read_bytes())
    return PluginTree(
        files=frozenset(files),
        dirs=frozenset(dirs),
        executable=frozenset(executable),
        texts=MappingProxyType(texts),
    )


def _utf8_or_none(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
