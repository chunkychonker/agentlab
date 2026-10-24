"""A minimal MCP server: one tool, stdio transport (the default).

Intent: demonstrate the smallest genuine MCP server against the current
(2.x) official Python SDK. `count_words` is pure -- no I/O, no globals,
deterministic given `text` -- per the repo's "core logic never imports I/O"
rule; the only side effect anywhere in this file is `mcp.run()` under the
`__main__` guard, which is the outermost (imperative) layer.

Do not add a top-level `print()` here or inside the tool body: once
`mcp.run()` starts serving over stdio, stdout *is* the JSON-RPC wire. The SDK
redirects flushed stdout writes to stderr only for output flushed *after*
serving begins -- an import-time print would still corrupt the stream. Use
`logging` if you ever need diagnostics from a running stdio server.
"""

from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

mcp = MCPServer("hello-world")


@mcp.tool()
def count_words(text: Annotated[str, Field(max_length=10_000)]) -> int:
    """Count whitespace-separated words in `text`.

    Failure modes (both surfaced to the caller as a non-raising
    `CallToolResult` with `is_error=True` -- the SDK validates the schema
    before this function body ever runs):
      - `text` longer than 10,000 characters: rejected by the `Field`
        constraint above, so the illegal state (an over-long string) is
        unrepresentable by the time this function runs at all.
      - `text` missing entirely: rejected as a required-argument violation,
        same non-raising shape.
    """
    return len(text.split())


if __name__ == "__main__":
    # Defaults to stdio transport. Blocks, waiting on stdin -- silence here
    # (no banner, no crash) is the expected, correct behavior; see README
    # for the manual way to confirm the process is actually alive.
    mcp.run()
