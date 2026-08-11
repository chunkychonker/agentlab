"""The imperative shell: reads the key, adapts the SDK to `CountTokens`, prints
the report.

This is the only file that imports `anthropic`, reads an environment variable,
or writes to a stream - and the SDK import is lazy, inside `main()`, so
`test_preview.py` can import this module with no dependency installed.

Run it live (needs a key; costs nothing - token counting is free):
    export ANTHROPIC_API_KEY=sk-ant-...
    python main.py

Run the offline self-test (no key, no network, stdlib only):
    python test_preview.py

See the research note this came from:
    research/2026-08-11-context-editing-preview.md
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence

import policy as policy_module
import preview as preview_module
import transcript as transcript_module

# Token counts are tokenizer-specific: Claude 4.7+ models use a newer tokenizer
# that reports roughly 30% more tokens for the same text. The model id therefore
# changes every number below, so it lives in exactly one constant.
# See knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"

API_KEY_ENV = "ANTHROPIC_API_KEY"

# The demo transcript: 20 complete tool-use round trips with 1200-character
# results. Big enough that clearing is worth measuring, small enough to count in
# one request.
ROUNDS = 20
RESULT_CHARS = 1200

# `tool_uses` rather than the API's default `input_tokens` trigger (100k), which
# a demo transcript would never reach. Production loops normally want the
# `input_tokens` form - see the README.
DEMO_POLICY = policy_module.ClearToolUsesPolicy(
    keep=3,
    trigger_kind="tool_uses",
    trigger_value=5,
)

# Exit codes. A preview that could not run is not a preview reporting no saving.
EXIT_OK = 0
EXIT_NO_KEY = 1

MISSING_KEY_MESSAGE = (
    f"error: {API_KEY_ENV} is not set, so there is nothing to count against.\n"
    "  Set it and re-run (token counting is free), or run "
    "'python test_preview.py' for the offline self-test."
)

_NOT_APPLIED_EXPLANATION = (
    "  No edit was applied. Either the trigger did not fire (fewer tool uses or\n"
    "  input tokens than the threshold), or `clear_at_least` could not be met -\n"
    "  that floor is all-or-nothing, so an over-ambitious value yields zero\n"
    "  saving rather than a partial one."
)


def make_counter(client, *, model: str) -> preview_module.CountTokens:
    """Adapt an `anthropic.Anthropic` client to the core's `CountTokens`.

    This is where the endpoint asymmetry noted in the research is absorbed: the
    count response's `context_management` carries **only**
    `original_input_tokens` (no `applied_edits`, unlike a generation response),
    and it is absent entirely when no edit was applied. Both wire forms - a
    missing key and an explicit null - arrive here as `None`.

    ``client`` needs only `.beta.messages.count_tokens(...)`, so a fake
    satisfies it. It must be the **beta** namespace: `client.messages` has no
    `betas` parameter and raises `TypeError` rather than sending the header.

    Failure modes: every `anthropic.APIError` propagates untouched. An
    unsupported model, a rejected beta, or a rate limit must reach the caller as
    an error, never as a zero-saving report.
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

        response = client.beta.messages.count_tokens(
            model=model,
            messages=list(messages),
            tools=list(tools),
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
    policy: policy_module.ClearToolUsesPolicy,
    *,
    model: str,
    rounds: int,
    result_chars: int,
) -> str:
    """Render a report for a terminal. Pure; no trailing newline.

    Prints the exact `context_management` object that was sent so the reader can
    paste it straight into their own `messages.create` call. Cannot fail.
    """
    config = json.dumps(policy.to_config(), indent=2)
    lines = [
        f"Context editing preview: {policy_module.STRATEGY}",
        f"  model       {model}",
        (
            f"  transcript  {rounds} tool-use rounds, "
            f"{result_chars}-char results ({1 + 2 * rounds} messages)"
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
    """Preview `DEMO_POLICY` against a synthetic transcript and print the report.

    Failure modes: returns ``EXIT_NO_KEY`` with a one-line message on stderr and
    makes no network call if the key is absent. Every API error propagates as an
    exception with a traceback - loudly, rather than as a report of no saving.
    """
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return EXIT_NO_KEY

    import anthropic  # imported lazily so the self-test needs no SDK

    client = anthropic.Anthropic(api_key=api_key)
    report = preview_module.preview(
        make_counter(client, model=MODEL),
        transcript_module.build_transcript(rounds=ROUNDS, result_chars=RESULT_CHARS),
        [transcript_module.tool_schema()],
        DEMO_POLICY,
    )
    print(
        render(
            report,
            DEMO_POLICY,
            model=MODEL,
            rounds=ROUNDS,
            result_chars=RESULT_CHARS,
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
