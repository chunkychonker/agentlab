"""The imperative shell: builds a byte-stable prefix, makes the two billed
calls, and proves the cache hit.

This is the only file that imports `anthropic`, reads an environment variable,
or writes to a stream - and the SDK import is lazy, inside `main()`, so both
self-tests can import this module with no dependency installed.

The demo is two `messages.create` calls, seconds apart, with an identical
`tools` + `system` prefix. Turn 1 writes that prefix to the cache; turn 2 reads
it back and writes only the delta the tool round trip added. The proof is
turn 2's `cache_read_input_tokens` against turn 1's `cache_creation_input_tokens`
- which is why the run cannot be previewed for $0: `count_tokens` deliberately
does not run caching logic and reports no cache fields at all.

Run it live (needs a key, and this one **costs money** - one to two cents):
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 main.py                 # the 5-minute TTL, 1.25x on the write
    python3 main.py --ttl 1h        # the 1-hour TTL, 2x on the write

The TTL is the one knob: `--ttl 1h` puts `"ttl": "1h"` on all four breakpoints,
prices the write at 2x, and then *checks* turn 1's nested `usage.cache_creation`
to prove the server actually wrote into the 1-hour bucket rather than trusting
that asking worked.

Run the offline self-tests (no key, no network, no SDK installed):
    python3 test_placement.py
    python3 test_report.py

See the research note this came from:
    research/2026-08-29-prompt-caching-tool-loop.md
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence

import placement
import report

# Cheapest current model at $2/MTok input; its 1,024-token minimum cacheable
# prefix is cleared comfortably by the ~2,600-token system block below. Haiku
# 4.5 is cheaper per token but needs a 4,096-token prefix before caching engages
# at all. See knowledge/anthropic-models.md and knowledge/prompt-caching.md.
MODEL = "claude-sonnet-5"

# Sonnet 5's base *input* rate, 2026-08-29 (knowledge/anthropic-models.md). Every
# dollar figure printed is derived from this one number, so a price change is a
# one-line edit here, not a hunt through report.py.
BASE_USD_PER_MTOK = 2.0

API_KEY_ENV = "ANTHROPIC_API_KEY"

# The one command-line knob. A two-value flag rather than `argparse`: argparse
# exits 2 on a usage error, and 2 already means EXIT_NO_CACHE_HIT here.
TTL_FLAG = "--ttl"
DEFAULT_TTL = placement.CACHE_TTL_5M

# What each TTL costs on the write. The composition lives here, at the entry
# point, for the same reason `BASE_USD_PER_MTOK` does: `placement` owns the wire
# shape and `report` owns the arithmetic, and neither should import the other to
# learn a price. Total over `CacheTTL` by construction - a third member would
# fail `write_multiplier_for` loudly rather than silently price at 5 minutes.
_WRITE_MULTIPLIER_BY_TTL: Mapping[placement.CacheTTL, float] = {
    placement.CACHE_TTL_5M: report.CACHE_WRITE_5M_MULTIPLIER,
    placement.CACHE_TTL_1H: report.CACHE_WRITE_1H_MULTIPLIER,
}

# Two of the four breakpoints are static and set here: one on the last tool, one
# on the last system block. The rest is what the growing message array may use.
STATIC_BREAKPOINTS = 2
MESSAGES_BUDGET = placement.MAX_BREAKPOINTS - STATIC_BREAKPOINTS

# The response cap. Small on purpose: output tokens are the expensive half of
# this demo and nothing here reads the model's prose.
MAX_TOKENS = 512

# A cache read below this share of what was written means the prefix did not
# survive between the two turns. Half is generous - a healthy run reads back
# essentially all of it - and it exists so the failure is loud rather than a
# report of a tiny saving.
MIN_READ_FRACTION = 0.5

# The system block is the bulk of the cached prefix: 24 sections of fixed policy
# text, ~10,600 characters or roughly 2,600 tokens. That clears claude-sonnet-5's
# 1,024-token minimum cacheable prefix with room to spare - a prefix under the
# minimum is processed *without* caching and reports no error at all.
SYSTEM_SECTIONS = 24
MIN_SYSTEM_CHARS = 10_000

_SYSTEM_PREAMBLE = (
    "You are the operations assistant for a fictional logistics desk. The "
    "sections below are your standing instructions. They never change between "
    "requests, which is the entire point of this example: an identical prefix "
    "is a cacheable prefix."
)

# One template, one loop variable. No timestamp, no request id, no random
# seed - anything varying here would make every request a fresh cache write.
_SECTION_TEMPLATE = (
    "Section {n:02d}. When a request reaches this desk, restate the caller's "
    "goal in one sentence before acting, prefer a tool call over a guess "
    "whenever a tool can answer the question exactly, keep every intermediate "
    "result that a later step will need, quote units and currencies "
    "explicitly, and never report a number that was not computed. If the "
    "request is ambiguous, ask one clarifying question rather than proceeding "
    "on an assumption."
)

# The fixed first user turn. Arithmetic, so the model reaches for the calculator
# and the loop gets a real tool_use block to answer.
TASK = (
    "Using your tools, work out 4839 * 1284, then tell me how many words are "
    "in the sentence you used to explain the result."
)

# This example never executes a tool: it needs a second, longer request with the
# same prefix, not a correct answer. Saying so in the payload keeps the
# transcript honest about what the model was fed.
CANNED_TOOL_RESULT = (
    "6213276 (canned result: this example does not execute tools; see README.md)"
)

# Used only if the model answers without calling a tool - the turn still has to
# grow for turn 2 to be a longer request against the same prefix.
FOLLOW_UP_TEXT = "Thanks. Summarise that in one more sentence."

EXIT_OK = 0

# A missing key is a skip, not a failure, and a skip exits 0. This is the one
# place this example differs from `context-editing-preview` and
# `server-side-compaction`, which exit 1: their live run is free or previewable,
# so refusing to run is a result worth flagging. Here the live run spends real
# money, so "no key, nothing spent, nothing measured" is the expected outcome on
# any machine without credentials - and the research note's acceptance criterion
# 2 requires exit 0.
EXIT_NO_KEY = EXIT_OK

# A run that reached the API and did not get its prefix back is a different
# thing entirely: it spent money and learned nothing.
EXIT_NO_CACHE_HIT = 2

# A run that got its prefix back, but written at a TTL it did not ask for. The
# cache worked; the experiment did not, and at 2x versus 1.25x the difference is
# money. Distinct from 2 so a script can tell "no cache" from "wrong cache".
EXIT_TTL_MISMATCH = 3

# sysexits EX_USAGE, matching `readme-transcript-check/check_transcript.py`.
# Deliberately not 2: a typo in a flag is not a failed experiment.
EXIT_USAGE = 64

MISSING_KEY_MESSAGE = (
    f"error: {API_KEY_ENV} is not set, so nothing was sent and nothing was spent; "
    "this demo needs one real two-turn generation pair (one to two cents) - "
    "run 'python3 test_placement.py' and 'python3 test_report.py' for the "
    "offline self-tests."
)

USAGE_MESSAGE = (
    f"usage: python3 main.py [{TTL_FLAG} "
    f"{placement.CACHE_TTL_5M.value}|{placement.CACHE_TTL_1H.value}]"
)

_TTL_MISMATCH_ADVICE = (
    "  The prefix was cached, but not at the TTL this run asked for, so the\n"
    "  write was billed at a multiplier the report would have got wrong. Check:\n"
    "    - every one of the four breakpoints has to carry the same `ttl`; a\n"
    "      single unmarked-TTL block writes its own 5-minute entry\n"
    "    - the `ttl` value is the string \"1h\", not 3600 and not \"1 hour\"\n"
    "    - a prefix already cached at another TTL by an earlier run can be read\n"
    "      back at that TTL instead of rewritten - wait out the old entry, or\n"
    "      change the prefix, and run again"
)

_CACHE_MISS_ADVICE = (
    "  The prefix turn 1 wrote was not there for turn 2. The usual causes, in\n"
    "  the order they bite (see 'Cache killers' in README.md):\n"
    "    - the cached prefix was under the model's minimum (1,024 tokens on\n"
    "      claude-sonnet-5); short prefixes are silently not cached, no error\n"
    "    - something in `tools` or `system` varies per request (a timestamp, a\n"
    "      request id, a re-sorted tool list) - the prefix is hashed byte-wise\n"
    "    - more than five minutes passed between the two turns, so the\n"
    "      5-minute entry was evicted\n"
    "    - the model id changed between the turns; each id is its own cache"
)


class UsageError(ValueError):
    """The command line did not parse. Carries the message to print, nothing else."""


class TTLMismatch(RuntimeError):
    """The server did not write the prefix at the TTL this run requested.

    Raised by `run()` rather than reported: a run that asked for the 1-hour TTL,
    paid a 2x premium and was actually written at 5 minutes has been billed for
    something it did not get, and a report that priced it at 2x would be wrong
    in the one direction this example exists to measure.
    """


class CacheMiss(RuntimeError):
    """Turn 2 did not read back the prefix turn 1 wrote.

    Raised by `run()` instead of returning a `Saving` of roughly zero: a run
    that paid for two generations and got no cache hit is a broken experiment,
    not a small result.
    """


def parse_ttl(argv: Sequence[str]) -> placement.CacheTTL:
    """Read the TTL out of the command line. Pure: no `sys.argv`, no env, no I/O.

    Accepts exactly `[]` (the 5-minute default) or `["--ttl", "5m"|"1h"]`.

    Failure mode: `UsageError`, carrying `USAGE_MESSAGE`, for an unknown flag, a
    missing value, an unknown TTL, or trailing arguments - never a silent
    fallback to the default, which would make a typo look like a cheap run.
    """
    if not argv:
        return DEFAULT_TTL
    if len(argv) != 2 or argv[0] != TTL_FLAG:
        raise UsageError(f"unexpected arguments {list(argv)!r}\n{USAGE_MESSAGE}")
    try:
        return placement.resolve_ttl(argv[1])
    except ValueError as exc:
        raise UsageError(f"{exc}\n{USAGE_MESSAGE}") from None


def write_multiplier_for(ttl: placement.CacheTTL) -> float:
    """The base-rate multiplier the write is billed at under `ttl`.

    Pure. Failure mode: `KeyError` if a `CacheTTL` member is added without a
    price - loudly, rather than defaulting to the cheaper multiplier and
    under-reporting the premium.
    """
    return _WRITE_MULTIPLIER_BY_TTL[ttl]


def build_system(*, ttl: placement.CacheTTL = DEFAULT_TTL) -> list[dict]:
    """The static system prefix: one text block, with the system breakpoint on it.

    Byte-stable by construction - the only variable in the text is the section
    number, and `ttl` changes only the marker, never the cached bytes.

    Failure modes: `ValueError` if the assembled text falls below
    `MIN_SYSTEM_CHARS`, because a prefix under the model's minimum is silently
    not cached and would surface as an unexplained cache miss much later; or if
    `ttl` is not a `CacheTTL` value (raised by `ephemeral_marker`).
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
            "type": placement.TEXT_BLOCK_TYPE,
            "text": text,
            placement.CACHE_CONTROL_KEY: placement.ephemeral_marker(ttl),
        }
    ]


