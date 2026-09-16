# Cargo.toml support for the dependency-pin scanner

## Question

`examples/skill-script-execution/`'s `scan_dependencies.py` flags unpinned
`requirements.txt`/`package.json` entries and explicitly puts other manifest
formats out of scope ("Manifest formats beyond `requirements.txt`/
`package.json` (e.g. `Cargo.toml`, `go.mod`) — a natural follow-up, not this
cycle's scope"). Is Cargo.toml a good next format to add — what exactly counts
as "pinned" there, can it be parsed with the stdlib alone, and is the increment
small enough for one day?

## Why this topic (backlog state today, 2026-09-16)

`BACKLOG.md`'s Coding agents / Skills / MCP sections currently have **zero**
plain `- [ ] ` items — every entry is `[done #N]` or `[stranded cycle/...]`.
The only genuinely unclaimed `- [ ] ` items in the file (28 of them) are all
in the auto-filed `## Health-check findings` tail at the bottom, and I
checked each candidate at the top of that list before setting it aside:

- Lines 211/212/214 (log stops mid-phase with no verdict; a FAILed cycle
  leaves `main` dirty) are exactly the failure mode open PR **#36**, "gate
  each phase on the artifact it was supposed to produce," exists to fix —
  its own PR body quotes "exit code is not evidence that an artifact was
  produced," which is this bug class verbatim. Re-researching it would
  duplicate #36's ground.
- Several more (missing run logs, wrong-hour runs) are the direct subject of
  open PR **#39** ("pipeline: run at 02:00 CT, and refuse to start outside
  the 01:00–05:59 window").
- The remainder are single historical incidents (a one-night VPN drop, a
  session-usage-limit hit, a stale backlog line whose own text already says
  "close it on sight") — forensic log-reading, not a coding-agents/skills/MCP
  topic with anything to research, and not a runnable increment under
  `examples/`.

That matches the researcher's own fallback rule: *"If everything is claimed
or already covered by an open PR, pick the most valuable stale one and say so
in your note."* The four `[stranded cycle/...]` items (`strict-tool-schemas`,
`mcp-prompts`, `mcp-resources-claude-code`,
`context-editing-clear-thinking-preview`) are not a fit either — I checked
each branch directly (`git log --oneline -3 <branch>` / `git show --stat
<branch>`) and all four already carry a complete research note *and* a
built, tested increment sitting unshipped from a session-limit-killed cycle.
Re-researching any of them duplicates real finished work that needs a
human/maintainer to resurrect the branch, not a fresh research cycle.

**One more thing worth being explicit about:** today's automated run
(`logs/run-2026-09-16_020005.log`) already produced a cycle-1 research note,
`research/2026-09-16-prompt-caching-1h-ttl.md` (1-hour cache-TTL support for
`examples/prompt-caching-tool-loop/`), reasoning through the exact same
"nothing plain-unclaimed, so pick the best self-filed gap" logic above and
landing on the TTL gap as its answer. That cycle's build then failed review
(`logs/last-review.md`: README never updated to describe the new `--ttl`
flag/exit code, and the "prove it billed at 2x" acceptance criterion never
disclosed as unmet) and the whole cycle — note, code, and backlog edit — got
snapshotted to `cycle/2026-09-16-unshipped-022321-1` when `main` was reset;
cycle 2 of that same run then died mid-research (log ends mid the `cycle 2/2:
research` header, no completion line — itself another instance of the #36
bug class). None of that is visible from a clean `BACKLOG.md`/`git status` on
`main`, only from `git branch -a` plus reading the stranded branch and the
run log directly. Re-running the TTL research would duplicate a note that
already exists (just unshipped); that branch's own second candidate —
"Manifest formats beyond `requirements.txt`/`package.json`" from this exact
README — is what this note picks up instead, so today's two research passes
land on two different, non-overlapping increments rather than colliding.
**One directly reusable fact from that failed cycle's review, carried
forward into the build proposal below:** a README left undescribing new CLI
flags / exit codes / assertion counts is what turned an otherwise-solid
increment into a review FAIL. Worth guarding against here on purpose.

## Findings

### Cargo's version-requirement syntax (primary source)

