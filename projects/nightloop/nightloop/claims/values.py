"""The value types the claim lifecycle passes around, each checked once at construction.

Pure: no I/O. ``Claim`` is an item a cycle claimed, ``PrNumber`` a PR's
number, ``StrandedBranch`` the branch name a stranded marker records. A
function handed one of these can assume it is well formed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Digits only, no sign and no leading zero. A fixed pattern, never built from
# item text.
_PR_DIGITS = re.compile(r"[1-9][0-9]*")


def check_key(key: object) -> str:
    """``key`` if it can be an item's identity (one non-blank line), else raise ``TypeError``/``ValueError``."""
    return _check_text_line("key", key)


def _check_text_line(field: str, value: object) -> str:
    """``value`` if it is a str holding one line with some non-blank text, else raise."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a str, got {type(value).__name__}")
    if value.strip() == "" or "\n" in value or "\r" in value:
        raise ValueError(f"{field} must be one non-blank line, got {value!r}")
    return value


@dataclass(frozen=True)
class Claim:
    """One claimed backlog item: ``key`` is its first line's text, ``item`` the whole item.

    ``item`` is ``key`` alone, or ``key`` followed by ``"\\n"``-separated
    continuation lines, each starting with a space or a tab, exactly as
    they appear in the backlog. Neither carries the ``- [<marker>] ``
    prefix. Raises ``TypeError`` for non-str fields and ``ValueError`` for
    a blank or multi-line ``key``, an ``item`` that does not start with
    ``key``, or a continuation line that is not indented.
    """

    key: str
    item: str

    def __post_init__(self) -> None:
        _check_text_line("key", self.key)
        if not isinstance(self.item, str):
            raise TypeError(f"item must be a str, got {type(self.item).__name__}")
        first, *rest = self.item.split("\n")
        if first != self.key:
            raise ValueError(f"item's first line must be key {self.key!r}, got {first!r}")
        for line in rest:
            if not line.startswith((" ", "\t")) or "\r" in line:
                raise ValueError(f"a continuation line must be indented, got {line!r}")

    @property
    def continuation(self) -> tuple[str, ...]:
        """The item's lines after the first, verbatim (empty for a one-line item)."""
        return tuple(self.item.split("\n")[1:])


@dataclass(frozen=True)
class PrNumber:
    """A pull request number: an int greater than zero (not a bool).

    Build one from text with ``PrNumber.parse``. Raises ``TypeError`` for a
    non-int and ``ValueError`` for zero or less.
    """

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise TypeError(f"PR number must be an int, got {type(self.value).__name__}")
        if self.value <= 0:
            raise ValueError(f"PR number must be > 0, got {self.value}")

    @classmethod
    def parse(cls, text: str) -> PrNumber:
        """``PrNumber`` from digits only (``"41"``); the ``#`` belongs to the marker, not here.

        Raises ``TypeError`` for a non-str and ``ValueError`` for anything
        else: empty, a sign, ``#41``, ``041``, whitespace, non-ASCII digits.
        """
        if not isinstance(text, str):
            raise TypeError(f"PR number text must be a str, got {type(text).__name__}")
        if _PR_DIGITS.fullmatch(text) is None:
            raise ValueError(f"not a PR number (digits only, no '#', no leading zero): {text!r}")
        return cls(int(text))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class StrandedBranch:
    """The branch a stranded marker names, e.g. ``cycle/2026-11-18-unshipped-010203-1``.

    Non-empty and without whitespace, so ``"] "`` cannot occur in it and
    ``- [stranded <name>] <text>`` always splits back into its marker and
    its text. (Git refnames cannot hold whitespace anyway.) Raises
    ``TypeError`` for a non-str and ``ValueError`` otherwise.
    """

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError(f"branch must be a str, got {type(self.name).__name__}")
        if self.name == "" or any(ch.isspace() for ch in self.name):
            raise ValueError(f"branch must be non-empty with no whitespace, got {self.name!r}")

    def __str__(self) -> str:
        return self.name