def build_tools(*, ttl: placement.CacheTTL = DEFAULT_TTL) -> list[dict]:
    """The static tool definitions, with the tools breakpoint on the last one.

    Fixed order, no generated ids: tool order is part of the hashed prefix, so a
    framework that sorts tool names or iterates a dict nondeterministically
    breaks the cache from the first moved tool onward.

    Failure mode: `ValueError` if `ttl` is not a `CacheTTL` value (raised by
    `ephemeral_marker`).
    """
    return [
        {
            "name": "calculator",
            "description": (
                "Evaluate an arithmetic expression and return the numeric result. "
                "Supports + - * / // % ** and parentheses over numbers. Use this "
                "for any arithmetic instead of computing it yourself."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The arithmetic expression, e.g. '4839 * 1284'.",
                    }
                },
                "required": ["expression"],
            },
        },
        {
            "name": "word_count",
            "description": "Count the whitespace-separated words in a string.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text whose words should be counted.",
                    }
                },
                "required": ["text"],
            },
            # The tools breakpoint: caches the whole `tools` array as one prefix.
            placement.CACHE_CONTROL_KEY: placement.ephemeral_marker(ttl),
        },
    ]


def _usage_of(response: object) -> report.TurnUsage:
    """Adapt one SDK response's `usage` into the core's `TurnUsage`.

    The single place this example touches the SDK's response shape. Field names
    are `anthropic` 1.x's (`cache_creation_input_tokens`,
    `cache_read_input_tokens`, `input_tokens`), plus the nested
    `usage.cache_creation` breakdown - verified against the pinned 1.2.0 as
    `Optional[CacheCreation]` with `ephemeral_5m_input_tokens` and
    `ephemeral_1h_input_tokens`.

    Failure modes: `AttributeError` if the SDK renames a flat counter - loudly,
    so a rename cannot read as a run that cached nothing; `TypeError` if a
    counter is neither `None` nor an `int`; `ValueError` if the breakdown does
    not sum to `cache_creation_input_tokens` (raised by `TurnUsage`).
    """
    usage = response.usage  # type: ignore[attr-defined]
    breakdown = _ttl_breakdown(usage)
    return report.TurnUsage(
        cache_creation_input_tokens=_counter(usage, "cache_creation_input_tokens"),
        cache_read_input_tokens=_counter(usage, "cache_read_input_tokens"),
        input_tokens=_counter(usage, "input_tokens"),
        ephemeral_5m_input_tokens=breakdown[0],
        ephemeral_1h_input_tokens=breakdown[1],
    )


