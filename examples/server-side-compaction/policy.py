"""Turns a validated compaction policy into the Messages API's
`context_management` edit dict.

Pure: no I/O, no `anthropic` import, no env reads. The only thing this module
knows about the network is the *shape* of one JSON object.

Everything is validated in `__post_init__`, so a `CompactionPolicy` that exists
is a policy the API will accept as well-formed. Interior code - `to_edit()`,
`to_config()` - assumes valid input and never re-checks.

Shapes verified against the generated types of `anthropic==0.121.0`, not the
docs prose:
    types/beta/beta_compact_20260112_edit_param.py   (four fields, all optional
                                                      but `type`)
    types/beta/beta_input_tokens_trigger_param.py    (type/value, input_tokens
                                                      is the only trigger kind
                                                      compaction accepts)
    types/anthropic_beta_param.py:29                 (`compact-2026-01-12` is
                                                      *absent* - see BETA below)

See the research note this came from:
    research/2026-08-12-server-side-compaction.md
"""

from __future__ import annotations

import dataclasses

# The beta header this strategy ships behind.
#
# Unlike `context-management-2025-06-27`, this string is **not** a member of the
# SDK's `AnthropicBetaParam` literal union in 0.121.0. The alias is
# `Union[str, Literal[...]]`, so a typo type-checks fine and fails at the server
# with a 400. That is why it is a named constant used in exactly one place
# rather than a string literal at the call site.
BETA = "compact-2026-01-12"

# The one strategy this example covers. `clear_tool_uses_20250919` is a
# different beta and already shipped in examples/context-editing-preview/.
STRATEGY = "compact_20260112"

# Documented on both the Anthropic and the Bedrock compaction pages: the trigger
# value "must be at least 50,000 tokens". The SDK types it as a bare `int`, so
# the floor is server-enforced only - which is exactly why it is enforced here,
# at construction, instead of being discovered as a 400.
MIN_TRIGGER_TOKENS = 50_000

# Compaction's trigger has exactly one shape. There is no `tool_uses` form (that
# exists only for `clear_tool_uses_20250919`), so this is a constant, not a
# constructor parameter.
TRIGGER_KIND = "input_tokens"


@dataclasses.dataclass(frozen=True)
class CompactionPolicy:
    """A `compact_20260112` edit that is well-formed by construction.

    Fields:
      trigger_tokens          prompt size, in input tokens, at which the server
                              summarises older context. The API's own default is
                              150,000; the documented floor is 50,000.
      pause_after_compaction  when true the turn stops as soon as the summary
                              exists: `stop_reason: "compaction"` and only the
                              compaction block in `content`. You then append that
                              response and re-request to get the real answer.
      instructions            a summarization prompt that **replaces** the
                              default one entirely - it does not append. The
                              default ends "You must wrap your summary in a
                              `<summary></summary>` block", so a custom prompt
                              silently drops that contract too.

    Failure modes - all raised at construction, never at call time:
      ``ValueError`` if ``trigger_tokens < MIN_TRIGGER_TOKENS``, or if
      ``instructions`` is present and empty or whitespace-only. A blank string is
      not "use the default": it replaces the default with nothing.
      ``TypeError`` if ``instructions`` is neither ``str`` nor ``None``.
    """

    trigger_tokens: int
    pause_after_compaction: bool = False
    instructions: str | None = None

    def __post_init__(self) -> None:
        if self.trigger_tokens < MIN_TRIGGER_TOKENS:
            raise ValueError(
                f"trigger_tokens must be >= {MIN_TRIGGER_TOKENS} (the documented "
                f"server floor), got {self.trigger_tokens}"
            )

        if self.instructions is None:
            return

        if not isinstance(self.instructions, str):
            raise TypeError(
                "instructions must be a str or None, got "
                f"{type(self.instructions).__name__}"
            )

        if not self.instructions.strip():
            raise ValueError(
                "instructions is empty or blank; it *replaces* the default "
                "summarization prompt rather than appending to it, so a blank "
                "string asks the model to summarise with no instructions at all. "
                "Pass None to keep the default."
            )

    def to_edit(self) -> dict[str, object]:
        """The single edit object, exactly as the API expects it.

        `instructions` is **omitted** when unset rather than emitted as `null`:
        `{"instructions": null}` is a different request from one that never
        mentioned it, and only the second means "use the default prompt".

        `pause_after_compaction` is always emitted, because ``False`` is a real
        choice ("generate the answer in the same turn"), not an absent one.

        Cannot fail: every value was validated at construction.
        """
        edit: dict[str, object] = {
            "type": STRATEGY,
            "trigger": {"type": TRIGGER_KIND, "value": self.trigger_tokens},
            "pause_after_compaction": self.pause_after_compaction,
        }
        if self.instructions is not None:
            edit["instructions"] = self.instructions
        return edit

    def to_config(self) -> dict[str, object]:
        """The `context_management` value for a `messages.create` call.

        Cannot fail.
        """
        return {"edits": [self.to_edit()]}
