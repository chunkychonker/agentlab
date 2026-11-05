"""Run one turn's tool calls concurrently under a shared deadline.

The calls run concurrently because Claude's code may ``asyncio.gather`` several
tool calls, and the container waits for all of them. The deadline has to stay
below the server's ~270 s limit for a pending call. Past that limit the server
gives up on its own, and the only trace the client gets is a ``TimeoutError``
line in the final ``stderr``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, wait

from reply import ToolError
from turn import ToolCall

# A tool implementation: the call's ``input`` dict in, result text out.
Handler = Callable[[dict], str]


class UnknownToolError(LookupError):
    """The response called a tool name with no handler. The API should never allow this."""


def run_calls(calls: Sequence[ToolCall], handlers: Mapping[str, Handler],
              timeout_s: float) -> dict[str, str | ToolError]:
    """Run every call concurrently. Return ``{call.id: result}`` for all of them.

    A handler that raises an ``Exception`` yields
    ``ToolError("<ExcType>: <message>")``. A handler still running after
    ``timeout_s`` (measured from when they all start) yields
    ``ToolError("timed out after <timeout_s>s")``. In both cases the failure
    is reported to the model, which is the policy for recoverable failures.
    It is not hidden.

    Raises:
        UnknownToolError: a call names a tool missing from ``handlers``. This
            is checked before ANY handler starts, so nothing has run.

    Partial failure / known limit: Python cannot kill a thread. A timed-out
    handler is abandoned, not stopped. It runs to completion in the
    background and its result is discarded. Handlers with side effects should
    enforce their own timeouts.
    """
    unknown = sorted({call.name for call in calls if call.name not in handlers})
    if unknown:
        raise UnknownToolError(f"no handler for tool(s) {unknown}; handlers: {sorted(handlers)}")

    pool = ThreadPoolExecutor(max_workers=max(1, len(calls)), thread_name_prefix="tool")
    try:
        futures = {call.id: pool.submit(handlers[call.name], call.input) for call in calls}
        done, _ = wait(futures.values(), timeout=timeout_s)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    results: dict[str, str | ToolError] = {}
    for call_id, future in futures.items():
        if future not in done:
            results[call_id] = ToolError(f"timed out after {timeout_s:g}s")
            continue
        error = future.exception()
        if error is None:
            results[call_id] = future.result()
        elif isinstance(error, Exception):
            results[call_id] = ToolError(f"{type(error).__name__}: {error}")
        else:
            raise error  # BaseException (SystemExit, KeyboardInterrupt): never reported to the model
    return results