def _ttl_breakdown(usage: object) -> tuple[int | None, int | None]:
    """The per-TTL split of the write, or `(None, None)` if the API sent none.

    `usage.cache_creation` is optional in the SDK's own type (`1.2.0`:
    `Optional[CacheCreation]`, default `None`), so its absence is a response
    that made no claim about TTLs - not a schema change, and not something to
    raise on. Absence is only a *failure* when the run asked for a non-default
    TTL and therefore needs the proof; that decision belongs to
    `_require_requested_ttl`, not here.

    Failure mode: `TypeError` if a present breakdown's counters are not ints
    (raised by `_counter`).
    """
    creation = getattr(usage, "cache_creation", None)
    if creation is None:
        return (None, None)
    return (
        _counter(creation, "ephemeral_5m_input_tokens"),
        _counter(creation, "ephemeral_1h_input_tokens"),
    )


def _counter(usage: object, name: str) -> int:
    """Read one non-negative token counter off a `usage` object.

    The SDK types the two cache counters as optional. `None` is the API's "no
    tokens in this bucket" and becomes 0; a *missing* attribute is a changed
    response shape and propagates as `AttributeError`. Failure modes:
    `AttributeError` (no such field), `TypeError` (not an int).
    """
    value = getattr(usage, name)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"usage.{name} must be an int or None, got {value!r}")
    return value


