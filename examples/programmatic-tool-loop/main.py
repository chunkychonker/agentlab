"""Live run: Claude fans out SQL queries from code, through the hand-written loop.

Live (needs a key):   python main.py ["question"]
Offline self-test:    python -m unittest -v
"""

from __future__ import annotations

import argparse
import os

from demo_tools import HANDLERS, TOOLS
from loop import Send, run
from turn import Reply

# Sonnet 5.x supports programmatic tool calling (docs, 2026-11-05). Haiku 4.5,
# the lab's usual cheap default, does NOT. anthropic 2.5.0 lists this id.
MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 4096
DEFAULT_QUESTION = (
    "Using one separate query per region, get each region's 2026-Q3 revenue and target, "
    "then tell me which regions missed target and by how much."
)


def sdk_send(client, model: str, max_tokens: int) -> Send:
    """Adapt an ``anthropic.Anthropic`` client to ``Send``.

    Each block is converted to its JSON dict here, once, with
    ``exclude_none=True``, so optional fields the server left out are not
    echoed back as explicit nulls. ``caller`` survives the round trip
    (``sdk_contract_check.py`` checks this).

    Failure modes: any SDK/API exception from ``messages.create`` propagates.
    """
    def send(request: dict) -> Reply:
        response = client.messages.create(model=model, max_tokens=max_tokens, **request)
        content = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
        container_id = response.container.id if response.container is not None else None
        return Reply(stop_reason=response.stop_reason, content=content, container_id=container_id)

    return send


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set - skipping the live run.\n"
              "Set it and re-run, or run 'python -m unittest -v' for the offline self-test.")
        return 0

    import anthropic  # lazy: the offline self-test needs no dependency

    print(f"> {args.question}")
    result = run(sdk_send(anthropic.Anthropic(api_key=api_key), MODEL, MAX_TOKENS),
                 args.question, TOOLS, HANDLERS, log=print)
    print(f"\n{result.turns} requests, {result.programmatic_calls} programmatic + "
          f"{result.direct_calls} direct tool calls")
    print(f"--- code stdout ---\n{result.stdout}")
    if result.stderr:
        print(f"--- code stderr ---\n{result.stderr}")
    print(f"--- answer ---\n{result.final_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
