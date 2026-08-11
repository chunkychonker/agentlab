"""Turns a validated tool-result clearing policy into the Messages API's
`context_management` edit dict.

Pure: no I/O, no `anthropic` import, no env reads. The only thing this module
knows about the network is the *shape* of one JSON object.

Everything is validated in `__post_init__`, so a `ClearToolUsesPolicy` that
exists is a policy the API will accept as well-formed. Interior code -
`to_edit()`, `to_config()` - assumes valid input and never re-checks.

Shapes verified against the generated types of `anthropic==0.121.0`, not the
docs prose:
    types/beta/beta_clear_tool_uses_20250919_edit_param.py
    types/beta/beta_tool_uses_trigger_param.py      (type/value)
    types/beta/beta_tool_uses_keep_param.py         (type/value, tool_uses only)
    types/beta/beta_input_tokens_clear_at_least_param.py
    types/anthropic_beta_param.py:29                (the beta literal below)

See the research note this came from:
    research/2026-08-11-context-editing-preview.md
"""

from __future__ import annotations

import dataclasses
from typing import Literal

# The beta header this strategy ships behind. A real literal in the SDK's
# AnthropicBetaParam union, so a typo is a type error rather than a no-op.
BETA = "context-management-2025-06-27"

# The one strategy this example covers. `clear_thinking_20251015` and
# `compact_20260112` are deliberately out of scope - see the README.
STRATEGY = "clear_tool_uses_20250919"

TriggerKind = Literal["input_tokens", "tool_uses"]

# `Literal` is erased at runtime, so the accepted values live here too and the
# check in __post_init__ reads from this one tuple.
TRIGGER_KINDS: tuple[TriggerKind, ...] = ("input_tokens", "tool_uses")

# The API measures `keep` only in tool uses and `clear_at_least` only in input
# tokens. Neither is a caller choice, so neither is a constructor parameter.
KEEP_KIND = "tool_uses"
CLEAR_AT_LEAST_KIND = "input_tokens"


@dataclasses.dataclass(frozen=True)
class ClearToolUsesPolicy:
    """A `clear_tool_uses_20250919` edit that is well-formed by construction.

    Fields:
      keep            how many of the most recent tool uses keep their results.
      trigger_kind    "tool_uses" fires on a count of tool uses; "input_tokens"
                      fires on prompt size (the API's default, 100k).
      trigger_value   the threshold for that kind.
      clear_at_least  optional floor, in input tokens. **All-or-nothing**: if the
                      API cannot clear at least this much, it applies no edit at
                      all rather than a partial one.
      exclude_tools   tool names whose results are never cleared.

    Failure modes - all raised at construction, never at call time:
      ``ValueError`` if ``keep < 0``, ``trigger_value < 1``,
      ``clear_at_least`` is present and ``< 1``, ``trigger_kind`` is not one of
      ``TRIGGER_KINDS``, or any entry of ``exclude_tools`` is empty or blank.
      ``TypeError`` if ``exclude_tools`` is a bare ``str`` - iterating one
      silently yields characters, which would exclude a set of one-letter tool
      names that do not exist and quietly clear the tool the caller meant to
      protect.

    ``keep`` rejects ``< 0`` rather than ``< 1`` because the SDK types it as a
    bare ``int`` with no minimum and the docs never state one; ``keep=0`` may
    well be legal. See the README's open questions.
    """

    keep: int
    trigger_kind: TriggerKind
    trigger_value: int
    clear_at_least: int | None = None
    exclude_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.keep < 0:
            raise ValueError(f"keep must be >= 0, got {self.keep}")

        if self.trigger_kind not in TRIGGER_KINDS:
            allowed = ", ".join(repr(kind) for kind in TRIGGER_KINDS)
            raise ValueError(
                f"trigger_kind must be one of {allowed}, got {self.trigger_kind!r}"
            )

        if self.trigger_value < 1:
            raise ValueError(f"trigger_value must be >= 1, got {self.trigger_value}")

        if self.clear_at_least is not None and self.clear_at_least < 1:
            raise ValueError(
                f"clear_at_least must be >= 1 when set, got {self.clear_at_least}"
            )

        if isinstance(self.exclude_tools, str):
            raise TypeError(
                "exclude_tools must be a sequence of tool names, not a bare str "
                f"({self.exclude_tools!r} would be iterated character by character)"
            )

        names = tuple(self.exclude_tools)
        for index, name in enumerate(names):
            if not isinstance(name, str):
                raise TypeError(
                    f"exclude_tools[{index}] must be a str, got {type(name).__name__}"
                )
            if not name.strip():
                raise ValueError(
                    f"exclude_tools[{index}] is empty or blank; a nameless tool "
                    "cannot be excluded"
                )

        # Normalise at the boundary so the frozen instance really is hashable
        # and immutable even when the caller passed a list.
        object.__setattr__(self, "exclude_tools", names)

    def to_edit(self) -> dict[str, object]:
        """The single edit object, exactly as the API expects it.

        Optional keys are **omitted** when unset rather than emitted as `null`:
        `{"clear_at_least": null}` is a different request from one that never
        mentioned `clear_at_least`, and only the second is what "unset" means.

        Cannot fail: every value was validated at construction.
        """
        edit: dict[str, object] = {
            "type": STRATEGY,
            "trigger": {"type": self.trigger_kind, "value": self.trigger_value},
            "keep": {"type": KEEP_KIND, "value": self.keep},
        }
        if self.clear_at_least is not None:
            edit["clear_at_least"] = {
                "type": CLEAR_AT_LEAST_KIND,
                "value": self.clear_at_least,
            }
        if self.exclude_tools:
            edit["exclude_tools"] = list(self.exclude_tools)
        return edit

    def to_config(self) -> dict[str, object]:
        """The `context_management` value for a `count_tokens` or `create` call.

        Cannot fail.
        """
        return {"edits": [self.to_edit()]}
