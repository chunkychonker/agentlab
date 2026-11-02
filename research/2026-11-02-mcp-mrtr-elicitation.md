# MCP 2026-07-28 Multi Round-Trip Requests (MRTR): elicitation without a held-open stream

Date: 2026-11-02. Mode: demo. `BACKLOG.md` feature sections had no plain `[ ]` item
(only health findings), so the researcher filed this topic (as on 2026-10-27/29). Open
PRs: only #62 (pipeline), no overlap. Existing `examples/mcp-*` cover hello-world,
resources, prompts, tool annotations, streamable-http, connecting to Claude Code; none
touch the 2026-07-28 server-to-client model. hn-search MCP was down; WebSearch/WebFetch only.

## Question
How does a tool ask the user a question mid-call under MCP spec 2026-07-28 (no sessions,
no server-initiated requests), and can the Python SDK 2.3.0 demonstrate and test that
offline?

## Findings
- **Spec release** [MCP 2026-07-28 post](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
  (2026-07-28) and the official
  [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) (fetched
  2026-11-02, current). No later spec revision found.
- **Stateless core.** `initialize`/`initialized` and `Mcp-Session-Id` are removed. Each request
  carries `_meta` keys `io.modelcontextprotocol/protocolVersion` and
  `io.modelcontextprotocol/clientCapabilities` (clientInfo SHOULD). `server/discover` is a
  required server RPC. `ping`, `logging/setLevel`, SSE resumability (`Last-Event-ID`) removed.
- **MRTR (SEP-2322).** `elicitation/create`, `sampling/createMessage`, `roots/list` are no longer
  server-initiated requests. The server returns an `InputRequiredResult`
  (`resultType: "input_required"`, field `inputRequests`); the client retries the *original*
  request with `inputResponses`. Servers correlate across retries via `requestState`. Every
  result now carries `resultType` (`"complete"` default; clients treat a missing field from
  older servers as complete).
- **Headers.** Streamable HTTP POSTs must carry `Mcp-Method` (and `Mcp-Name` for name-bearing
  methods); mismatch -> `HeaderMismatch` error, renumbered `-32020` (spec reserves `-32020..-32099`).
  Resource-not-found changed `-32002` -> `-32602`.
- **Deprecated** (still work >= 12 months): Roots, Sampling, Logging, HTTP+SSE, DCR (use CIMD).
  Elicitation is the one server-to-client feature that is *not* deprecated, which is why it is
  the right demo.
- **Python SDK `mcp` 2.3.0** (PyPI, latest as of 2026-11-02; I downloaded the wheel and read
  source, did not run it). Verified in source:
  - `mcp.server.mcpserver.resolve`: `Annotated[T, Resolve(fn)]` fills a tool parameter by
    running resolver `fn` first; a resolver may return `Elicit(message, SchemaModel)`. On
    >= 2026-07-28 the framework batches requests into `InputRequiredResult` and resumes when
    the client retries; on <= 2025-11-25 it sends a standalone request mid-call. Resolver bodies
    may re-run every round; only asked outcomes ride `request_state` so each question is asked
    once. Consumer annotation `ElicitationResult[T]` gives accept/decline/cancel branching;
    plain `T` aborts on decline/cancel.
  - `mcp.client.Client` (dataclass): `mode="auto"` probes `server/discover`, falls back to
    initialize; `mode="legacy"` forces old handshake; `mode="2026-07-28"` pins modern without
    a probe. `elicitation_callback=` handles questions; `input_required_max_rounds` caps retries
    (`InputRequiredRoundsExceededError`); `client.session.call_tool(..., allow_input_required=True)`
    returns the raw `InputRequiredResult` for manual driving; `call_tool(input_responses=,
    request_state=)` seeds a resume. A `HEADER_MISMATCH` triggers one automatic re-list+retry.
  - `mcp.server.request_state`: `RequestStateBoundary` seals outgoing `requestState`
    (AES-GCM via HKDF, TTL, request/principal binding) and verifies echoes, since the spec treats
    the echo as attacker-controlled. Default key is process-local (rejected after restart).
  - `Client.cache` (`CacheConfig`) honors `ttlMs`/`cacheScope` on list results by default.
- Caveat: exact public names (`Elicit`, `Resolve`, `ElicitationResult` import paths) were
  read from module source, not docs; builder must confirm by import.

## Build proposal
**Directory:** `examples/mcp-mrtr-elicitation/` (not taken on main; no open PR).

**Intent.** Show and test, offline, the same human-confirmation tool under both protocol eras so the
wire-level difference is concrete. Out of scope: HTTP transport, auth, tasks extension, sampling/
roots, a real LLM, any network or API key.

**Shape.** `server.py`: `MCPServer("mrtr-demo")` with one tool `delete_note(note_id: str)`
whose `confirm` parameter is `Annotated[ElicitationResult[Confirm], Resolve(ask_confirm)]`
(`Confirm` = pydantic model with `ok: bool`); tool deletes from an in-memory dict only on accept.
Pure function `decide(note_id, outcome) -> str` holds the policy (core, no I/O). `run_demo.py`:
connects in-memory `Client` to the server and prints a short transcript. `test_mrtr.py`: stdlib
`unittest` + `anyio`/`asyncio.run`. `requirements.txt` pins `mcp==2.3.0`. README with transcript
(must match output; see `readme-transcript-check`).

**Acceptance criteria (self-test, all offline, no key):**
1. Modern (`mode="2026-07-28"`) + callback answering `ok=True`: `call_tool("delete_note")` succeeds,
   note removed, callback invoked exactly once.
2. Same with `ok=False` (decline) -> note retained, tool result reports "kept".
3. Manual drive: `client.session.call_tool(..., allow_input_required=True)` returns an
   `InputRequiredResult` (assert `resultType == "input_required"` and `inputRequests` non-empty)
   and the note is NOT yet deleted; retrying with the returned `request_state` + a built
   `input_responses` completes it.
4. Legacy (`mode="legacy"`) with same callback: same end state (callback once) — proves
   behavior parity, and the README states which wire shape each used.
5. `input_required_max_rounds=1` against a resolver that never settles (or a tool that always
   returns input_required) raises `InputRequiredRoundsExceededError`.
6. A tampered `request_state` string on resume raises/returns an error and does not delete.
7. `python -I -m unittest` passes; README transcript equals `run_demo.py` output.

**Failure modes to document:** process-local state key (resume after restart fails); resolver body
re-runs each round so it must be idempotent; decline with plain `T` annotation aborts the call.

## Open questions
- Exact import paths and whether `Elicit`/`Resolve`/`ElicitationResult` are exported from
  `mcp.server.mcpserver` (read only in `resolve.py`).
- How tests construct `input_responses` by hand (shape of `InputResponse` in `mcp_types`).
- Whether in-memory `Client` + `mode="legacy"` against `MCPServer` works without a transport; if
  not, drop criterion 4 and say so.
- Spec/SDK details of `Mcp-Method`/`Mcp-Name` are HTTP-only and therefore untested here.
