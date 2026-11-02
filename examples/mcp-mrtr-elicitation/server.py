"""Builds an MCP server whose destructive tools ask the human before acting.

The question is declared once, as a resolver returning `Elicit(...)`. The SDK
picks the wire shape from the negotiated protocol version:

  2026-07-28  tools/call returns InputRequiredResult (resultType "input_required");
              the client retries the same tools/call with inputResponses + requestState.
  2025-11-25  the server sends a standalone elicitation/create request mid-call.

The tool bodies are identical in both eras. The note store is injected and
mutated in place, so a caller (the tests, the demo) can observe what a call did.
Run over stdio with `python server.py`.
"""

from typing import Annotated

from mcp.server.mcpserver import (
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)
from mcp.server.mcpserver.exceptions import ToolError

from policy import Confirm, Decision, TypedCount, decide, decide_purge

SERVER_NAME = "mrtr-demo"

SEED_NOTES: dict[str, str] = {
    "n1": "buy milk",
    "n2": "call the dentist",
}


def build_server(store: dict[str, str]) -> MCPServer:
    """Return a server with `delete_note` and `purge_notes` acting on `store`.

    `store` is mutated in place by confirmed deletions; give each server its own
    dict. Cannot fail.
    """
    server = MCPServer(SERVER_NAME)

    # Resolver bodies may re-run on every MRTR round, so they must be idempotent:
    # these only read the store and build a question.

    def ask_delete(note_id: str) -> Elicit[Confirm]:
        """Ask whether to delete `note_id`.

        Raises:
            ToolError: the note does not exist (raised before any question is asked).
        """
        if note_id not in store:
            raise ToolError(f"no note with id {note_id!r}")
        return Elicit(f"Delete note {note_id!r} ({store[note_id]!r})?", Confirm)

    def ask_purge() -> Elicit[Confirm]:
        return Elicit(f"Purge all {len(store)} notes?", Confirm)

    def ask_count(first: Annotated[Confirm, Resolve(ask_purge)]) -> Elicit[TypedCount]:
        """Second question; depends on the first, so it can only be asked a round later.

        Raises:
            ToolError: the human accepted the first form but answered ok=False.
        """
        if not first.ok:
            raise ToolError("purge not confirmed")
        return Elicit("Type the number of notes that will be deleted.", TypedCount)

    @server.tool()
    def delete_note(
        note_id: str,
        confirm: Annotated[ElicitationResult[Confirm], Resolve(ask_delete)],
    ) -> str:
        """Delete one note after the human confirms. Keeps it on no/decline/cancel."""
        decision = decide(confirm)
        if decision is Decision.DELETE:
            del store[note_id]
            return f"deleted {note_id}"
        return f"{decision.value} {note_id}"

    @server.tool()
    def purge_notes(
        typed: Annotated[ElicitationResult[TypedCount], Resolve(ask_count)],
    ) -> str:
        """Delete every note after two confirmations (yes/no, then the typed count)."""
        expected = len(store)
        decision = decide_purge(typed, expected)
        if decision is Decision.DELETE:
            store.clear()
            return f"purged {expected}"
        return decision.value

    return server


mcp = build_server(dict(SEED_NOTES))

if __name__ == "__main__":
    mcp.run()
