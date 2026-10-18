# MCP tool annotations

Verified 2026-10-18 (spec 2025-11-25 schema source, `mcp` 2.3.0 run in a venv). Note: [research/2026-10-18-mcp-tool-annotations](../research/2026-10-18-mcp-tool-annotations.md).

- Four optional boolean hints on a tool plus `title`: `readOnlyHint` (default false), `destructiveHint` (default **true**), `idempotentHint` (default false), `openWorldHint` (default **true**). Destructive and idempotent are meaningful only when not read-only. An unannotated tool is therefore assumed a destructive open-world writer.
- Spec: clients MUST treat annotations as untrusted unless the server is trusted. They are advice, not enforcement.
- Python SDK 2.x: `@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, ...))`, `from mcp.types import ToolAnnotations`. Client side `list_tools()` gives `t.annotations`; unset fields are `None` (no defaults injected), no annotations means `None`. Wire names are camelCase via `model_dump(by_alias=True, exclude_none=True)`.
- Whether Claude Code acts on them is unconfirmed (open issues say it does not use them for permissions); Codex CLI does.

Related: [[mcp-python-sdk]], [[mcp-resources]], [[claude-code-mcp-connection]].
