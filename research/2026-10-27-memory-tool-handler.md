# Memory tool (`memory_20250818`): a path-safe client-side handler

Date: 2026-10-27. Mode: demo. The hn-search MCP server was unavailable this cycle, so no practitioner-reception data; docs only.

## Question
What does the Anthropic memory tool require of the application, and what is the smallest runnable example that proves a handler implements it safely (path traversal included) without an API key?

## Findings
Source: [Memory tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool), fetched 2026-10-27 (current).

- **Client-side.** Claude emits `tool_use` (name `memory`); the app executes it against storage it owns and returns a `tool_result`. Nothing is stored by Anthropic.
- **Declaration.** `tools=[{"type": "memory_20250818", "name": "memory"}]` is the whole config; `name` must be `memory`, no input schema. **No beta header** is needed for the tool itself. Available on all Claude 4 and later models. The SDK helper/tool-runner surfaces live in the `beta` namespace though.
- **Auto system prompt.** When the tool is present the API injects a "ALWAYS VIEW YOUR MEMORY DIRECTORY FIRST ... ASSUME INTERRUPTION" protocol, so the first call of any run is `view /memories`.
- **Six commands**, inputs:
  - `view` (`path`, optional `view_range [start,end]`, end `-1` = to EOF)
  - `create` (`path`, `file_text`)
  - `str_replace` (`path`, `old_str`, optional `new_str`)
  - `insert` (`path`, `insert_line`, `insert_text`; inserted *after* line N, 0 = top)
  - `delete` (`path`; recursive for dirs; the `/memories` root must be rejected)
  - `rename` (`old_path`, `new_path`; no overwrite; root rejected)
- **Return strings are recommendations**, Claude reads whatever text you return. Documented ones: directory view header `Here're the files and directories up to 2 levels deep in {path}, excluding hidden items and node_modules:` then `{size}\t{path}` lines; file view `Here's the content of {path} with line numbers:` then `%6d\t{line}`; `File created successfully at: {path}`; `The memory file has been edited.` (plus snippet); `The file {path} has been edited.`; `Successfully deleted {path}`; `Successfully renamed {old} to {new}`; errors `The path {path} does not exist. Please provide a valid path.`, `Error: File {path} already exists`, `Error: The destination {new_path} already exists`, str_replace duplicate: `No replacement was performed. Multiple occurrences of old_str ... in lines: ...`. Errors may be sent with `is_error: true`.
- **Quirk to plan for:** Claude's own tool description says `create` "creates or overwrites", so expect `create` on existing paths; the docs call both returning the error and overwriting valid.
- **Security is the app's job**: validate every path in every command (`/memories/../../secrets.env`), canonicalize and confirm containment (`pathlib.Path.resolve()` + `relative_to()`), reject `../`, `..\\`, and URL-encoded `%2e%2e%2f`; cap file size and `view` output; optionally strip secrets before writing.
- **SDK helpers:** Python `anthropic.tools.BetaAbstractMemoryTool` (subclass) and `BetaLocalFilesystemMemoryTool(base_path=...)` used with `client.beta.messages.tool_runner`. Not verified against the installed SDK version here (see open questions). Docs example model is `claude-opus-5-5`, which is not an id in `knowledge/anthropic-models.md` (`claude-opus-5`); treat as a docs inconsistency and use `claude-haiku-4-5` per repo convention.
- **Pairs with** [[context-editing]] (clear old tool results, memory keeps what matters) and [[compaction]] (memory preserves what must survive summarization). Not covered by any existing lab example.

## Build proposal
**Directory:** `examples/memory-tool-handler/` (checked: not in `ls examples/`, no open PR other than #62 which is pipeline work, no branch of that name).

**Intent.** Show that the memory tool is just a tool loop plus a storage handler, and that the handler, not the model, is the security boundary. Out of scope: the SDK `BetaAbstractMemoryTool` helper, image views, size/expiry policies, multi-user storage, combining with context editing.

**Layout (one concern per file, Python, pin `anthropic` as other examples do):**
- `paths.py`: pure `resolve_memory_path(raw: str) -> str` returning a normalized virtual path under `/memories`, raising `MemoryPathError` for anything else.
- `store.py`: `Store` Protocol (`read`, `write`, `delete`, `rename`, `list`, `exists`) plus `InMemoryStore` and `DirStore(root: Path)`. DirStore maps virtual paths to a real directory and re-checks containment with `resolve().relative_to(root)` as defense in depth.
- `commands.py`: pure `execute(store: Store, cmd: dict) -> Result` where `Result = (text: str, is_error: bool)`; dispatches the six commands, returns the documented strings, never raises for model-caused errors (returns `is_error=True`), rejects root `delete`/`rename`, rejects duplicate `str_replace` matches. Unknown command is an error result.
- `agent.py`: the hand-written loop (as in `examples/minimal-agent-loop/`), `MODEL = "claude-haiku-4-5"` constant, key read only in `main`, runs two "sessions" against the same `DirStore` so session 2 recalls a fact from session 1. Client injectable.
- `test_memory.py`: offline self-tests, run with `python test_memory.py`.

**Acceptance criteria (checkable, no key):**
1. `resolve_memory_path` accepts `/memories`, `/memories/a.txt`; rejects `/etc/passwd`, `/memories/../x`, `/memories/../../secrets.env`, `/memoriesX/a`, `%2e%2e%2f` variants, backslash traversal, empty string, and null bytes.
2. For both `InMemoryStore` and `DirStore`, a table of the six commands yields the exact documented success and error strings (header text, `%6d\t` numbering, 1-indexed, `insert_line` 0 = top, `view_range` incl. `-1`).
3. `str_replace` with 0 matches and with 2 matches both change nothing and return `is_error=True`.
4. `delete`/`rename` of `/memories` itself is refused; `delete` of a directory removes its children.
5. Traversal payloads in every path-bearing field (`path`, `old_path`, `new_path`) return an error result and leave a sentinel file outside the `DirStore` root byte-identical (symlink escape case included).
6. Scripted fake client replays: `view /memories` then `create` then end_turn; the loop sends a `tool_result` with the right `tool_use_id`, `is_error` set on failures, and the declared tool equals `{"type": "memory_20250818", "name": "memory"}` exactly.
7. A two-session fake-client test: file written in session 1 is returned by `view` in session 2 through a fresh `DirStore` on the same dir.

**Live part (optional, billed, one cheap run, state this in the README):** `agent.py` with `ANTHROPIC_API_KEY`, two sessions on haiku; paste the transcript, mark it `<!-- transcript-check: skip -->` per [[doc-transcript-drift]]. Record whether the API really needs no beta header and how Haiku handles `create` on an existing path.

**Failure domain.** Local directory only; a handler bug can only touch the DirStore root, which is why criterion 5 exists.

## Open questions
- Is `anthropic.tools.BetaLocalFilesystemMemoryTool` present in the version the lab pins? Builder: check `pip show anthropic` and the SDK source before mentioning it in the README.
- Docs use model id `claude-opus-5-5`; the lab's model note says `claude-opus-5`. Unresolved; avoid either, use Haiku.
- Does `claude-haiku-4-5` honor the injected memory protocol as reliably as larger models? Unverified until the live run.
- Practitioner reception (HN) not gathered this cycle.
