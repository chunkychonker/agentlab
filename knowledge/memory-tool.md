# Memory tool (`memory_20250818`)

Verified against the [official docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) on 2026-10-27.

- Client-side: Claude sends `tool_use` name `memory`; your app executes and returns `tool_result`. Declared as `{"type": "memory_20250818", "name": "memory"}`, no schema, no beta header (SDK helpers sit in the `beta` namespace anyway).
- Six commands: `view`, `create`, `str_replace`, `insert` (after line N, 0 = top), `delete`, `rename`. Return strings are recommendations; Claude reads whatever text you send. Errors can use `is_error: true`.
- The API injects a "view your memory directory first" protocol into the system prompt, so the first call is `view /memories`.
- Claude's tool text says `create` overwrites, so expect `create` on existing paths; erroring or overwriting are both valid.
- The handler is the security boundary: validate every path field in every command (traversal, `%2e%2e%2f`, symlinks), refuse delete/rename of the `/memories` root, cap file and view sizes.
- Complements [[context-editing]] and [[compaction]]: memory keeps what must survive clearing or summarization. Loop shape: [[tool-use-loop]]; error disposition: [[tool-failure-taxonomy]].
- See `research/2026-10-27-memory-tool-handler.md`.
