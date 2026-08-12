"""Builds the synthetic conversation this example compacts.

Pure and deterministic: the same `(turns, chars_per_turn)` always produces the
same messages, so two runs differ only if the API changed. No I/O, no
`anthropic` import, no clock, no randomness.

Deliberately *not* imported from `examples/context-editing-preview/transcript.py`,
which builds a tool-use transcript for a different strategy. Examples in this
repo are self-contained by design; the shared thing is the idea, not the module.

Compaction's trigger is measured in input tokens and floors at 50,000, so this
fixture is sized in characters and has to be genuinely large - there is no
`tool_uses` trigger to fire cheaply on a small transcript. Roughly four
characters per token means ~200,000 characters just to reach the floor, which is
why `main.py` counts the real thing before spending anything.

See the research note this came from:
    research/2026-08-12-server-side-compaction.md
"""

from __future__ import annotations

_OPENING = (
    "Help me reconstruct the ingest service incident. I will paste notes one at "
    "a time; keep the timeline as we go. "
)

_FINAL_QUESTION = (
    "That is everything I have. Summarise the timeline of the incident and name "
    "the change that caused it."
)

# Long enough that a turn's numbered label and the opening framing both survive
# truncation, so every turn stays distinguishable from every other.
MIN_CHARS_PER_TURN = len(_OPENING) + 32

# Real words rather than a repeated character: filler that tokenizes like prose
# keeps the token counts in the same ballpark as a genuine transcript.
_USER_WORDS = (
    "note excerpt shard replica queue lag offset partition consumer checkpoint "
    "retry backoff quota tenant migration rollout region cache index dashboard "
    "alert runbook rollback canary drain latency budget threshold deploy revert "
)

_ASSISTANT_WORDS = (
    "recorded timeline entry follows the previous one and refines the ordering "
    "of the events observed across the affected shards during the window under "
    "discussion including the operator actions taken and their apparent effect "
)


def _sized(prefix: str, words: str, chars: int) -> str:
    """``prefix`` followed by word-shaped filler, exactly ``chars`` characters."""
    body = prefix + words * (chars // len(words) + 1)
    return body[:chars]


def build_transcript(turns: int, chars_per_turn: int) -> list[dict[str, object]]:
    """A conversation of ``turns`` user/assistant exchanges, plus a final ask.

    The result is ``2 * turns + 1`` messages with strictly alternating roles,
    ending on a **user** turn - the exact shape you would send to ask for the
    next assistant turn, which is when compaction fires. Every message except the
    closing question is exactly ``chars_per_turn`` characters, so the
    transcript's size is a function of its arguments and nothing else.

    Each turn is numbered in its text, so a summary of this transcript is
    checkable by eye: a model that invents an event that is not in it is visibly
    wrong.

    Pure. Failure modes: ``ValueError`` if ``turns < 1`` (a conversation with
    nothing to summarise) or ``chars_per_turn < MIN_CHARS_PER_TURN`` - a body too
    short to hold its own numbered label would make turns indistinguishable, and
    the bound is one named constant rather than a guess at the call site.
    """
    if turns < 1:
        raise ValueError(f"turns must be >= 1, got {turns}")
    if chars_per_turn < MIN_CHARS_PER_TURN:
        raise ValueError(
            f"chars_per_turn must be >= {MIN_CHARS_PER_TURN}, got {chars_per_turn}"
        )

    messages: list[dict[str, object]] = []
    for index in range(turns):
        opening = _OPENING if index == 0 else ""
        messages.append(
            {
                "role": "user",
                "content": _sized(
                    f"[note {index:03d}] {opening}", _USER_WORDS, chars_per_turn
                ),
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": _sized(
                    f"[timeline {index:03d}] ", _ASSISTANT_WORDS, chars_per_turn
                ),
            }
        )

    messages.append({"role": "user", "content": _FINAL_QUESTION})
    return messages
