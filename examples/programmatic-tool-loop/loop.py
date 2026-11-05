"""The hand-written loop for programmatic tool calling, over an injected ``Send``.

Imperative shell: request sequencing only. Reading a response is turn.py,
building the reply is reply.py, and running tools is execute.py. The API
call itself sits behind ``Send``, so the offline tests can replay recorded
responses.

See research/2026-11-05-programmatic-tool-calling-loop.md.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from execute import Handler, run_calls
from reply import build_reply
from toolset import validate_tools
from turn import CodeRun, Finished, Reply, classify

# The server abandons a pending programmatic call after about this long.
SERVER_PENDING_TIMEOUT_S = 270.0

# The one capability the loop needs from the outside world. It receives
# ``tools``, ``messages`` and (once known) ``container``. The adapter adds
# model and max_tokens.
Send = Callable[[dict], Reply]


@dataclass(frozen=True)
class RunResult:
    final_text: str
    stdout: str  # every finished code run's stdout, in order
    stderr: str  # same for stderr. A tool the server gave up on shows up here as TimeoutError.
    turns: int  # requests sent
    programmatic_calls: int
    direct_calls: int


class MaxTurnsError(RuntimeError):
    """The model was still calling tools after ``max_turns`` requests."""


def run(send: Send, user_prompt: str, tools: Sequence[dict], handlers: Mapping[str, Handler], *,
        max_turns: int = 8, tool_timeout_s: float = 200.0,
        log: Callable[[str], None] | None = None) -> RunResult:
    """Run the loop until the model ends its turn.

    Each request resends the identical ``tools`` object and the full history,
    with every assistant turn replayed unchanged (``server_tool_use`` blocks
    included). Once any response has carried a container id, every later
    request passes the most recent one as ``container``.

    Raises (a raised error means the run stopped; nothing is retried):
        ValueError: ``max_turns < 1``, or ``tool_timeout_s`` outside
            (0, SERVER_PENDING_TIMEOUT_S).
        ToolsetError: ``tools`` / ``handlers`` are unusable. Raised before any request.
        ProtocolError / UnknownCallerError / MissingContainerError /
            CodeExecutionFailedError / UnexpectedStopError: from ``classify``.
        UnknownToolError: the model called a tool with no handler.
        NonTextResultError: a handler returned something other than ``str``.
        MaxTurnsError: still paused after ``max_turns`` requests.
        Any exception from ``send`` propagates unchanged. An expired
            container, for example, is the API's 400 and is not retried.
        Tool calls already run are not rolled back.
    """
    if max_turns < 1:
        raise ValueError(f"max_turns must be >= 1, got {max_turns}")
    if not 0 < tool_timeout_s < SERVER_PENDING_TIMEOUT_S:
        raise ValueError(f"tool_timeout_s must be in (0, {SERVER_PENDING_TIMEOUT_S:g}), got {tool_timeout_s}")
    validate_tools(tools, frozenset(handlers))

    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    container_id: str | None = None
    code_runs: list[CodeRun] = []
    programmatic = direct = 0

    for turn_no in range(1, max_turns + 1):
        request: dict = {"tools": tools, "messages": list(messages)}
        if container_id is not None:
            request["container"] = container_id
        reply = send(request)
        turn = classify(reply)
        container_id = reply.container_id or container_id
        code_runs.extend(turn.code_runs)

        if isinstance(turn, Finished):
            return RunResult(final_text=turn.text, stdout="".join(r.stdout for r in code_runs),
                             stderr="".join(r.stderr for r in code_runs), turns=turn_no,
                             programmatic_calls=programmatic, direct_calls=direct)

        if log is not None:
            for call in turn.calls:
                via = f"code {call.caller_tool_id}" if call.is_programmatic else "direct"
                log(f"[turn {turn_no}] {call.name}({call.input}) via {via}")
        results = run_calls(turn.calls, handlers, tool_timeout_s)
        messages.append({"role": "assistant", "content": reply.content})
        messages.append(build_reply(turn, results))
        programmatic += len(turn.programmatic)
        direct += len(turn.direct)

    raise MaxTurnsError(f"still calling tools after {max_turns} requests")
