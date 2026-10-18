# MCP tool annotations (2026-10-18)

## Question
What do `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint` mean exactly, how does the current Python SDK let a server declare them, and what can a client rely on?

## Findings

- **Spec (2025-11-25), `ToolAnnotations`**, read from the schema source (`schema/2025-11-25/schema.ts`, fetched 2026-10-18): all fields optional. `title?: string`; `readOnlyHint?: boolean` (default **false**); `destructiveHint?: boolean` (default **true**; "meaningful only when `readOnlyHint == false`"; false means additive updates only); `idempotentHint?: boolean` (default **false**; also meaningful only when not read-only); `openWorldHint?: boolean` (default **true**; web search is open, a memory tool is closed). Tool objects carry them in an optional `annotations` field. ([tools spec](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), fetched 2026-10-18)
- **Trust language:** the spec says clients "MUST consider tool annotations to be untrusted unless they come from trusted servers", and says there SHOULD be a human in the loop able to deny invocations. The SDK docs (`docs/servers/tools.md`) say the same: "hints, not security. Never rely on a client honouring them."
- **Consequence of the defaults:** an unannotated tool is, per spec, assumed to be a destructive, non-idempotent, open-world writer. The safe-by-omission direction is pessimistic. A server author who wants a tool treated as safe must say so; one who wants clarity should set all relevant hints explicitly rather than lean on defaults.
- **Python SDK 2.x** (latest release v2.3.0, 2026-10-02): `@mcp.tool(title=..., annotations=ToolAnnotations(...))` with `from mcp.types import ToolAnnotations`; snake_case Python fields `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint`, `title` (source: `src/mcp/server/mcpserver/server.py` `tool()` signature; official `docs_src/tools/tutorial005.py`).
- **Verified by running it** (venv, `mcp 2.3.0`, in-memory `Client`): `list_tools()` returns `t.annotations` as a `ToolAnnotations`; unset fields are `None` (the SDK does not fill in spec defaults); a tool with no `annotations=` has `t.annotations is None`. `model_dump(by_alias=True, exclude_none=True)` gives wire names, e.g. `{'readOnlyHint': True, 'openWorldHint': False}`. Passing only `destructive_hint=True, idempotent_hint=True` leaves `read_only_hint=None`.
- **Do clients honor them?** Evidence is weak and mostly from issue trackers (not official docs): an open Claude Code feature request (Aug 2026) says its permission engine treats all MCP calls uniformly and does not read these hints; plan-mode admission via `readOnlyHint` is also only proposed ([issue](https://claudeissues.com/issue/87452-feature-permission-engine-should-process-mcp-toolannotations-hints-readonlyhint), [plan mode](https://claudeissues.com/issue/90058-plan-mode-admit-an-mcp-tool-that-declares-readonlyhint-and-is-allow-listed)). Codex CLI does use them for approval (third-party blog, [Apr 2026](https://codex.danielvaughan.com/2026/04/12/mcp-tool-annotations-risk-vocabulary-codex-cli/)). Treat Claude Code behavior as unconfirmed; the README must not claim it.

## Build proposal

**Directory:** `examples/mcp-tool-annotations/` (checked: not in `examples/`, no open PRs, no matching branches).

**Intent.** Show how an MCP server declares behavioral hints and how a client reads them, and be plain that they are advice. Out of scope: a live Claude Code run, any enforcement/permission layer, resources/prompts, HTTP transport.

**Files**
- `server.py`: `MCPServer("notes-annotated")` with two tools over an in-memory dict store (module-level, no I/O):
  - `read_note(note_id: str) -> str`: `ToolAnnotations(title="Read a note", read_only_hint=True, open_world_hint=False)`.
  - `delete_note(note_id: str) -> str`: `ToolAnnotations(title="Delete a note", read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)` (deleting twice leaves the same end state).
  - Third tool `list_notes` is optional; skip it. Keep `__main__` guard and `mcp.run()`, same conventions as `examples/mcp-hello-world/server.py`.
- `policy.py`: pure function `needs_confirmation(a: ToolAnnotations | None) -> bool` implementing the spec defaults: `None` -> True; `read_only_hint is True` -> False; otherwise `destructive_hint is not False` -> True (default true); else False. This is the "what a polite client would do" demo, no I/O.
- `test_server.py` (plain async `test_*` + `main() -> int`, `ok  ...` lines, as in `mcp-hello-world/test_server.py`), via in-memory `Client(mcp)`:
  1. `list_tools` returns exactly `read_note` and `delete_note`.
  2. `read_note` annotations dumped with `by_alias=True, exclude_none=True` equal `{"title": "Read a note", "readOnlyHint": True, "openWorldHint": False}`.
  3. `delete_note` annotations equal `{"title": "Delete a note", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}`.
  4. Hints are not enforcement: calling `delete_note` through the client succeeds with no confirmation, and the note is actually gone (second `read_note` returns tool error `is_error is True`, or a defined "not found" per implementation: raise `ValueError`, which surfaces as `is_error=True`). Also `read_note` on an unknown id is `is_error=True`.
  5. `policy.needs_confirmation`: table test: `None` -> True, read-only -> False, destructive -> True, `ToolAnnotations(read_only_hint=False, destructive_hint=False)` -> False, `ToolAnnotations(read_only_hint=False)` (destructive unset) -> True (spec default).
  6. Unannotated tool sanity: register a throwaway `MCPServer` with one plain tool in the test and assert `annotations is None` (shows the SDK does not inject defaults).
- `requirements.txt`: `mcp>=2.0.0,<3` (verified on 2.3.0).
- `README.md`: what the four hints mean with their spec defaults; the "untrusted advice, not enforcement" statement quoted from the spec; the pessimistic-defaults point; the note that Claude Code's use of these hints is unconfirmed (cite the issues as unofficial); run command `python3 test_server.py`. No billed calls.

**It works when:** `python3 test_server.py` (in a venv with the requirements) prints all `ok` lines and exits 0, with no network and no key. Failure modes: wrong `ToolAnnotations` import path (use `mcp.types`), asserting on snake_case keys after a by-alias dump, and asserting that unset fields equal spec defaults (they are `None`).

## Open questions
- Whether current Claude Code reads `readOnlyHint` at all (plan mode, auto-allow, parallelism). Evidence is issue-tracker only; a later cycle could test it with a billed run like `examples/mcp-connect-claude-code/`.
- Whether `title` in annotations vs the top-level tool `title` takes precedence in clients (spec has both).

Sources: spec pages above; python-sdk repo at v2.3.0 (2026-10-02).
