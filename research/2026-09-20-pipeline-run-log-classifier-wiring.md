# 2026-09-20 — wire `classify_run_log` into the pipeline observer

## Question

The topmost unclaimed `[ ]` item in `BACKLOG.md` (line 235) is a single-night
health finding: `run-2026-09-01_134705` — "shipped 0/2. Cycle 1 clean VERDICT:
FAIL (no increment: researcher killed by the 600s ceiling...). Cycle 2 verdict
MISSING (same 600s researcher kill...)." Is this still worth building against
today, and if not, what unresolved gap does chasing it actually lead to?

This is the same move the 2026-09-17 note made against a different stale-looking
line (then line 211, now `[done #44]`): don't take the topmost item's wording
at face value, check what's already shipped, and if the specific incident is
moot, find the real gap it's a symptom of.

## Findings

**The proximate cause line 235 names is already fixed, on `main`, as of
tonight.** `git log -1 --format=%B 832134b` (merged 2026-09-20 01:51, i.e.
earlier in this same run window):

> `fix(pipeline): wait for background tasks instead of killing them at 600s`
> ... "Five nights lost whole cycles to this (2026-08-29, **09-01 with three
> phases**, 09-13 and 09-14 with both cycles each)..." Export
> `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` before `run_phase` is defined.

That commit's own message names 2026-09-01 — line 235's exact subject — as one
of the nights it fixes, and `grep -n CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS
.pipeline/run.sh` confirms the export is live at line 58, ahead of
`run_phase`'s definition, with `test_gates.sh` cases R15/R16 pinning both the
export and its position. Building anything against the literal 600s-ceiling
cause of line 235 (or its duplicates at lines 236, 251, 252, 254 — all citing
the identical "600s ceiling" root cause across different nights) would be
re-researching ground a same-day merge already covered — the same principle
the researcher's own open-PR check exists for, just via "already on `main`"
instead of "already an open PR". (Confirmed no open PR duplicates this either:
`gh pr list --state open` returns empty right now.)

