"""One MCP server, one notes store, exposed through both primitives at once.

Intent: make the resources-vs-tools contrast from
../../research/2026-08-09-mcp-resources-vs-tools.md checkable in code, not
just described. The same in-memory notes store is exposed three ways:

  - `notes://index`      static resource  -- read-only, no arguments
  - `notes://{note_id}`  resource template -- read-only, one path parameter
  - `create_note(...)`   tool              -- the only way to add a note
                                               (a side effect with
                                               model-chosen content)

Out of scope (see README "Explicitly out of scope"): `list_changed`
notifications, subscriptions, wiring this server into a live host's
`@`-mention flow -- this file only proves the protocol/SDK-level contract,
driven by the SDK's own in-memory `Client` in test_server.py.

Do not add a top-level `print()` here or inside a resource/tool body: once
`mcp.run()` starts serving over stdio, stdout *is* the JSON-RPC wire (see
knowledge/mcp-python-sdk.md and examples/mcp-hello-world/server.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from pydantic import Field


@dataclass(frozen=True)
class Note:
    """One note. Immutable -- `NoteStore.add` produces a new `Note`, never
    mutates an existing one, so a `Note` handed out by `list()`/`get()` can
    never change under the caller."""

    id: str
    title: str
    body: str


class NoteStore:
    """In-memory notes, keyed by id. Pure data structure -- no I/O, no
    network, no global state outside the one module-level instance below.

    Not thread-safe: this example runs a single stdio server handling one
    request at a time (the SDK's stdio transport), so that's an accepted,
    stated limitation, not an oversight.
    """

    def __init__(self, seed: list[Note]) -> None:
        self._notes: dict[str, Note] = {note.id: note for note in seed}
        self._next_id = len(seed) + 1

    def list(self) -> list[Note]:
        """All notes, in insertion order."""
        return list(self._notes.values())

    def get(self, note_id: str) -> Note | None:
        """The note with `note_id`, or `None` if it doesn't exist."""
        return self._notes.get(note_id)

    def add(self, title: str, body: str) -> Note:
        """Create and store a new note with a fresh, monotonically
        increasing id. Always succeeds -- `title`/`body` are assumed
        already validated by the caller (the tool's `Field` constraints)."""
        note = Note(id=str(self._next_id), title=title, body=body)
        self._notes[note.id] = note
        self._next_id += 1
        return note


mcp = MCPServer("notes")

_store = NoteStore(seed=[Note(id="1", title="Welcome", body="This is the seeded first note.")])

# Proves the research note's "listing is free, reading executes" claim
# empirically: test_server.py asserts this counter is unchanged after
# list_resources() and incremented by exactly 1 after a read_resource() of
# this exact URI.
list_notes_index_calls = 0


@mcp.resource("notes://index")
def list_notes_index() -> str:
    """Return JSON: [{"id": ..., "title": ...}, ...] for every note.

    Never called by resources/list (which returns only registered
    resources' declared metadata -- name, uri, description -- without
    invoking any handler body). Only resources/read of this exact URI runs
    this function. No failure modes: an empty store returns `[]`, which is
    valid JSON, not an error.
    """
    global list_notes_index_calls
    list_notes_index_calls += 1
    return json.dumps([{"id": note.id, "title": note.title} for note in _store.list()])


@mcp.resource("notes://{note_id}")
def read_note(note_id: str) -> str:
    """Return the full note (id, title, body) as JSON for one `note_id`.

    Failure mode: unknown `note_id` -> raise `ResourceNotFoundError`. The
    SDK propagates this to the client as `MCPError` with
    `code == -32602` (invalid params) -- a raised exception, not a
    returned error string. This is the deliberate contrast with
    `create_note` below, whose validation failures never raise.
    """
    note = _store.get(note_id)
    if note is None:
        raise ResourceNotFoundError(f"no note with id {note_id!r}")
    return json.dumps({"id": note.id, "title": note.title, "body": note.body})


@mcp.tool()
def create_note(
    title: Annotated[str, Field(min_length=1, max_length=200)],
    body: Annotated[str, Field(max_length=10_000)],
) -> str:
    """Create a note, return its id.

    Failure modes (both surfaced as a non-raising `CallToolResult` with
    `is_error=True` -- the SDK validates the schema before this function
    body ever runs, same shape as examples/mcp-hello-world/count_words):
      - `title` empty or longer than 200 characters: rejected by the
        `Field` constraint, so the illegal state never reaches this body.
      - `body` longer than 10,000 characters: same shape.
    """
    return _store.add(title=title, body=body).id


if __name__ == "__main__":
    # Defaults to stdio transport. Blocks, waiting on stdin -- silence here
    # (no banner, no crash) is expected and correct; see README.
    mcp.run()
