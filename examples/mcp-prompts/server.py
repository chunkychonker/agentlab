"""A minimal MCP server exposing the *prompts* primitive: two prompt templates.

Intent: show the third MCP primitive at the protocol/SDK level. Tools are
model-driven and resources are application-driven; **prompts are
user-controlled** -- the server publishes named message templates, a person
picks one from a menu and fills in its arguments, and the rendered messages
drop into the conversation as if typed. `examples/mcp-resources-vs-tools/`
covers the other two; this file completes the trilogy.

Both prompt functions are pure: no I/O, no globals, no clock, deterministic
given their arguments, per the repo's "core logic never imports I/O" rule.
The only side effect in this file is `mcp.run()` under the `__main__` guard,
which is the outermost (imperative) layer.

Do not add a `print()` here or inside a prompt body: once `mcp.run()` starts
serving over stdio, stdout *is* the JSON-RPC wire. Use `logging` if a running
stdio server ever needs diagnostics.
"""

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.prompts.base import AssistantMessage, Message, UserMessage
from pydantic import Field

mcp = MCPServer("prompts-demo")


@mcp.prompt(title="Code review")
def review_code(
    code: Annotated[str, Field(description="The code to review.")],
    language: Annotated[str, Field(description="Language the code is written in.")] = "python",
) -> str:
    """Ask for a review of a piece of code.

    Returning a bare `str` is the SDK's shorthand for "one message, role
    `user`" -- the single most common prompt shape.

    `code` has no default, so the SDK publishes it as `required: true`;
    `language` has one, so it is published as `required: false` and this body
    never sees a missing value. Arguments are a flat list of named *strings*
    (a form a person fills in), not a JSON Schema payload a model constructs.

    Failure modes (raised to the client, never returned as a string):
      - `code` omitted: the SDK's `Prompt.render()` rejects the call before
        this body runs, and the client sees a raised `MCPError`. See the
        README's "Spec vs SDK" section for the code it actually carries --
        it is not the one the spec mandates.
    """
    return f"Please review this {language} code:\n\n{code}"


@mcp.prompt()
def debug_error(
    error_text: Annotated[str, Field(description="The error message / traceback.")],
) -> list[Message]:
    """Start a debugging conversation about an error.

    Returning a `list[Message]` seeds a multi-turn exchange instead of a
    single user turn. The final `AssistantMessage` is a **pre-filled
    assistant turn**: the spec's documented way to steer what the model says
    next, which a plain string prompt cannot express.

    No declared failure modes beyond the missing-required-argument case that
    the SDK enforces for `error_text` before this body runs.
    """
    return [
        UserMessage("I hit this error:"),
        UserMessage(error_text),
        AssistantMessage("Let's work through it. What did you expect to happen?"),
    ]


if __name__ == "__main__":
    # Defaults to stdio transport. Blocks, waiting on stdin -- silence here
    # (no banner, no crash) is the expected, correct behavior.
    mcp.run()
