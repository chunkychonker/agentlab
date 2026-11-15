"""Shortens a finished command's output to the one line an error message may quote.

Pure: no I/O. Shared by the modules that run ``git`` and ``gh``, so every
reason and error message quotes a child process the same way: only its last
non-blank line, never its whole output (which could carry more than the
failure, e.g. a URL with credentials in an earlier line).
"""

from __future__ import annotations

TAIL_CHARS = 300


def last_line(stderr: str, stdout: str, returncode: int) -> str:
    """The last non-blank line of ``stderr`` (else ``stdout``), stripped and cut to ``TAIL_CHARS``.

    ``"exit <returncode>"`` if both are blank. Never raises for str inputs.
    """
    for stream in (stderr, stdout):
        lines = [line for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1].strip()[:TAIL_CHARS]
    return f"exit {returncode}"
