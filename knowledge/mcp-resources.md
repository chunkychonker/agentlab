# MCP resources vs tools

Both are ways an MCP server exposes something to a client, but the control
model, wire mechanics, and failure shape are all different. Verified
2026-08-09 against the spec, the installed `mcp` 2.0.0 SDK's own source, and
Claude Code's current docs — not against blog summaries (several turned up
in search that restate the spec correctly but weren't checked against code).

## Control model (the spec's own framing)

- **Tools are model-driven**: the model reads name/description/schema and
  decides when to call, with what arguments. See [[tool-use-loop]].
- **Resources are application-driven**: "host applications determin[e] how
  to incorporate context based on their needs" — a UI picker, search/filter,
  or automatic inclusion *by the host*, not the model deciding mid-turn.
  ([spec](https://modelcontextprotocol.io/docs/concepts/resources), protocol
  version `2026-07-28`, fetched 2026-08-09.)

## Wire mechanics that follow from that split

- Resources: `resources/list` (metadata only) and `resources/read` (content
  for one URI) are separate RPCs. A **resource template**
  (`resources/templates/list`) is the resource-side equivalent of a
  parameterized tool — a URI with RFC 6570 `{placeholders}` instead of named
  arguments.
- `@mcp.resource(uri)` in the Python SDK decides static-vs-template purely
  from whether `uri` contains a `{param}`. If it does, the URI's variable
  names must exactly equal the function's parameter names, checked (and
  raised as `ValueError`) **at decoration time**, not first call — same
  "fail at declaration, not at use" posture as [[agent-skills]] frontmatter
  validation.
- **Listing never executes a resource's function.** Read the SDK's own
  `MCPServer.list_resources()` (`mcp/server/mcpserver/server.py`): it maps
  registered `Resource` objects straight to metadata, no `.read()` call. Only
  `read_resource()` calls the function. Practical effect: you can register
  (and list) an arbitrarily large number of resources for free; only the
  ones actually opened cost anything. Tools have no equivalent free tier —
  every tool's *schema* is a token cost to every request that includes it
  (Claude Code specifically mitigates this with tool search / deferred
  loading, see below, but that's a host-side patch, not a protocol-level
  property tools have).

## The failure-shape trap

Resources and tools fail in *opposite* ways — code that tests both the same
way will get one wrong:

- **Tool failure** (established in [[mcp-python-sdk]]): schema violation or
  raised exception inside the tool body → `result.is_error=True` content,
  **does not raise** on the client. The model can read the message and
  retry.
- **Resource failure**: raising `ResourceNotFoundError` (or any
  `ResourceError`) from a resource/template function propagates through
  `read_resource()` and **raises** on the client as
  `mcp.shared.exceptions.MCPError` with `code=-32602` (`INVALID_PARAMS`) —
  same exception class [[mcp-python-sdk]] documents for tools calling
  `raise MCPError(...)` directly (the deliberate "no retry from the model
  fixes this" escape hatch), but here it's the *default*, unavoidable
  outcome of a resource not existing, not an opt-in choice.

## Claude Code's actual behavior (verified against current docs, 2026-08-09)

The spec's "application-driven" framing plays out at the host level as: the
**user** drives resource access via `@`-mention
(`@server:protocol://resource/path`, autocompletes alongside files;
"Resources are automatically fetched and included as attachments when
referenced" — [code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp),
referencing CLI versions through v2.1.221). Resource *templates* are
supported this way today — a 2025-07 closed issue asking for template
support predates the fix; don't trust GitHub issues on this topic without
checking their close date and re-verifying against current docs, host
behavior here changes over time.

Separately, Claude Code also synthesizes list/read tools
(`ListMcpResourcesTool` etc.) so the **model** can pull resource content
autonomously, not only on explicit user `@`-mention. This blurs the clean
user-driven/model-driven line the protocol draws — it's a Claude-Code
implementation choice layered on top, not something the protocol requires,
and a different host (or a future Claude Code version) is free to do this
differently. Don't generalize Claude Code's specific behavior to "how MCP
resources work" — check the spec for the protocol contract and the relevant
host's own docs for how it's actually surfaced.

## The practical rule

- Side effect, or the model must choose *if/when/with-what-arguments* → tool.
- Read-only reference content a user or host attaches on demand, cheap to
  enumerate in bulk → resource.
- A resource template ~= a tool's input schema in spirit (validate the shape
  once, many concrete instances) but is still fetched by URI via a different
  RPC than tools, with the opposite failure shape.

Research note: [2026-08-09-mcp-resources-vs-tools](../research/2026-08-09-mcp-resources-vs-tools.md).

Related: [[mcp-python-sdk]] (the tool-side SDK mechanics this note
contrasts against), [[agent-skills]] (the same "fail at declaration, not at
use" pattern for template/parameter mismatches), [[tool-use-loop]] (the
model-driven tool-calling loop resources are deliberately *not* part of).
