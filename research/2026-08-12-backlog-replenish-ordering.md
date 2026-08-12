# Backlog replenishment runs too late — fixing the ordering gap in `run.sh`

**Date:** 2026-08-12
**Backlog item:** "Fix the replenishment ordering gap in `run.sh`: top the backlog
up *before* the cycle loop when unclaimed items are fewer than the night's draw,
not only after it."

## Why this item and not the one above it

The topmost unclaimed item in `BACKLOG.md` was **server-side compaction
(`compact_20260112`)**. I skipped it: cycle 1 of tonight's own run
(`logs/run-2026-08-12_210001.log`) already researched *and* built it. The
maintain phase died on an `API Error: 529 Overloaded`, so no PR was opened, and
`run.sh`'s snapshot logic pushed the finished work to
`cycle/2026-08-12-unshipped-213702-1`:

```
 examples/server-side-compaction/{README,cost,main,policy,preflight,report,
   transcript,verify_fixture_schema,test_compaction}.*   (10 files)
 knowledge/compaction.md
 research/2026-08-12-server-side-compaction.md
 BACKLOG.md  ([ ] -> [building])
 16 files changed, 2755 insertions(+)
```

`gh pr list --state open` is empty, so the topic *looks* unclaimed — which is
exactly the trap my instructions warn about. Re-researching it would have
written the same `research/2026-08-12-server-side-compaction.md` path and asked
the builder to create an `examples/server-side-compaction/` that already exists
on that branch. That branch also carries a PASS review with three concrete
non-blocking doc-accuracy findings (see the tail of the run log).

**Action needed from a human:** open a PR from
`cycle/2026-08-12-unshipped-213702-1`. It is reviewed and passing; only the
529 stopped it. Nothing in this cycle's proposal touches those files, and the
branch's `BACKLOG.md` edit is on line 53 while mine is on line 58, so the two
merge cleanly.

## Question

Does moving backlog replenishment ahead of the cycle loop actually fix "the last
cycle of a drain-the-backlog night finds nothing to claim", and what is the
smallest change to `run.sh` that makes the fix testable rather than
just-look-at-it-and-trust-me?

## Findings

### 1. The replenishment phase has never once fired

`.pipeline/run.sh` lines 250–280 gate replenishment on
`UNCLAIMED < CYCLES`, and that block sits *after* the `for (( k=1; k<=CYCLES ))`
loop (line 229–246). Grepping every run log in `logs/` for `replenish` returns
exactly one execution record:

```
logs/run-2026-08-11_210001.log:236:
  --- phase: replenish skipped (2 unclaimed >= 2/night) ---
```

That is the *only* time the gate has ever been evaluated with a drained backlog
in play, and it skipped. The reason is the interesting part.

### 2. The failure mode is worse than "the last cycle finds nothing"

On 2026-08-11 (`logs/run-2026-08-11_210001.log`, line 129), cycle 2's researcher
reported:

> **Why this one:** the backlog had no unclaimed items. […] Rather than re-pick
> a done item, the researcher reconciled those markers to `[done #24]`, filed a
> new topic under a "Context & cost" section, and wrote the reasoning into the
> note.

It filed **two** new items by hand. By the time the post-loop gate ran, it
counted those two hand-filed items, saw `2 >= 2`, and skipped (line 236). So the
automated replenishment measured a backlog that a *researcher* had just
replenished out of band, mid-loop, as a side effect of its empty-backlog
fallback. The feature is not merely mistimed — it is masked by the very symptom
it exists to prevent, which is why it has no execution history to learn from.

That same researcher correctly identified the structural cause and filed it
rather than fixing it out of phase (line 131): "orchestration wasn't its phase
to touch." This cycle is that item.

### 3. The pre-loop gate is sufficient for the night, and idempotent by
construction

