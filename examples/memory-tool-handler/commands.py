"""Executes one memory-tool command against a Store and returns the tool_result text.

Pure dispatcher: all I/O goes through the injected ``Store``. Model-caused
problems (bad path, missing file, no/duplicate match, root delete, unknown
command, malformed input) come back as ``Result(is_error=True)`` and never
raise. Host faults (OSError from the disk, a store bug) propagate: those are
not the model's to fix.

Return strings follow the memory tool docs (fetched 2026-10-28); they are the
docs' recommendations, which Claude reads as plain text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from paths import MemoryPath, MemoryPathError, resolve_memory_path
from store import Store

# Cap on any file the model writes, in characters. Keeps a runaway model from
# filling the disk through one create.
MAX_FILE_CHARS = 100_000
# Documented view limit.
MAX_VIEW_LINES = 999_999
# Directory listings go this many levels below the viewed path.
_LIST_DEPTH = 2
# Lines of context shown around a str_replace edit.
_SNIPPET_CONTEXT = 4
_HIDDEN_NAMES = ("node_modules",)


@dataclass(frozen=True)
class Result:
    text: str
    is_error: bool


class _ModelError(Exception):
    """Internal: a model-caused failure, converted to Result(is_error=True)."""


def execute(store: Store, cmd: Mapping[str, Any]) -> Result:
    """Run one memory command (the ``input`` of a ``memory`` tool_use block).

    Never raises for model-caused errors (see module docstring). Raises
    OSError (or whatever the store raises) on host faults; a command that fails
    that way may have partially applied only if the underlying filesystem
    operation itself is non-atomic (e.g. a recursive delete interrupted mid-way).
    """
    if not isinstance(cmd, Mapping):
        return Result("Error: tool input must be an object", True)
    name = cmd.get("command")
    handler = _HANDLERS.get(name) if isinstance(name, str) else None
    if handler is None:
        return Result(f"Error: Unknown command {name!r}. Valid commands: {', '.join(_HANDLERS)}", True)
    try:
        return Result(handler(store, cmd), False)
    except _ModelError as exc:
        return Result(str(exc), True)
    except MemoryPathError as exc:
        return Result(f"Error: {exc}", True)


# --------------------------------------------------------------------------- #
# Input helpers
# --------------------------------------------------------------------------- #

def _field(cmd: Mapping[str, Any], key: str, kind: type, *, optional: bool = False) -> Any:
    if key not in cmd or cmd[key] is None:
        if optional:
            return None
        raise _ModelError(f"Error: Missing required parameter `{key}` for command `{cmd['command']}`")
    value = cmd[key]
    if kind is int and isinstance(value, bool) or not isinstance(value, kind):
        raise _ModelError(f"Error: Parameter `{key}` must be of type {kind.__name__}")
    return value


def _path(cmd: Mapping[str, Any], key: str = "path") -> MemoryPath:
    return resolve_memory_path(_field(cmd, key, str))


def _check_size(text: str, path: MemoryPath) -> None:
    if len(text) > MAX_FILE_CHARS:
        raise _ModelError(
            f"Error: {path.virtual} would be {len(text)} characters; the limit is {MAX_FILE_CHARS}"
        )


def _check_ancestors_are_dirs(store: Store, p: MemoryPath) -> None:
    for anc in p.ancestors():
        if store.exists(anc) and not store.is_dir(anc):
            raise _ModelError(f"Error: {anc.virtual} is a file, not a directory")


def _require_file(store: Store, p: MemoryPath, missing_msg: str) -> str:
    # A directory passed where a file is required is reported as "does not exist".
    if not store.exists(p) or store.is_dir(p):
        raise _ModelError(missing_msg)
    return store.read(p)


def _numbered(lines: list[str], first_lineno: int) -> str:
    return "\n".join(f"{n:6d}\t{line}" for n, line in enumerate(lines, start=first_lineno))


# --------------------------------------------------------------------------- #
# view
# --------------------------------------------------------------------------- #

def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    size = n / 1024
    for unit in ("K", "M"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


def _is_hidden(p: MemoryPath) -> bool:
    name = p.parts[-1]
    return name.startswith(".") or name in _HIDDEN_NAMES


def _total_size(store: Store, p: MemoryPath) -> int:
    if not store.is_dir(p):
        return store.size(p)
    return sum(_total_size(store, c) for c in store.children(p))


def _listing(store: Store, p: MemoryPath, depth: int) -> list[str]:
    rows = [f"{_human_size(_total_size(store, p))}\t{p.virtual}"]
    if depth == 0 or not store.is_dir(p):
        return rows
    for c in store.children(p):
        if not _is_hidden(c):
            rows.extend(_listing(store, c, depth - 1))
    return rows


def _view(store: Store, cmd: Mapping[str, Any]) -> str:
    p = _path(cmd)
    if not store.exists(p):
        raise _ModelError(f"The path {p.virtual} does not exist. Please provide a valid path.")
    if store.is_dir(p):
        header = (
            f"Here're the files and directories up to {_LIST_DEPTH} levels deep in "
            f"{p.virtual}, excluding hidden items and node_modules:"
        )
        return "\n".join([header, *_listing(store, p, _LIST_DEPTH)])

    lines = store.read(p).split("\n")
    if len(lines) > MAX_VIEW_LINES:
        raise _ModelError(f"File {p.virtual} exceeds maximum line limit of {MAX_VIEW_LINES:,} lines.")
    start, end = 1, len(lines)
    view_range = _field(cmd, "view_range", list, optional=True)
    if view_range is not None:
        ok = (
            len(view_range) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in view_range)
            and 1 <= view_range[0] <= len(lines)
            and (view_range[1] == -1 or view_range[0] <= view_range[1] <= len(lines))
        )
        if not ok:
            raise _ModelError(
                f"Error: Invalid `view_range` parameter: {view_range}. It should be "
                f"[start, end] with 1 <= start <= end <= {len(lines)}, or end = -1 for end of file"
            )
        start = view_range[0]
        end = len(lines) if view_range[1] == -1 else view_range[1]
    body = _numbered(lines[start - 1 : end], start)
    return f"Here's the content of {p.virtual} with line numbers:\n{body}"


# --------------------------------------------------------------------------- #
# create / str_replace / insert
# --------------------------------------------------------------------------- #

def _create(store: Store, cmd: Mapping[str, Any]) -> str:
    p = _path(cmd)
    text = _field(cmd, "file_text", str)
    # Reference behaviour per the docs: refuse to overwrite (see README).
    if store.exists(p):
        raise _ModelError(f"Error: File {p.virtual} already exists")
    _check_ancestors_are_dirs(store, p)
    _check_size(text, p)
    store.write(p, text)
    return f"File created successfully at: {p.virtual}"


def _occurrence_lines(text: str, needle: str) -> list[int]:
    lines, i = [], text.find(needle)
    while i != -1:
        lines.append(text.count("\n", 0, i) + 1)
        i = text.find(needle, i + len(needle))
    return lines


def _str_replace(store: Store, cmd: Mapping[str, Any]) -> str:
    p = _path(cmd)
    old = _field(cmd, "old_str", str)
    new = _field(cmd, "new_str", str, optional=True) or ""
    if old == "":
        raise _ModelError("Error: `old_str` must not be empty")
    text = _require_file(
        store, p, f"Error: The path {p.virtual} does not exist. Please provide a valid path."
    )
    hits = _occurrence_lines(text, old)
    if not hits:
        raise _ModelError(
            f"No replacement was performed, old_str `{old}` did not appear verbatim in {p.virtual}."
        )
    if len(hits) > 1:
        raise _ModelError(
            f"No replacement was performed. Multiple occurrences of old_str `{old}` in lines: "
            f"{', '.join(map(str, hits))}. Please ensure it is unique"
        )
    updated = text.replace(old, new, 1)
    _check_size(updated, p)
    store.write(p, updated)

    lines = updated.split("\n")
    first = max(1, hits[0] - _SNIPPET_CONTEXT)
    last = min(len(lines), hits[0] + new.count("\n") + _SNIPPET_CONTEXT)
    return "The memory file has been edited.\n" + _numbered(lines[first - 1 : last], first)


def _insert(store: Store, cmd: Mapping[str, Any]) -> str:
    p = _path(cmd)
    line_no = _field(cmd, "insert_line", int)
    insert_text = _field(cmd, "insert_text", str)
    text = _require_file(store, p, f"Error: The path {p.virtual} does not exist")
    lines = text.split("\n")
    if not 0 <= line_no <= len(lines):
        raise _ModelError(
            f"Error: Invalid `insert_line` parameter: {line_no}. It should be within the "
            f"range of lines of the file: [0, {len(lines)}]"
        )
    updated = "\n".join(lines[:line_no] + insert_text.split("\n") + lines[line_no:])
    _check_size(updated, p)
    store.write(p, updated)
    return f"The file {p.virtual} has been edited."


# --------------------------------------------------------------------------- #
# delete / rename
# --------------------------------------------------------------------------- #

def _delete(store: Store, cmd: Mapping[str, Any]) -> str:
    p = _path(cmd)
    if p.is_root:
        raise _ModelError("Error: Cannot delete the /memories directory itself")
    if not store.exists(p):
        raise _ModelError(f"Error: The path {p.virtual} does not exist")
    store.delete(p)
    return f"Successfully deleted {p.virtual}"


def _rename(store: Store, cmd: Mapping[str, Any]) -> str:
    src = _path(cmd, "old_path")
    dst = _path(cmd, "new_path")
    if src.is_root or dst.is_root:
        raise _ModelError("Error: Cannot rename the /memories directory itself")
    if not store.exists(src):
        raise _ModelError(f"Error: The path {src.virtual} does not exist")
    if store.exists(dst):
        raise _ModelError(f"Error: The destination {dst.virtual} already exists")
    if dst.is_within(src):
        raise _ModelError(f"Error: Cannot move {src.virtual} inside itself")
    _check_ancestors_are_dirs(store, dst)
    store.rename(src, dst)
    return f"Successfully renamed {src.virtual} to {dst.virtual}"


_HANDLERS: dict[str, Callable[[Store, Mapping[str, Any]], str]] = {
    "view": _view,
    "create": _create,
    "str_replace": _str_replace,
    "insert": _insert,
    "delete": _delete,
    "rename": _rename,
}
