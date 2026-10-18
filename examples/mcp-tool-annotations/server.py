"""An MCP server whose two tools declare behavioral hints (`ToolAnnotations`).

Intent: show how a server tells a client "this tool only reads" vs "this tool
destroys data" using the MCP 2025-11-25 `ToolAnnotations` fields, against the
2.x official Python SDK. The hints are advice to the client -- nothing here
(or in the SDK) enforces them; see test_server.py, which proves a client can
call the destructive tool with no confirmation at all.

Out of scope: any permission/enforcement layer (policy.py is a pure demo of
what a polite client *could* do), resources, prompts, HTTP transport.

The note store is an in-memory dict injected into `build_server`, so each
server instance (and each test) owns its own state. No filesystem, no
network. As in examples/mcp-hello-world/server.py, do not print() anywhere in
this module: under stdio, stdout is the JSON-RPC wire.
"""

from collections.abc import Mapping

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

SERVER_NAME = "notes-annotated"

SEED_NOTES: Mapping[str, str] = {
    "groceries": "eggs, rice, coffee",
    "todo": "finish the annotations example",
}

READ_NOTE_ANNOTATIONS = ToolAnnotations(
    title="Read a note",
    read_only_hint=True,
    # A closed, local store: the tool never reaches outside this process.
    open_world_hint=False,
)

DELETE_NOTE_ANNOTATIONS = ToolAnnotations(
    title="Delete a note",
    read_only_hint=False,
    destructive_hint=True,
    # Deleting the same id twice leaves the same end state as deleting once.
    idempotent_hint=True,
    open_world_hint=False,
)


def build_server(notes: Mapping[str, str]) -> MCPServer:
    """Build a server over a private copy of `notes`.

    The copy means the caller's mapping is never mutated and two servers
    never share state. Cannot fail for any `Mapping[str, str]` input.
    """
    store: dict[str, str] = dict(notes)
    server = MCPServer(SERVER_NAME)

    @server.tool(annotations=READ_NOTE_ANNOTATIONS)
    def read_note(note_id: str) -> str:
        """Return the text of the note called `note_id`.

        Failure modes: raises ToolError if no such note exists; the SDK
        surfaces that to the client as a CallToolResult with is_error=True
        and this message in `content` (the client does not raise).
        """
        # The docstring above is sent to the model as the tool description, so
        # implementation notes live here instead: ToolError, not ValueError --
        # in mcp 2.3.0 any other exception is treated as a crash and the client
        # sees only "Error executing tool read_note", without this message.
        if note_id not in store:
            raise ToolError(f"no note with id {note_id!r}")
        return store[note_id]

    @server.tool(annotations=DELETE_NOTE_ANNOTATIONS)
    def delete_note(note_id: str) -> str:
        """Delete the note called `note_id`, permanently.

        Idempotent by design (and declared so via idempotent_hint): deleting
        an id that is already absent is NOT an error -- the requested end
        state ("no such note") already holds -- and the return value says
        which case happened. Cannot fail for a string `note_id`.
        """
        if store.pop(note_id, None) is None:
            return f"note {note_id!r} was already absent"
        return f"deleted note {note_id!r}"

    return server


mcp = build_server(SEED_NOTES)


if __name__ == "__main__":
    # Defaults to stdio transport; blocks silently waiting on stdin.
    mcp.run()