def _assistant_message(response: object) -> dict:
    """Turn the model's reply into a plain-dict assistant message to send back.

    Plain dicts, not SDK block objects: the message list is validated and copied
    by `placement.place_breakpoints`, whose contract is mappings.

    Failure modes: `ValueError` if the response has no content blocks;
    `TypeError` if a block is not an SDK model (no `model_dump`).
    """
    blocks = getattr(response, "content", None) or []
    if not blocks:
        raise ValueError("the model returned no content blocks to send back")
    return {"role": "assistant", "content": [_block_dict(block) for block in blocks]}


def _block_dict(block: object) -> dict:
    """One SDK content block as a plain dict, with nulls dropped."""
    dump = getattr(block, "model_dump", None)
    if dump is None:
        raise TypeError(
            f"expected an SDK content block with model_dump(), got "
            f"{type(block).__name__}"
        )
    return dump(exclude_none=True)


def _user_reply(response: object) -> dict:
    """The user message that closes the turn the model just opened.

    Answers *every* `tool_use` block in the response - the API rejects a turn
    that leaves one unanswered - with the same canned result. If the model
    called no tool, sends a fixed follow-up line instead, so the message array
    still grows either way. Failure mode: `AttributeError` if the response has
    no `content` at all.
    """
    tool_uses = [
        block for block in response.content  # type: ignore[attr-defined]
        if getattr(block, "type", None) == "tool_use"
    ]
    if not tool_uses:
        return {"role": "user", "content": FOLLOW_UP_TEXT}
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": CANNED_TOOL_RESULT,
            }
            for block in tool_uses
        ],
    }


def run(
    client,
    *,
    model: str,
    base_rate: float,
    ttl: placement.CacheTTL = DEFAULT_TTL,
) -> report.Saving:
    """Make the two billed calls and return what the cache hit was worth.

    `client` needs only `.messages.create(...)`, so a fake satisfies it. Both
    calls send the *same* `tools` and `system` objects; only `messages` grows,
    with the rolling breakpoint moved to the frozen tail by
    `place_breakpoints`.

    `ttl` goes on all four breakpoints at once - the two static ones here and
    the message ones inside `place_breakpoints` - because the write is one
    prefix and mixing TTLs across it is out of scope. It also picks the write
    multiplier the saving is priced at.

    Failure modes: `ValueError` if `ttl` is not a `CacheTTL` value, raised
    before the first call; every `anthropic.APIError` propagates untouched;
    `CacheMiss` if turn 1 wrote nothing, turn 2 read nothing, or turn 2 read
    back less than `MIN_READ_FRACTION` of the write; `TTLMismatch` if turn 1's
    write did not land in the requested TTL's bucket. Not idempotent and not
    free: each call spends tokens.
    """
    # Boundary check first: a bad TTL must fail before the first billed call,
    # not when the write multiplier is looked up after both of them.
    ttl = placement.resolve_ttl(ttl)

    system = build_system(ttl=ttl)
    tools = build_tools(ttl=ttl)
    messages: list[dict] = [{"role": "user", "content": TASK}]

    first = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        tools=tools,
        messages=messages,
    )
    turn1 = _usage_of(first)

    grown = [*messages, _assistant_message(first), _user_reply(first)]
    placed = placement.place_breakpoints(grown, budget=MESSAGES_BUDGET, ttl=ttl)

    second = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        tools=tools,
        messages=placed.messages,
    )
    turn2 = _usage_of(second)

    _require_cache_hit(turn1, turn2)
    _require_requested_ttl(turn1, ttl)
    return report.summarize(
        turn1,
        turn2,
        base_usd_per_mtok=base_rate,
        write_multiplier=write_multiplier_for(ttl),
    )


