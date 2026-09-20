"""Turns a validated context-editing policy into the Messages API's
`context_management` edit dict.

Pure: no I/O, no `anthropic` import, no env reads. The only thing this module
knows about the network is the *shape* of one JSON object.

Everything is validated in `__post_init__`, so a policy that exists is one the
API will accept as well-formed. Interior code - `to_edit()`, `to_config()` -
assumes valid input and never re-checks.

Two strategies live here, both behind the same beta and both satisfying
`preview.EditPolicy` structurally:

    ClearToolUsesPolicy   clear_tool_uses_20250919  - drops old tool results
    ClearThinkingPolicy   clear_thinking_20251015   - drops old thinking blocks

Shapes verified against the generated types of `anthropic==0.121.0`, not the
docs prose:
    types/beta/beta_clear_tool_uses_20250919_edit_param.py
    types/beta/beta_tool_uses_trigger_param.py      (type/value)
    types/beta/beta_tool_uses_keep_param.py         (type/value, tool_uses only)
    types/beta/beta_input_tokens_clear_at_least_param.py
    types/beta/beta_clear_thinking_20251015_edit_param.py  (type + optional keep)
    types/beta/beta_thinking_turns_param.py         (type/value, both Required)
    types/beta/beta_all_thinking_turns_param.py     ({"type": "all"} object form)
    types/anthropic_beta_param.py:29                (the beta literal below)

See the research notes this came from:
    research/2026-08-11-context-editing-preview.md
    research/2026-09-08-context-editing-clear-thinking-preview.md
"""

from __future__ import annotations

import dataclasses
from typing import Literal

# The beta header this strategy ships behind. A real literal in the SDK's
# AnthropicBetaParam union, so a typo is a type error rather than a no-op.
BETA = "context-management-2025-06-27"

# The tool-result strategy. Kept under the bare name `STRATEGY` that `main.py`
# and `test_preview.py` already import. `compact_20260112` remains out of scope -
# it needs a different beta (`compact-2026-01-12`); see the README.
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


# The sibling strategy: clears reasoning, not tool results. Same beta, no
# trigger of its own - see `ClearThinkingPolicy`.
STRATEGY_CLEAR_THINKING = "clear_thinking_20251015"

# `keep` for the thinking strategy is measured in assistant *turns* that carried
# thinking, never in tokens or tool uses. The object form's discriminator.
KEEP_THINKING_KIND = "thinking_turns"

# The documented string shorthand for "keep every thinking block". The SDK also
# accepts an object form, `{"type": "all"}` (BetaAllThinkingTurnsParam); the two
# mean the same thing, and one spelling for one meaning is enough here.
KEEP_ALL = "all"


@dataclasses.dataclass(frozen=True)
class ClearThinkingPolicy:
    """A `clear_thinking_20251015` edit that is well-formed by construction.

    Fields:
      keep  how much prior reasoning survives the edit. Three forms, one field:
              ``N`` (int > 0)  keep the N most recent assistant thinking-turns,
                               serialised as {"type": "thinking_turns", "value": N}
              ``"all"``        keep every thinking block
              ``None``         omit the field and take the model's default,
                               which is model-specific: Opus 4.5+ / Sonnet 4.6+
                               keep all prior turns, earlier Opus/Sonnet and
                               every Haiku keep only the last one

    Unlike `ClearToolUsesPolicy` there is **no trigger, no `clear_at_least`, no
    `exclude_tools`, no `clear_tool_inputs`**: the SDK's edit param has exactly
    two fields, `type` and an optional `keep`. The edit always fires, so
    `applied: False` from a preview means there was nothing left to clear - not
    that a threshold went unmet.

    Failure modes - all raised at construction, never at call time:
      ``ValueError`` if ``keep`` is an int ``<= 0`` (zero thinking turns kept is
      spelled by the API as clearing everything, which is what an omitted or
      exhausted `keep` already does, and the docs state ``value`` must be > 0),
      or a ``str`` other than ``"all"``.
      ``TypeError`` if ``keep`` is not ``int | str | None``. ``bool`` is
      rejected explicitly because it is an ``int`` subclass: ``keep=True`` would
      otherwise serialise as ``{"type": "thinking_turns", "value": true}``, a
      request no caller meant to send.
    """

    keep: int | Literal["all"] | None = None

    def __post_init__(self) -> None:
        if self.keep is None:
            return

        # Checked before `int` because `isinstance(True, int)` is True.
        if isinstance(self.keep, bool):
            raise TypeError(
                "keep must be a turn count, 'all', or None; got the bool "
                f"{self.keep!r}, which is not a number of thinking turns"
            )

        if isinstance(self.keep, int):
            if self.keep <= 0:
                raise ValueError(
                    f"keep must be >= 1 when it is a count, got {self.keep}"
                )
            return

        if isinstance(self.keep, str):
            if self.keep != KEEP_ALL:
                raise ValueError(
                    f"the only string keep the API accepts is {KEEP_ALL!r}, "
                    f"got {self.keep!r}"
                )
            return

        raise TypeError(
            "keep must be an int, 'all', or None, got "
            f"{type(self.keep).__name__} ({self.keep!r})"
        )

    def to_edit(self) -> dict[str, object]:
        """The single edit object, exactly as the API expects it.

        `keep` is **omitted** when unset rather than emitted as `null`: an
        omitted `keep` means "use the model default", while `{"keep": null}` is
        a different request the SDK's `total=False` TypedDict cannot express.

        Cannot fail: `keep` was validated at construction.
        """
        edit: dict[str, object] = {"type": STRATEGY_CLEAR_THINKING}
        if self.keep is None:
            return edit
        if self.keep == KEEP_ALL:
            edit["keep"] = KEEP_ALL
            return edit
        edit["keep"] = {"type": KEEP_THINKING_KIND, "value": self.keep}
        return edit

    def to_config(self) -> dict[str, object]:
        """The `context_management` value for a `count_tokens` or `create` call.

        One edit only. Combining this with `clear_tool_uses_20250919` is out of
        scope here, and the docs require `clear_thinking_20251015` to be listed
        first when it is combined.

        Cannot fail.
        """
        return {"edits": [self.to_edit()]}
