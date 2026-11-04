"""Hand-written tool loop over a deferred catalog, with server-side tool search.

Imperative shell: the API call happens behind an injected ``Send`` function;
the block handling is blocks.py and the tools array is toolset.py.
See research/2026-11-04-tool-search-deferred-tools.md.

Live (needs a key):   python agent.py ["question"] [--tools N] [--variant regex|bm25]
Offline self-test:    python test_tool_search.py
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from dataclasses import dataclass

from backends import run_tool
from blocks import (
    SearchFailure,
    discovered_tools,
    followup_user_turn,
    pending_client_tool_uses,
    search_failures,
)
from catalog import HOT_TOOLS, make_catalog
from toolset import SearchVariant, build_tools, validate_tools

# Listed as supporting tool search in the docs' model table (2026-11-04).
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024
DEFAULT_CATALOG_SIZE = 40
SYSTEM_PROMPT = (
    "You have a large tool catalog, most of it not loaded. If no loaded tool fits "
    "the request, search the catalog for one before answering."
)


@dataclass(frozen=True)
class Reply:
    """What the loop needs from one API response. ``content`` is plain JSON dicts."""

    stop_reason: str
    content: list[dict]


# The one capability the loop needs from the outside world.
Send = Callable[[dict], Reply]


@dataclass(frozen=True)
class RunResult:
    text: str
    discovered: tuple[str, ...]  # every tool name any search returned, in order
    requests: int


class ToolSearchFailed(RuntimeError):
    """A server search returned ``tool_search_tool_result_error``."""

    def __init__(self, failure: SearchFailure):
        self.failure = failure
        detail = f": {failure.message}" if failure.message else ""
        super().__init__(f"tool search {failure.tool_use_id} failed with {failure.error_code}{detail}")


class UnexpectedStop(RuntimeError):
    """The response stopped for a reason this loop does not handle (e.g. pause_turn, max_tokens)."""


def run_agent(send: Send, tools: list[dict], user_message: str, *, max_turns: int = 6,
              verbose: bool = False) -> RunResult:
    """Run the loop until the model ends its turn; return its text and what search found.

    Every request carries the same full ``tools`` array (deferred definitions
    included), and each assistant turn is echoed back unchanged, search blocks
    included. Only ``tool_use`` blocks get a ``tool_result``.

    Failure modes (nothing is retried; a raised error means the run stopped):
        - ``ToolsetError`` from validate_tools before any request is sent.
        - ``ToolSearchFailed`` as soon as a response contains a failed search,
          even if the model went on to recover in the same turn. Deliberately
          strict: an agent that should let the model retry must handle this.
        - ``UnexpectedStop`` for a stop_reason other than end_turn / tool_use.
        - ``RuntimeError`` if the model calls a name that is not a client tool
          in ``tools`` (the API should never allow it), or if it is still
          calling tools after ``max_turns`` requests.
        - Any exception from ``send`` propagates unchanged.
        Tool calls already run are not rolled back; the backends are canned
        and side-effect free.
    """
    validate_tools(tools)
    client_tools = {tool["name"] for tool in tools if tool.get("type", "custom") == "custom"}
    messages: list[dict] = [{"role": "user", "content": user_message}]
    discovered: list[str] = []

    for turn in range(1, max_turns + 1):
        request = {"model": MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
                   "tools": tools, "messages": list(messages)}
        reply = send(request)

        failures = search_failures(reply.content)
        if failures:
            raise ToolSearchFailed(failures[0])
        found = discovered_tools(reply.content)
        discovered.extend(found)
        if verbose:
            _print_searches(turn, reply.content, found)

        if reply.stop_reason == "end_turn":
            text = "".join(b["text"] for b in reply.content if b.get("type") == "text")
            return RunResult(text=text, discovered=tuple(discovered), requests=turn)
        if reply.stop_reason != "tool_use":
            raise UnexpectedStop(f"stop_reason={reply.stop_reason!r} on turn {turn}")

        outputs = {}
        for call in pending_client_tool_uses(reply.content):
            if call["name"] not in client_tools:
                raise RuntimeError(f"model called {call['name']!r}, which is not a client tool in this request")
            outputs[call["id"]] = run_tool(call["name"], call["input"])
            if verbose:
                print(f"  [tool]   {call['name']}({json.dumps(call['input'])}) -> {outputs[call['id']]}")
        messages.append({"role": "assistant", "content": reply.content})
        messages.append(followup_user_turn(reply.content, outputs))

    raise RuntimeError(f"agent did not finish within {max_turns} turns")


def _print_searches(turn: int, content: list[dict], found: list[str]) -> None:
    for block in content:
        if block.get("type") == "server_tool_use":
            print(f"  [turn {turn}] {block['name']}({json.dumps(block['input'])})")
    if found:
        print(f"  [turn {turn}] discovered: {found}")


def sdk_send(client) -> Send:
    """Adapt an ``anthropic.Anthropic`` client to ``Send``.

    Converts each response block to its JSON dict once, here at the boundary,
    with ``exclude_none=True`` so optional fields the server omitted are not
    echoed back as explicit nulls.

    Failure modes: any SDK/API exception from ``messages.create`` propagates.
    """
    def send(request: dict) -> Reply:
        response = client.messages.create(**request)
        content = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
        return Reply(stop_reason=response.stop_reason, content=content)

    return send


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", default="What's the weather in Madison, WI right now?")
    parser.add_argument("--tools", type=int, default=DEFAULT_CATALOG_SIZE, help="catalog size (default 40)")
    parser.add_argument("--variant", choices=[v.name.lower() for v in SearchVariant], default="regex")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set - skipping the live run.\n"
              "Set it and re-run, or run 'python test_tool_search.py' for the offline self-test.")
        return 0

    import anthropic  # lazy: the offline self-test needs no dependency

    tools = build_tools(make_catalog(args.tools), HOT_TOOLS, SearchVariant[args.variant.upper()])
    deferred = sum(1 for tool in tools if tool.get("defer_loading") is True)
    print(f"> {args.question}")
    print(f"  {len(tools)} tools sent ({deferred} deferred, loaded: {sorted(HOT_TOOLS)} + search tool)")
    result = run_agent(sdk_send(anthropic.Anthropic(api_key=api_key)), tools, args.question, verbose=True)
    print(f"\nFinal answer ({result.requests} requests): {result.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
