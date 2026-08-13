"""The imperative shell: reads the key, counts the transcript, makes one paused
compaction call, prints the report.

This is the only file that imports `anthropic`, reads an environment variable,
or writes to a stream - and the SDK import is lazy, inside `main()`, so
`test_compaction.py` can import this module with no dependency installed.

Run it live (needs a key; this one **costs money** - roughly $0.15, see README):
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 main.py

Run the offline self-test (no key, no network, stdlib only):
    python3 test_compaction.py

See the research note this came from:
    research/2026-08-12-server-side-compaction.md
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence

import cost as cost_module
import policy as policy_module
import preflight as preflight_module
import report as report_module
import transcript as transcript_module

# `claude-haiku-4-5`, this repo's usual default for cheap examples, does **not**
# support compaction - it is absent from the docs' supported-model list. Sonnet 5
# is the cheapest model that does. See knowledge/anthropic-models.md and
# knowledge/compaction.md.
MODEL = "claude-sonnet-5"

# Sonnet 5 list price per million tokens. The pricing page notes the scheduled
# 2026-09-01 rise to $3/$15 "will not occur", so these are the standard rates,
# not an introductory offer. They are the only prices in the program.
USD_IN_PER_MTOK = 2.0
USD_OUT_PER_MTOK = 10.0

API_KEY_ENV = "ANTHROPIC_API_KEY"

# The demo transcript: 60 exchanges of 2,000 characters, i.e. 121 messages and
# ~242,000 characters. At roughly four characters per token that clears the
# 50,000-token trigger with margin - but "roughly" is not a plan, which is what
# the preflight count is for.
DEMO_TURNS = 60
DEMO_CHARS_PER_TURN = 2_000

# `pause_after_compaction=True` is the cheap path: the turn stops once the
# summary exists, so we pay for one compaction iteration instead of a compaction
# plus a full answer. `trigger_tokens` sits on the documented floor so the demo
# fires on the smallest transcript the API allows.
DEMO_POLICY = policy_module.CompactionPolicy(
    trigger_tokens=policy_module.MIN_TRIGGER_TOKENS,
    pause_after_compaction=True,
)

# Required by the API even when we expect to pause before any answer is written.
# Generous enough that a long summary is not truncated.
MAX_TOKENS = 4_096

# Exit codes. "Could not run" and "ran and found nothing" must not share one.
EXIT_OK = 0
EXIT_NO_KEY = 1
EXIT_BELOW_TRIGGER = 2

MISSING_KEY_MESSAGE = (
    f"error: {API_KEY_ENV} is not set, so there is nothing to compact against.\n"
    "  This example makes one billed call (~$0.15). Set the key and re-run, or "
    "run\n  'python3 test_compaction.py' for the free offline self-test."
)

_FAILED_COMPACTION_EXPLANATION = (
    "  Compaction ran but produced no summary: the block's content is null.\n"
    "  The server treats such a block as a no-op, but it must still be\n"
    "  round-tripped - so the continuation list above is still the right\n"
    "  thing to send."
)


def count_transcript_tokens(
    client, *, model: str, messages: Sequence[Mapping[str, object]]
) -> int:
    """Count the transcript through the free token-counting endpoint.

    Uses the **stable** namespace deliberately: counting needs no compaction
    beta, and `client.messages.count_tokens` has no `betas` parameter at all
    (passing one raises `TypeError` rather than sending a header - a bug the
    sibling context-editing example hit for real).

    Counting is free, idempotent, and rate-limited separately from message
    creation, so this is safe to re-run.

    Failure modes: every `anthropic.APIError` propagates untouched. A failed
    count must never be read as "small enough, don't bother compacting" or as
    "big enough, go spend money".
    """
    response = client.messages.count_tokens(model=model, messages=list(messages))
    return response.input_tokens


def compact_once(
    client,
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    compaction_policy: policy_module.CompactionPolicy,
    max_tokens: int,
) -> dict[str, object]:
    """Make one compaction call and return the response as a plain JSON dict.

    This is the whole seam between the SDK and the pure core: `model_dump` here,
    `Mapping`s everywhere downstream. It is also the only place that names the
    beta - `compact-2026-01-12` is not in the SDK's literal union, so nothing
    type-checks it and a second spelling of it somewhere else would fail only at
    the server.

    **Not idempotent and not free.** Each call is a new sampling iteration over
    the whole prompt and is billed as one; re-running is re-paying.

    Failure modes: every `anthropic.APIError` propagates untouched - an
    unsupported model, an unknown beta, or a 400 on the trigger floor must reach
    the caller as an error, never as an empty report.
    """
    response = client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=list(messages),
        betas=[policy_module.BETA],
        context_management=compaction_policy.to_config(),
    )
    return response.model_dump(mode="json")


def render_preflight(check: preflight_module.Preflight) -> str:
    """Render the go/no-go line and, when it is no-go, what is missing.

    Pure; no trailing newline. Cannot fail.
    """
    lines = [
        "Preflight (free token count, before anything is billed):",
        f"  counted     {check.counted_tokens} input tokens",
        f"  trigger     {check.trigger_tokens} input tokens",
        f"  verdict     {'GO' if check.will_fire else 'NO-GO'}",
    ]
    if not check.will_fire:
        lines.append(
            f"  shortfall   {check.shortfall} input tokens. Compaction cannot "
            "fire, so the call\n              would be billed for nothing. "
            "Not sending it."
        )
    return "\n".join(lines)


def render(
    compaction_report: report_module.CompactionReport,
    compaction_policy: policy_module.CompactionPolicy,
    continuation: Sequence[Mapping[str, object]],
    *,
    model: str,
    turns: int,
    chars_per_turn: int,
    usd_in_per_mtok: float,
    usd_out_per_mtok: float,
) -> str:
    """Render a report for a terminal. Pure; no trailing newline.

    Prints the exact `context_management` object that was sent, the true bill
    (which is not what `usage.input_tokens` says), the summary the model wrote,
    and the shape of the continuation message list. Cannot fail.
    """
    config = json.dumps(compaction_policy.to_config(), indent=2)
    compaction_usd = cost_module.estimate_usd(
        compaction_report.compaction_input_tokens,
        compaction_report.compaction_output_tokens,
        usd_in_per_mtok,
        usd_out_per_mtok,
    )
    total_usd = cost_module.estimate_usd(
        compaction_report.compaction_input_tokens
        + compaction_report.message_input_tokens,
        compaction_report.compaction_output_tokens
        + compaction_report.message_output_tokens,
        usd_in_per_mtok,
        usd_out_per_mtok,
    )
    final_block_types = ", ".join(
        str(block.get("type", "?"))
        for block in continuation[-1]["content"]  # type: ignore[index]
    )

    lines = [
        f"Server-side compaction: {policy_module.STRATEGY}",
        f"  model       {model}",
        (
            f"  transcript  {turns} exchanges of {chars_per_turn} chars "
            f"({2 * turns + 1} messages)"
        ),
        f"  beta        {policy_module.BETA}",
        "",
        "context_management sent (paste this into messages.create):",
        *(f"  {line}" for line in config.splitlines()),
        "",
        f"  triggered   {compaction_report.triggered}",
        f"  paused      {compaction_report.paused}",
        "",
        "billing (the top-level usage numbers exclude the compaction iteration):",
        (
            f"  compaction  {compaction_report.compaction_input_tokens} in / "
            f"{compaction_report.compaction_output_tokens} out  "
            f"(${compaction_usd:.4f})"
        ),
        (
            f"  message     {compaction_report.message_input_tokens} in / "
            f"{compaction_report.message_output_tokens} out"
        ),
        (
            f"  total       {compaction_report.total_billed_tokens} tokens "
            f"billed  (${total_usd:.4f})"
        ),
        "",
        "continuation messages (send these next; the server drops everything",
        "before the compaction block):",
        f"  messages    {len(continuation)}",
        f"  last blocks {final_block_types}",
        "",
    ]

    if compaction_report.summary is None:
        lines.append("summary: none")
        lines.append(_FAILED_COMPACTION_EXPLANATION)
    else:
        lines.append(f"summary ({len(compaction_report.summary)} chars):")
        lines += [f"  {line}" for line in compaction_report.summary.splitlines()]

    return "\n".join(lines)


def main() -> int:
    """Compact `DEMO_POLICY`'s transcript once and print the report.

    Failure modes: returns ``EXIT_NO_KEY`` with a one-line message on stderr and
    makes no network call if the key is absent; returns ``EXIT_BELOW_TRIGGER``
    after the free count if the transcript cannot reach the trigger, again
    without any billed call. Every API error propagates as an exception with a
    traceback - loudly, rather than as an empty report.
    """
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return EXIT_NO_KEY

    import anthropic  # imported lazily so the self-test needs no SDK

    client = anthropic.Anthropic(api_key=api_key)
    messages = transcript_module.build_transcript(
        turns=DEMO_TURNS, chars_per_turn=DEMO_CHARS_PER_TURN
    )

    check = preflight_module.Preflight(
        counted_tokens=count_transcript_tokens(
            client, model=MODEL, messages=messages
        ),
        trigger_tokens=DEMO_POLICY.trigger_tokens,
    )
    print(render_preflight(check))
    if not check.will_fire:
        return EXIT_BELOW_TRIGGER
    print()

    response = compact_once(
        client,
        model=MODEL,
        messages=messages,
        compaction_policy=DEMO_POLICY,
        max_tokens=MAX_TOKENS,
    )
    compaction_report = report_module.read_response(response)
    continuation = report_module.continuation_messages(
        messages, response["content"]  # type: ignore[arg-type]
    )
    print(
        render(
            compaction_report,
            DEMO_POLICY,
            continuation,
            model=MODEL,
            turns=DEMO_TURNS,
            chars_per_turn=DEMO_CHARS_PER_TURN,
            usd_in_per_mtok=USD_IN_PER_MTOK,
            usd_out_per_mtok=USD_OUT_PER_MTOK,
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
