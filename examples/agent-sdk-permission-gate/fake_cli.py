"""A scripted stand-in for the Claude Code CLI's permission traffic, for offline tests only.

Implements the SDK's ``Transport`` interface and speaks the stream-json
control protocol, so ``query()`` runs its real code path: it registers hooks
in ``initialize``, receives ``hook_callback`` / ``can_use_tool`` control
requests, calls our adapters, and writes the replies back.

What this fake does NOT do: decide whether the CLI *would* send a request.
It does not model permission modes or the evaluation order; the test picks
which requests to send. So nothing here can confirm the bypass claim, which
rests on the docs and the live run. It confirms the wire contract: what the
SDK registers, what it hands our adapters, and what it sends back.

Pinned to claude-agent-sdk==0.2.177; ``Transport`` is documented as an
internal API that may change between releases.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

import anyio
from claude_agent_sdk import Transport

# How long the fake waits after the prompt arrives before its first request,
# giving the SDK time to close stdin if it was going to.
_SETTLE_SECONDS = 0.05


@dataclass(frozen=True)
class HookCall:
    """Fire every registered PreToolUse callback whose matcher matches ``tool_name``."""

    tool_name: str
    payload: Any  # the hook's ``input``; deliberately untyped so tests can send garbage


@dataclass(frozen=True)
class PermissionCall:
    """Send a ``can_use_tool`` request, as the CLI does at the last step."""

    tool_name: str
    tool_input: dict[str, Any]


Step = HookCall | PermissionCall


class StdinClosedError(RuntimeError):
    """The fake needed to send a control request after the SDK closed stdin."""


class ScriptedPermissionCLI(Transport):
    """Fake CLI: initialize, wait for the prompt, send the scripted requests, finish.

    After the run: ``initialize`` is the SDK's initialize request body,
    ``replies`` holds each control reply (``{"subtype", "request_id",
    "response" | "error"}``) in script order, ``stdin_open_after_prompt``
    says whether stdin was still open when the first request would go out,
    and ``unrouted`` lists HookCall tool names no registered matcher matched.
    """

    def __init__(self, script: Sequence[Step], *, on_failure_supported: bool = True) -> None:
        self._script = list(script)
        self._on_failure_supported = on_failure_supported
        self._to_cli, self._inbox = anyio.create_memory_object_stream[dict[str, Any]](100)
        self._ready = False
        self._stdin_closed = False
        self._next_id = 0
        self.initialize: dict[str, Any] | None = None
        self.replies: list[dict[str, Any]] = []
        self.stdin_open_after_prompt: bool | None = None
        self.unrouted: list[str] = []

    # -- Transport interface ------------------------------------------------ #

    async def connect(self) -> None:
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    async def write(self, data: str) -> None:
        for line in data.splitlines():
            if line.strip():
                self._to_cli.send_nowait(json.loads(line))

    async def end_input(self) -> None:
        self._stdin_closed = True

    async def close(self) -> None:
        self._ready = False

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        init = await self._expect("control_request")
        self.initialize = init["request"]
        yield _success(init["request_id"], {"hook_on_failure_supported": self._on_failure_supported})

        await self._expect("user")
        await anyio.sleep(_SETTLE_SECONDS)
        self.stdin_open_after_prompt = not self._stdin_closed

        for request in self._expand(self._script):
            if self._stdin_closed:
                raise StdinClosedError(f"stdin closed before {request['subtype']} could be answered")
            request_id = self._new_id()
            yield {"type": "control_request", "request_id": request_id, "request": request}
            reply = await self._expect("control_response")
            if reply["response"]["request_id"] != request_id:
                raise RuntimeError(f"reply for {reply['response']['request_id']}, expected {request_id}")
            self.replies.append(reply["response"])

        yield _result()

    # -- scripted CLI behaviour --------------------------------------------- #

    async def _expect(self, kind: str) -> dict[str, Any]:
        message = await self._inbox.receive()
        if message.get("type") != kind:
            raise RuntimeError(f"fake CLI expected {kind!r}, got {message.get('type')!r}")
        return message

    def _new_id(self) -> str:
        self._next_id += 1
        return f"cli_req_{self._next_id}"

    def _expand(self, script: Sequence[Step]) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        for i, step in enumerate(script):
            tool_use_id = f"toolu_{i}"
            if isinstance(step, PermissionCall):
                requests.append({
                    "subtype": "can_use_tool",
                    "tool_name": step.tool_name,
                    "input": step.tool_input,
                    "tool_use_id": tool_use_id,
                })
                continue
            callback_ids = self._callback_ids_for(step.tool_name)
            if not callback_ids:
                self.unrouted.append(step.tool_name)
            for callback_id in callback_ids:
                requests.append({
                    "subtype": "hook_callback",
                    "callback_id": callback_id,
                    "input": step.payload,
                    "tool_use_id": tool_use_id,
                })
        return requests

    def _callback_ids_for(self, tool_name: str) -> list[str]:
        assert self.initialize is not None
        matchers = (self.initialize.get("hooks") or {}).get("PreToolUse") or []
        ids: list[str] = []
        for matcher in matchers:
            pattern = matcher.get("matcher")
            if pattern is None or re.fullmatch(pattern, tool_name):
                ids.extend(matcher["hookCallbackIds"])
        return ids


def _success(request_id: str, response: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "control_response",
        "response": {"subtype": "success", "request_id": request_id, "response": response},
    }


def _result() -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "scripted",
        "result": "done",
        "total_cost_usd": 0.0,
    }
