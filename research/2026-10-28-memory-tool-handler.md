# Memory tool (`memory_20250818`): a path-safe client-side handler

Date: 2026-10-28. Mode: demo. Re-picked from the stranded claim
`cycle/2026-10-27-unshipped-021126-1` (that cycle's note never reached main;
no open PR covers it, only #62 which is pipeline work). The hn-search MCP
server failed to connect (missing venv), so no practitioner-reception data.

## Question
What does the Anthropic memory tool require of the application, and what is the
smallest runnable example proving a handler implements it safely (path
traversal included) without an API key?

## Findings
Primary source: [Memory tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool), fetched 2026-10-28 (current). Also the Python SDK's
[examples/memory/basic.py](https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/memory/basic.py), fetched 2026-10-28.

- **Client-side.** Claude emits `tool_use` named `memory`; the app executes it against storage it owns and returns a `tool_result`. Nothing is stored by Anthropic. `/memories` is a *prefix your handler maps* onto real storage.
- **Declaration.** `tools=[{"type": "memory_20250818", "name": "memory"}]` is the entire config; `name` must be `memory`; no input schema. **No beta header** for the tool itself. Available on all Claude 4+ models. SDK helper/tool-runner surfaces live in the `beta` namespace anyway.
- **Auto system prompt.** With the tool present the API injects "ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE ... ASSUME INTERRUPTION", so the first call of a run is `view /memories`. First `view` of an empty store is NOT an error.
- **Six commands** and inputs:
  - `view`: `path`, optional `view_range [start, end]` (end `-1` = EOF)
  - `create`: `path`, `file_text`
  - `str_replace`: `path`, `old_str`, optional `new_str` (omitted = delete `old_str`)
  - `insert`: `path`, `insert_line`, `insert_text` (inserted AFTER line N; 0 = top)
  - `delete`: `path` (recursive on dirs; root `/memories` must be rejected)
  - `rename`: `old_path`, `new_path` (no overwrite; root rejected)
- **Return strings are recommendations**; Claude reads whatever text comes back. Documented:
  - dir view: `Here're the files and directories up to 2 levels deep in {path}, excluding hidden items and node_modules:` then `{size}\t{path}` lines (human sizes like `5.5K`; hidden items and `node_modules` excluded)
  - file view: `Here's the content of {path} with line numbers:` then `%6d\t{line}`, 1-indexed; >999,999 lines -> `File {path} exceeds maximum line limit of 999,999 lines.`
  - `File created successfully at: {path}`; `The memory file has been edited.` (+ numbered snippet); `The file {path} has been edited.`; `Successfully deleted {path}`; `Successfully renamed {old_path} to {new_path}`
  - errors: `The path {path} does not exist. Please provide a valid path.` (view), `Error: File {path} already exists` (create), `Error: The path {path} does not exist` (insert/delete/rename), ``Error: Invalid `insert_line` parameter: {n}. It should be within the range of lines of the file: [0, {n_lines}]``, `Error: The destination {new_path} already exists`, str_replace not found ``No replacement was performed, old_str `X` did not appear verbatim in {path}.``, duplicate ``No replacement was performed. Multiple occurrences of old_str `X` in lines: {nums}. Please ensure it is unique``. Directory passed to str_replace/insert is treated as "does not exist".
  - Errors may be sent with `is_error: true` on the `tool_result`.
- **Quirks.** Claude's own tool description says `create` "creates or overwrites", so expect `create` on existing paths; docs say returning the error is the reference behaviour and overwriting is valid. The description also says `view` shows images and truncates text beyond 16,000 characters, so expect ranged follow-up views.
- **Security is the app's job.** Validate every path in every command: must start with `/memories`, canonicalize and confirm containment (`pathlib.Path.resolve()` + `relative_to()`), reject `../`, `..\\`, URL-encoded `%2e%2e%2f`. Also: cap file size and `view` output, optionally strip secrets before writing, expire stale files.
- **SDK helpers (verified in the SDK's own example, 2026-10-28).** `from anthropic.tools import BetaLocalFilesystemMemoryTool`; `memory = BetaLocalFilesystemMemoryTool()`; passed as `tools=[memory]` to `client.beta.messages.tool_runner`. Docs also name `BetaAbstractMemoryTool` to subclass. The SDK example uses model id `claude-sonnet-5-5`, the docs examples use `claude-opus-5-5`; neither is in `knowledge/anthropic-models.md` (`claude-opus-5`, `claude-sonnet-5`). Unresolved docs/lab inconsistency, so the example should use `claude-haiku-4-5` from a constant and not assert either.
- **Pairs with** [[context-editing]] and [[compaction]] (docs: memory preserves what must survive clearing or summarization), and the multisession pattern (initializer session writes progress log + checklist; later sessions read first). No existing lab example persists anything across conversations.

## Build proposal
**Directory:** `examples/memory-tool-handler/` (checked 2026-10-28: absent from `ls examples/`; no open PR besides #62; no branch of that name besides the stranded `cycle/2026-10-27-unshipped-021126-1`, whose work is not on main).

**Intent.** Show the memory tool is a tool loop plus a storage handler, and that the handler, not the model, is the security boundary. Out of scope: the SDK `BetaAbstractMemoryTool`/`BetaLocalFilesystemMemoryTool` helpers (mention only), image views, expiry, multi-user storage, combining with context editing.

**Layout (one concern per file; pin `anthropic` like sibling examples; core imports no I/O):**
- `paths.py`: pure `resolve_memory_path(raw: str) -> str`; returns a normalized virtual path under `/memories` or raises `MemoryPathError`. Failure: any path outside the prefix, `..` segments, backslashes, percent-encoded traversal (decode then re-check), NUL bytes, empty string.
- `store.py`: `Store` Protocol (`read`, `write`, `delete`, `rename`, `list`, `exists`, `is_dir`) with `InMemoryStore` and `DirStore(root: Path)`. DirStore maps virtual paths to a real directory and re-checks `resolve().relative_to(root.resolve())` (defense in depth, covers symlink escape).
- `commands.py`: pure `execute(store: Store, cmd: dict) -> Result` where `Result = (text: str, is_error: bool)`. Dispatches the six commands with the documented strings; model-caused errors (bad path, no match, duplicate match, root delete/rename, unknown command) return `is_error=True`, never raise. `create` on an existing path: return the documented error (state the choice in the README).
- `agent.py`: hand-written loop as in `examples/minimal-agent-loop/`; `MODEL = "claude-haiku-4-5"`; key read only in `main`; client injectable; runs two "sessions" against the same `DirStore`.
- `test_memory.py`: offline, `python test_memory.py`, no key, no network.

**Acceptance criteria (checkable):**
1. `resolve_memory_path` accepts `/memories` and `/memories/a.txt`; rejects `/etc/passwd`, `/memories/../x`, `/memories/../../secrets.env`, `/memoriesX/a`, `/memories/%2e%2e%2fx`, `/memories/..\\x`, `""`, and a path containing `\x00`.
2. For both `InMemoryStore` and `DirStore`, a table of the six commands yields the exact documented success and error strings (header text, `%6d\t` numbering 1-indexed, `insert_line` 0 = top, `view_range` incl. `-1`, empty `/memories` view is a non-error).
3. `str_replace` with 0 matches and with 2 matches both leave the file byte-identical and return `is_error=True`; the duplicate message lists line numbers.
4. `delete`/`rename` of `/memories` itself is refused; `delete` of a directory removes its children.
5. Traversal payloads in every path-bearing field (`path`, `old_path`, `new_path`) return an error and leave a sentinel file outside the DirStore root byte-identical; includes a symlink inside the root pointing outside.
6. Scripted fake-client replay (`view /memories`, `create`, end_turn): the loop returns a `tool_result` with the matching `tool_use_id`, sets `is_error` on failures, and the declared tool equals `{"type": "memory_20250818", "name": "memory"}` exactly.
7. Two-session fake-client test: a file written in session 1 appears in the `view` result of session 2 using a fresh `DirStore` on the same directory.

**Live part (optional, billed, one cheap run; say so in the README):** `agent.py` with `ANTHROPIC_API_KEY`, two sessions on Haiku; paste the transcript marked `<!-- transcript-check: skip -->` per [[doc-transcript-drift]]. Record whether no beta header truly works and how Haiku handles `create` on an existing path.

**Failure domain.** A local directory only; a handler bug can touch at most the DirStore root, which is why criterion 5 exists.

## Open questions
- Whether `BetaLocalFilesystemMemoryTool` is present in the lab's pinned `anthropic` version (SDK example exists on main; builder should check `pip show anthropic` and the source before naming it in the README).
- Model-id inconsistency (`claude-opus-5-5` / `claude-sonnet-5-5` in docs vs `claude-opus-5` / `claude-sonnet-5` in the lab note). Not resolved; avoid.
- Does `claude-haiku-4-5` honor the injected memory protocol as reliably as larger models? Unverified until a live run.
- Practitioner reception (HN) not gathered; hn-search MCP was down.