If `unclaimed >= CYCLES` when the loop starts, every cycle has something to
claim (each cycle consumes at most one item). So a single pre-loop reconcile is
enough; the post-loop call then becomes a usually-no-op that leaves the backlog
stocked for *tomorrow*. Both call sites want identical logic, which argues for
one function called twice rather than a copy-pasted block — the current code
already duplicates the `grep -c '^- \[ \]'` count at lines 257 and 265, and the
same literal regex is restated in prose in `PIPELINE.md` and in the researcher's
agent definition. That is coupling-by-meaning (Protocol §2).

Framing it as *reconcile toward a desired state* rather than *do a thing at a
point in time* is also what the global protocol asks for ("Declare desired
state; reconcile toward it… every operation is idempotent and safe to retry").
Calling the same reconciler twice a night is then trivially correct.

### 4. Hard environment constraint: bash 3.2, no bats, no shellcheck

Verified on this box:

```
$ which -a bash      -> /bin/bash        (only one)
$ /bin/bash --version -> GNU bash, version 3.2.57(1)-release (arm64-apple-darwin25)
$ which bats          -> not found
$ which shellcheck    -> not found
```

There is no Homebrew bash. `run.sh`'s shebang is `#!/bin/bash` and the
documented invocation is `bash .pipeline/run.sh`, both of which resolve to
3.2.57 here. **The builder must stay bash-3.2-compatible**: no `declare -A`,
no `mapfile`/`readarray`, no `${var,,}`/`${var^^}`, no `&>>`.

I verified the three bash-3.2 behaviours the proposal depends on rather than
trusting memory:

| behaviour | result |
|---|---|
| `grep -c PATTERN file` with zero matches under `set -uo pipefail` | prints `0`, exits 1 → the existing `\|\| true` is load-bearing |
| command-name indirection `runner() { local a="$1"; shift; "$a" "$@"; }` | works |
| indirection propagates the callee's exit code | `rc=3` preserved |

The last two are what make dependency injection possible in bash 3.2, which is
how the gate becomes testable offline.

### 5. Should we add `bats`?

[bats-core](https://github.com/bats-core/bats-core/releases) is alive — v1.14.0
shipped 2026-07-21, v1.13.0 on 2025-11-07 — but it is not installed here, so
adding it means a `brew install bats-core` step in a repo whose only shell test
today is plain bash. Practitioner signal is thin and mostly stale: the HN
discussion is
[Testing Bash with BATS (2019-02-21, 51 pts, 33 comments)](https://news.ycombinator.com/item?id=19229589)
and [Testing Dolt using BATS (2020-03-23)](https://news.ycombinator.com/item?id=22665624),
both >5 years old. More relevant is the project's own
[call for maintainers (2024-11-28)](https://github.com/orgs/bats-core/discussions/1023),
where the lead cited burnout ("My available free time has reduced and the chores
have worn my motivation down"); it drew partial help for packaging/CI but no
full maintainer handover in-thread. Releases have continued regardless.

**Recommendation: do not add it.** The cost (a new external dep + an install
step on a machine that runs unattended via launchd) buys assertion sugar for
about a dozen assertions. This repo already has the precedent to copy —
`eval/run_reviewer_eval.sh` is plain bash, `set -uo pipefail`, exit code as the
verdict. Match that.

### 6. Where the pre-loop call can physically go

Not obvious from the item text: the reconciler needs `run_phase`, which is
defined at line 121, and it must run after `MODE`/`CYCLES` are parsed (lines
92–119). So the only valid insertion point is **between `run_cycle`'s definition
(ends line 226) and `SHIPPED=0` (line 228)** — not next to the `cycles tonight:`
echo, which is above `run_phase`. Putting it at line 120 would fail with
`run_phase: command not found` only on the nights it actually fires, which is
the worst possible time to find out.

## Build proposal

### Layer 1 — Intent

`run.sh` replenishes `BACKLOG.md` only *after* the cycle loop, so on any night
where the loop drains the backlog the remaining cycles find nothing to claim and
fall back to filing their own work. Extract the replenishment decision into a
sourceable, unit-testable bash library, make it an idempotent reconciler, and
call it **both** before the loop and after it.

**Explicitly out of scope:**
- The *other* claim-loss bug found today (see Open questions) — different cause,
  separate item.
- Changing the replenishment prompt's wording or the `3 × CYCLES` target
  heuristic. Behaviour-preserving move only.
- Adding `bats`, `shellcheck`, or any dependency.
- Anything under `examples/`, `knowledge/`, or `research/`.
- Project mode. Replenishment stays demo-only, as today.

### Layer 2 — Behavioral spec

**Files** (none of these paths exist yet — `.pipeline/` currently holds only
`cycles`, `mode`, `run.sh`; verified against `main` and against the sole open
branch, which touches only `examples/server-side-compaction/`,
`knowledge/`, `research/`, and `BACKLOG.md`):

- `.pipeline/backlog.sh` — new. Pure-ish core, sourceable, zero side effects on
  source.
- `.pipeline/test_backlog.sh` — new. Offline self-test, no network, no
  `ANTHROPIC_API_KEY`, no git, no `claude`.
- `.pipeline/run.sh` — modified. Sources the lib; one `replenish_action`
  function; two call sites.
- `PIPELINE.md` — modified. Its "Backlog replenishment" section currently says
  "After the last cycle"; that sentence becomes false.

**Inputs:** `BACKLOG.md` (path), `CYCLES` (positive integer, already validated at
run.sh's boundary, line 115), and an injected *action* — a shell function name
invoked as `action <target>`.

**Outputs:** a possibly-modified `BACKLOG.md`, log lines, and exit codes.

**Invariants:**
1. Sourcing `.pipeline/backlog.sh` runs nothing and prints nothing.
2. `backlog_count_unclaimed` counts a line iff it begins with the literal
   `- [ ] ` at column 0 — the documented contract in `PIPELINE.md`. Indented
   continuation lines, `- [done #N]`, `- [researching]`, `- [building]` never
   count.
3. The reconciler invokes `action` **at most once** per call, and **zero** times
   when `unclaimed >= cycles`.
4. Calling the reconciler twice in a row with no intervening consumption is a
   no-op the second time (idempotence).
5. `run.sh` remains valid under bash 3.2 (`bash -n` clean, no bash-4 syntax).

**Failure modes (must be distinguishable, not collapsed into "failed"):**
- backlog path unreadable → reconciler exits 3, message on stderr.
- `action` returns non-zero (the `claude -p` phase died — exactly tonight's 529)
  → reconciler exits 2. The night continues; a replenish failure must never
  abort the run.
- `action` succeeded but the backlog is *still* short of `cycles` → exits 1.
  Today's code cannot detect this at all: it commits whatever the agent wrote
  without re-checking. This is the reconcile-and-verify half that's missing.
- Pre-loop commit/push of `BACKLOG.md` fails → log loudly, **do not abort**. The
  new items are on disk and cycle 1 can still claim them; the existing postflight
  `snapshot_dirty_main` rescues the edit. State this decision in a comment.

**Acceptance criteria** (each is one assertion in `.pipeline/test_backlog.sh`
unless marked):

1. `backlog_count_unclaimed` on a fixture mixing `- [ ]`, `- [done #4]`,
   `- [researching]`, `- [building]`, an indented `  - [ ]`, and indented
   continuation text returns only the count of column-0 `- [ ] ` lines.
2. Same, on a file with no matches → prints `0`, exits 0.
3. Same, on a nonexistent path → exits non-zero, prints nothing to **stdout**.
4. `backlog_replenish_target 2` → `6`; `backlog_replenish_target 1` → `3`
   (preserves today's `CYCLES * 3`).
5. `backlog_should_replenish 1 2` exits 0; `backlog_should_replenish 2 2` exits
   1; `backlog_should_replenish 0 1` exits 0.
6. **Gate fires when short:** fixture with 1 unclaimed, `cycles=2`, fake action
   that really appends 6 items to the fixture → action invoked exactly once,
   with argument `6`; reconciler exits 0.
7. **Idempotence:** immediately calling the reconciler again on that
   now-stocked fixture invokes the action **zero** times and exits 0.
8. **No-op when stocked:** fixture with 5 unclaimed, `cycles=2` → action
   invoked zero times, exit 0.
9. **Under-delivery detected:** fake action appends only 1 item (total still
   `< cycles`) → exit 1.
10. **Action failure surfaced:** fake action returns 1 → reconciler exits 2, and
    the run is not aborted.
11. Unreadable backlog path → exit 3.
12. *(structural)* In `.pipeline/run.sh`, a reconciler call site appears at a
    line number **less than** the `for (( k=1; k<=CYCLES` line, and another
    appears **greater than** it. This is the assertion that binds the fix to
    the file; it is grep/line-number based and therefore the most brittle
    criterion here — say so in a comment.
13. *(smoke)* `bash -n .pipeline/run.sh` exits 0, and `bash -n` on both new
    files exits 0.
14. The fake action in criteria 6–9 must **mutate the real fixture file** and
    record its invocations in a counter file the assertion reads back — not a
    variable the test sets and then asserts on. The reconciler must re-read the
    file to decide, so the test exercises real behaviour.

`bash .pipeline/test_backlog.sh` prints one line per case and exits 0 iff all
pass, matching `eval/run_reviewer_eval.sh`'s shape.

### Layer 3 — Interfaces

`.pipeline/backlog.sh` (stubs; no bodies):

```bash
# Number of unclaimed '- [ ] ' items in <path>, to stdout.
# Failure: <path> missing/unreadable -> message on stderr, exit 1, no stdout.
backlog_count_unclaimed()   # <path> -> stdout:int

# Desired unclaimed count for a night of <cycles>. Assumes a validated
# positive integer (run.sh validates at its boundary, line 115).
backlog_replenish_target()  # <cycles> -> stdout:int

# Exit 0 iff <unclaimed> is short of one night's draw of <cycles>.
backlog_should_replenish()  # <unclaimed> <cycles> -> exit 0|1

# Reconcile <path> toward ">= <cycles> unclaimed" by invoking
# `<action> <target>` at most once. Idempotent.
# Exit: 0 stocked | 1 action ran, still short | 2 action failed | 3 path unreadable
backlog_ensure_stocked()    # <path> <cycles> <action> -> exit 0|1|2|3
```

`.pipeline/run.sh` (the imperative shell — keeps `run_phase`, `$LOG`, git, and
the prompt, which is why it does not move into the lib):

```bash
. "$REPO/.pipeline/backlog.sh"   # hard-abort with a clear message if absent

replenish_action()  # <target> -> exit 0 on phase success
                    # run_phase "<existing prompt, parameterised on target>"
                    # then commit+push BACKLOG.md if changed

stock_backlog()     # <label> -> logs the backlog_ensure_stocked outcome;
                    # no-ops entirely unless MODE == demo
```

Call sites: `stock_backlog "pre-loop"` immediately before `SHIPPED=0`
(line 228 today — see Finding 6 for why it cannot go earlier), and
`stock_backlog "post-loop"` where the current block sits (replacing lines
256–280).

### Layer 4/5 — Notes for the builder

- Guard the lib against being executed directly if you like, but the
  `${BASH_SOURCE[0]}` vs `$0` idiom
  ([SysTutorials](https://www.systutorials.com/how-to-get-bash-scripts-own-path/),
  [DJ Adams, 2021-10-14](https://qmacro.org/blog/posts/2021/10/14/sourcing-vs-executing-in-bash/))
  is only needed if the lib would otherwise do something on execution. If it
  defines functions and nothing else, you don't need it — don't add ceremony.
- `set -u` is on in `run.sh`; the lib is sourced into that shell, so every
  parameter expansion in it must be safe under `-u`.
- Keep the existing `|| true` on `grep -c` (Finding 4) — removing it silently
  breaks the zero-match path.
- **Blast radius: the whole night.** A syntax error in `run.sh` kills the
  launchd job. Run `bash -n .pipeline/run.sh` and the new test before handing
  off, and do not reformat parts of `run.sh` you weren't asked to touch (§5).
- `.pipeline/test_backlog.sh` lives outside `examples/`, so the every-3rd-night
  health check will not pick it up. Add one line to `PIPELINE.md` telling a
  human to run it after editing `run.sh`, the same way
  `eval/run_reviewer_eval.sh` is documented as on-demand.

## Open questions

1. **The bug that actually bit tonight is a different one.** The backlog had 2
   unclaimed and `CYCLES=2`, so a pre-loop gate (`2 < 2` → false) would have
   skipped, and cycle 2 would still have collided with cycle 1's topic. The
   real cause: cycle 1 marked the item `[researching]`/`[building]` in a working
   tree that was never merged, so when the cycle failed, `snapshot_dirty_main`
   carried the claim off to a branch and `reset_to_clean_main` restored a
   `BACKLOG.md` that shows the item unclaimed. **A failed cycle silently
   releases its claim.** `PIPELINE.md` even asserts the opposite ("Sequential
   cycles see the previous cycle's claim because it lands on `main` as part of
   that cycle's merged PR") — true only when the cycle *ships*. Today the
   researcher's `gh pr list` check is the manual workaround, and it doesn't
   cover snapshot branches (there was no PR tonight). I did **not** fold this
   into the proposal — one intent per change — but it deserves its own backlog
   item, and it is arguably higher-value than the ordering fix.
2. Should the pre-loop reconcile run before or after `scrub_artifacts`/the
   preflight clean-main check? I put it after (immediately before the loop),
   which means it commits to a main the preflight already proved clean. I did
   not test the interaction with a *failed* pre-loop push feeding into cycle 1's
   maintainer; the spec above says log-and-continue, but that path is unproven.
3. Is `3 × CYCLES` still the right target now that replenishment may fire twice
   a night? I preserved it deliberately (behaviour-preserving move), but nobody
   has data — the phase has never run.
4. I could not verify how the replenishment prompt behaves in practice, for the
   same reason: zero executions on record. The first real firing of this gate
   will be the first evidence of whether the prompt produces usable items.

## Sources

- `~/agentlab/.pipeline/run.sh` (read in full, 2026-08-12) — lines 121–129
  (`run_phase`), 229–246 (cycle loop), 250–280 (replenishment block).
- `~/agentlab/logs/run-2026-08-11_210001.log` lines 129, 131, 236 — the drained
  backlog, the hand-filed items, the skipped gate.
- `~/agentlab/logs/run-2026-08-12_210001.log` — tonight's 529 on maintain and
  the snapshot to `cycle/2026-08-12-unshipped-213702-1`.
- `~/agentlab/eval/run_reviewer_eval.sh` — the plain-bash test precedent.
- [bats-core releases](https://github.com/bats-core/bats-core/releases) —
  v1.14.0 (2026-07-21), v1.13.0 (2025-11-07).
- [bats-core call for maintainers](https://github.com/orgs/bats-core/discussions/1023)
  — 2024-11-28 onward. **Older than a year; treat the maintainership status as
  possibly stale** — continued releases through 2026-07 suggest it was resolved.
- [Testing Bash with BATS](https://news.ycombinator.com/item?id=19229589) (HN,
  2019-02-21) and [Testing Dolt using BATS](https://news.ycombinator.com/item?id=22665624)
  (HN, 2020-03-23) — **both stale (>5 years)**; used only as evidence that
  practitioner discussion is thin, not for technical claims.
- [Sourcing vs executing in Bash](https://qmacro.org/blog/posts/2021/10/14/sourcing-vs-executing-in-bash/)
  (2021-10-14) and [$0 vs ${BASH_SOURCE[0]}](https://www.systutorials.com/how-to-get-bash-scripts-own-path/)
  — background for the source guard; **older than a year**, but this is stable
  bash semantics, not a moving API.
- Local verification (2026-08-12): `bash --version` → 3.2.57; `which bats`,
  `which shellcheck` → not found; the `grep -c`, command-indirection, and
  exit-code-propagation behaviours in Finding 4 were run, not recalled.
