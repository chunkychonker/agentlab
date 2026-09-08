"""The imperative shell for the thinking strategy: reads the key, adapts the SDK
to `CountTokens`, prints the report.

Sibling of `main.py`. Same free double-count, same core `preview()`, a different
policy and a different fixture: this one measures what
`clear_thinking_20251015` would drop from a reasoning-heavy conversation.

Like `main.py`, this is the only kind of file that imports `anthropic`, reads an
environment variable, or writes to a stream - and the SDK import is lazy, inside
`main()`, so `test_preview_thinking.py` can import this module with no
dependency installed.

Run it live (needs a key; costs nothing - token counting is free):
    export ANTHROPIC_API_KEY=sk-ant-...
    python preview_thinking.py

Run the offline self-test (no key, no network, stdlib only):
    python test_preview_thinking.py

See the research note this came from:
    research/2026-09-08-context-editing-clear-thinking-preview.md
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence

import policy as policy_module
import preview as preview_module
import thinking_transcript as transcript_module

# **Not** `main.py`'s `claude-haiku-4-5`, and the difference is correctness, not
# cost. Thinking blocks from previous assistant turns count toward input tokens
# only on models that keep all prior turns (Opus 4.5+, Sonnet 4.6+); on earlier
# Opus/Sonnet and on every Haiku the API strips them before counting, so there
# is nothing left for `clear_thinking_20251015` to clear and the measured saving
# is zero. `claude-sonnet-5` is the cheapest id that qualifies.
# See knowledge/anthropic-models.md and the README.
MODEL = "claude-sonnet-5"

API_KEY_ENV = "ANTHROPIC_API_KEY"

# 5-series models take the adaptive thinking config only; a manual
# `budget_tokens` is a 400 on 4.7+. The value is built fresh per call rather
# than shared from a module-level dict, so no caller can mutate what the next
# request sends. See knowledge/thinking-blocks.md.
THINKING_MODE = "adaptive"

# The demo transcript: 8 assistant thinking turns with 1200-character reasoning
# bodies. Big enough that clearing is worth measuring, small enough to count in
# one request.
TURNS = 8
THINKING_CHARS = 1200

# Keep the two most recent thinking turns; the other six are cleared. There is
# no trigger to choose - this strategy has none.
DEMO_POLICY = policy_module.ClearThinkingPolicy(keep=2)

# Exit codes. A preview that could not run is not a preview reporting no saving.
EXIT_OK = 0
EXIT_NO_KEY = 1

MISSING_KEY_MESSAGE = (
    f"error: {API_KEY_ENV} is not set, so there is nothing to count against.\n"
    "  Set it and re-run (token counting is free), or run "
    "'python test_preview_thinking.py' for the offline self-test."
)

_NOT_APPLIED_EXPLANATION = (
    "  No edit was applied, and for this strategy that never means a trigger\n"
    "  went unmet - `clear_thinking_20251015` has no trigger, it always fires.\n"
    "  It means there was nothing left to clear. The usual cause is the model:\n"
    "  only models that keep all prior thinking turns (Opus 4.5+, Sonnet 4.6+)\n"
    "  still have prior-turn thinking in the request by the time the edit runs;\n"
    "  earlier Opus/Sonnet and every Haiku have already had it stripped. The\n"
    "  other cause is a `keep` that already covers every thinking turn sent."
)


def make_thinking_counter(client, *, model: str) -> preview_module.CountTokens:
    """Adapt an `anthropic.Anthropic` client to the core's `CountTokens`, with
    thinking enabled.

    Three things differ from `main.make_counter`, all of them about this
    strategy rather than about counting:

      * `thinking={"type": "adaptive"}` goes on every call. Without it the
        request is not an extended-thinking request and the blocks in the
        transcript are not counted the way a real turn would be.
      * `tools` is omitted when empty rather than sent as `[]`. This fixture is
        tool-free, and an unset optional is omitted, never sent as an empty
        value - the same rule `to_edit()` follows for `keep`.
      * the policy is a `ClearThinkingPolicy`, which the core never has to know.

    Unchanged: the count response's `context_management` carries **only**
    `original_input_tokens` (no `applied_edits`, unlike a generation response),
    and is absent entirely when no edit was applied. Both wire forms - a missing
    key and an explicit null - arrive here as `None`.

    ``client`` needs only `.beta.messages.count_tokens(...)`, so a fake
    satisfies it. It must be the **beta** namespace: `client.messages` has no
    `betas` parameter and raises `TypeError` rather than sending the header.

    Failure modes: every `anthropic.APIError` propagates untouched. An
    unsupported model, a rejected beta, or a rejected synthetic thinking-block
    signature must reach the caller as an error, never as a zero-saving report.
    """

    def count(
        *,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        context_management: Mapping[str, object] | None,
    ) -> preview_module.TokenCount:
        extra: dict[str, object] = {}
        if context_management is not None:
            extra["context_management"] = context_management
        if tools:
            extra["tools"] = list(tools)

        response = client.beta.messages.count_tokens(
            model=model,
            messages=list(messages),
            thinking={"type": THINKING_MODE},
            betas=[policy_module.BETA],
            **extra,
        )

        reported = getattr(response, "context_management", None)
        return preview_module.TokenCount(
            input_tokens=response.input_tokens,
            original_input_tokens=(
                None if reported is None else reported.original_input_tokens
            ),
        )

    return count


def render(
    report: preview_module.PreviewReport,
    pol: policy_module.ClearThinkingPolicy,
    *,
    model: str,
    turns: int,
    thinking_chars: int,
) -> str:
    """Render a report for a terminal. Pure; no trailing newline.

    Prints the exact `context_management` object that was sent so the reader can
    paste it straight into their own `messages.create` call. Cannot fail.
    """
    config = json.dumps(pol.to_config(), indent=2)
    lines = [
        f"Context editing preview: {policy_module.STRATEGY_CLEAR_THINKING}",
        f"  model       {model}",
        (
            f"  transcript  {turns} thinking turns, "
            f"{thinking_chars}-char reasoning ({1 + 2 * turns} messages, no tools)"
        ),
        f"  beta        {policy_module.BETA}",
        "",
        "context_management sent (paste this into messages.create):",
        *(f"  {line}" for line in config.splitlines()),
        "",
        f"  applied     {report.applied}",
        f"  original    {report.original_input_tokens} input tokens",
        f"  edited      {report.edited_input_tokens} input tokens",
        f"  saved       {report.tokens_saved} tokens ({report.percent_saved}%)",
    ]
    if not report.applied:
        lines += ["", _NOT_APPLIED_EXPLANATION]
    return "\n".join(lines)


def main() -> int:
    """Preview `DEMO_POLICY` against a synthetic thinking transcript and print
    the report.

    Failure modes: returns ``EXIT_NO_KEY`` with a one-line message on stderr and
    makes no network call if the key is absent. Every API error propagates as an
    exception with a traceback - loudly, rather than as a report of no saving.
    That includes a 400 about the fixture's synthetic thinking-block signatures,
    which is the one thing about this path that has never been run live.
    """
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return EXIT_NO_KEY

    import anthropic  # imported lazily so the self-test needs no SDK

    client = anthropic.Anthropic(api_key=api_key)
    report = preview_module.preview(
        make_thinking_counter(client, model=MODEL),
        transcript_module.build_thinking_transcript(
            turns=TURNS, thinking_chars=THINKING_CHARS
        ),
        [],
        DEMO_POLICY,
    )
    print(
        render(
            report,
            DEMO_POLICY,
            model=MODEL,
            turns=TURNS,
            thinking_chars=THINKING_CHARS,
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
