"""Run a small fixed eval set as one Message Batch, poll until it ends, and
join the results back to their cases by custom_id.

Imperative shell: the SDK calls, the clock and the key live here; the
decisions are plan.poll_decision, join.join_results and join.grade.
See research/2026-10-19-message-batches-eval.md.

Dry run (default, no key, no network):  python batch_cli.py
Live, billed batch run (opt-in):        python batch_cli.py --live
Offline self-test:                      python test_batch.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable

from cost import BATCH_DISCOUNT, PRICES_FETCHED, batch_cost_usd
from join import Grade, Succeeded, grade, join_results
from plan import EvalCase, PollDecision, build_requests, poll_decision

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 64
# Most batches end within an hour; polling once a minute is plenty.
POLL_INTERVAL_S = 60.0

EVAL_CASES = (
    EvalCase("capital-france", "What is the capital of France? Answer in one word.", "Paris"),
    EvalCase("arith-17x23", "What is 17 * 23? Reply with only the number.", "391"),
    EvalCase("chem-water", "What is the chemical formula for water? Reply with only the formula.", "H2O"),
    EvalCase("planet-red", "Which planet is called the Red Planet? Answer in one word.", "Mars"),
)


def submit(client, requests: list[dict]) -> str:
    """Create the batch and return its id.

    Raises whatever the SDK raises (auth, rate limit, 4xx on a malformed
    envelope). Per-request params are validated asynchronously by the API, so
    a bad param shows up later as an ``errored`` result, not here.
    """
    batch = client.messages.batches.create(requests=requests)
    return batch.id


def wait_and_collect(
    client,
    batch_id: str,
    *,
    sleep: Callable[[float], None],
    interval: float,
    on_poll: Callable[[object], None] = lambda batch: None,
) -> list[dict]:
    """Retrieve the batch until poll_decision says FETCH, then return every
    result line as a plain JSONL-shaped dict.

    ``sleep`` has no default so a caller must say where its clock comes from;
    ``main`` binds ``time.sleep``. ``results`` is requested only after the
    batch has ended. Termination relies on the API ending every batch within
    its 24-hour expiry.

    Raises:
        ValueError: an unknown processing_status (from poll_decision).
        Any SDK error from ``retrieve`` or ``results``, unchanged. Results read
        before a failure mid-stream are discarded, not returned.
    """
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        on_poll(batch)
        if poll_decision(batch.processing_status) is PollDecision.FETCH:
            break
        sleep(interval)
    return [line.model_dump(mode="json") for line in client.messages.batches.results(batch_id)]


def _print_dry_run(requests: list[dict]) -> None:
    print(json.dumps(requests, indent=2))
    output_ceiling = batch_cost_usd(MODEL, 0, len(requests) * MAX_TOKENS)
    prompt_chars = sum(len(r["params"]["messages"][0]["content"]) for r in requests)
    print(
        f"\n{len(requests)} requests to {MODEL}, max_tokens={MAX_TOKENS}.\n"
        f"Output-side cost ceiling at batch price ({int(BATCH_DISCOUNT * 100)}% off, "
        f"prices fetched {PRICES_FETCHED}): ${output_ceiling:.6f}\n"
        f"Input tokens are not counted offline ({prompt_chars} prompt chars total).\n"
        "Dry run only: nothing was sent. Re-run with --live for a billed batch."
    )


def _run_live(client, requests: list[dict], *, interval: float) -> int:
    """Submit, poll, join, grade and print. Raises on any SDK or join error."""
    batch_id = submit(client, requests)
    print(f"submitted {batch_id}; polling every {interval:.0f}s")

    def report(batch) -> None:
        c = batch.request_counts
        print(
            f"  {batch.processing_status}: processing={c.processing} "
            f"succeeded={c.succeeded} errored={c.errored} "
            f"canceled={c.canceled} expired={c.expired}"
        )

    lines = wait_and_collect(
        client, batch_id, sleep=time.sleep, interval=interval, on_poll=report
    )
    joined = join_results(EVAL_CASES, lines)
    in_tok = out_tok = 0
    for case, outcome in joined:
        g = grade(case, outcome)
        detail = repr(outcome.text) if isinstance(outcome, Succeeded) else repr(outcome)
        print(f"{g.value:<9} {case.id:<16} {detail}")
        if isinstance(outcome, Succeeded):
            in_tok += outcome.input_tokens
            out_tok += outcome.output_tokens
            print(f"          service_tier={outcome.service_tier}")
    passed = sum(1 for c, o in joined if grade(c, o) is Grade.PASS)
    print(
        f"\n{passed}/{len(joined)} passed; {in_tok} in / {out_tok} out tokens "
        f"= ${batch_cost_usd(MODEL, in_tok, out_tok):.6f} at batch price"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--live", action="store_true", help="submit a real, billed batch (needs ANTHROPIC_API_KEY)"
    )
    args = parser.parse_args()
    requests = build_requests(EVAL_CASES, MODEL, MAX_TOKENS)

    if not args.live:
        _print_dry_run(requests)
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("--live needs ANTHROPIC_API_KEY set; nothing was sent.", file=sys.stderr)
        return 2
    import anthropic  # lazy: the dry run and self-test need no dependency

    return _run_live(anthropic.Anthropic(api_key=api_key), requests, interval=POLL_INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
