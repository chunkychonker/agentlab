"""Immutable config value types that refuse to hold an invalid state.

Every invariant is enforced in ``__post_init__``, so a ``Window``, ``Phase`` or
``Config`` that exists is valid however it was built (by the TOML loader or
directly, e.g. in later milestones' tests). Violations raise ``ConfigError``
whose ``field`` is relative to the object being built (``end_hour``,
``allowed_tools[2]``, ``review.postcondition``); the TOML loader rewrites it
to the document path. Pure: no I/O.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping

from .errors import ConfigError
from .tool_rules import rule_violation

# Run window, in local-clock hours: start inclusive, end exclusive, the same
# semantics as agentlab's .pipeline/schedule.sh ([WINDOW_START, WINDOW_END)).
_START_HOUR_MIN, _START_HOUR_MAX = 0, 23
_END_HOUR_MIN, _END_HOUR_MAX = 1, 24
_CYCLES_MIN = 1

_AGENT_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
_MODEL_NAME = re.compile(r"\S+")


class Postcondition(enum.Enum):
    """Closed set of per-phase gates; implemented in milestone 2."""

    RESEARCH_NOTE_FRESH = "research_note_fresh"
    INCREMENT_BUILT = "increment_built"
    REVIEW_VERDICT = "review_verdict"
    NONE = "none"


# The phases a config must define (exactly these), and which gates each may
# use. Review has no "none": an ungated review would let any output ship.
PHASE_POSTCONDITIONS: Mapping[str, frozenset[Postcondition]] = MappingProxyType(
    {
        "research": frozenset({Postcondition.RESEARCH_NOTE_FRESH, Postcondition.NONE}),
        "build": frozenset({Postcondition.INCREMENT_BUILT, Postcondition.NONE}),
        "review": frozenset({Postcondition.REVIEW_VERDICT}),
    }
)


def _kind(value: object) -> str:
    return type(value).__name__


def _check_int(field: str, value: object, lo: int, hi: int | None) -> None:
    """Raise ``ConfigError(field)`` unless ``value`` is an int (not bool) in range."""
    # bool is a subclass of int: `cycles = true` must not pass as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(field, f"must be an integer, got {_kind(value)}")
    if value < lo or (hi is not None and value > hi):
        bound = f">= {lo}" if hi is None else f"between {lo} and {hi}"
        raise ConfigError(field, f"must be {bound}, got {value}")


def _check_str(field: str, value: object) -> str:
    """Return ``value`` if it is a str, else raise ``ConfigError(field)``."""
    if not isinstance(value, str):
        raise ConfigError(field, f"must be a string, got {_kind(value)}")
    return value


@dataclass(frozen=True)
class Window:
    """Hours during which a night may start a cycle: [start_hour, end_hour).

    Raises ``ConfigError`` (field ``start_hour`` or ``end_hour``) if either is
    not an int, is out of range (start 0..23, end 1..24), or end <= start.
    Windows that wrap midnight are not representable in schema version 1.
    """

    start_hour: int
    end_hour: int

    def __post_init__(self) -> None:
        _check_int("start_hour", self.start_hour, _START_HOUR_MIN, _START_HOUR_MAX)
        _check_int("end_hour", self.end_hour, _END_HOUR_MIN, _END_HOUR_MAX)
        if self.end_hour <= self.start_hour:
            raise ConfigError(
                "end_hour",
                f"must be greater than the start hour ({self.start_hour}); "
                f"the end hour is exclusive, got {self.end_hour}",
            )


@dataclass(frozen=True)
class Phase:
    """How one phase runs: which agent, on which model, gated how, with which tools.

    Raises ``ConfigError`` with field ``agent``, ``model``, ``postcondition``,
    ``allow_unrestricted_bash``, ``allowed_tools`` or ``allowed_tools[<i>]``
    (``i`` = index of the first offending rule; for a duplicate, the index of
    the repeat). Which postcondition a phase may use is checked by ``Config``,
    since it depends on the phase's name.
    """

    agent: str
    model: str
    postcondition: Postcondition
    allowed_tools: tuple[str, ...]
    allow_unrestricted_bash: bool = False

    def __post_init__(self) -> None:
        agent = _check_str("agent", self.agent)
        if not _AGENT_NAME.fullmatch(agent):
            raise ConfigError(
                "agent",
                f"must match {_AGENT_NAME.pattern} (lowercase letters, digits, "
                f"hyphens), got {agent!r}",
            )
        model = _check_str("model", self.model)
        if not _MODEL_NAME.fullmatch(model):
            raise ConfigError("model", f"must be a non-empty name without spaces, got {model!r}")
        if not isinstance(self.postcondition, Postcondition):
            raise ConfigError(
                "postcondition", f"must be a Postcondition, got {_kind(self.postcondition)}"
            )
        if not isinstance(self.allow_unrestricted_bash, bool):
            raise ConfigError(
                "allow_unrestricted_bash",
                f"must be a boolean, got {_kind(self.allow_unrestricted_bash)}",
            )
        self._check_allowed_tools()

    def _check_allowed_tools(self) -> None:
        tools = self.allowed_tools
        if not isinstance(tools, tuple):
            raise ConfigError("allowed_tools", f"must be a tuple of strings, got {_kind(tools)}")
        if not tools:
            # dontAsk denies anything not pre-approved, so an empty list is a
            # phase that can do nothing.
            raise ConfigError("allowed_tools", "must list at least one tool")
        seen: set[str] = set()
        for index, rule in enumerate(tools):
            field = f"allowed_tools[{index}]"
            _check_str(field, rule)
            if rule in seen:
                raise ConfigError(field, f"duplicate rule {rule!r}")
            seen.add(rule)
            problem = rule_violation(rule, allow_unrestricted_bash=self.allow_unrestricted_bash)
            if problem is not None:
                raise ConfigError(field, f"{rule!r} {problem}")


@dataclass(frozen=True)
class Config:
    """A whole, valid nightloop configuration (schema version 1).

    Raises ``ConfigError`` with field ``backlog``, ``cycles``, ``window``,
    ``<phase>`` (not a ``Phase``) or ``<phase>.postcondition`` (a gate that
    phase may not use, per ``PHASE_POSTCONDITIONS``).
    """

    backlog: str
    cycles: int
    window: Window
    research: Phase
    build: Phase
    review: Phase

    def __post_init__(self) -> None:
        _check_backlog(self.backlog)
        _check_int("cycles", self.cycles, _CYCLES_MIN, None)
        if not isinstance(self.window, Window):
            raise ConfigError("window", f"must be a Window, got {_kind(self.window)}")
        for name, allowed in PHASE_POSTCONDITIONS.items():
            phase = getattr(self, name)
            if not isinstance(phase, Phase):
                raise ConfigError(name, f"must be a Phase, got {_kind(phase)}")
            if phase.postcondition not in allowed:
                choices = ", ".join(sorted(p.value for p in allowed))
                raise ConfigError(
                    f"{name}.postcondition",
                    f"{phase.postcondition.value!r} is not allowed for the {name} "
                    f"phase; expected one of: {choices}",
                )


def _check_backlog(value: object) -> None:
    """Raise ``ConfigError("backlog")`` unless ``value`` is a safe repo-relative file path."""
    backlog = _check_str("backlog", value)
    if backlog == "":
        raise ConfigError("backlog", "must be a non-empty path")
    if "\\" in backlog:
        raise ConfigError("backlog", f"must use '/' separators, got {backlog!r}")
    path = PurePosixPath(backlog)
    if path.is_absolute() or backlog.startswith("~"):
        raise ConfigError("backlog", f"must be relative to the repo root, got {backlog!r}")
    if ".." in path.parts:
        raise ConfigError("backlog", f"must not contain '..', got {backlog!r}")
    if not path.parts or path.parts == (".",):
        raise ConfigError("backlog", f"must name a file, got {backlog!r}")
