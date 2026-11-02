# MCP 2026-07-28: stateless core and Multi Round-Trip Requests

Researched 2026-11-02 (spec changelog + `mcp==2.3.0` wheel source; unrun). See
`research/2026-11-02-mcp-mrtr-elicitation.md`. Extends [[mcp-python-sdk]], [[mcp-streamable-http]].

- No `initialize` handshake, no `Mcp-Session-Id`: each request carries `_meta`
  `io.modelcontextprotocol/protocolVersion` + `.../clientCapabilities`; `server/discover` is mandatory.
- Server-to-client asks (elicitation, sampling, roots) become **MRTR**: server returns
  `resultType: "input_required"` + `inputRequests`; client retries the original call with `inputResponses`
  (+ `requestState`, which the server must treat as attacker-controlled). Missing `resultType` = `"complete"`.
- Roots/Sampling/Logging/HTTP+SSE/DCR deprecated (>=12 months); elicitation is not.
- HTTP requires `Mcp-Method`/`Mcp-Name` headers; `HeaderMismatch` = -32020; not-found = -32602.
- SDK 2.3.0: `Resolve`/`Elicit` resolvers on `MCPServer` tools, `Client(mode=...)`
  (`auto`/`legacy`/version pin), `elicitation_callback`, `input_required_max_rounds`,
  `allow_input_required=True` for manual driving, sealed `request_state`, default list caching via `ttlMs`.