**What line 235 leads to instead: the pipeline observer still classifies every
run log by eye, and a classifier built specifically to stop that
(`.pipeline/run_log.sh`, PR #44, merged 2026-09-17) sits on disk unused.**
Three direct checks:

1. `grep -n "for lib in" .pipeline/run.sh` — the sourced-lib loop is `backlog
   verdict health pipeline_health preflight postcondition schedule
   network_retry`. Eight libraries, no `run_log`. `run.sh` never sources it.
2. `grep -n "run_log" .pipeline/test_gates.sh` — the only hit is a *comment*
   ("run_log.sh and the pipeline observer read a run's fate off the last
   content line..."), not a sourced lib or a call. `test_run_log.sh` exists
   and passes standalone (`bash .pipeline/test_run_log.sh`) but nothing wires
   it into the aggregate suite either.
3. `.claude/agents/agentlab-pipeline-observer.md`, section "1. Run outcomes",
   still reads (verbatim, unchanged since before PR #44):
   > "ABORTED — never reached `=== done`. State the last thing it did and
   > **the abort message (the script's abort lines end with `Aborting.`)**."

   `knowledge/pipeline-run-log-shapes.md` — written by the 2026-09-17 research
   cycle specifically to record this — already proves that claim false for the
   silent-death shape (`run-2026-08-29_114701.log`: last content is a
   `--- phase: ... ---` header with nothing after it, no `Aborting.` anywhere
   in the file). PR #44's own "explicitly out of scope" list named exactly
   this wiring as deferred, pending PR #36 (phase postconditions) landing and
   its `run_phase` signature churn settling:
   > "Wiring `classify_run_log` into `run.sh`'s pipeline-observer phase call...
   > a natural next increment once #36 lands..."

   `git merge-base --is-ancestor <postcondition-commit> main` confirms #36 is
   now fully on `main` (`postcondition.sh` exists, `run_phase` already takes
   its 4-arg signature throughout `run.sh`). The blocker PR #44 named is gone;
   the wiring it deferred was never picked up.

**This gap is the direct, current cost driver behind most of the rest of the
`## Health-check findings` section.** Lines 243–260 are eighteen separate
lines, almost all "phase X exited non-zero on session limit" or "pipeline
observer exited non-zero on session limit" — e.g. line 259: "phase 'pipeline
observer' exited non-zero — 3x ... (all session-limit)". The observer's own
job description says why it's expensive: it "reads `logs/run-*.log` newer
than its last cutoff, in full, by eye, every run" (2026-09-17 note, confirmed
still true by the agent-doc read above). A cheap, deterministic
pre-classification of *structure* before the model has to reason about
anything is exactly what removes tokens from the one phase that keeps
blowing its own budget doing this by hand — not a nicety, the direct lever on
the recurring finding.

**Nothing currently parses a `run-*.log` filename's embedded date against a
cutoff, anywhere in the codebase.** `run.sh`'s own `PIPE_CUTOFF` computation
(lines ~980–992) extracts a date from `lab-pipeline-*.log` filenames (for the
7-day cadence gate) but the *window of run logs the observer should read* is
never computed in code — the phase prompt just hands the subagent a cutoff
string ("Window: examine `logs/run-*.log` dated on or after `$PIPE_CUTOFF`...")
and the model does the filename parsing and the file-opening itself, on every
run. `grep -n "run-\[0-9\]"` and `grep -n "run-\*\.log"` across `run.sh`/
`health.sh` turn up only that one prompt string — confirmed by direct search,
not inference.

**Practitioner angle (HN):** searched `mcp__hn-search__search_stories` for
"claude code session limit" and "claude code rate limit background task" —
no substantive discussion threads turned up (mostly unrelated general-AI
threads with those words in a comment). This particular problem (a private
pipeline hitting Claude Code's own session-usage window while running long
headless phases) is infra-specific to this repo, not a broadly-discussed
public pattern; nothing external to cite here beyond this repo's own logs.

**Not fixable by this increment, and I'm not proposing to try:** the session
limit itself (an Anthropic account-level usage window, per `PIPELINE.md`'s
own framing: "a job this long sharing a rolling 5-hour usage window with
interactive daytime work"). Cutting the observer's own token spend on the
*mechanical* part of its job reduces how often it hits that ceiling but cannot
eliminate it, and diagnosing *why* a phase died (session limit vs. something
else) is explicitly out of scope for `run_log.sh` by design — see Build
proposal.

## Build proposal

### Intent

Give the pipeline-observer phase a precomputed, mechanical OK/PARTIAL/ABORTED
classification for every run log in its window, so it no longer re-derives
run outcomes by reading each file's raw text — cutting the token cost of the
one recurring, mechanical part of its job, and fixing the now-documented-false
"abort lines end with `Aborting.`" assumption in its own instructions.

**Explicitly out of scope** (matching `run_log.sh`'s own stated boundary):
diagnosing *why* a phase died (session limit vs. anything else) — that stays
a human/model judgment call reported as a count, not a classifier's job, per
`run_log.sh`'s own docstring ("This is STRUCTURE, not CAUSE... a confident
wrong diagnosis is worse than a count") and the observer's own stated posture.
No change to `test_gates.sh`'s libraries beyond adding `run_log` to the
sourced list. No change to `health.sh`/`pipeline_health.sh` (they parse the
*agent-written* markdown report, untouched by this). No retry/backoff logic
for session limits. No change to the six existing `## <heading>` sections'
report *shape* the observer writes to `logs/last-pipeline-health.md` — only
how it derives section 1's content changes.

### Behavioral spec

**Inputs:** the contents of `logs/` at the time `run.sh` reaches the
pipeline-observer phase; the already-computed `PIPE_CUTOFF` (a `YYYY-MM-DD`
date string, or the literal sentinel `ALL`); `TS`, this run's own timestamp
(used to exclude `logs/run-$TS.log`, still being written).

**Outputs:** a text block of `<basename>|<STATE>|<reason>` lines (one per run
log in the window, `STATE` ∈ `{OK, PARTIAL, ABORTED}`), spliced into the
pipeline-observer phase's prompt string, and a corrected "Run outcomes"
section in `.claude/agents/agentlab-pipeline-observer.md` that consumes it.

**Invariants:**
- The manifest covers exactly the run logs the *current* phase prompt already
  promises to cover (same cutoff semantics: `ALL` means every `run-*.log`
  present; otherwise, on-or-after `$PIPE_CUTOFF`'s date, inclusive — matching
  the existing prompt wording "dated on or after `$PIPE_CUTOFF`"), always
  excluding `logs/run-$TS.log`.
- Every line in the manifest is exactly what `classify_run_log` would print
  for that file, unmodified — no new classification logic, no string-matching
  against `run.sh`'s wording (same coupling-avoidance rule `run_log.sh`
  already documents for itself).
- An empty window (no matching logs) is a valid, non-error outcome: an empty
  manifest, not a failure.

**Failure modes:**
- `logs/` missing or unreadable: the manifest-building step reports this
  distinctly rather than silently returning an empty (and therefore
  indistinguishable from "checked, found nothing") result.
- A `run-*.log` file that fails `classify_run_log`'s own readability check
  (return 3: missing/unreadable/directory) is skipped from the manifest with
  its own line noting the path was unreadable, rather than silently dropped —
  a manifest that's silently short is worse than the by-eye status quo.

**Acceptance criteria ("it works"):**
1. `bash .pipeline/test_run_log.sh` still exits 0, all existing cases (C1–C10,
   E1–E9, INV1–INV2, R1) unchanged and passing, plus new cases (see below) for
   the two new functions.
2. `run_log` is added to `run.sh`'s sourced-lib list (now nine:
   `backlog verdict health pipeline_health preflight postcondition schedule
   network_retry run_log`); sourcing it prints nothing (already covered by
   `test_run_log.sh`'s C9-equivalent silent-sourcing case, and by the same
   "sourcing every lib is silent" convention `test_gates.sh` already asserts
   for the other eight).
3. By hand, against this machine's real (git-ignored) `logs/` directory:
   `run_log_manifest logs 2026-08-29 logs/run-2026-09-20_020002.log` (or
   whatever today's own log is named) produces one `<basename>|ABORTED|phase
   'cycle 2/2: review' (model: opus) started, nothing followed` line for
   `run-2026-08-29_114701.log` — the exact silent-death incident this whole
   chain of research notes traces back to — proving the wiring surfaces
   exactly the shape the agent doc's current instructions get wrong.
4. `.claude/agents/agentlab-pipeline-observer.md` section "1. Run outcomes" no
   longer states "the script's abort lines end with `Aborting.`" as a
   universal fact; it states the two-sub-shape taxonomy (with-message vs.
   silent) and says to use the precomputed manifest line for OK/PARTIAL/
   ABORTED, falling back to opening the file directly only for sections 2–4
   (cause text, phase-failure specifics, claim-state drift), which need more
   than the terse reason string carries.
5. `bash -n .pipeline/run.sh` stays clean, and the phase prompt string still
   contains the existing "must not modify anything under examples/, ..." hard
   rules unchanged — this increment only adds information to the prompt, it
   does not loosen anything the observer is forbidden to touch.

### Interfaces (stubs, no bodies — builder's job is layer 4)

Two new public functions added to `.pipeline/run_log.sh`, alongside the
existing `classify_run_log`. Both are read-only (list a directory, read file
contents via `classify_run_log`) — the same "impure enough to touch disk, pure
enough to unit-test with real temp-dir fixtures" shape `postcondition.sh`'s
`artifact_freshness` already establishes as this pipeline's convention for
this class of function.

```bash
# run_log_in_window <basename> <cutoff>
#
# Pure string/date logic, no I/O. <basename> is a run log's filename (a
# leading path, if any, is ignored — only the trailing
# "run-YYYY-MM-DD_HHMMSS.log" is read). <cutoff> is either the literal
# sentinel "ALL" or a "YYYY-MM-DD" date. The window is INCLUSIVE of <cutoff>'s
# date — matches run.sh's existing PIPE_CUTOFF comment ("dated on or after
# $PIPE_CUTOFF") and its "on or after" prompt wording, not a boundary this
# function invents.
#
# ISO 8601 dates compare correctly as strings; this is the same trick
# run.sh's own PIPE_LAST_DATE/LAST_DATE extraction already relies on inline
# in two places (health and pipeline-observer cadence) — pinning it here in
# one tested function is strictly narrowing existing duplication, not adding
# a new technique.
#
# Prints nothing. Return code is the answer:
#   0  in the window (cutoff is ALL, or the embedded date >= cutoff)
#   1  before the window (embedded date < cutoff)
#   2  <basename> does not match the run-*.log naming convention — cannot be
#      judged, and is NOT silently treated as either in or out of the window
run_log_in_window () { : ; }

# run_log_manifest <logs_dir> <cutoff> <exclude_path>
#
# Lists "<logs_dir>/run-*.log", drops <exclude_path> (this run's own,
# still-being-written log; compared after resolving both sides to the same
# form so a relative-vs-absolute mismatch cannot defeat the exclusion), keeps
# only entries run_log_in_window accepts, and for each remaining file prints
# one line: "<basename>|<STATE>|<reason>" — classify_run_log's own output,
# basename-prefixed, one file per line, sorted by filename (which sorts
# chronologically for this naming scheme, oldest first).
#
# A file that matches the glob and the window but fails classify_run_log's
# own readability check (its return code 3) gets its own line instead of
# being silently dropped: "<basename>|UNREADABLE|-".
#
# Prints nothing else. An empty window (nothing matches) prints nothing and
# returns 0 — empty is a valid result, not a failure, same posture as
# backlog.sh's zero-unclaimed-items case.
#
# Returns: 0 on a normal pass (including an empty manifest); 1 only if
# <logs_dir> itself is missing, not a directory, or unreadable — a listing
# that could not be trusted must say so rather than reporting "nothing found"
# as though that meant "checked, and there is nothing".
run_log_manifest () { : ; }
```

```bash
# .pipeline/test_run_log.sh — new sections following the existing case-ID
# convention (C1..C10 structural, E1..E9 edge cases, INV1/2 invariants, R1
# syntax): add "W1..Wn" for run_log_in_window and "M1..Mn" for
# run_log_manifest, same heredoc-fixtures-in-a-temp-dir harness already in
# the file. No network, no key, no real logs/ read (logs/ is git-ignored, so
# every fixture is synthetic, same as the existing C1-C10 fixtures).
```

`run.sh` wiring (glue, in the impure shell — not a new sourceable lib, same
split `network_preflight`'s impure loop / `network_retry_decision`'s pure
policy already establishes):

- Add `run_log` to the `for lib in ...` list (one line) and update the
  explanatory comment above it (currently lists all eight libraries by name
  and purpose; add the ninth).
- Immediately before the existing `run_phase sonnet "pipeline observer" ...`
  call, compute the manifest (`run_log_manifest logs "$PIPE_CUTOFF"
  "logs/run-$TS.log"`) and splice its text into the phase prompt string,
  replacing the current "examine `logs/run-*.log` dated on or after
  `$PIPE_CUTOFF`..." instruction with one that hands the manifest over
  directly as the ground truth for section 1, while keeping the existing
  hard-rule sentence ("must not modify anything under examples/, ...")
  unchanged and intact.

### Where it goes / how it's tested

`.pipeline/run_log.sh` and `.pipeline/test_run_log.sh` both already exist
(PR #44) — this increment extends both files, plus edits (not new files) to
`.pipeline/run.sh` (lib list + one phase-prompt call site) and
`.claude/agents/agentlab-pipeline-observer.md` (section 1 only). Confirmed no
name collision and no other in-flight work touches this: `git diff
main..<branch>` was checked against every local and remote `cycle/*` branch
and `feat/phase-postconditions` — the only hits are stale branches predating
`run_log.sh`'s existence on `main` (their diff shows the file being
*removed*, i.e. they're just behind, not building on it), none add to it.

"It works" = `bash .pipeline/test_run_log.sh` exits 0 reporting more passed
cases than before this change (the existing count plus the new W/M sections),
`bash .pipeline/test_gates.sh` still exits 0 unchanged (this increment adds no
cases there — `run_log` joins the sourced-lib list in `run.sh` itself, not in
`test_gates.sh`, since its own tests already live in `test_run_log.sh`), and
the by-hand check against the real `run-2026-08-29_114701.log` (acceptance
criterion 3 above) prints the exact silent-death classification.

## Open questions

- Whether `test_gates.sh` should *also* gain a case asserting `run_log` is
  present in `run.sh`'s `for lib in ...` list (mirroring how it already pins
  other structural facts about `run.sh`, e.g. the `CLAUDE_CODE_PRINT_BG_WAIT_
  CEILING_MS` export's position) is the builder's call — it weakly couples
  `test_gates.sh` to `run.sh`'s exact lib-list wording, which cuts against
  the coupling-avoidance principle `run_log.sh` itself invokes elsewhere, but
  every other lib in that list already has exactly this kind of pin.
- Whether the manifest should also flag "this OK/PARTIAL run's `=== done`
  line is not the file's actual last content" (the pipeline-observer-reading-
  an-old-log's-echoed-closing-line gotcha `knowledge/pipeline-run-log-shapes.
  md` already documents under "Related" → `[[network-preflight-retry]]`) is
  out of scope here: `classify_run_log` already keys on the *last* content
  line specifically to avoid that trap, so nothing new is needed — flagging
  this only so a future reader doesn't think it's an open gap.
- Whether reducing the observer's own token spend measurably lowers how often
  it personally hits the session-limit finding (lines 258/259) can only be
  answered empirically, over the next several nightly runs, once this ships —
  not something this cycle can verify in advance.

## Knowledge base

Extended `knowledge/pipeline-run-log-shapes.md` with a new section, "Not yet
wired in (as of 2026-09-20)", recording the three concrete gaps found above
(no code sources `run_log.sh`; the agent doc still states the disproven
"ends in `Aborting.`" claim as fact; nothing anywhere parses a `run-*.log`
filename's date against a cutoff) so a future cycle can check whether they're
still true rather than re-deriving them. Added one dated addendum line to
`knowledge/pipeline-claim-lifecycle.md`'s Failure 3 "what it still does not
cover" paragraph, recording a confirmed live instance (PRs #46–#49, merged via
an out-of-band "salvage" workflow today, left `[building]` until a
hand-written `chore(backlog): mark the four salvaged items done` commit) of
the gap that paragraph already predicted — evidence for, not a rewrite of,
the existing prediction.
