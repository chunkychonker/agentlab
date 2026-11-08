"""Maps a ``nightloop.toml`` document onto the validated ``Config`` types.

This module owns the file *layout* (which tables and keys exist, which are
required, what is unknown); the value rules live in ``model`` and
``tool_rules``. ``parse_config`` is pure; ``load_config`` is the only function
that touches the filesystem, and only to read the one file it is handed.

Schema version 1::

    schema_version = 1
    [repo]
    backlog = "BACKLOG.md"
    [run]
    cycles = 1
    window_start_hour = 1
    window_end_hour = 6
    [phase.research]   # and [phase.build], [phase.review]; exactly these three
    agent = "agentlab-researcher"
    model = "sonnet"
    postcondition = "research_note_fresh"
    allowed_tools = ["Read", "Bash(git log *)"]
    allow_unrestricted_bash = false   # optional
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from .errors import FIELD_FILE, FIELD_TOML, ConfigError
from .model import PHASE_POSTCONDITIONS, Config, Phase, Postcondition, Window

__all__ = [
    "Config",
    "ConfigError",
    "Phase",
    "Postcondition",
    "Window",
    "load_config",
    "parse_config",
]

SCHEMA_VERSION = 1
# tomllib's docs warn that hostile input can exhaust CPU/memory; config is
# repo-owned, but capping before parsing is free.
MAX_CONFIG_BYTES = 64 * 1024

_TOP_KEYS = ("schema_version", "repo", "run", "phase")
_REPO_KEYS = ("backlog",)
_RUN_KEYS = ("cycles", "window_start_hour", "window_end_hour")
_PHASE_REQUIRED_KEYS = ("agent", "model", "postcondition", "allowed_tools")
_PHASE_OPTIONAL_KEYS = ("allow_unrestricted_bash",)

# Model-relative error fields -> document paths.
_WINDOW_FIELDS = {
    "start_hour": "run.window_start_hour",
    "end_hour": "run.window_end_hour",
}
_CONFIG_FIELDS = {"backlog": "repo.backlog", "cycles": "run.cycles"}

_T = TypeVar("_T")


def parse_config(text: str) -> Config:
    """Parse and validate a ``nightloop.toml`` document given as text.

    Pure: no I/O, no environment, no clock. Raises ``ConfigError`` and nothing
    else: field ``<toml>`` if ``text`` is not a ``str`` or not valid TOML 1.0
    (including nesting too deep to parse); otherwise the dotted path of the
    first invalid key found (missing, unknown, wrong type, or out of range).
    """
    if not isinstance(text, str):
        raise ConfigError(FIELD_TOML, f"expected the document as str, got {type(text).__name__}")
    doc = _parse_toml(text)

    # Checked before unknown keys: a future version's new keys should be
    # reported as a version mismatch, not as typos.
    _check_schema_version(doc)
    _check_keys("", doc, required=_TOP_KEYS, optional=())

    repo = _table(doc, "repo", "repo")
    _check_keys("repo", repo, required=_REPO_KEYS, optional=())
    run = _table(doc, "run", "run")
    _check_keys("run", run, required=_RUN_KEYS, optional=())
    phases = _table(doc, "phase", "phase")
    _check_keys("phase", phases, required=tuple(PHASE_POSTCONDITIONS), optional=())

    window = _relabel(
        lambda: Window(start_hour=run["window_start_hour"], end_hour=run["window_end_hour"]),
        _WINDOW_FIELDS.__getitem__,
    )
    built = {name: _parse_phase(name, _table(phases, name, f"phase.{name}")) for name in phases}
    return _relabel(
        lambda: Config(
            backlog=repo["backlog"],
            cycles=run["cycles"],
            window=window,
            research=built["research"],
            build=built["build"],
            review=built["review"],
        ),
        _config_field,
    )


def load_config(path: Path) -> Config:
    """Read the file at ``path`` (at most ``MAX_CONFIG_BYTES``) and parse it.

    Raises ``ConfigError`` and nothing else: field ``<file>`` if the file
    cannot be opened/read (missing, a directory, unreadable) or exceeds
    ``MAX_CONFIG_BYTES``; field ``<toml>`` if it is not UTF-8; otherwise as
    ``parse_config``. Reads only; safe to call repeatedly.
    """
    try:
        with open(path, "rb") as fp:
            data = fp.read(MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise ConfigError(FIELD_FILE, f"cannot read {path}: {exc.strerror or exc}") from exc
    if len(data) > MAX_CONFIG_BYTES:
        raise ConfigError(FIELD_FILE, f"{path} is larger than the {MAX_CONFIG_BYTES}-byte limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(FIELD_TOML, f"{path} is not valid UTF-8: {exc}") from exc
    return parse_config(text)


def _parse_toml(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        # lineno/colno only exist on 3.14+; str(exc) carries the position on 3.11+.
        raise ConfigError(FIELD_TOML, str(exc)) from exc
    except RecursionError as exc:
        # tomllib recurses per nesting level; ~30k nested arrays fit under the
        # size cap and overflow the stack on 3.11-3.13.
        raise ConfigError(FIELD_TOML, "document is nested too deeply to parse") from exc


def _check_schema_version(doc: Mapping[str, Any]) -> None:
    if "schema_version" not in doc:
        raise ConfigError("schema_version", "is required")
    version = doc["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConfigError("schema_version", f"must be an integer, got {type(version).__name__}")
    if version != SCHEMA_VERSION:
        raise ConfigError(
            "schema_version", f"unsupported version {version}; this loader reads {SCHEMA_VERSION}"
        )


def _check_keys(
    path: str,
    table: Mapping[str, Any],
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
) -> None:
    """Raise for the first unknown key, then the first missing required key.

    Unknown keys are reported first so that a typo (``cylces``) names the
    typo rather than the key it was meant to be.
    """
    known = set(required) | set(optional)
    for key in table:
        if key not in known:
            raise ConfigError(_join(path, key), f"unknown key; expected one of: {', '.join(sorted(known))}")
    for key in required:
        if key not in table:
            raise ConfigError(_join(path, key), "is required")


def _table(parent: Mapping[str, Any], key: str, path: str) -> Mapping[str, Any]:
    value = parent[key]
    if not isinstance(value, dict):
        raise ConfigError(path, f"must be a table, got {type(value).__name__}")
    return value


def _parse_phase(name: str, table: Mapping[str, Any]) -> Phase:
    path = f"phase.{name}"
    _check_keys(path, table, required=_PHASE_REQUIRED_KEYS, optional=_PHASE_OPTIONAL_KEYS)

    raw_postcondition = table["postcondition"]
    if not isinstance(raw_postcondition, str):
        raise ConfigError(
            f"{path}.postcondition", f"must be a string, got {type(raw_postcondition).__name__}"
        )
    try:
        postcondition = Postcondition(raw_postcondition)
    except ValueError as exc:
        choices = ", ".join(p.value for p in Postcondition)
        raise ConfigError(
            f"{path}.postcondition",
            f"unknown postcondition {raw_postcondition!r}; expected one of: {choices}",
        ) from exc

    raw_tools = table["allowed_tools"]
    if not isinstance(raw_tools, list):
        raise ConfigError(
            f"{path}.allowed_tools", f"must be an array of strings, got {type(raw_tools).__name__}"
        )

    return _relabel(
        lambda: Phase(
            agent=table["agent"],
            model=table["model"],
            postcondition=postcondition,
            allowed_tools=tuple(raw_tools),
            allow_unrestricted_bash=table.get("allow_unrestricted_bash", False),
        ),
        lambda field: f"{path}.{field}",
    )


def _relabel(build: Callable[[], _T], to_document_path: Callable[[str], str]) -> _T:
    """Run ``build``; re-raise its ``ConfigError`` with the field as a document path."""
    try:
        return build()
    except ConfigError as exc:
        raise ConfigError(to_document_path(exc.field), exc.message) from exc


def _config_field(field: str) -> str:
    if field in _CONFIG_FIELDS:
        return _CONFIG_FIELDS[field]
    head = field.split(".", 1)[0]
    if head in PHASE_POSTCONDITIONS:
        return f"phase.{field}"
    # Window and Phase objects are already validated when Config is built, so
    # no other field can reach here; if one does, the mapping is out of date.
    raise AssertionError(f"Config field {field!r} has no document path")


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key
