"""A scripted stand-in for the Claude Code CLI, for offline tests only.

Implements the SDK's public ``Transport`` interface and speaks the same
stream-json control protocol the bundled CLI does, so ``query()`` runs its real
code path -- including routing ``tools/call`` into our in-process MCP server --
with no subprocess, no key and no network. The "model" is a script: call the
tool once per expression, then answer with the last tool result.

Pinned to claude-agent-sdk==0.2.168; ``Transport`` is documented as an
internal API that may change between releases.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import anyio
from claude_agent_sdk import ProcessError, Transport

_MCP_PROTOCOL_VERSION = "2025-06-18"


class ScriptedCLI(Transport):
    """Fake CLI: MCP handshake, one ``tools/call`` per expression, then a result.

    ``tool_results`` collects each JSON-RPC response the SDK sent back for a
    ``tools/call``, in order. With ``final_subtype != "success"`` the fake
    emits an error result and then fails like the real CLI does (non-zero exit).
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        tool_wire_name: str,
        calls: Sequence[dict[str, Any]],
        final_subtype: str = "success",
    ) -> None:
        self._server = server_name
        self._tool = tool_name
        self._wire_name = tool_wire_name
        self._calls = list(calls)
        self._final_subtype = final_subtype
        self._send, self._recv = anyio.create_memory_object_stream[dict[str, Any]](100)
        self._ready = False
        self._next_call = 0
        self.tool_results: list[dict[str, Any]] = []

    # -- Transport interface ------------------------------------------------ #

    async def connect(self) -> None:
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    async def end_input(self) -> None:
        return None

    async def close(self) -> None:
        self._ready = False

    async def write(self, data: str) -> None:
        for line in data.splitlines():
            await self._on_sdk_message(json.loads(line))

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        async for message in self._recv:
            yield message
        if self._final_subtype != "success":
            raise ProcessError("Command failed with exit code 1", exit_code=1)

    # -- scripted CLI behaviour --------------------------------------------- #

    async def _on_sdk_message(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "control_request" and msg["request"]["subtype"] == "initialize":
            await self._send.send(
                {
                    "type": "control_response",
                    "response": {"subtype": "success", "request_id": msg["request_id"], "response": {}},
                }
            )
        elif kind == "user":
            await self._mcp("init", "initialize", {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "scripted-cli", "version": "0"},
            })
        elif kind == "control_response":
            await self._on_mcp_response(msg["response"]["request_id"], msg["response"]["response"])

    async def _on_mcp_response(self, request_id: str, response: dict[str, Any]) -> None:
        if request_id == "init":
            await self._mcp("initialized", "notifications/initialized", None)
            return
        if request_id.startswith("call-"):
            self.tool_results.append(response["mcp_response"])
        if self._next_call < len(self._calls):
            await self._call_tool(self._next_call)
            self._next_call += 1
        else:
            await self._finish()

    async def _call_tool(self, index: int) -> None:
        args = self._calls[index]
        await self._send.send(_assistant([
            {"type": "tool_use", "id": f"toolu_{index}", "name": self._wire_name, "input": args}
        ]))
        await self._mcp(f"call-{index}", "tools/call", {"name": self._tool, "arguments": args})

    async def _mcp(self, request_id: str, method: str, params: dict[str, Any] | None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["id"] = request_id
            message["params"] = params
        await self._send.send(
            {
                "type": "control_request",
                "request_id": request_id,
                "request": {"subtype": "mcp_message", "server_name": self._server, "message": message},
            }
        )

    async def _finish(self) -> None:
        last = self.tool_results[-1]["result"]["content"][0]["text"] if self.tool_results else ""
        answer = f"The result is {last}."
        is_error = self._final_subtype != "success"
        if not is_error:
            await self._send.send(_assistant([{"type": "text", "text": answer}]))
        await self._send.send(
            {
                "type": "result",
                "subtype": self._final_subtype,
                "duration_ms": 1,
                "duration_api_ms": 1,
                "is_error": is_error,
                "num_turns": len(self._calls) + 1,
                "session_id": "scripted",
                "result": None if is_error else answer,
                "total_cost_usd": 0.0,
                "errors": ["max turns reached"] if is_error else [],
            }
        )
        await self._send.aclose()


def _assistant(content: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "model": "scripted", "content": content},
        "parent_tool_use_id": None,
    }
