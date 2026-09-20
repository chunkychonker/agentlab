"""Builds the synthetic thinking-heavy request this preview measures.

Pure and deterministic: the same `(turns, thinking_chars)` always produces the
same messages, so two runs of the preview differ only if the API changed. No
I/O, no `anthropic` import, no clock, no randomness.

Every assistant turn carries a `thinking` block followed by a `text` block -
the shape an extended-thinking model returns, and the shape
`clear_thinking_20251015` prunes. The transcript ends on a user turn, the exact
request you would send to ask for the next assistant turn, which is when context
editing matters.

The `signature` on every thinking block is a **synthetic placeholder**, not a
value Claude produced. Real signatures come back only from `messages.create`,
which this example never calls - that is the whole point of a $0 preview. The
evidence that `count_tokens` accepts an unverifiable one is the token-counting
docs' own worked example, which counts a visibly truncated signature and returns
a number; every documented signature-400 traces back to `messages.create`, not
to counting. That is a best read of the evidence, not a live confirmation - see
the README's open questions. If a live count does reject it, the error surfaces
loudly from the counter and this fixture is what needs replacing.

This module deliberately shares nothing with `transcript.py`: that fixture is
tool-heavy, this one is tool-free, and the two exist to be edited by different
strategies.

See the research note this came from:
    research/2026-09-08-context-editing-clear-thinking-preview.md
"""

from __future__ import annotations

# This fixture advertises no tools and contains no tool_use / tool_result
# blocks, unlike `transcript.py`'s. Named because it is the reason
# `preview_thinking.py` counts with an empty tool list, and the reason
# `clear_tool_uses_20250919` would find nothing here to clear.
TOOL_FREE = True

# A placeholder in the position where a real block carries
# `"signature": "EuYBCkQYAiJAgCs1le6/Pol5Z4/JMomVOouG..."`. Spelled so that any
# error message quoting it says what it is. Never send this to
# `messages.create`: that endpoint verifies signatures and will reject it with a
# 400 `invalid_request_error`.
SYNTHETIC_SIGNATURE = (
    "SYNTHETIC-NOT-A-REAL-SIGNATURE-this-block-was-never-generated-by-Claude-"
    "and-is-here-only-so-count-tokens-has-a-structurally-complete-thinking-block"
)

_USER_REQUEST = (
    "Our ingest service's p99 latency tripled after the last rollout. Work "
    "through the possible causes one at a time and tell me which to rule out "
    "first."
)

# Real words rather than a repeated character: filler that tokenizes like prose
# keeps the token counts in the same ballpark as a genuine transcript. This
# vocabulary is reasoning-shaped on purpose - it is standing in for the model's
# scratchpad, not for a document body.
_FILLER_WORDS = (
    "so if the rollout changed the batch size then the queue would drain "
    "slower which means the p99 climbs before the average does but that only "
    "holds when the consumer count stayed fixed let me check the other branch "
    "instead because a cache miss storm would show up in both percentiles "
)

_ASSISTANT_ANSWER = (
    "Rule out the consumer count first: it is one query and it eliminates half "
    "the branches above."
)


def _filler(chars: int) -> str:
    """Exactly ``chars`` characters of deterministic word-shaped text."""
    repeats = chars // len(_FILLER_WORDS) + 1
    return (_FILLER_WORDS * repeats)[:chars]


def build_thinking_transcript(
    turns: int, thinking_chars: int
) -> list[dict[str, object]]:
    """A conversation of ``turns`` assistant turns, every one of them thinking.

    The result is ``1 + 2 * turns`` messages: one opening user turn, then per
    turn an assistant message whose content is exactly
    ``[thinking block, text block]`` and a user follow-up. Every `thinking` body
    is exactly ``thinking_chars`` characters - which is what makes the tokens
    cleared by a `keep` of N predictable - and every one carries
    ``SYNTHETIC_SIGNATURE``.

    Pure. Failure modes: ``ValueError`` if ``turns < 1`` (a transcript with no
    thinking cannot demonstrate thinking clearing) or ``thinking_chars < 1``
    (an empty thinking body is not a thinking block; the API types the field as
    a required string and there would be nothing to clear).
    """
    if turns < 1:
        raise ValueError(f"turns must be >= 1, got {turns}")
    if thinking_chars < 1:
        raise ValueError(f"thinking_chars must be >= 1, got {thinking_chars}")

    body = _filler(thinking_chars)
    messages: list[dict[str, object]] = [{"role": "user", "content": _USER_REQUEST}]

    for index in range(turns):
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": body,
                        "signature": SYNTHETIC_SIGNATURE,
                    },
                    {"type": "text", "text": _ASSISTANT_ANSWER},
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Checked branch {index:02d}: it was not the cause. "
                    "Keep going."
                ),
            }
        )

    return messages
