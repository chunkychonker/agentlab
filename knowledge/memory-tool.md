# Memory tool (`memory_20250818`)

Client-side tool: Claude requests file ops under `/memories`, your app executes
them and returns `tool_result`s. Verified against the
[docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) on 2026-10-28.

- Declaration is exactly `{"type": "memory_20250818", "name": "memory"}`; no schema, no beta header (SDK helpers still sit in `client.beta`).
- API auto-injects a "view your memory directory first / assume interruption" system prompt; first call of a run is `view /memories`; empty store is not an error.
- Six commands: `view`, `create`, `str_replace`, `insert` (after line N, 0 = top), `delete`, `rename`. Return strings are *recommended*, not enforced; errors go back with `is_error: true`.
- Gotcha: Claude's tool description says `create` "creates or overwrites", so `create` on an existing path happens; erroring or overwriting are both valid.
- Gotcha: the handler is the security boundary. Validate every path field in every command (`path`, `old_path`, `new_path`); canonicalize with `resolve()` + `relative_to()`; reject `..`, backslash and `%2e%2e%2f`; refuse `delete`/`rename` of `/memories` itself.
- SDK: `anthropic.tools.BetaLocalFilesystemMemoryTool` / `BetaAbstractMemoryTool` with `client.beta.messages.tool_runner`.
- Docs examples use model ids (`claude-opus-5-5`) absent from [[anthropic-models]]; unresolved.
- Complements [[context-editing]] and [[compaction]]; loop mechanics in [[tool-use-loop]], error disposition in [[tool-failure-taxonomy]].

Example: `research/2026-10-28-memory-tool-handler.md` (build proposal).