def _require_cache_hit(turn1: report.TurnUsage, turn2: report.TurnUsage) -> None:
    """Assert the experiment actually happened. Raises `CacheMiss` if it did not."""
    written = turn1.cache_creation_input_tokens
    read = turn2.cache_read_input_tokens

    if written <= 0:
        raise CacheMiss(
            "turn 1 wrote 0 tokens to the cache "
            f"(cache_creation_input_tokens=0, input_tokens={turn1.input_tokens})\n"
            f"{_CACHE_MISS_ADVICE}"
        )
    if read <= 0:
        raise CacheMiss(
            f"turn 1 wrote {written} tokens but turn 2 read 0 back\n"
            f"{_CACHE_MISS_ADVICE}"
        )
    if read < MIN_READ_FRACTION * written:
        raise CacheMiss(
            f"turn 2 read only {read} of the {written} tokens turn 1 wrote "
            f"(below the {MIN_READ_FRACTION:.0%} floor this example requires)\n"
            f"{_CACHE_MISS_ADVICE}"
        )


def _require_requested_ttl(turn1: report.TurnUsage, ttl: placement.CacheTTL) -> None:
    """Assert turn 1's write landed in the bucket `ttl` asked for.

    Pure. The default 5-minute path is deliberately left unchecked: it is the
    behaviour this example shipped with, and an SDK or API that reports no
    breakdown at all must keep working there. Asking for a non-default TTL is
    asking to pay more, so that path has to prove it got what it paid for -
    same "prove it, don't assume it" discipline as `_require_cache_hit`.

    Failure mode: `TTLMismatch` if a non-default TTL was requested and the
    response either carried no breakdown or put any of the write in another
    bucket.
    """
    if ttl is DEFAULT_TTL:
        return

    requested = {
        placement.CACHE_TTL_5M: turn1.ephemeral_5m_input_tokens,
        placement.CACHE_TTL_1H: turn1.ephemeral_1h_input_tokens,
    }
    landed = requested[ttl]
    if landed is None:
        raise TTLMismatch(
            f"asked for the {ttl.value} TTL, but the response carried no "
            "usage.cache_creation breakdown, so there is no proof the write "
            f"was billed at {write_multiplier_for(ttl)}x\n{_TTL_MISMATCH_ADVICE}"
        )

    written = turn1.cache_creation_input_tokens
    if landed != written:
        elsewhere = written - landed
        raise TTLMismatch(
            f"asked for the {ttl.value} TTL, but only {landed} of the {written} "
            f"tokens turn 1 wrote landed in the {ttl.value} bucket "
            f"({elsewhere} went elsewhere: 5m="
            f"{turn1.ephemeral_5m_input_tokens}, 1h="
            f"{turn1.ephemeral_1h_input_tokens})\n{_TTL_MISMATCH_ADVICE}"
        )


def main(argv: Sequence[str] = ()) -> int:
    """Run the two-turn demo and print what the cache saved.

    Failure modes, in the order they are checked: `EXIT_USAGE` (64) for a
    command line that does not parse, before anything else happens; one line to
    stderr and `EXIT_NO_KEY` (0), with no network call, if the key is absent;
    `EXIT_NO_CACHE_HIT` (2) with the cache-killer checklist if the prefix did
    not survive between the turns; `EXIT_TTL_MISMATCH` (3) if it survived but
    not at the requested TTL. Every API error propagates with a traceback
    rather than becoming a report of no saving.
    """
    try:
        ttl = parse_ttl(argv)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return EXIT_NO_KEY

    import anthropic  # imported lazily so the self-tests need no SDK

    client = anthropic.Anthropic(api_key=api_key)
    try:
        saving = run(client, model=MODEL, base_rate=BASE_USD_PER_MTOK, ttl=ttl)
    except CacheMiss as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_CACHE_HIT
    except TTLMismatch as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_TTL_MISMATCH

    print(report.render(saving))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
