"""Measure input tokens for one catalog sent fully loaded vs. deferred behind tool search.

Two methods:
  --method count   (default, $0)  client.messages.count_tokens on both requests.
  --method create  (billed)       one tiny messages.create per variant; reads
                                  usage.input_tokens. About 2 requests on
                                  claude-haiku-4-5 with max_tokens=16.

Whether count_tokens honors defer_loading is undocumented, and an earlier lab
note found it rejects some server tools. Either outcome is reported as-is.
See research/2026-11-04-tool-search-deferred-tools.md.

Live (needs a key):   python measure.py [--tools N] [--method count|create]
"""

from __future__ import annotations

import argparse
import os

from catalog import HOT_TOOLS, make_catalog
from toolset import SearchVariant, build_tools

MODEL = "claude-haiku-4-5"  # count with the tokenizer of the model the loop runs
MEASURE_MAX_TOKENS = 16
PROMPT = "Reply with the single word OK."
EXIT_REJECTED = 2


def describe_saving(loaded: int, deferred: int) -> str:
    """One line comparing the two input-token counts, naming the direction plainly.

    Equal counts are reported as "no difference" rather than as a saving of
    zero, because that outcome means the method did not see the deferral.

    Raises:
        ValueError: if ``loaded`` is not positive or ``deferred`` is negative.
    """
    if loaded <= 0 or deferred < 0:
        raise ValueError(f"need loaded > 0 and deferred >= 0, got loaded={loaded} deferred={deferred}")
    if deferred == loaded:
        return f"no difference ({loaded} tokens each): this method does not reflect defer_loading"
    delta = loaded - deferred
    direction = "smaller" if delta > 0 else "LARGER"
    return f"deferred is {abs(delta)} tokens {direction} ({abs(delta) / loaded:.1%} of {loaded})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tools", type=int, default=40, help="catalog size (default 40)")
    parser.add_argument("--method", choices=["count", "create"], default="count")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set - skipping the measurement.")
        return 0

    import anthropic  # lazy: the offline self-test needs no dependency

    client = anthropic.Anthropic(api_key=api_key)
    catalog = make_catalog(args.tools)
    variants = {
        "loaded": catalog,
        "deferred": build_tools(catalog, HOT_TOOLS, SearchVariant.REGEX),
    }
    messages = [{"role": "user", "content": PROMPT}]
    counts = {}
    for label, tools in variants.items():
        try:
            if args.method == "count":
                counts[label] = client.messages.count_tokens(model=MODEL, tools=tools, messages=messages).input_tokens
            else:
                response = client.messages.create(model=MODEL, max_tokens=MEASURE_MAX_TOKENS, tools=tools,
                                                  messages=messages)
                counts[label] = response.usage.input_tokens
        except anthropic.BadRequestError as exc:
            print(f"{args.method}: the API rejected the {label} request ({len(tools)} tools): {exc.message}")
            if args.method == "count":
                print("Re-run with --method create for a billed measurement (2 tiny requests).")
            return EXIT_REJECTED
        print(f"{args.method}: {label:<8} {len(tools):>3} tools -> {counts[label]} input tokens")
    print(describe_saving(counts["loaded"], counts["deferred"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
