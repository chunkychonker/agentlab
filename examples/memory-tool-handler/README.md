# Memory tool handler (`memory_20250818`), path-safe

The Anthropic memory tool is **client-side**. Claude sends `tool_use` blocks
named `memory` with one of six commands (`view`, `create`, `str_replace`,
`insert`, `delete`, `rename`). Your application runs them against storage it
owns and returns a `tool_result`. Anthropic stores nothing. The model picks the
paths, so the handler, not the model, is the security boundary. This example is
the handler plus a hand-written loop, and its tests prove that no path
(`/memories/../../secrets.env`, `%2e%2e%2f`, `..\`, a planted symlink) can get
outside the memory directory.

From the research note:
[`research/2026-10-27-memory-tool-handler.md`](../../research/2026-10-27-memory-tool-handler.md).
Background: [`knowledge/memory-tool.md`](../../knowledge/memory-tool.md).

## What's here

| File | One job |
|------|---------|
| `paths.py` | Parses a model-supplied string into a `MemoryPath`. That type cannot hold `..`, `.`, an empty segment, `/`, `\` or NUL, because construction is the validation. Pure. |
| `parse.py` | Turns a raw `tool_use.input` dict into one typed command (`View \| Create \| StrReplace \| Insert \| Delete \| Rename`). Pure. |
| `store.py` | The `Store` Protocol that the core depends on, plus `NodeKind`. |
| `commands.py` | `handle(store, raw, create_mode) -> Result`. Checks preconditions, runs one command, renders the documented response text. Does no I/O itself. |
| `memory_store.py` | `InMemoryStore`, a dict-backed `Store`. |
| `dir_store.py` | `DirStore(root)`, a `Store` on one real directory. It re-checks containment with `resolve().relative_to(root)`. |
| `agent.py` | The tool loop (`run_session`) and the live two-session demo. `MODEL` constant; the API key is read only in `main`. |
| `test_memory.py` | Offline self-test for acceptance criteria C1-C7 from the note. |

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/memory-tool-handler
python3 test_memory.py
```

Expected output:

```
ok  C1 valid /memories paths parse to the expected segments
ok  C1 24 invalid paths and 6 bad segments are rejected
ok  C2 all six commands return the documented strings on both stores
ok  C2 listings stop at 2 levels and hide dot-files and node_modules
ok  unknown commands and malformed parameters become is_error results
ok  C3 str_replace with 0 or 2+ matches is an error and changes nothing
ok  C4 /memories cannot be deleted or renamed; deleting a directory removes its children
ok  C5 70 traversal attempts across path/old_path/new_path leave the outside untouched
ok  C6 loop declares the tool exactly and pairs each tool_use with its tool_result
ok  C6 bad stop_reason and exhausted turns raise; a foreign tool name is an is_error result
ok  a storage OSError propagates instead of being reported to the model
ok  C7 a fact written in session 1 is read back by session 2 via a fresh DirStore

All 12 self-tests passed.
```

C5 is the one that matters most. It plants `secrets.env` next to the
`DirStore` root and a symlink `root/link -> ..` inside it, then tries ten
hostile paths in every path-bearing field of every command (`path`, `old_path`,
`new_path`). It asserts that every attempt is an `is_error` result, that no
result text leaks the sentinel, and that the outside directory is
byte-identical afterwards. During the build I checked the suite against three
mutants, one per defense: segment validation off, `DirStore` containment off,
percent-escape rejection off. Each mutant fails at least one test. With
containment off, `view /memories/link/secrets.env` returns the secret, so the
symlink check is load-bearing and not a formality.

## Run it live (billed; needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 agent.py            # fresh temp memory dir
python3 agent.py ./mem      # or a directory you keep between runs
```

The live demo runs two sessions. Session 1 asks Claude to remember a name, a
color and a units preference. Session 2 starts with an empty message history
and a new `DirStore` over the same directory, then asks for the preferences
back. The only thing the two sessions share is what is on disk. Without
`ANTHROPIC_API_KEY`, `agent.py` prints a one-line note and exits 0. This build
had no key, so **no live run has been recorded yet**. The next run with a key
should paste its transcript here under
`<!-- transcript-check: skip — billed live-API run -->`, and record two things:
whether the API really accepts the tool with no beta header, and whether Haiku
calls `create` on an existing path.

Model id is the constant `MODEL` in `agent.py` (default `claude-haiku-4-5`). See
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## Design decisions

- **Path checks are a type, not a habit.** Every command field that holds a
  path is parsed into a `MemoryPath` once, in `parse.py`. Stores accept only
  `MemoryPath`, so there is no "forgot to validate `new_path`" bug to write.
  Nothing gets normalized: `/memories/a/../b` is rejected, not collapsed to
  `/memories/b`.
- **Percent-escapes are rejected, not decoded.** If `unquote(raw) != raw`, the
  path is refused. That covers `%2e%2e%2f` and double-encoded `%252e`. A name
  like `50%.txt` (not a valid escape) is still allowed.
- **Who caused the error decides how it surfaces.** Model mistakes come back as
  `Result(is_error=True)` and leave the store unchanged: bad path, missing file,
  ambiguous `str_replace`, existing destination, root delete/rename. Storage
  failures (`OSError`) propagate as exceptions, because the model cannot fix a
  full disk.
- **`create` on an existing file is an explicit `CreateMode`.** The docs allow
  both behaviors, and Claude's own tool description says `create` "creates or
  overwrites". So `handle` takes `create_mode` with no default. The demo uses
  `OVERWRITE` so a turn isn't spent on an "already exists" error. The tests
  cover both modes, including the documented `Error: File {path} already
  exists`.
- **Directory sizes are the recursive sum of file bytes**, not `st_size`.
  Directory `st_size` varies by OS and filesystem. The byte sum makes
  `InMemoryStore` and `DirStore` produce identical listings, so one table test
  checks both. Directory entries get a trailing `/`, as in the SDK helper.
- **Atomic writes, owner-only modes.** `DirStore` writes to a hidden temp
  sibling and `os.replace`s it into place. Files are `0600` and directories
  `0700`, matching the SDK helper.

## Deviations from the proposal

- `resolve_memory_path` returns a `MemoryPath`, not a `str`. A string would
  need re-validating at every store. The type can't hold a bad value.
- The proposal put the `Store` Protocol and both implementations in one
  `store.py`, and parsing inside `commands.py`. Here they are split into
  `store.py`, `memory_store.py`, `dir_store.py` and `parse.py`, one concern per
  file.
- I recorded no live transcript (no key during the build; see above).

## The SDK helper (not used here)

The installed `anthropic==0.113.0` has
`anthropic.lib.tools._beta_builtin_memory_tool.BetaLocalFilesystemMemoryTool`.
Only `BetaAbstractMemoryTool` is re-exported from `anthropic.lib.tools`. In
that version its `_validate_path` checks `path.startswith("/memories")`, so
`/memoriesX/a` is accepted (it lands inside the root, so it is sloppy rather
than an escape). Its directory sizes come from `st_size`. This example
deliberately writes its own handler to keep the boundary visible. I have not
checked whether newer SDK versions (the repo pins `>=0.120.0`) changed any of
this.

## Known gaps and failure domain

- **Failure domain:** the one directory passed to `DirStore`. A handler bug can
  only touch that root, which is why C5 exists.
- **TOCTOU:** containment is checked, and then the operation runs. A local
  process that swaps a directory for a symlink between the two steps is not
  defended against. Use `openat`-style descriptors if that threat matters.
- **A planted escape symlink makes `view` of its parent fail loudly** (the
  whole listing becomes an `is_error`) instead of silently skipping the entry.
- **No size caps** on files or `view` output, and no secret scrubbing before
  writes. The research note recommends both, and the proposal put size policy
  out of scope.
- Image views, multi-user storage, expiry, and pairing with context editing are
  out of scope.
- Windows-specific names (`CON`, `a:b`) are not rejected. Only POSIX was tested.
