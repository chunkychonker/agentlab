"""Reads and appends the ``metrics.jsonl`` file.

The only module in ``nightloop.cli`` that touches the metrics file. Lines go
in append mode, one ``write`` per line, and the file is closed (so flushed)
after each, so a crash mid-night tears at most the line being written.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

_ENCODING = "utf-8"


class MetricsFileError(RuntimeError):
    """The metrics file could not be read. The message names the path."""


def append_sink(path: Path) -> Callable[[str], None]:
    """A metrics sink that appends each line, plus ``\\n``, to ``path``.

    Nothing is touched until the first line arrives. Then ``path``'s parent
    directory is created if it is missing. The returned sink raises
    ``ValueError`` for a line that holds a newline (it would become two
    records) and ``OSError`` if the directory or file cannot be written.
    Neither is caught here: a lost metrics line is an error, not a default.
    """

    def emit(line: str) -> None:
        if "\n" in line or "\r" in line:
            raise ValueError("a metrics line must not contain a newline")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding=_ENCODING) as fp:
            fp.write(line + "\n")

    return emit


def read_lines(path: Path) -> list[str]:
    """Every line of ``path``, without line endings.

    Bytes that are not UTF-8 are replaced, never raised, so a torn line
    reaches the summary as a non-JSON line and is skipped there. Raises
    ``MetricsFileError`` if ``path`` is missing, is a directory or cannot be
    read.
    """
    try:
        with open(path, "r", encoding=_ENCODING, errors="replace", newline=None) as fp:
            return fp.read().splitlines()
    except OSError as exc:
        raise MetricsFileError(f"cannot read metrics file {path}: {exc.strerror or exc}") from exc
