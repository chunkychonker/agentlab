"""What one backlog edit decided: the closed set of three results.

Pure: no I/O. ``Edit`` is a sum type, not ``changed: bool`` plus text, so
"nothing to do" and "the item is not there" can never be confused, and a
caller cannot write text that was never produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class Rewritten:
    """The edit applies; ``text`` is the whole new backlog. Raises ``TypeError`` for a non-str."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError(f"text must be a str, got {type(self.text).__name__}")


@dataclass(frozen=True)
class AlreadyHandled:
    """The item is there under a marker that means "leave it alone": nothing to write."""


@dataclass(frozen=True)
class ItemNotFound:
    """No item line carries this text: it was reworded, removed, or never on main."""


Edit = Union[Rewritten, AlreadyHandled, ItemNotFound]
