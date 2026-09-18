"""One MCP server exposing the third primitive: two user-controlled prompts.

Intent: complete the tools/resources/prompts trilogy that
../mcp-resources-vs-tools/ left at two, from
../../research/2026-09-18-mcp-prompts.md. Prompts are *user*-controlled
message templates: the server authors them, the user picks one and fills in
its arguments, and the rendered messages enter the conversation as if typed.
Neither function below decides *when* it runs -- that is the whole point of
the primitive, and the reason nothing here is reachable by the model on its
own the way a tool is.

Two shapes, one each:

  - `review_code`  -- one required + one optional argument, returning a
                      plain `str`, which the SDK renders as a single
                      `user`-role message.
  - `debug_error`  -- one required argument, returning `list[Message]`, a
                      seeded conversation whose pre-filled `assistant` turn
                      steers the model's next reply.

Both bodies are pure: no I/O, no network, no globals, deterministic given
their arguments. The only side effect in this file is `mcp.run()` under the
`__main__` guard (the outermost, imperative layer).

Out of scope here (see README "Explicitly out of scope"): `Image`/`Audio`
prompt content, bare content-block returns, `list_changed` notifications,
`InputRequiredResult` multi-round-trip argument collection, argument
completions, and any wiring into a live host.

Do not add a top-level `print()` here or inside a prompt body: once
`mcp.run()` starts serving over stdio, stdout *is* the JSON-RPC wire (see
knowledge/mcp-python-sdk.md and examples/mcp-hello-world/server.py).
"""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import AssistantMessage, Message, UserMessage
from pydantic import Field

mcp = MCPServer("prompts-demo")


@mcp.prompt(title="Code review")
def review_code(
    code: Annotated[str, Field(description="The code snippet to review.")],
    language: Annotated[str, Field(description="Language name, e.g. 'rust'.")] = "python",
) -> str:
    """Ask for a review of a code snippet.

    A `str` return renders as exactly one `user`-role message. `code` has no
    default, so the SDK marks it required; `language` has one, so it is
    optional and falls back to "python".

    Failure modes: none reachable in this body. A `prompts/get` missing
    `code` is rejected by `Prompt.render()` *before* this function runs, and
    surfaces to the client as `MCPError(code=-32603)` -- the SDK's generic
    internal-error bucket, not the spec's `-32602`.
    """
    return f"Please review this {language} code:\n\n{code}"


@mcp.prompt(title="Debug an error")
def debug_error(
    error: Annotated[str, Field(description="The error text or traceback.")],
) -> list[Message]:
    """Seed a debugging conversation with a pre-filled assistant turn.

    A `list[Message]` return renders as a multi-turn conversation in list
    order. The `AssistantMessage` is not a reply the model produced -- it is
    a turn the prompt author put in the model's mouth, which is the
    documented way to steer whatever the model says next.

    Failure modes: none reachable in this body; a `prompts/get` missing
    `error` fails the same way `review_code` does, before this function runs.
    """
    return [
        UserMessage(f"I'm seeing this error:\n\n{error}"),
        AssistantMessage("I'll help debug that. What have you tried so far?"),
    ]


if __name__ == "__main__":
    # Defaults to stdio transport. Blocks, waiting on stdin -- silence here
    # (no banner, no crash) is expected and correct; see README.
    mcp.run()
