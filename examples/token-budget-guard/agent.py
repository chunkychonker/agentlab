"""Hand-written tool loop that counts each pending request and refuses to send
one that would exceed an input-token budget.

Imperative shell: the API calls happen here; the go/stop decision is
budget.check_budget. See research/2026-10-16-token-budget-guard.md.

Live (needs a key):   python agent.py [--limit N]
Offline self-test:    python test_guard.py
"""

from __future__ import annotations

import argparse
import os

from budget import BudgetExceeded, check_budget
from tools import TOOL_FUNCTIONS, TOOLS

# Count against the model the loop actually runs: token counts are
# tokenizer-specific (4.7+ models count ~30% higher), never reuse a number.
MODEL = "claude-haiku-4-5"
# Reply cap per turn; also the output reserve passed to check_budget.
MAX_TOKENS = 1024
SYSTEM_PROMPT = (
    "You are a careful assistant. Use the calculator tool for any arithmetic "
    "rather than doing it in your head."
)


def run_guarded_agent(
    client,
    user_message: str,
    *,
    token_limit: int,
    max_turns: int = 5,
    verbose: bool = False,
) -> str | BudgetExceeded:
    """Run the tool loop; return the final text, or BudgetExceeded if a turn's
    request (counted input + MAX_TOKENS reserve) would exceed ``token_limit``.

    Before every ``messages.create`` the identical request is measured with
    ``messages.count_tokens``. An over-budget request is never sent.

    Failure modes:
        - Any exception from ``count_tokens`` (network, auth, invalid request)
          propagates unchanged and nothing further is sent. This is fail-closed:
          if the meter is down the agent stops rather than running unmetered.
        - Any exception from ``create`` propagates unchanged.
        - ``RuntimeError`` if the model is still calling tools after
          ``max_turns`` requests.
        - ``ValueError`` from check_budget if ``token_limit`` is not positive
          (raised on turn 1, before any create call).
        Tool calls already executed before a failure are not rolled back; the
        calculator is side-effect free, so there is nothing to undo.
    """
    messages: list[dict] = [{"role": "user", "content": user_message}]

    for turn in range(1, max_turns + 1):
        # One request definition, used for both the count and the send, so
        # what is measured is exactly what would be sent (minus max_tokens,
        # which count_tokens does not accept).
        request = {"model": MODEL, "system": SYSTEM_PROMPT, "tools": TOOLS, "messages": messages}

        counted = client.messages.count_tokens(**request).input_tokens
        verdict = check_budget(counted, token_limit, turn, reserve=MAX_TOKENS)
        if verbose:
            status = "STOP" if verdict else "ok"
            print(f"  [turn {turn}] counted={counted} +reserve={MAX_TOKENS} limit={token_limit} -> {status}")
        if verdict is not None:
            return verdict

        response = client.messages.create(**request, max_tokens=MAX_TOKENS)

        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            fn = TOOL_FUNCTIONS.get(block.name)
            result_text = f"Error: unknown tool {block.name!r}" if fn is None else fn(block.input)
            if verbose:
                print(f"  [tool] {block.name}({block.input}) -> {result_text}")
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": result_text}
            )
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agent did not finish within {max_turns} turns")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--limit",
        type=int,
        default=8000,
        help="input-token budget per request, including the MAX_TOKENS reserve (default 8000)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ANTHROPIC_API_KEY is not set - skipping the live run.\n"
            "Set it and re-run for a real call, or run 'python test_guard.py' "
            "for the offline self-test."
        )
        return 0

    import anthropic  # lazy: the offline self-test needs no dependency

    client = anthropic.Anthropic(api_key=api_key)
    question = "What is 4839 * 1284, and is that more than five million?"
    print(f"> {question}  (limit={args.limit})")
    result = run_guarded_agent(client, question, token_limit=args.limit, verbose=True)
    if isinstance(result, BudgetExceeded):
        print(
            f"\nStopped by budget on turn {result.turn}: "
            f"{result.counted_tokens} counted + {MAX_TOKENS} reserve > {result.limit}"
        )
    else:
        print(f"\nFinal answer: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
