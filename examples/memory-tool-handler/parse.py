"""Boundary parser: a raw ``tool_use.input`` dict becomes one typed command.

After ``parse_command`` succeeds, every path is a ``MemoryPath`` and every
field has its documented type, so ``commands.py`` never re-validates input.

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Union

from paths import MemoryPath, MemoryPathError, resolve_memory_path

# The `view_range` end value meaning "through the last line" (documented).
TO_EOF = -1


class CommandError(ValueError):
    """The model sent a command that cannot be executed.

    ``str(exc)`` is the complete text returned to the model, verbatim. Documented
    response strings are used where the docs give one (some carry an ``Error:``
    prefix, some do not); every other message is prefixed ``Error:``.
    """


@dataclass(frozen=True)
class ViewRange:
    """1-indexed inclusive line range. ``end == TO_EOF`` means through the last line.

    Raises:
        CommandError: if ``start < 1`` or ``end`` is neither ``TO_EOF`` nor ``>= start``.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1:
            raise CommandError(f"Error: Invalid `view_range`: start must be >= 1, got {self.start}.")
        if self.end != TO_EOF and self.end < self.start:
            raise CommandError(
                f"Error: Invalid `view_range`: end must be -1 or >= start, got [{self.start}, {self.end}]."
            )


@dataclass(frozen=True)
class View:
    path: MemoryPath
    view_range: ViewRange | None


@dataclass(frozen=True)
class Create:
    path: MemoryPath
    file_text: str


@dataclass(frozen=True)
class StrReplace:
    path: MemoryPath
    old_str: str
    new_str: str


@dataclass(frozen=True)
class Insert:
    path: MemoryPath
    insert_line: int
    insert_text: str


@dataclass(frozen=True)
class Delete:
    path: MemoryPath


@dataclass(frozen=True)
class Rename:
    old_path: MemoryPath
    new_path: MemoryPath


Command = Union[View, Create, StrReplace, Insert, Delete, Rename]


def _field(raw: Mapping[str, object], key: str) -> object:
    if key not in raw:
        raise CommandError(f"Error: Missing required parameter `{key}`.")
    return raw[key]


def _str(raw: Mapping[str, object], key: str) -> str:
    value = _field(raw, key)
    if not isinstance(value, str):
        raise CommandError(f"Error: Parameter `{key}` must be a string.")
    return value


def _int(raw: Mapping[str, object], key: str) -> int:
    value = _field(raw, key)
    # bool is an int subclass; True is not a line number.
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError(f"Error: Parameter `{key}` must be an integer.")
    return value


def _path(raw: Mapping[str, object], key: str) -> MemoryPath:
    value = _field(raw, key)
    try:
        return resolve_memory_path(value)
    except MemoryPathError as exc:
        # Re-raise naming the field and the offending value, so the model can fix it.
        raise MemoryPathError(f"Invalid `{key}` {value!r}: {exc}.") from None


def _view_range(raw: Mapping[str, object]) -> ViewRange | None:
    if raw.get("view_range") is None:
        return None
    value = raw["view_range"]
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) for v in value)
    ):
        raise CommandError("Error: Parameter `view_range` must be a list of two integers [start, end].")
    return ViewRange(value[0], value[1])


def parse_command(raw: Mapping[str, object]) -> Command:
    """Turn a memory ``tool_use.input`` into a typed command.

    ``str_replace.new_str`` is optional and defaults to ``""`` (documented: an
    omitted ``new_str`` deletes ``old_str``). ``view.view_range`` is optional.

    Raises:
        CommandError: unknown or missing ``command``, missing parameter, wrong
            parameter type, or an invalid ``view_range``.
        paths.MemoryPathError: any path-bearing field (``path``, ``old_path``,
            ``new_path``) is not a valid ``/memories`` path.
    """
    if not isinstance(raw, Mapping):
        raise CommandError("Error: Tool input must be an object.")
    name = _str(raw, "command")
    if name == "view":
        return View(_path(raw, "path"), _view_range(raw))
    if name == "create":
        return Create(_path(raw, "path"), _str(raw, "file_text"))
    if name == "str_replace":
        new_str = raw.get("new_str", "")
        if new_str is None:
            new_str = ""
        if not isinstance(new_str, str):
            raise CommandError("Error: Parameter `new_str` must be a string.")
        return StrReplace(_path(raw, "path"), _str(raw, "old_str"), new_str)
    if name == "insert":
        return Insert(_path(raw, "path"), _int(raw, "insert_line"), _str(raw, "insert_text"))
    if name == "delete":
        return Delete(_path(raw, "path"))
    if name == "rename":
        return Rename(_path(raw, "old_path"), _path(raw, "new_path"))
    raise CommandError(
        f"Error: Unknown command `{name}`. Valid commands: view, create, str_replace, insert, delete, rename."
    )