[The Cargo Book, "Specifying Dependencies"](https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html)
(official Rust project docs; this page's semver syntax has been stable for
years — corroborated independently below, not flagged as possibly stale
despite the "prefer sources from the last several months" default, because
Cargo's version-requirement grammar is not fast-moving API surface the way
an LLM vendor's endpoints are):

- **Caret requirements are the default**, with or without the leading `^`:
  `log = "1.2.3"` means exactly `log = "^1.2.3"` — SemVer-compatible updates
  only (`^1.2.3 := >=1.2.3, <2.0.0`; `^0.2.3 := >=0.2.3, <0.3.0`). This is
  the sharpest gotcha for this scanner: **a bare version string is not an
  exact pin**, the same trap `package.json`'s bare-semver case already
  covers for npm, just via a different default operator.
- **Tilde requirements** (`~1.2.3 := >=1.2.3, <1.3.0`) restrict to
  patch-level updates only — still a range, not a pin.
- **Wildcard requirements** (`*`, `1.*`, `1.2.*`) — ranges.
- **Comparison requirements** (`>= 1.2.0`, `> 1`, `< 2`) and **multiple,
  comma-separated requirements** (`">= 1.2, < 1.5"`, all must hold) — ranges.
- **Exact requirement**: a **leading `=`** is the only form that pins —
  `package = "=1.2.3"`. This is the one and only "pinned" case for this
  scanner to recognize, matching `requirements.txt`'s `==` and
  `package.json`'s bare-exact-string cases in spirit (one operator that
  means "no other version is acceptable").
- **Table form**: any bare-string entry can instead be a table with a
  `version` key plus extras (`regex = { version = "1.10", features =
  ["unicode"] }`) — the pin/no-pin question is decided by the `version`
  string inside, same rule as above.
- **Three dependency sections**, exact npm parallel: `[dependencies]`,
  `[dev-dependencies]` (tests/examples/benches, not propagated to
  dependents), `[build-dependencies]` (build-script-only).

