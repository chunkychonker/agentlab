"""Reads a wire-shaped Messages response into a compaction report.

This is the pure core. It consumes plain `Mapping`s - what
`response.model_dump(mode="json")` produces - so it never imports `anthropic`,
never reads an env var, and never touches the network. `main.py` does the one
`model_dump` call that stands between the SDK and this module.

Two things here that the vendors' own example snippets get wrong:

* A compaction block's `content` may be **null**, meaning the compaction failed
  to produce a valid summary. Both docs pages slice it (`block["content"][:200]`)
  and would crash. `summary` is `None` in that case, and nothing here indexes it.
* The top-level `usage.input_tokens` / `usage.output_tokens` **exclude** the
  compaction iteration. Anything summing those two numbers under-reports the bill
  by an entire sampling pass over the pre-compaction prompt. When
  `usage.iterations` is present it is the only source of truth used here.

See the research note this came from:
    research/2026-08-12-server-side-compaction.md
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence

# The `type` discriminators this module matches on. All three happen to be the
# same string in three different unions - a content block type, a stop reason,
# and a usage-iteration type - which is precisely why they are named separately.
COMPACTION_BLOCK_TYPE = "compaction"
COMPACTION_STOP_REASON = "compaction"
COMPACTION_ITERATION_TYPE = "compaction"

# `applied_edits` is deliberately not consulted anywhere in this module:
# `BetaContextManagementResponse.applied_edits` unions only the two *clear* edit
# responses, so it never mentions compaction at all.


@dataclasses.dataclass(frozen=True)
class CompactionReport:
    """What one response says about compaction, including the true bill.

    Fields:
      triggered                 the response carries a compaction: a compaction
                                block, a `compaction` stop reason, or a
                                compaction usage iteration.
      paused                    the turn stopped at the summary
                                (`stop_reason == "compaction"`), so no answer was
                                generated and the caller must re-request.
      summary                   the summary text, or ``None`` when the compaction
                                failed to produce one. ``None`` is a real,
                                documented outcome, not a missing value.
      encrypted_content         opaque metadata from prior compaction. Carried
                                here only so a report can report it; the
                                round-trip itself goes through
                                `continuation_messages`, verbatim.
      compaction_input_tokens   summed over `usage.iterations` entries of type
      compaction_output_tokens  "compaction". Zero when no *new* compaction ran.
      message_input_tokens      summed over every other iteration, or the
      message_output_tokens     top-level usage when `iterations` is absent.

    Failure modes: ``ValueError`` if any count is negative, if a summary or a
    compaction iteration is reported without `triggered`, if `paused` without
    `triggered`, or if `summary` is an empty or whitespace-only string - the API
    forbids empty compaction content, so blank text is a malformed response and
    must not be confused with the null (failed) case.
    """

    triggered: bool
    paused: bool
    summary: str | None
    encrypted_content: str | None
    compaction_input_tokens: int
    compaction_output_tokens: int
    message_input_tokens: int
    message_output_tokens: int

    def __post_init__(self) -> None:
        counts = {
            "compaction_input_tokens": self.compaction_input_tokens,
            "compaction_output_tokens": self.compaction_output_tokens,
            "message_input_tokens": self.message_input_tokens,
            "message_output_tokens": self.message_output_tokens,
        }
        for name, value in counts.items():
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")

        if self.summary is not None and not self.summary.strip():
            raise ValueError(
                "summary is present but blank; the API forbids empty compaction "
                "content, and a failed compaction is reported as None, not ''"
            )

        if not self.triggered:
            if self.paused:
                raise ValueError(
                    "paused means stop_reason == 'compaction', which cannot "
                    "happen without a compaction"
                )
            if self.summary is not None:
                raise ValueError("a summary cannot exist without a compaction")
            if self.compaction_input_tokens or self.compaction_output_tokens:
                raise ValueError(
                    "compaction iteration tokens were billed without a compaction"
                )

    @property
    def total_billed_tokens(self) -> int:
        """Every token this request is billed for, compaction included.

        This is the number `usage.input_tokens + usage.output_tokens` is *not*.
        Derived rather than stored, so it cannot drift from its four parts.
        """
        return (
            self.compaction_input_tokens
            + self.compaction_output_tokens
            + self.message_input_tokens
            + self.message_output_tokens
        )


def read_response(response: Mapping[str, object]) -> CompactionReport:
    """Read one wire-shaped Messages response into a `CompactionReport`.

    ``response`` is the JSON object the API returned - in practice
    ``message.model_dump(mode="json")``. Pure and idempotent.

    When several compaction blocks are present the **last** one is reported: the
    docs state that "the last compaction block reflects the final state of the
    prompt".

    Failure modes - every one of them raises rather than reporting a zero:
      ``ValueError`` if `content` or `usage` is missing or of the wrong shape, if
      `usage.iterations` is present but empty (it is documented as present only
      when a compaction fired, so an empty array contradicts itself and would
      silently total zero tokens), if an iteration entry is malformed, or if a
      compaction block's `content` / `encrypted_content` is neither a string nor
      null. A compaction block whose `content` is null is **not** a failure here:
      it is reported as ``summary=None``.
    """
    content = _require_sequence(response, "content")
    usage = _require_mapping(response, "usage")

    blocks = [
        block
        for block in content
        if isinstance(block, Mapping) and block.get("type") == COMPACTION_BLOCK_TYPE
    ]
    last = blocks[-1] if blocks else None

    summary = None if last is None else _optional_str(last, "content", "content block")
    encrypted = (
        None
        if last is None
        else _optional_str(last, "encrypted_content", "content block")
    )

    paused = response.get("stop_reason") == COMPACTION_STOP_REASON

    compaction_in, compaction_out, message_in, message_out = _split_usage(usage)

    triggered = bool(blocks) or paused or bool(compaction_in or compaction_out)

    return CompactionReport(
        triggered=triggered,
        paused=paused,
        summary=summary,
        encrypted_content=encrypted,
        compaction_input_tokens=compaction_in,
        compaction_output_tokens=compaction_out,
        message_input_tokens=message_in,
        message_output_tokens=message_out,
    )


def continuation_messages(
    original: Sequence[Mapping[str, object]],
    response_content: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """The message list that carries a conversation past a compaction boundary.

    Returns ``original`` plus exactly one appended assistant message holding
    ``response_content`` verbatim - compaction block included, `encrypted_content`
    included. Nothing is dropped locally: on the next request the **server**
    discards every block before the compaction block. That is why the block must
    be round-tripped rather than rebuilt, and why a client that "cleans up" its
    own history here loses the opaque metadata silently.

    Blocks are shallow-copied so the returned list does not alias the caller's
    dicts at the top level; every key and value is preserved unchanged.

    Pure. Failure modes: ``ValueError`` if ``response_content`` is empty - an
    assistant message with no content is not a valid message, and appending one
    would 400 on the next request.
    """
    blocks = [dict(block) for block in response_content]
    if not blocks:
        raise ValueError(
            "response_content is empty; an assistant message must have content"
        )
    return [dict(message) for message in original] + [
        {"role": "assistant", "content": blocks}
    ]


# --------------------------------------------------------------------------- #
# Private helpers. Each one fails loudly on a shape the API says cannot happen.
# --------------------------------------------------------------------------- #


def _require_sequence(
    response: Mapping[str, object], key: str
) -> Sequence[object]:
    value = response.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(
            f"response[{key!r}] must be a list, got {type(value).__name__}"
        )
    return value


def _require_mapping(response: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = response.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(
            f"response[{key!r}] must be an object, got {type(value).__name__}"
        )
    return value


def _optional_str(block: Mapping[str, object], key: str, where: str) -> str | None:
    value = block.get(key)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(
        f"{where} {key!r} must be a string or null, got {type(value).__name__}"
    )


def _require_int(entry: Mapping[str, object], key: str) -> int:
    value = entry.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(
            f"usage iteration {key!r} must be an integer, got {type(value).__name__}"
        )
    return value


def _split_usage(usage: Mapping[str, object]) -> tuple[int, int, int, int]:
    """Split a `usage` object into (compaction in/out, message in/out).

    `iterations` is absent whenever no *new* compaction ran - including when a
    previous compaction block was merely re-applied, which is free. In that case
    the top-level numbers are accurate and are used as-is. When `iterations` is
    present it supersedes them completely: mixing the two double-counts the
    non-compaction iterations.

    Every iteration that is not a compaction (`message`, `advisor_message`,
    `fallback_message`) is folded into the message totals, so the four numbers
    always sum to the whole `iterations` array.
    """
    iterations = usage.get("iterations")
    if iterations is None:
        top_level_in = _require_int(usage, "input_tokens")
        top_level_out = _require_int(usage, "output_tokens")
        return 0, 0, top_level_in, top_level_out

    if not isinstance(iterations, Sequence) or isinstance(iterations, (str, bytes)):
        raise ValueError(
            "usage['iterations'] must be a list or null, got "
            f"{type(iterations).__name__}"
        )
    if not iterations:
        raise ValueError(
            "usage['iterations'] is present but empty; it is documented as "
            "present only when a compaction fired, so this response reports a "
            "bill of zero tokens for work that was done"
        )

    compaction_in = compaction_out = message_in = message_out = 0
    for entry in iterations:
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"each usage iteration must be an object, got {type(entry).__name__}"
            )
        input_tokens = _require_int(entry, "input_tokens")
        output_tokens = _require_int(entry, "output_tokens")
        if entry.get("type") == COMPACTION_ITERATION_TYPE:
            compaction_in += input_tokens
            compaction_out += output_tokens
        else:
            message_in += input_tokens
            message_out += output_tokens

    return compaction_in, compaction_out, message_in, message_out
