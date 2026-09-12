"""A minimal MCP server exposed over the Streamable HTTP transport.

Intent: the Streamable HTTP sibling of `examples/mcp-hello-world/` -- same
shape (one server, one small tool), different transport, and proven at the
*wire* level (session-ID header, HTTP status framing, DNS-rebinding host
check) rather than through the SDK's in-memory `Client`, which bypasses HTTP
entirely by construction.

`add` is pure -- no I/O, no globals, deterministic given `a` and `b` -- per
the repo's "core logic never imports I/O" rule. The only side effect in this
file is `mcp.run()` under the `__main__` guard, the outermost (imperative)
layer.

`app` is built at module scope so `test_server.py` can import and mount it
directly with `httpx2.ASGITransport`. Building the app does NOT bind a
socket, start a server, or enter the session manager's task group -- that
last one is a real gotcha; see `test_server.py` and the README.

Unlike the stdio server in `examples/mcp-hello-world/server.py`, stdout is
*not* the JSON-RPC wire here (the wire is HTTP), so printing from this file
would not corrupt the protocol. It still prints nothing, to keep the two
examples' shapes comparable.
"""

from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

# Operand bound, enforced by the JSON Schema rather than by an `if` in the
# tool body. Named once so the self-test asserts against the same constant
# instead of a duplicated literal (magic values are coupling-by-meaning).
#
# Schema-level is the right layer for a second reason that is specific to
# this SDK and verified by execution (see README, "Two error channels"): a
# schema violation's *detail* is sent to the client, whereas an ordinary
# exception raised inside a tool body is redacted to a generic
# "Error executing tool add" and its text stays in the server log.
MAX_ABS_OPERAND = 1_000_000

# Path the Streamable HTTP app serves MCP on. "/mcp" is the SDK's default; it
# is named here because the self-test and the manual-poke path must agree on
# it, and a mismatch would fail as a confusing 404 rather than as an obvious
# wrong-constant error.
MCP_PATH = "/mcp"

Operand = Annotated[int, Field(ge=-MAX_ABS_OPERAND, le=MAX_ABS_OPERAND)]

mcp = MCPServer("streamable-http-demo")


@mcp.tool()
def add(a: Operand, b: Operand) -> int:
    """Add two integers.

    Failure modes -- all surfaced to the caller as a normal, non-raising
    `CallToolResult` with `is_error=True`, never as a client-side raise (see
    `knowledge/mcp-python-sdk.md`, "Failure-mode mechanics", which documents
    this tool-error-vs-protocol-error split for the in-memory `Client`; the
    self-test confirms it holds identically over Streamable HTTP):
      - `a` or `b` missing, or not an integer: rejected by the SDK's schema
        validation before this body runs.
      - `|a|` or `|b|` greater than `MAX_ABS_OPERAND`: rejected by the
        `Field(ge=..., le=...)` bound above, so an out-of-range operand is
        an illegal state that never reaches this body at all.

    There is no in-body failure path: given two in-range ints, this cannot
    fail.
    """
    return a + b


# Module-level, and no run() call at import time: importable by the self-test.
app = mcp.streamable_http_app()


if __name__ == "__main__":
    # Manual-poke path only. The self-test never reaches this branch and
    # never binds a socket; this one DOES bind 127.0.0.1:8000 and blocks.
    # See the README's "Poke it manually" section.
    mcp.run(transport="streamable-http")