Independently confirmed (second source, not just the one doc page):
[rustfaq.org, "How to Specify Exact Dependency Versions in Rust"](https://www.rustfaq.org/en/how-to-specify-exact-dependency-versions-in-rust/)
states the same `=` rule and separately warns that pinning in `Cargo.toml`
alone "locks you to a single release" and "prevents you from getting
security fixes" — the same tradeoff this repo's own scanner already surfaces
implicitly for `requirements.txt`/`package.json` (it reports pin status, not
a recommendation to always pin).

### Practitioner reality check: manifest pin ≠ reproducible build

A 2026 discussion of dependency pinning generally
([oneuptime.com, "How to Pin Toolchains, Runtimes, and Lockfiles for
Deterministic Builds," 2026-07-28](https://oneuptime.com/blog/post/2026-07-28-pin-toolchains-runtimes-lockfiles/view))
makes the point this scanner's scope already implicitly assumes for the
other two formats and is worth stating outright for Cargo too: **`Cargo.toml`
pinning is not the same guarantee as `Cargo.lock`.** The lockfile — not the
manifest — is what actually fixes every transitive dependency's resolved
version for a real build. This scanner (all three formats, unchanged by this
increment) only ever reads the manifest, never the lockfile, and that scope
boundary should stay explicit in the README rather than let a reader assume
"scanner says pinned" means "build is reproducible."

### `tomllib`: stdlib, no new dependency, read-only

[Python 3 docs, `tomllib`](https://docs.python.org/3/library/tomllib.html) —
added in **Python 3.11**, stdlib, read-only (parsing only, matching this
script's needs exactly — it never writes a manifest). `tomllib.loads(text) ->
dict` raises `tomllib.TOMLDecodeError` on invalid TOML, the direct structural
analogue of `json.loads`/`json.JSONDecodeError` that `scan_package_json`
already uses — confirmed interactively against the exact nested-table and
git/path-dependency shapes this increment needs to handle:

```
>>> import tomllib
>>> tomllib.loads('[dependencies]\nserde = "1.0"\nregex = { version = "1.10" }\n'
...                'pathdep = { path = "../foo" }\ngitdep = { git = "..." }\n')
{'dependencies': {'serde': '1.0', 'regex': {'version': '1.10'},
                   'pathdep': {'path': '../foo'}, 'gitdep': {'git': '...'}}}
```

**Version floor, stated plainly:** this is the first thing in the repo that
needs Python ≥3.11 specifically (the rest of the example is 3.9-safe). The
build environment here runs 3.13.1, well above the floor, and no other
`agentlab` example declares a Python-version floor anywhere — this increment
should be the one place that states it (a comment at the top of
`scan_dependencies.py` plus a README line), not a silent assumption.

### Workspace-inherited dependencies: a real form with no meaningful "pin" to check

[The Cargo Book, "Workspaces"](https://doc.rust-lang.org/cargo/reference/workspaces.html) —
stable since Cargo 1.64 (2022): a member crate can write `serde = { workspace
= true }`, deferring the actual version requirement to a `[workspace.
dependencies]` table in a *different* file (the workspace root's
`Cargo.toml`). Resolving that would mean reading a second manifest outside
the one being scanned — a different feature, not this increment's scope.

## Build proposal

### Intent

Extend `examples/skill-script-execution/`'s `scan_dependencies.py` to also
scan `Cargo.toml`, closing the explicitly-named gap in its own README,
without touching the `requirements.txt`/`package.json` behavior, the CLI
contract, or the skill's trigger/permission mechanics. Out of scope: `go.mod`
(Go's module file has no floating-range syntax to flag — every entry names
one specific version or pseudo-version, so the "pinned vs. unpinned"
question this scanner asks doesn't apply there the way it does for pip/npm/
cargo); `[workspace.dependencies]` resolution; anything about `Cargo.lock`;
any change to `skill-anatomy`'s validator.

### Behavioral spec

**Inputs:** a directory tree that may contain zero or more `Cargo.toml`
files (via the same `os.walk` + `IGNORE_DIRS` traversal `scan_directory`
already does for the other two formats).

**Outputs:** the same `Finding(file, package, version_spec, reason)` shape
already used for `requirements.txt`/`package.json`, merged into the same
`{"scanned": [...], "findings": [...], "count": N}` result — no new top-level
field, no change to the JSON contract the skill's `SKILL.md` documents.

**Invariants:**
- A dependency entry (bare string or table) is **pinned** (not flagged) if
  and only if its version string's first non-whitespace character sequence
  is `=` (e.g. `"=1.2.3"`) — the literal Cargo "exact requirement" form.
- Every other version form — bare (`"1.2.3"`, implicit caret), explicit `^`,
  `~`, wildcard (`*`, `1.*`), a bare comparison (`>=1.2`), or a
  comma-separated multiple requirement (`">=1.2, <2.0"`) — is **not** an
  exact pin and is flagged.
- `[dependencies]`, `[dev-dependencies]`, and `[build-dependencies]` are all
  scanned; any other top-level table is ignored.
- A table-form entry with **no `version` key** (`{ path = "..." }`, `{ git =
  "..." }`, `{ workspace = true }`) expresses no semver requirement to
  evaluate — it is silently skipped, not flagged, not counted, and does not
  raise. Same precedent as `requirements.txt`'s comment/`-r` lines.
- Malformed `Cargo.toml` (invalid TOML) never aborts the scan of the rest of
  the tree — it produces exactly one `Finding` with a parse-error `reason`,
  mirroring `scan_package_json`'s existing `try/except
  json.JSONDecodeError` pattern with `tomllib.TOMLDecodeError` instead.
- No new third-party dependency (`tomllib` is stdlib on the ≥3.11 floor this
  increment introduces and states explicitly).

**Failure modes:**
- Invalid TOML → one `Finding`, scan continues (see above); never an
  unhandled exception, never a bare traceback on stdout (same contract the
  nonexistent-root-directory case already holds `main` to).
- A dependency table with an unexpected shape (e.g. `version` present but
  not a string) → treat defensively the same way `_npm_deps_unpinned` already
  does via `str(version)` — coerce and evaluate the string form rather than
  raising, since a `KeyError`/`TypeError` on one odd entry must not sink the
  whole scan.

**Acceptance criteria (concrete, checkable):**
1. `serde = "1.0"` (bare, implicit caret) → flagged.
2. `regex = { version = "^1.10", features = [...] }` (table, explicit caret)
   → flagged, `version_spec == "^1.10"`.
3. `once_cell = "=1.19.0"` → **not** flagged.
4. `tokio = { path = "../tokio-local" }` (no `version` key) → does not
   appear in `findings` at all; does not raise.
5. `libc = { git = "https://..." }` (no `version` key) → same as (4).
6. `tempfile = "~3.8"` under `[dev-dependencies]` → flagged.
7. `cc = "=1.0.83"` under `[build-dependencies]` → **not** flagged.
8. A malformed `Cargo.toml` next to a valid `requirements.txt` in the same
   tree → exactly one parse-error `Finding` for the `Cargo.toml`, and the
   `requirements.txt` findings are still present and correct (mirrors the
   existing `test_malformed_json_reported_not_crashed` case exactly, one
   format over).
9. A tree containing `requirements.txt` + `package.json` + `Cargo.toml`
   together → all three appear in `"scanned"`, sorted, and every format's
   findings are present in the merged `findings` list and `count`.
10. Every pre-existing test in `test_scan_dependencies.py` still passes
    unchanged — this increment is additive, not a rewrite.
11. `README.md`'s worked JSON example, the "Run the self-test" expected test
    count, and the "Explicitly out of scope" list are all updated in the
    *same* change to name `Cargo.toml` as now covered — **this is not
    optional polish.** Today's stranded `prompt-caching-1h-ttl` cycle failed
    review for exactly this gap (new flag/behavior shipped, README left
    describing the old contract). Run
    `examples/readme-transcript-check/check_transcript.py` (or the
    repo-wide `sweep.py`) against the touched README before calling this
    done, not after.
12. `SKILL.md`'s `description` frontmatter line ("Scans a project's
    `requirements.txt` and `package.json` for unpinned dependency versions")
    is updated to mention `Cargo.toml` — otherwise the trigger contract
    silently can't fire on a Rust-only ask like "are my Cargo dependencies
    pinned?" (see `knowledge/agent-skills.md`'s "trigger contract" section).

### Interfaces (stubs, no bodies — builder's to implement)

```python
# skills/scanning-dependencies/scripts/scan_dependencies.py

def scan_cargo_toml(path: Path, display_path: str) -> list[Finding]:
    """Parse one Cargo.toml and return findings for unpinned entries.

    Scans [dependencies], [dev-dependencies], [build-dependencies]. Only a
    version whose value (bare string, or a table's "version" key) begins
    with "=" counts as an exact pin; bare/^/~/wildcard/comparison forms are
    flagged. Table entries with no "version" key (path/git/workspace
    dependencies) are skipped, not flagged.

    Malformed TOML is caught here (not propagated) and reported as one
    Finding with a parse-error reason, mirroring scan_package_json.
    """
    ...

def _cargo_requirement_is_pinned(version_spec: str) -> bool:
    """True only for Cargo's exact-requirement form (a leading '=')."""
    ...
```

`scan_directory`'s `for filename in ("requirements.txt", "package.json"):`
loop gains `"Cargo.toml"` as a third case dispatching to `scan_cargo_toml`.
No change to `Finding`, `scan_requirements_txt`, `scan_package_json`,
`main`, or the CLI's exit codes (`0`/`2`).

### What "it works" means (self-test)

```bash
cd examples/skill-script-execution
python3 test_scan_dependencies.py
```

Extend the existing `unittest` file with a `ScanCargoTomlTests` class
covering acceptance criteria 1–8 above (one assertion per case, following
`ScanPackageJsonTests`'s existing style exactly — assert the *specific*
finding or its absence, not just "some finding appeared"), plus updates to
the existing `ScanDirectoryTests`/`CliTests` cases for the three-format tree
(criterion 9). All offline, stdlib `unittest`, no network, no API key — the
same self-test contract every other case in this file already holds. Add
`fixtures/Cargo.toml` (pinned/unpinned/path/git mix, matching the acceptance
criteria) alongside the existing `fixtures/requirements.txt` and
`fixtures/package.json`, for the manual "run the script directly" demo in
the README.

## Open questions

- Whether to give Cargo-specific `reason` strings per requirement kind
  (e.g. distinguishing "default caret (bare version)" from "explicit caret"
  from "tilde" from "wildcard/comparison") or reuse one shared "range or
  floating version, not an exact pin" string the way `package.json`'s
  findings already do. Either is consistent with an existing precedent in
  this same file; left to the builder's judgment rather than dictated here.
- Whether `IGNORE_DIRS` should gain `"target"` (Cargo's build-output
  directory). I did not find a reason it's needed for correctness — unlike
  `node_modules`, Cargo's build output doesn't vendor copies of manifest
  files that would produce false-positive scans — so I'm not proposing it;
  flagging only so the builder doesn't feel obligated to add it without one.
- I did not verify live whether Claude Code's skill-trigger classifier
  actually fires on a Rust-flavored phrasing ("are my Cargo dependencies
  pinned?") against the updated `description` — that's the same
  live-triggering caveat `knowledge/agent-skills.md` already carries for
  this whole mechanism, not a new gap this increment introduces.
