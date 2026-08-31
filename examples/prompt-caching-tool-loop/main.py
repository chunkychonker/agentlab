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
    python3 main.py

Run the offline self-tests (no key, no network, no SDK installed):
    python3 test_placement.py
    python3 test_report.py

See the research note this came from:
    research/2026-08-29-prompt-caching-tool-loop.md
"""

from __future__ import annotations

import os
import sys

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

MISSING_KEY_MESSAGE = (
    f"error: {API_KEY_ENV} is not set, so nothing was sent and nothing was spent; "
    "this demo needs one real two-turn generation pair (one to two cents) - "
    "run 'python3 test_placement.py' and 'python3 test_report.py' for the "
    "offline self-tests."
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


class CacheMiss(RuntimeError):
    """Turn 2 did not read back the prefix turn 1 wrote.

    Raised by `run()` instead of returning a `Saving` of roughly zero: a run
    that paid for two generations and got no cache hit is a broken experiment,
    not a small result.
    """


def build_system() -> list[dict]:
    """The static system prefix: one text block, with the system breakpoint on it.

    Byte-stable by construction - the only variable in the text is the section
    number. Failure mode: `ValueError` if the assembled text falls below
    `MIN_SYSTEM_CHARS`, because a prefix under the model's minimum is silently
    not cached and would surface as an unexplained cache miss much later.
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
            placement.CACHE_CONTROL_KEY: dict(placement.EPHEMERAL),
        }
    ]


def build_tools() -> list[dict]:
    """The static tool definitions, with the tools breakpoint on the last one.

    Fixed order, no generated ids: tool order is part of the hashed prefix, so a
    framework that sorts tool names or iterates a dict nondeterministically
    breaks the cache from the first moved tool onward. Cannot fail.
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
            placement.CACHE_CONTROL_KEY: dict(placement.EPHEMERAL),
        },
    ]


def _usage_of(response: object) -> report.TurnUsage:
    """Adapt one SDK response's `usage` into the core's `TurnUsage`.

    The single place this example touches the SDK's response shape. Field names
    are `anthropic` 1.x's (`cache_creation_input_tokens`,
    `cache_read_input_tokens`, `input_tokens`); the nested `usage.cache_creation`
    breakdown by TTL is out of scope.

    Failure modes: `AttributeError` if the SDK renames a counter - loudly, so a
    rename cannot read as a run that cached nothing; `TypeError` if a counter is
    neither `None` nor an `int`.
    """
    usage = response.usage  # type: ignore[attr-defined]
    return report.TurnUsage(
        cache_creation_input_tokens=_counter(usage, "cache_creation_input_tokens"),
        cache_read_input_tokens=_counter(usage, "cache_read_input_tokens"),
        input_tokens=_counter(usage, "input_tokens"),
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


def run(client, *, model: str, base_rate: float) -> report.Saving:
    """Make the two billed calls and return what the cache hit was worth.

    `client` needs only `.messages.create(...)`, so a fake satisfies it. Both
    calls send the *same* `tools` and `system` objects; only `messages` grows,
    with the rolling breakpoint moved to the frozen tail by
    `place_breakpoints`.

    Failure modes: every `anthropic.APIError` propagates untouched; `CacheMiss`
    if turn 1 wrote nothing, turn 2 read nothing, or turn 2 read back less than
    `MIN_READ_FRACTION` of the write. Not idempotent and not free: each call
    spends tokens.
    """
    system = build_system()
    tools = build_tools()
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
    placed = placement.place_breakpoints(grown, budget=MESSAGES_BUDGET)

    second = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        tools=tools,
        messages=placed.messages,
    )
    turn2 = _usage_of(second)

    _require_cache_hit(turn1, turn2)
    return report.summarize(turn1, turn2, base_usd_per_mtok=base_rate)


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


def main() -> int:
    """Run the two-turn demo and print what the cache saved.

    Failure modes: prints one line to stderr and returns `EXIT_NO_KEY` (0)
    without making any network call if the key is absent; returns
    `EXIT_NO_CACHE_HIT` (2) after printing the cache-killer checklist if the
    prefix did not survive between the turns. Every API error propagates with a
    traceback rather than becoming a report of no saving.
    """
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return EXIT_NO_KEY

    import anthropic  # imported lazily so the self-tests need no SDK

    client = anthropic.Anthropic(api_key=api_key)
    try:
        saving = run(client, model=MODEL, base_rate=BASE_USD_PER_MTOK)
    except CacheMiss as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_CACHE_HIT

    print(report.render(saving))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
