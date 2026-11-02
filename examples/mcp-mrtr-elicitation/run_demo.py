"""Prints the same confirm-before-delete call under both protocol eras, offline.

The 2026-07-28 leg drives the MRTR loop by hand (`allow_input_required=True`)
so the intermediate `InputRequiredResult` is visible; the legacy leg lets the
SDK handle the mid-call `elicitation/create`. A scripted "human" answers yes.

Run: python run_demo.py
"""

import asyncio
import json

from mcp import Client
from mcp.client import ClientRequestContext
from mcp.types import CallToolResult, ElicitRequestParams, ElicitResult, InputRequiredResult

from server import SEED_NOTES, build_server

MODERN = "2026-07-28"
LEGACY = "legacy"
YES = {"ok": True}


def _text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


async def _never_called(_ctx: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
    """Advertises the elicitation capability; the manual MRTR drive answers instead.

    Raises:
        RuntimeError: always -- reaching it means the SDK auto-dispatched a question
            that this leg meant to answer by hand.
    """
    raise RuntimeError(f"callback reached on a manually driven call: {params.message!r}")


async def _modern(lines: list[str]) -> None:
    store = dict(SEED_NOTES)
    async with Client(build_server(store), mode=MODERN, elicitation_callback=_never_called) as client:
        lines.append(f"== {client.protocol_version}: Multi Round-Trip Request ==")
        args = {"note_id": "n1"}
        lines.append(f"client -> tools/call delete_note {json.dumps(args)}")
        first = await client.session.call_tool("delete_note", args, allow_input_required=True)
        if not isinstance(first, InputRequiredResult):
            raise RuntimeError(f"expected InputRequiredResult on {MODERN}, got {type(first).__name__}")
        wire = first.model_dump(by_alias=True, exclude_none=True)
        lines.append(f"server -> resultType={wire['resultType']!r}, requestState=<sealed, opaque>")
        responses = {}
        for key, req in wire["inputRequests"].items():
            lines.append(f"           inputRequests[{key!r}]: {req['method']} {req['params']['message']!r}")
            lines.append(f"human  -> accept {json.dumps(YES)}")
            responses[key] = ElicitResult(action="accept", content=YES)
        lines.append(f"notes before retry: {sorted(store)}")
        lines.append("client -> tools/call delete_note (same args + inputResponses + requestState)")
        final = await client.session.call_tool(
            "delete_note", args, input_responses=responses, request_state=first.request_state
        )
        if not isinstance(final, CallToolResult):
            raise RuntimeError(f"expected CallToolResult on retry, got {type(final).__name__}")
        lines.append(f"server -> resultType={final.result_type!r}: {_text(final)}")
        lines.append(f"notes after: {sorted(store)}")


async def _legacy(lines: list[str]) -> None:
    store = dict(SEED_NOTES)

    async def human(_ctx: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
        lines.append(f"server -> elicitation/create {params.message!r}  (standalone request, mid-call)")
        lines.append(f"human  -> accept {json.dumps(YES)}")
        return ElicitResult(action="accept", content=YES)

    async with Client(build_server(store), mode=LEGACY, elicitation_callback=human) as client:
        lines.append(f"== {client.protocol_version}: legacy, server-initiated request ==")
        args = {"note_id": "n1"}
        lines.append(f"client -> tools/call delete_note {json.dumps(args)}")
        final = await client.call_tool("delete_note", args)
        lines.append(f"server -> tools/call result: {_text(final)}")
        lines.append(f"notes after: {sorted(store)}")


async def transcript() -> list[str]:
    """Run both legs and return the transcript lines.

    Raises:
        RuntimeError: the SDK returned a result shape other than the one this
            protocol version should produce.
        mcp.shared.exceptions.MCPError: the server rejected a request.
    """
    lines: list[str] = []
    await _modern(lines)
    lines.append("")
    await _legacy(lines)
    return lines


def main() -> int:
    for line in asyncio.run(transcript()):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
