"""``PluginTree``: an immutable snapshot of what a plugin directory held.

Pure: no I/O. ``tree.read_tree`` builds it; ``rules.check_tree`` reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PluginTree:
    """What a plugin directory held when it was read.

    Paths are POSIX, relative to the plugin root, without a leading ``./``.
    ``texts`` holds the files ``layout.wants_text`` names; a value is ``None``
    when the file is not valid UTF-8. A symlink is listed under the kind of
    its target and is never descended into; a dangling one is a file.
    """

    files: frozenset[str]
    dirs: frozenset[str]
    executable: frozenset[str]
    texts: Mapping[str, str | None]
