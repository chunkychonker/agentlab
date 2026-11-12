"""Gives ``GitHubShipper`` its ``read_verdict``: the review verdict file's text, read when called.

The only module in ``nightloop.ship`` that reads a file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def verdict_reader(path: Path) -> Callable[[], str]:
    """A function that returns ``path``'s text (UTF-8, undecodable bytes replaced).

    Pass the same absolute path the review gate read. Raises ``TypeError``/
    ``ValueError`` here if ``path`` is not an absolute ``Path``; the returned
    function raises ``OSError`` if the file cannot be read.
    """
    if not isinstance(path, Path):
        raise TypeError(f"path must be a Path, got {type(path).__name__}")
    if not path.is_absolute():
        raise ValueError(f"path must be absolute, got {str(path)!r}")

    def read() -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    return read
