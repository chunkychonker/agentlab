"""Builds the synthetic tool-heavy request this preview measures.

Pure and deterministic: the same `(rounds, result_chars)` always produces the
same messages, so two runs of the preview differ only if the API changed. No
I/O, no `anthropic` import, no clock, no randomness.

The transcript ends on a `tool_result` user turn - the exact shape you would
send to ask for the next assistant turn, which is when context editing matters.

See the research note this came from:
    research/2026-08-11-context-editing-preview.md
"""

from __future__ import annotations

# The one tool the synthetic transcript calls. Named once so the advertised
# schema, the tool_use blocks, and any `exclude_tools` entry cannot drift.
TOOL_NAME = "read_document"

_USER_REQUEST = (
    "Read every document in the ingest service's runbook set and tell me which "
    "steps changed between revisions."
)

_ASSISTANT_PREAMBLE = "Reading the next document."

# Real words rather than a repeated character: filler that tokenizes like prose
# keeps the token counts in the same ballpark as a genuine transcript.
_FILLER_WORDS = (
    "service latency budget rollout region shard cache index queue retry "
    "backoff quota tenant schema migration checkpoint offset partition replica "
    "consumer lag threshold dashboard alert runbook rollback canary drain "
)


def _filler(chars: int) -> str:
    """Exactly ``chars`` characters of deterministic word-shaped text."""
    repeats = chars // len(_FILLER_WORDS) + 1
    return (_FILLER_WORDS * repeats)[:chars]


def tool_schema() -> dict[str, object]:
    """The wire definition of `read_document`, fresh each call.

    A new dict per call rather than a module constant, so a caller that mutates
    it cannot change what the next caller sends. Cannot fail.
    """
    return {
        "name": TOOL_NAME,
        "description": (
            "Read the full text of one document from the runbook set. Use this "
            "instead of recalling document contents from memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The document identifier, e.g. 'doc-0007'.",
                }
            },
            "required": ["document_id"],
        },
    }


def build_transcript(rounds: int, result_chars: int) -> list[dict[str, object]]:
    """A conversation of ``rounds`` complete tool-use round trips.

    The result is ``1 + 2 * rounds`` messages: one opening user turn, then per
    round an assistant turn holding one `tool_use` block and a user turn holding
    its one matching `tool_result`. Every `tool_use_id` is unique, and every
    `tool_result` body is exactly ``result_chars`` characters - which is what
    makes the tokens cleared by a `keep` of N predictable.

    Pure. Failure modes: ``ValueError`` if ``rounds < 1`` (a transcript with no
    tool use cannot demonstrate tool-use clearing) or ``result_chars < 1`` (an
    empty result body is not a result).
    """
    if rounds < 1:
        raise ValueError(f"rounds must be >= 1, got {rounds}")
    if result_chars < 1:
        raise ValueError(f"result_chars must be >= 1, got {result_chars}")

    body = _filler(result_chars)
    messages: list[dict[str, object]] = [{"role": "user", "content": _USER_REQUEST}]

    for index in range(rounds):
        use_id = f"toolu_{index:04d}"
        document_id = f"doc-{index:04d}"
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": _ASSISTANT_PREAMBLE},
                    {
                        "type": "tool_use",
                        "id": use_id,
                        "name": TOOL_NAME,
                        "input": {"document_id": document_id},
                    },
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": use_id,
                        "content": body,
                    }
                ],
            }
        )

    return messages
