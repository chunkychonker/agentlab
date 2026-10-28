# Memory tool handler (`memory_20250818`), path-safe

Claude's memory tool runs **on the client**. Claude emits `tool_use` blocks
named `memory` (`view`, `create`, `str_replace`, `insert`, `delete`, `rename`),
and your app runs each one against storage it owns, then returns a
`tool_result`. Anthropic stores nothing. `/memories` is just a prefix that your
handler maps onto real storage. That makes **the handler, not the model, the
security boundary**. This example is that handler plus a hand-written tool
loop, with an offline test suite that runs path-traversal payloads against
every path-bearing field.

From the research note:
[`research/2026-10-28-memory-tool-handler.md`](../../research/2026-10-28-memory-tool-handler.md).
The previous day's claim on this topic was stranded on an unshipped branch, and
this example re-builds it from that note.

## What's here

| File | One job |
|------|---------|
| `paths.py` | Turns a model-supplied string into a validated `MemoryPath`, or raises `MemoryPathError`. Pure. |
| `store.py` | The `Store` protocol, plus `InMemoryStore` and `DirStore(root)`. `DirStore` re-checks real-path containment, which catches symlink escapes. |
| `commands.py` | `execute(store, input) -> Result(text, is_error)`. Dispatches the six commands and returns the documented strings. No I/O of its own. |
| `agent.py` | The tool loop (`run_session`) and a live two-session demo (`main`). The API key is read only in `main`. |
| `test_memory.py` | Offline self-test, one test per acceptance criterion (AC1-AC7). |

### Declaring the tool

```python
tools=[{"type": "memory_20250818", "name": "memory"}]
```

That is the whole declaration: no input schema and no beta header. With the
tool present, the API adds a system prompt telling Claude to view its memory
directory first, so every run starts with `view /memories`. On an empty store,
that view returns a listing with only the root and is **not** an error.

## Run the self-test (no API key needed)

```bash
cd examples/memory-tool-handler
python3 test_memory.py
```

Expected output:

```
ok  AC1 resolve_memory_path accepts 4 safe paths, rejects 11 unsafe ones
ok  AC2 six commands return the documented strings (InMemoryStore)
ok  AC2 six commands return the documented strings (DirStore)
ok  AC3 str_replace with 0 and 2 matches errors and leaves the file intact (InMemoryStore)
ok  AC3 str_replace with 0 and 2 matches errors and leaves the file intact (DirStore)
ok  AC4 root delete/rename refused, directory delete is recursive (InMemoryStore)
ok  AC4 root delete/rename refused, directory delete is recursive (DirStore)
ok  AC5 63 traversal attempts (incl. symlinks) all errored; sentinel byte-identical
ok  AC6 loop answers each tool_use id, flags is_error, declares the tool exactly
ok  loop raises RuntimeError when max_turns is exceeded
ok  AC7 a file written in session 1 is listed and readable in session 2

All 8 self-tests passed.
```

AC5 is the security test. It puts a sentinel file **next to** the `DirStore`
root and plants two symlinks inside the root: one to the parent directory and
one to the sentinel. It then sends 9 hostile paths through all 7 path-bearing
slots (`path` for each command, plus `old_path` and `new_path` of `rename`). All
63 calls must return `is_error=True`, and afterwards the sentinel must be
byte-identical. If you delete the `relative_to` check in `DirStore._real`, AC5
fails: the symlink lets `view` read the sentinel.

## Run it live (needs a key; billed)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 agent.py            # uses a fresh temp dir
python3 agent.py ./mem      # or a directory you choose (re-runs accumulate)
```

This runs two short sessions on `claude-haiku-4-5` (the `MODEL` constant), each
with a new `DirStore` on the same directory. Session 1 states some preferences
and asks Claude to remember them. Session 2 asks about those preferences in a
new conversation. Every memory call and its result is printed. Without
`ANTHROPIC_API_KEY`, `agent.py` prints a skip notice and exits 0.

**Not yet run live.** No key was available when this was built, so these are
still unverified: (a) whether the plain `client.messages.create` call works with
no beta header, as the docs say; (b) whether Haiku reliably does the
view-first step; (c) how Haiku reacts to the `create`-on-existing error. A
transcript should be added (marked `<!-- transcript-check: skip -->`) after the
first live run.

## Design choices (where the docs leave room)

- **`create` on an existing path returns `Error: File ... already exists`.**
  The docs treat this as the reference behaviour, but Claude's own tool
  description says `create` "creates or overwrites", so expect it to try. The
  error tells Claude to use `str_replace`, or to `delete` and then `create`.
  Overwriting would also be valid. It is a one-line change in
  `commands._create`.
- **All percent-encoding is rejected.** The handler does not decode
  `%2e%2e%2f` and then re-check it. Any path that `urllib.parse.unquote` would
  change is refused, so the handler and the storage can never read the same
  path two different ways (double-encoding included). One side effect: a
  literal `%41` in a filename is not allowed.
- **There are two checks, at two layers.** `paths.py` rejects unsafe *text*
  without touching the filesystem. `DirStore` rejects paths whose *real*
  location falls outside the root, which only the filesystem can know
  (symlinks). A `MemoryPath` can only be built through validation, so stores
  never see raw strings.
- **Error split.** Model-caused problems (bad path, no match, duplicate match,
  root delete or rename, unknown command, a missing or mistyped field) come back
  as `tool_result` with `is_error: true`, and the loop continues. Host faults
  (`OSError` from the disk) are raised and stop the session, because the model
  can't fix them.
- **Directory sizes** in `view` listings are the sum of the file sizes inside,
  in UTF-8 bytes (`512B`, `5.5K`). This is not the filesystem's block size,
  because `InMemoryStore` has no blocks. Hidden entries (`.name`) and
  `node_modules` are left out of listings, but they can still be reached by
  explicit path.
- **Limits.** Any write is capped at `MAX_FILE_CHARS = 100_000`. A file view
  over 999,999 lines returns the documented error.
- **Lines** are `text.split("\n")`: `insert_line=0` inserts at the top, and
  `insert_line=N` inserts after line N.

## SDK helpers (not used here)

The Python SDK has `anthropic.tools.BetaAbstractMemoryTool` (subclass it with
your own storage) and `BetaLocalFilesystemMemoryTool`, both used with
`client.beta.messages.tool_runner`. Both are present in `anthropic` 0.113.0,
checked locally on 2026-10-28. This example writes the handler and loop by
hand on purpose, so the security boundary can be read and tested. If you use
the helpers, run AC5's payloads against them too.

## Scope and failure domain

Out of scope: image views, expiring stale files, stripping secrets before
writes, multi-user storage, and combining memory with context editing or
compaction (the docs' main use case; a natural follow-up). The failure domain
is one local directory: a handler bug can affect at most the `DirStore` root,
and AC5 exists to guard that edge. One known gap: `DirStore` checks paths, then
acts on them. A process that swaps a directory for a symlink between the check
and the operation (a TOCTOU race) is outside this threat model. The model is
the only writer here.
