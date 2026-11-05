"""Read one assistant response into the client's next obligation.

Pure: no I/O, no SDK. ``Reply.content`` is a list of plain JSON dicts (the
shell converts SDK objects once, at the boundary).

With programmatic tool calling, one response can carry two kinds of client
tool call, told apart only by the ``caller`` field on each ``tool_use`` block:

  - ``{"type": "direct"}`` (or no ``caller`` at all, as in older responses):
    the model called the tool itself, as in any tool loop.
  - ``{"type": "code_execution_20260120", "tool_id": "srvtoolu_..."}``: Python
    running in the code-execution container called the tool. The container
    is paused until the client answers, and the client must pass the
    container id back to resume it.

See research/2026-11-05-programmatic-tool-calling-loop.md.
"""

from __future__ import annotations

from dataclasses import dataclass

STOP_PAUSED = "tool_use"
STOP_FINISHED = "end_turn"
DIRECT_CALLER = "direct"
# code_execution_20260120 is what the API tags today; the SDK also types
# code_execution_20250825. Any code_execution_* caller is a programmatic call.
CODE_EXECUTION_CALLER_PREFIX = "code_execution_"

_TOOL_USE = "tool_use"
_TEXT = "text"
_CODE_RESULT_BLOCK = "code_execution_tool_result"
_CODE_RESULT_OK = "code_execution_result"
_CODE_RESULT_ERROR = "code_execution_tool_result_error"
_CODE_RESULT_ENCRYPTED = "encrypted_code_execution_result"


@dataclass(frozen=True)
class Reply:
    """What the loop needs from one API response."""

    stop_reason: str
    content: list[dict]
    container_id: str | None  # top-level ``container.id``; None if no container was used


class ProtocolError(RuntimeError):
    """The response breaks a documented shape this loop depends on."""


class UnknownCallerError(ProtocolError):
    """A ``tool_use`` block has a ``caller.type`` that is neither direct nor code execution."""


class MissingContainerError(ProtocolError):
    """A programmatic call is pending but the response carried no container id to resume."""


class UnexpectedStopError(RuntimeError):
    """A ``stop_reason`` this loop does not handle (``pause_turn``, ``max_tokens``, ``refusal``, ...)."""


class CodeExecutionFailedError(RuntimeError):
    """The code-execution tool itself failed (``code_execution_tool_result_error``)."""

    def __init__(self, tool_use_id: str, error_code: str):
        self.tool_use_id = tool_use_id
        self.error_code = error_code
        super().__init__(f"code execution {tool_use_id} failed with {error_code}")


@dataclass(frozen=True)
class ToolCall:
    """One client tool call. ``caller_tool_id`` is the ``srvtoolu_`` id of the
    code execution that made it, or None for a direct call."""

    id: str
    name: str
    input: dict
    caller_tool_id: str | None

    @property
    def is_programmatic(self) -> bool:
        return self.caller_tool_id is not None


@dataclass(frozen=True)
class CodeRun:
    """The output of one finished code execution. A tool that never answered
    shows up only here, as a ``TimeoutError`` line in ``stderr`` (``return_code`` stays 0)."""

    tool_use_id: str
    stdout: str
    stderr: str
    return_code: int


@dataclass(frozen=True)
class Paused:
    """The model stopped for client tool calls. Cannot hold zero calls, or a
    programmatic call without a container id to resume.

    Raises (on construction):
        ProtocolError: ``calls`` is empty.
        MissingContainerError: a call is programmatic and ``container_id`` is None.
    """

    calls: tuple[ToolCall, ...]  # response order
    container_id: str | None
    code_runs: tuple[CodeRun, ...]

    def __post_init__(self) -> None:
        if not self.calls:
            raise ProtocolError("stop_reason is tool_use but the response has no tool_use blocks")
        if self.container_id is None and any(call.is_programmatic for call in self.calls):
            raise MissingContainerError("programmatic tool call pending but the response has no container id")

    @property
    def programmatic(self) -> tuple[ToolCall, ...]:
        return tuple(call for call in self.calls if call.is_programmatic)

    @property
    def direct(self) -> tuple[ToolCall, ...]:
        return tuple(call for call in self.calls if not call.is_programmatic)


@dataclass(frozen=True)
class Finished:
    """The model ended its turn. ``text`` joins every text block in order."""

    text: str
    code_runs: tuple[CodeRun, ...]


Turn = Paused | Finished


def classify(reply: Reply) -> Turn:
    """Classify one response as ``Paused`` (calls to run) or ``Finished``.

    Raises:
        UnknownCallerError: a ``tool_use`` has an unrecognised ``caller.type``.
        ProtocolError: a programmatic ``caller`` lacks ``tool_id``; ``tool_use``
            blocks appear under ``end_turn``; ``tool_use`` stop with no calls;
            or an ``encrypted_code_execution_result`` arrives (only produced
            when code calls web search, which this loop never offers).
        MissingContainerError: a programmatic call is pending without a container id.
        CodeExecutionFailedError: a ``code_execution_tool_result_error`` block is
            present. Deliberately strict, even if the model recovers later in
            the same turn.
        UnexpectedStopError: any ``stop_reason`` other than tool_use / end_turn.
    """
    calls = tuple(_tool_call(block) for block in reply.content if block.get("type") == _TOOL_USE)
    runs = tuple(_code_run(block) for block in reply.content if block.get("type") == _CODE_RESULT_BLOCK)

    if reply.stop_reason == STOP_PAUSED:
        return Paused(calls=calls, container_id=reply.container_id, code_runs=runs)
    if reply.stop_reason == STOP_FINISHED:
        if calls:
            raise ProtocolError(f"end_turn response still has {len(calls)} tool_use block(s)")
        text = "".join(block["text"] for block in reply.content if block.get("type") == _TEXT)
        return Finished(text=text, code_runs=runs)
    raise UnexpectedStopError(f"stop_reason={reply.stop_reason!r}")


def _tool_call(block: dict) -> ToolCall:
    return ToolCall(id=block["id"], name=block["name"], input=block["input"],
                    caller_tool_id=_caller_tool_id(block))


def _caller_tool_id(block: dict) -> str | None:
    caller = block.get("caller")
    if caller is None:
        return None
    kind = caller.get("type")
    if kind == DIRECT_CALLER:
        return None
    if isinstance(kind, str) and kind.startswith(CODE_EXECUTION_CALLER_PREFIX):
        if "tool_id" not in caller:
            raise ProtocolError(f"tool_use {block['id']}: programmatic caller has no tool_id")
        return caller["tool_id"]
    raise UnknownCallerError(f"tool_use {block['id']}: unknown caller type {kind!r}")


def _code_run(block: dict) -> CodeRun:
    result = block["content"]
    kind = result["type"]
    if kind == _CODE_RESULT_OK:
        return CodeRun(tool_use_id=block["tool_use_id"], stdout=result["stdout"],
                       stderr=result["stderr"], return_code=result["return_code"])
    if kind == _CODE_RESULT_ERROR:
        raise CodeExecutionFailedError(block["tool_use_id"], result["error_code"])
    if kind == _CODE_RESULT_ENCRYPTED:
        raise ProtocolError("encrypted_code_execution_result: only produced when code calls web search, "
                            "which this loop never offers")
    raise ProtocolError(f"unknown code_execution_tool_result content type {kind!r}")
