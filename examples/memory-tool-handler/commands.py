"""The memory-tool dispatcher: one raw tool input in, one tool_result payload out.

``handle`` parses the input (``parse.py``), checks every precondition the
``Store`` contract demands, performs the operation on the injected store, and
renders the documented response text. Errors the *model* caused (bad path,
missing file, ambiguous ``str_replace``, ...) come back as
``Result(is_error=True)`` so the model can recover. Errors the *environment*
caused (disk full, permission denied) are not the model's to fix and propagate
as exceptions.

No I/O of its own: all storage goes through the injected ``Store``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from parse import (
    TO_EOF,
    CommandError,
    Create,
    Delete,
    Insert,
    Rename,
    StrReplace,
    View,
    parse_command,
)
from paths import MemoryPath, MemoryPathError
from store import NodeKind, Store

# Directory listings go this many levels below the viewed directory (documented).
LIST_DEPTH = 2
# Names never shown in a directory listing, besides dot-files (documented).
_LIST_EXCLUDED = frozenset({"node_modules"})
# `%6d\t` line numbering (documented).
_LINE_NO_WIDTH = 6
# Lines of context either side of a str_replace edit in the returned snippet.
_SNIPPET_CONTEXT = 2


class CreateMode(Enum):
    """What ``create`` does when the path is already a file. The docs allow both."""

    OVERWRITE = "overwrite"  # matches Claude's own tool description ("creates or overwrites")
    REJECT = "reject"  # returns "Error: File {path} already exists"


@dataclass(frozen=True)
class Result:
    """Payload for one ``tool_result`` block."""

    text: str
    is_error: bool


def handle(store: Store, raw: Mapping[str, object], create_mode: CreateMode) -> Result:
    """Execute one memory command against ``store``.

    Returns:
        ``Result(text, is_error=False)`` on success; ``Result(text,
        is_error=True)`` for any model-correctable failure (invalid input or
        path, path escape, missing target, existing destination, root
        delete/rename, ``str_replace`` with zero or multiple matches,
        out-of-range ``insert_line``). A failed command leaves the store
        unchanged.

    Raises:
        OSError: storage itself failed. A partially applied ``DirStore``
            ``delete`` (``rmtree`` interrupted) can leave some children
            removed; every other operation is single-step.
    """
    try:
        cmd = parse_command(raw)
        if isinstance(cmd, View):
            return Result(_view(store, cmd), False)
        if isinstance(cmd, Create):
            return Result(_create(store, cmd, create_mode), False)
        if isinstance(cmd, StrReplace):
            return Result(_str_replace(store, cmd), False)
        if isinstance(cmd, Insert):
            return Result(_insert(store, cmd), False)
        if isinstance(cmd, Delete):
            return Result(_delete(store, cmd), False)
        if isinstance(cmd, Rename):
            return Result(_rename(store, cmd), False)
        raise AssertionError(f"unhandled command type {type(cmd).__name__}")
    except CommandError as exc:
        return Result(str(exc), True)
    except MemoryPathError as exc:
        return Result(f"Error: {exc}", True)


# --------------------------------------------------------------------------- #
# Shared checks and formatting
# --------------------------------------------------------------------------- #


def _not_found(path: MemoryPath) -> CommandError:
    return CommandError(f"The path {path} does not exist. Please provide a valid path.")


def _require_file(store: Store, path: MemoryPath) -> None:
    k = store.kind(path)
    if k is NodeKind.MISSING:
        raise _not_found(path)
    if k is NodeKind.DIR:
        raise CommandError(f"Error: The path {path} is a directory, not a file.")


def _require_no_file_ancestor(store: Store, path: MemoryPath) -> None:
    for anc in path.ancestors():
        if store.kind(anc) is NodeKind.FILE:
            raise CommandError(f"Error: Cannot use {path}: {anc} is a file, not a directory.")


def _lines(text: str) -> list[str]:
    """File text as lines. A trailing newline does not create an extra empty line."""
    return text.splitlines()


def _numbered(lines: list[str], first: int) -> str:
    return "\n".join(f"{n:{_LINE_NO_WIDTH}d}\t{line}" for n, line in enumerate(lines, start=first))


def format_size(n: int) -> str:
    """Human-readable size, 1024-based: ``0B``, ``512B``, ``1.5K``, ``4K``, ``2.0M``."""
    units = ("B", "K", "M", "G")
    value = float(n)
    unit = 0
    while value >= 1024 and unit < len(units) - 1:
        value /= 1024
        unit += 1
    return f"{int(value)}{units[unit]}" if value == int(value) else f"{value:.1f}{units[unit]}"


# --------------------------------------------------------------------------- #
# The six commands
# --------------------------------------------------------------------------- #


def _view(store: Store, cmd: View) -> str:
    k = store.kind(cmd.path)
    if k is NodeKind.MISSING:
        raise _not_found(cmd.path)
    if k is NodeKind.DIR:
        if cmd.view_range is not None:
            raise CommandError("Error: `view_range` only applies to files, not directories.")
        return _view_dir(store, cmd.path)
    return _view_file(store, cmd)


def _view_dir(store: Store, path: MemoryPath) -> str:
    rows = [f"{format_size(store.size(path))}\t{path}"]

    def walk(directory: MemoryPath, depth: int) -> None:
        for name in store.children(directory):
            if name.startswith(".") or name in _LIST_EXCLUDED:
                continue
            child = directory.child(name)
            is_dir = store.kind(child) is NodeKind.DIR
            rows.append(f"{format_size(store.size(child))}\t{child}{'/' if is_dir else ''}")
            if is_dir and depth < LIST_DEPTH:
                walk(child, depth + 1)

    walk(path, 1)
    header = (
        f"Here're the files and directories up to {LIST_DEPTH} levels deep in {path}, "
        "excluding hidden items and node_modules:"
    )
    return header + "\n" + "\n".join(rows)


def _view_file(store: Store, cmd: View) -> str:
    lines = _lines(store.read_text(cmd.path))
    first = 1
    if cmd.view_range is not None:
        start, end = cmd.view_range.start, cmd.view_range.end
        if start > max(len(lines), 1) or (end != TO_EOF and end > len(lines)):
            raise CommandError(
                f"Error: Invalid `view_range` [{start}, {end}]: {cmd.path} has {len(lines)} lines."
            )
        lines = lines[start - 1 : None if end == TO_EOF else end]
        first = start
    header = f"Here's the content of {cmd.path} with line numbers:"
    return header + ("\n" + _numbered(lines, first) if lines else "")


def _create(store: Store, cmd: Create, mode: CreateMode) -> str:
    if cmd.path.is_root:
        raise CommandError(f"Error: Cannot create {cmd.path}: it is the memory root directory.")
    _require_no_file_ancestor(store, cmd.path)
    k = store.kind(cmd.path)
    if k is NodeKind.DIR:
        raise CommandError(f"Error: Cannot create {cmd.path}: it is a directory.")
    if k is NodeKind.FILE and mode is CreateMode.REJECT:
        raise CommandError(f"Error: File {cmd.path} already exists")
    store.write_text(cmd.path, cmd.file_text)
    return f"File created successfully at: {cmd.path}"


def _str_replace(store: Store, cmd: StrReplace) -> str:
    _require_file(store, cmd.path)
    if cmd.old_str == "":
        raise CommandError("No replacement was performed: `old_str` must not be empty.")
    text = store.read_text(cmd.path)
    starts = []
    i = text.find(cmd.old_str)
    while i != -1:
        starts.append(i)
        i = text.find(cmd.old_str, i + 1)
    if not starts:
        raise CommandError(
            f"No replacement was performed, old_str `{cmd.old_str}` did not appear verbatim in {cmd.path}."
        )
    if len(starts) > 1:
        line_nos = ", ".join(str(text.count("\n", 0, s) + 1) for s in starts)
        raise CommandError(
            f"No replacement was performed. Multiple occurrences of old_str `{cmd.old_str}` "
            f"in lines: {line_nos}. Please ensure it is unique"
        )
    pos = starts[0]
    new_text = text[:pos] + cmd.new_str + text[pos + len(cmd.old_str):]
    store.write_text(cmd.path, new_text)

    changed = text.count("\n", 0, pos)  # 0-indexed line of the edit
    lines = _lines(new_text)
    lo = max(0, changed - _SNIPPET_CONTEXT)
    hi = min(len(lines), changed + _SNIPPET_CONTEXT + 1)
    return (
        "The memory file has been edited. Here is the snippet showing the change "
        "(with line numbers):\n" + _numbered(lines[lo:hi], lo + 1)
    )


def _insert(store: Store, cmd: Insert) -> str:
    _require_file(store, cmd.path)
    text = store.read_text(cmd.path)
    lines = _lines(text)
    if not 0 <= cmd.insert_line <= len(lines):
        raise CommandError(
            f"Error: Invalid `insert_line` parameter: {cmd.insert_line}. "
            f"It should be within the range [0, {len(lines)}]."
        )
    new_lines = lines[: cmd.insert_line] + _lines(cmd.insert_text) + lines[cmd.insert_line :]
    store.write_text(cmd.path, "\n".join(new_lines) + "\n")
    return f"The file {cmd.path} has been edited."


def _delete(store: Store, cmd: Delete) -> str:
    if cmd.path.is_root:
        raise CommandError(f"Error: Cannot delete the {cmd.path} directory itself.")
    if store.kind(cmd.path) is NodeKind.MISSING:
        raise _not_found(cmd.path)
    store.delete(cmd.path)
    return f"Successfully deleted {cmd.path}"


def _rename(store: Store, cmd: Rename) -> str:
    old, new = cmd.old_path, cmd.new_path
    if old.is_root or new.is_root:
        raise CommandError("Error: Cannot rename the /memories directory itself, or rename onto it.")
    if store.kind(old) is NodeKind.MISSING:
        raise _not_found(old)
    if store.kind(new) is not NodeKind.MISSING:
        raise CommandError(f"Error: The destination {new} already exists")
    if new.is_within(old):
        raise CommandError(f"Error: Cannot move {old} inside itself ({new}).")
    _require_no_file_ancestor(store, new)
    store.rename(old, new)
    return f"Successfully renamed {old} to {new}"
