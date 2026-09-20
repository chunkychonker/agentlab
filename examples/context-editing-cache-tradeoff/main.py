"""The imperative shell: previews the clear for $0, buys the one measurement
that cannot be previewed, and prints the payback.

This is the only file that imports `anthropic`, reads an environment variable,
or writes to a stream - and the SDK import is lazy, inside `main()`, so both
self-tests can import this module with no dependency installed.

The run is three calls in two halves:

  - the free half, twice to `beta.messages.count_tokens`: the same transcript
    plain, then under `context_management`. The difference is what the clear
    would remove, and it costs nothing.
  - the billed half, twice to `beta.messages.create`: turn A sends the whole
    transcript with the clear on, so the clear fires and the message-level cache
    prefix is invalidated; turn B sends one round more, so the prefix turn A
    re-cached gets read back. Turn A's `cache_creation_input_tokens` is the cost
    of the invalidation, and it is the number `count_tokens` cannot give you -
    the counting endpoint runs no caching logic and reports no cache fields at
    all.

Run it live (needs a key, and this one **costs money** - two to three cents):
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 main.py

Run the offline self-tests (no key, no network, no SDK installed):
    python3 test_compose.py
    python3 test_payback.py

See the research note this came from:
    research/2026-09-07-context-editing-cache-tradeoff.md
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from typing import NamedTuple

import compose
import payback
import transcript

# Cheapest current model at $2/MTok input whose 1,024-token minimum cacheable
# prefix this transcript clears comfortably. See knowledge/anthropic-models.md.
MODEL = "claude-sonnet-5"

# Sonnet 5's base *input* rate, re-checked 2026-09-07
# (knowledge/anthropic-models.md). Every dollar figure printed is derived from
# this one number, so a price change is a one-line edit here rather than a hunt
# through payback.py. Note that `payback_turns` itself does not depend on it.
BASE_USD_PER_MTOK = 2.0

API_KEY_ENV = "ANTHROPIC_API_KEY"

# The beta header context editing ships behind. Named in `compose` (the module
# that owns the edit shape) and re-exported here so the request builder below
# has one name to reach for.
BETA = compose.BETA

# The demo transcript: 12 complete tool-use round trips with 1600-character
# results. Twelve rounds against `KEEP = 3` means nine rounds' worth of results
# are cleared - deep history, well below the rolling breakpoint, which is the
# case `payback.py`'s model assumes.
ROUNDS = 12
RESULT_CHARS = 1600

# The clearing policy. `tool_uses` rather than the API's default `input_tokens`
# trigger (100k), which a demo transcript would never reach.
KEEP = 3
TRIGGER = 5

# Two of the four breakpoints are static and set here: one on the last tool, one
# on the last system block. The rest is what the growing message array may use.
STATIC_BREAKPOINTS = 2
MESSAGES_BUDGET = compose.MAX_BREAKPOINTS - STATIC_BREAKPOINTS

# The response cap. Small on purpose: output tokens are the expensive half of
# this demo and nothing here reads the model's prose.
MAX_TOKENS = 512

# Turn B reading back less than half of what turn A wrote means the re-cached
# prefix did not survive, so there is no payback to report. Half is generous - a
# healthy run reads back nearly all of it.
MIN_READ_FRACTION = 0.5

# How far the free preview's subtraction and the billed response's
# `cleared_input_tokens` may differ before the run refuses to pick one. They
# measure the same edit on the same messages, so they should agree exactly; 2%
# is slack for a tokenizer difference between the two endpoints, not for a
# disagreement about what was cleared.
TOLERANCE = 0.02

EXIT_OK = 0

# A missing key is a $0 skip, not a failure, and a skip exits 0 - the same
# contract as `examples/prompt-caching-tool-loop/`, whose live run also spends
# real money. (`context-editing-preview/` exits 1 instead: its live run is free,
# so refusing to run it is a result worth flagging.)
EXIT_NO_KEY = EXIT_OK

# A run that reached the API and learned nothing is a different thing entirely:
# it spent money. Each of these is a distinct diagnosis, so each gets its code.
EXIT_NO_INVALIDATION = 2
EXIT_NO_RECACHE = 3
EXIT_PREVIEW_MISMATCH = 4

MISSING_KEY_MESSAGE = (
    f"error: {API_KEY_ENV} is not set, so nothing was sent and nothing was spent; "
    "this demo needs one real generation pair (two to three cents) because the "
    "cache-write cost a clear forces cannot be previewed for $0 - run "
    "'python3 test_compose.py' and 'python3 test_payback.py' for the offline "
    "self-tests."
)

_NO_INVALIDATION_ADVICE = (
    "  The clearing turn was supposed to invalidate the cached message prefix\n"
    "  and write a new one. It did not. The usual causes, in the order they\n"
    "  bite (see 'When this run fails' in README.md):\n"
    "    - an identical request ran less than five minutes ago, so the edited\n"
    "      prefix was already cached and this turn read it instead of writing\n"
    "      it; wait out the 5-minute TTL and re-run\n"
    "    - the trigger did not fire (fewer than the configured tool uses)\n"
    "    - the cached prefix was under the model's minimum (1,024 tokens on\n"
    "      claude-sonnet-5); short prefixes are silently not cached, no error\n"
    "  If none of those apply, this run has corroborated the third-party claim\n"
    "  that clearing happens after the cache lookup without destroying the\n"
    "  prefix - which every Anthropic source contradicts. Say so in the note."
)

_NO_RECACHE_ADVICE = (
    "  Turn A re-cached a prefix and turn B did not read it back, so the write\n"
    "  the clear forced never paid anything back. The usual causes:\n"
    "    - the anchor breakpoint is in the wrong place, so the cached region\n"
    "      spans content the next clear rewrites (this is exactly what\n"
    "      compose.clearing_boundary exists to prevent)\n"
    "    - more than five minutes passed between the two turns\n"
    "    - the model id or the tools/system prefix changed between the turns"
)

# The system block: 12 sections of fixed policy text, ~5,300 characters or
# roughly 1,300 tokens. Sized, not decorative - see the README's "Deviations
# from the research note". Two prefixes have to clear claude-sonnet-5's
# 1,024-token minimum cacheable prefix, and a prefix under the minimum is
# processed *without* caching and reports no error at all:
#
#   - `tools` + `system`, or the two static breakpoints cache nothing and the
#     clearing turn has no surviving cache to read (acceptance criterion 3);
#   - `tools` + `system` + the message prefix up to the clearing-aware anchor.
#     After the clear that message prefix is nine placeholder round trips,
#     roughly 900 tokens - on its own, below the floor.
SYSTEM_SECTIONS = 12
MIN_SYSTEM_CHARS = 4_800

_SYSTEM_PREAMBLE = (
    "You are a documentation triage assistant working through a runbook set "
    "one document at a time. The sections below are your standing "
    "instructions. They never change between requests, which is half the point "
    "of this example: an identical prefix is a cacheable prefix, and anything "
    "varying here - a timestamp, a request id, a re-sorted list - would make "
    "every turn a fresh cache write."
)

# One template, one loop variable. Nothing else may vary.
_SECTION_TEMPLATE = (
    "Section {n:02d}. Read what the tools return rather than recalling a "
    "document's contents from memory, keep a short running list of the "
    "revisions you have already seen, and name the document identifier "
    "whenever you refer to a step. Quote a revision number exactly as the tool "
    "reported it, never paraphrase one, and say plainly when two revisions "
    "disagree instead of choosing between them. When you have read enough to "
    "answer, answer in at most three sentences."
)


class NoInvalidation(RuntimeError):
    """The clear did not invalidate and re-write the message prefix.

    Raised instead of reporting a payback of zero: a run that paid for two
    generations and saw no invalidation has measured nothing. Also raised when
    the response carries no `applied_edits`, or when the free preview reports an
    edit that removed no tokens - the same diagnosis from a different angle.
    """


class NoRecache(RuntimeError):
    """Turn B did not read back the prefix turn A's clear forced it to write.

    Raised rather than reporting a payback the run has no evidence for: the
    whole model assumes the re-written prefix is reusable on later turns.
    """


class PreviewMismatch(RuntimeError):
    """Two measurements of the same edit disagree.

    Either the free preview's two counts contradict each other about the
    original size, or the preview's subtraction and the billed response's
    `cleared_input_tokens` differ by more than `TOLERANCE`. Raised rather than
    silently picking one: every number in the report is derived from these.
    """


class _Count(NamedTuple):
    """One response from the token-counting endpoint.

    `original_input_tokens` is `None` when the response carried no
    `context_management` object - no edit was applied, either because none was
    requested or because the trigger did not fire. Note the endpoint asymmetry:
    the *count* response has only `original_input_tokens` and no
    `applied_edits`, unlike a generation response.
    """

    input_tokens: int
    original_input_tokens: int | None


def build_system() -> list[dict]:
    """The static system prefix: one text block, carrying the system breakpoint.

    Byte-stable by construction - the only variable in the text is the section
    number. Failure mode: `ValueError` if the assembled text falls below
    `MIN_SYSTEM_CHARS`, because a prefix under the model's minimum is silently
    not cached and would surface much later as an unexplained `NoRecache`.
    """
    sections = "\n\n".join(
        _SECTION_TEMPLATE.format(n=n) for n in range(1, SYSTEM_SECTIONS + 1)
    )
    text = f"{_SYSTEM_PREAMBLE}\n\n{sections}"
    if len(text) < MIN_SYSTEM_CHARS:
        raise ValueError(
            f"system prefix is {len(text)} characters, below the "
            f"{MIN_SYSTEM_CHARS} this example requires to clear the model's "
            "minimum cacheable prefix"
        )
    return [
        {
            "type": compose.TEXT_BLOCK_TYPE,
            "text": text,
            compose.CACHE_CONTROL_KEY: dict(compose.EPHEMERAL),
        }
    ]


def build_tools() -> list[dict]:
    """The static tool definitions, with the tools breakpoint on the last one.

    The first is the tool the synthetic transcript actually calls; the second is
    advertised and never called, which is enough to make the point that tool
    *order* is part of the hashed prefix - a framework that sorts tool names
    breaks the cache from the first moved tool onward. Cannot fail.
    """
    return [
        transcript.tool_schema(),
        {
            "name": "list_documents",
            "description": (
                "List the document identifiers in the runbook set. Use this "
                "before reading if you do not already know the identifiers."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "prefix": {
                        "type": "string",
                        "description": "Optional identifier prefix, e.g. 'doc-00'.",
                    }
                },
                "required": [],
            },
            # The tools breakpoint: caches the whole `tools` array as one prefix.
            compose.CACHE_CONTROL_KEY: dict(compose.EPHEMERAL),
        },
    ]


def _usage_of(response: object) -> payback.TurnUsage:
    """Adapt one SDK response's `usage` into the core's `TurnUsage`.

    One of the two places this example touches the SDK's response shape. Field
    names are `anthropic` 1.x's; the nested `usage.cache_creation` breakdown by
    TTL is out of scope.

    Failure modes: `AttributeError` if the SDK renames a counter - loudly, so a
    rename cannot read as a run that cached nothing; `TypeError` if a counter is
    neither `None` nor an `int`.
    """
    usage = response.usage  # type: ignore[attr-defined]
    return payback.TurnUsage(
        cache_creation_input_tokens=_counter(usage, "cache_creation_input_tokens"),
        cache_read_input_tokens=_counter(usage, "cache_read_input_tokens"),
        input_tokens=_counter(usage, "input_tokens"),
    )


def _counter(holder: object, name: str) -> int:
    """Read one non-negative token counter off an SDK object.

    The SDK types the cache counters as optional. `None` is the API's "no tokens
    in this bucket" and becomes 0; a *missing* attribute is a changed response
    shape and propagates as `AttributeError`.

    Failure modes: `AttributeError` (no such field), `TypeError` (not an int),
    `ValueError` (negative).
    """
    value = getattr(holder, name)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int or None, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def _first_applied_edit(response: object) -> object:
    """The one `applied_edits` entry a `clear_tool_uses` generation reports.

    The other place this example touches the SDK's response shape. This field
    exists only on the *generation* response: `count_tokens` reports
    `original_input_tokens` and nothing else, which is why the free preview has
    to subtract.

    Failure modes: `NoInvalidation` if the response carries no
    `context_management` object or an empty `applied_edits` list - the edit did
    not fire, and there is nothing to attribute the cache write to.
    """
    reported = getattr(response, "context_management", None)
    if reported is None:
        raise NoInvalidation(
            "the response carried no `context_management` object, so no edit "
            f"was applied\n{_NO_INVALIDATION_ADVICE}"
        )
    edits = getattr(reported, "applied_edits", None)
    if not edits:
        raise NoInvalidation(
            "the response reported `applied_edits` as empty, so no edit was "
            f"applied\n{_NO_INVALIDATION_ADVICE}"
        )
    return edits[0]


def _cleared_input_tokens(response: object) -> int:
    """Tokens the clear actually removed, as the billed response reports them.

    Failure modes: those of `_first_applied_edit` and `_counter`.
    """
    return _counter(_first_applied_edit(response), "cleared_input_tokens")


def _cleared_tool_uses(response: object) -> int:
    """Tool uses whose results the clear emptied, as the billed response reports
    them.

    Failure modes: those of `_first_applied_edit` and `_counter`.
    """
    return _counter(_first_applied_edit(response), "cleared_tool_uses")


def _count_tokens(
    client,
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    tools: Sequence[Mapping[str, object]],
    context_management: Mapping[str, object] | None,
) -> _Count:
    """Count one request's tokens, optionally under a context-management config.

    Free, idempotent, and safe to re-run: the counting endpoint bills nothing
    and its rate limits are independent of the message-creation limits.

    `client` needs only `.beta.messages.count_tokens(...)`, so a fake satisfies
    it. It must be the **beta** namespace: `client.messages` has no `betas`
    parameter and raises `TypeError` rather than sending the header.

    Failure modes: every `anthropic.APIError` propagates untouched - an API
    error must reach the caller as an error, never as a zero-saving preview.
    """
    extra: dict[str, object] = {}
    if context_management is not None:
        extra["context_management"] = context_management

    response = client.beta.messages.count_tokens(
        model=model,
        messages=list(messages),
        tools=list(tools),
        betas=[BETA],
        **extra,
    )

    reported = getattr(response, "context_management", None)
    return _Count(
        input_tokens=_counter(response, "input_tokens"),
        original_input_tokens=(
            None if reported is None else _counter(reported, "original_input_tokens")
        ),
    )


def _preview_removed(
    client,
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    tools: Sequence[Mapping[str, object]],
    config: Mapping[str, object],
) -> _Count:
    """The free half: what the clear would remove, for $0, before any generation.

    Counts the same request twice - plain, then under `config` - and returns the
    edited count. The plain count is the independent measurement: when the API
    also reports an `original_input_tokens`, the two must agree, or every number
    derived from them is untrustworthy.

    Failure modes: `NoInvalidation` if no edit was applied or the edit removed
    nothing; `PreviewMismatch` if the plain count and the API's reported
    original disagree; anything the API raises propagates untouched.
    """
    plain = _count_tokens(
        client, model=model, messages=messages, tools=tools, context_management=None
    )
    edited = _count_tokens(
        client, model=model, messages=messages, tools=tools, context_management=config
    )

    if edited.original_input_tokens is None:
        raise NoInvalidation(
            "the free preview reported no edit: `context_management` was absent "
            f"from the count response\n{_NO_INVALIDATION_ADVICE}"
        )
    if edited.original_input_tokens != plain.input_tokens:
        raise PreviewMismatch(
            "the plain count and the API's reported original disagree: "
            f"plain={plain.input_tokens}, "
            f"original_input_tokens={edited.original_input_tokens}"
        )
    if edited.original_input_tokens <= edited.input_tokens:
        raise NoInvalidation(
            "the free preview reported an edit that removed nothing: "
            f"original={edited.original_input_tokens}, "
            f"edited={edited.input_tokens}\n{_NO_INVALIDATION_ADVICE}"
        )
    return edited


def _grown_transcript(messages: Sequence[Mapping[str, object]]) -> list[dict]:
    """The same transcript with one more canned tool-use round appended.

    Rebuilt from `transcript.build_transcript` rather than hand-appended,
    because that function is deterministic per round: round N is byte-identical
    whether the transcript has N+1 rounds or N+2. The model's own turn-A reply
    is deliberately not used - this example never executes a tool, and a reply
    that varied per run would move the cache boundary for reasons that have
    nothing to do with the edit.

    Failure modes: `RuntimeError` if the longer transcript does not extend the
    shorter one byte for byte. That invariant is what makes turn B a re-read of
    turn A's prefix; without it, turn B would report a cache miss that says
    nothing about clearing.
    """
    grown = transcript.build_transcript(rounds=ROUNDS + 1, result_chars=RESULT_CHARS)
    if grown[: len(messages)] != list(messages):
        raise RuntimeError(
            "the grown transcript does not extend the original byte for byte, "
            "so turn B would not be re-reading turn A's prefix"
        )
    return grown


def run(client, *, model: str, base_rate: float) -> str:
    """Preview the clear for $0, buy the two generations, return the report.

    `client` needs `.beta.messages.count_tokens(...)` and
    `.beta.messages.create(...)`, so a fake satisfies it. Both create calls send
    the same `tools`, `system` and `context_management`; only `messages` grows,
    with the breakpoints re-placed by `compose.place_breakpoints_for_clearing`.

    Not idempotent and not free: the two `create` calls spend tokens (the two
    counts do not). The gates run as early as the evidence allows, so a run that
    has already failed does not pay for the second generation.

    Failure modes: every `anthropic.APIError` propagates untouched;
    `NoInvalidation` if the clear did not fire or forced no cache write;
    `PreviewMismatch` if the free and billed measurements of the clear disagree
    by more than `TOLERANCE`; `NoRecache` if turn B read back less than
    `MIN_READ_FRACTION` of what turn A wrote.
    """
    system = build_system()
    tools = build_tools()
    messages = transcript.build_transcript(rounds=ROUNDS, result_chars=RESULT_CHARS)
    config = compose.clearing_config(
        keep_tool_uses=KEEP, trigger_tool_uses=TRIGGER
    )

    preview = _preview_removed(
        client, model=model, messages=messages, tools=tools, config=config
    )
    removed_preview = preview.original_input_tokens - preview.input_tokens

    placed_a = compose.place_breakpoints_for_clearing(
        messages, budget=MESSAGES_BUDGET, keep_tool_uses=KEEP
    )
    first = client.beta.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        tools=tools,
        messages=placed_a.messages,
        context_management=config,
        betas=[BETA],
    )
    turn_a = _usage_of(first)
    cleared_tokens = _cleared_input_tokens(first)
    cleared_uses = _cleared_tool_uses(first)

    # Both gates that turn A alone can settle, before turn B costs anything.
    _require_invalidation(turn_a)
    _require_preview_agreement(removed_preview, cleared_tokens)

    grown = _grown_transcript(messages)
    placed_b = compose.place_breakpoints_for_clearing(
        grown, budget=MESSAGES_BUDGET, keep_tool_uses=KEEP
    )
    second = client.beta.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        tools=tools,
        messages=placed_b.messages,
        context_management=config,
        betas=[BETA],
    )
    turn_b = _usage_of(second)

    _require_recache(turn_a, turn_b)

    tradeoff = payback.summarize(
        turn_a, removed=cleared_tokens, base_usd_per_mtok=base_rate
    )
    return render(
        preview=preview,
        removed_preview=removed_preview,
        turn_a=turn_a,
        turn_b=turn_b,
        cleared_tokens=cleared_tokens,
        cleared_uses=cleared_uses,
        anchor_index=compose.clearing_boundary(messages, keep_tool_uses=KEEP),
        marker_count=placed_a.marker_count,
        tradeoff=tradeoff,
        model=model,
    )


def _require_invalidation(turn_a: payback.TurnUsage) -> None:
    """Assert the clear forced a cache write. Raises `NoInvalidation` if it did not."""
    if turn_a.cache_creation_input_tokens <= 0:
        raise NoInvalidation(
            "the clearing turn wrote 0 tokens to the cache "
            f"(cache_creation_input_tokens=0, "
            f"cache_read_input_tokens={turn_a.cache_read_input_tokens}, "
            f"input_tokens={turn_a.input_tokens})\n{_NO_INVALIDATION_ADVICE}"
        )


def _require_preview_agreement(removed_preview: int, cleared_tokens: int) -> None:
    """Assert the free and billed measurements of the clear agree.

    Raises `PreviewMismatch` if they differ by more than `TOLERANCE`, relative
    to the billed figure - the run reports the disagreement rather than picking
    one of the two.
    """
    if cleared_tokens <= 0:
        raise NoInvalidation(
            "the billed response reported `cleared_input_tokens=0`, so the edit "
            f"applied but removed nothing\n{_NO_INVALIDATION_ADVICE}"
        )
    drift = abs(removed_preview - cleared_tokens) / cleared_tokens
    if drift > TOLERANCE:
        raise PreviewMismatch(
            f"the free preview says the clear removes {removed_preview} tokens "
            f"and the billed response says it removed {cleared_tokens} "
            f"({drift:.1%} apart, above the {TOLERANCE:.0%} this example "
            "tolerates); neither number can be trusted for the payback"
        )


def _require_recache(turn_a: payback.TurnUsage, turn_b: payback.TurnUsage) -> None:
    """Assert the re-written prefix was reusable. Raises `NoRecache` if it was not."""
    written = turn_a.cache_creation_input_tokens
    read = turn_b.cache_read_input_tokens
    if read < MIN_READ_FRACTION * written:
        raise NoRecache(
            f"turn B read back {read} of the {written} tokens the clearing turn "
            f"wrote (below the {MIN_READ_FRACTION:.0%} floor this example "
            f"requires)\n{_NO_RECACHE_ADVICE}"
        )


def render(
    *,
    preview: _Count,
    removed_preview: int,
    turn_a: payback.TurnUsage,
    turn_b: payback.TurnUsage,
    cleared_tokens: int,
    cleared_uses: int,
    anchor_index: int,
    marker_count: int,
    tradeoff: payback.Tradeoff,
    model: str,
) -> str:
    """Render the whole run for a terminal. Pure; no trailing newline; cannot fail."""
    original = preview.original_input_tokens or 0
    percent = 0.0 if original == 0 else round(100.0 * removed_preview / original, 1)
    read_back = (
        0.0
        if turn_a.cache_creation_input_tokens == 0
        else round(
            turn_b.cache_read_input_tokens / turn_a.cache_creation_input_tokens, 3
        )
    )
    return "\n".join(
        [
            f"Context editing against prompt caching: {compose.STRATEGY}",
            f"  model         {model}",
            (
                f"  transcript    {ROUNDS} tool-use rounds, {RESULT_CHARS}-char "
                f"results ({1 + 2 * ROUNDS} messages)"
            ),
            f"  policy        keep={KEEP} tool uses, trigger={TRIGGER} tool uses",
            (
                f"  breakpoints   {STATIC_BREAKPOINTS} static (tools, system) + "
                f"{marker_count} in messages; anchor on messages[{anchor_index}], "
                "the first message that survives the clear"
            ),
            "",
            "Free preview - count_tokens, $0:",
            f"  original      {original} input tokens",
            f"  edited        {preview.input_tokens} input tokens",
            f"  removed       {removed_preview} tokens ({percent}%)",
            "",
            "Turn A - the clearing turn (billed):",
            f"  cache_read_input_tokens      {turn_a.cache_read_input_tokens}",
            f"  cache_creation_input_tokens  {turn_a.cache_creation_input_tokens}"
            "   <- the prefix the clear forced back to a cold write",
            f"  input_tokens                 {turn_a.input_tokens}",
            f"  applied_edits[0]             cleared {cleared_uses} tool uses, "
            f"{cleared_tokens} input tokens",
            "",
            "Turn B - one round later, same policy (billed):",
            f"  cache_read_input_tokens      {turn_b.cache_read_input_tokens}"
            f"   <- {read_back:.0%} of turn A's write, back at the 0.1x read rate",
            f"  cache_creation_input_tokens  {turn_b.cache_creation_input_tokens}",
            f"  input_tokens                 {turn_b.input_tokens}",
            "",
            payback.render(tradeoff),
        ]
    )


def main() -> int:
    """Run the preview and the two billed turns, and print the payback.

    Failure modes: prints one line to stderr and returns `EXIT_NO_KEY` (0)
    without making any network call if the key is absent; returns
    `EXIT_NO_INVALIDATION` (2), `EXIT_NO_RECACHE` (3) or
    `EXIT_PREVIEW_MISMATCH` (4) with a diagnostic when the experiment did not
    happen. Every API error propagates with a traceback rather than becoming a
    report of a free clear.
    """
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return EXIT_NO_KEY

    import anthropic  # imported lazily so the self-tests need no SDK

    client = anthropic.Anthropic(api_key=api_key)
    try:
        report = run(client, model=MODEL, base_rate=BASE_USD_PER_MTOK)
    except NoInvalidation as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_INVALIDATION
    except NoRecache as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_RECACHE
    except PreviewMismatch as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PREVIEW_MISMATCH

    print(report)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
