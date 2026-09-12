# The `(none)` placeholder becomes a fake finding when the observer bullets it

**Date:** 2026-09-12
**Backlog item:** `## Health-check findings` — `fix (health 2026-09-02): (none)`. Marked
`[researching]`.

## Question

`BACKLOG.md` carries a filed "finding" whose entire text is the literal string `(none)` — no
subject, no detail, nothing a builder could act on. Where did a contentless item come from, and
is it a one-off fluke or a live, reproducing bug in the pipeline's own bookkeeping?

## Why this item, not the ones above it

`BACKLOG.md`'s `## Health-check findings` section (auto-filed, not hand-curated) is the only
place in the file with literal `- [ ] ` lines today — every item above it is `[done #N]` or
`[stranded <branch>]`, neither of which is "unclaimed" per `knowledge/pipeline-claim-lifecycle.md`
(a stranded claim explicitly "leaves both the researcher's pick and the replenishment count… and
names the branch a human has to salvage"). Working the literal `- [ ] ` lines top-down:

- The first four (`run-2026-08-29…`, `run-2026-08-30…`, `run-2026-09-01…`, the 600s-ceiling
  recurring cause) are all instances of one root cause — a phase's CLI exits 0 without producing
  the artifact the next phase depends on — and open PR #36 ("gate each phase on the artifact it
  was supposed to produce", branch `feat/phase-postconditions`) is explicitly built for exactly
  this: its own body quotes the 600s-ceiling incident and PIPELINE.md's postmortem line
  verbatim, and `logs/lab-pipeline-2026-09-02_134705.log` (the report that filed these four
  findings) names PR #36 as "the in-flight mitigation" for the recurring cause. Re-researching
  this would duplicate PR #36's ground, which the top-level instructions say to skip.
- The fifth (`NETWORK UNREACHABLE … first occurrence since the PR #31 probe fix`) is not covered
  by any open PR, but it is also not a bug: the pipeline-observer's own report calls it correctly
  handled (aborted at the reachability probe, as designed) and explicitly declines to file it as a
  recurring cause ("not enough to call a trend"). There is nothing to build here.
- The sixth is `(none)` — this note's subject.

I did not find an open PR touching `.pipeline/health.sh`, `.pipeline/pipeline_health.sh`,
`.claude/agents/agentlab-health.md`, or `.claude/agents/agentlab-pipeline-observer.md`
(`gh pr list --state open --json files` on PRs #36, #39, #40 checked 2026-09-12 — none touch any
of the four).

## Findings

### Reproducing where the `(none)` item came from

`git show 243df77` (`chore(backlog): file 10 pipeline finding(s) from 2026-09-02`) is the commit
that added all ten current `## Health-check findings` lines in one shot, from
`file_pipeline_findings` (`.pipeline/run.sh`) parsing `logs/last-pipeline-health.md` on the night
of 2026-09-02. The archived permanent copy of that same report,
`logs/lab-pipeline-2026-09-02_134705.log`, is still on disk and its six `##`-numbered sections map
1:1 onto the ten filed lines in order: 4 from §1 Run outcomes, 1 from §2 Recurring causes, **1 from
§3 Phase failures**, 2 from §4 Claim-state drift, 1 from §5 Schedule gaps, 1 from §6 Quarantined
strays. §3's prose in the archived log reads:

> None. No run in the window printed a `phase '<name>' exited non-zero` line.

That is not a `- ` bulleted line, so by the parser's own contract it should never have become a
finding at all. The filed item is the bare word `(none)`, not that sentence — which means the
*actual* machine-readable file the parser consumed that night,
`logs/last-pipeline-health.md` (git-ignored, overwritten every run, no longer on disk to inspect
directly), must have carried a *different*, bulleted line for that section: `- (none)`.

### The agent's own template teaches the broken form

`.claude/agents/agentlab-pipeline-observer.md`'s Output section gives the exact machine-readable
shape `logs/last-pipeline-health.md` must have, and its worked example is:

```
## Phase failures
- (none)
```

That is a **dash-bulleted** `(none)`. Compare `.pipeline/pipeline_health.sh`'s parser, which has
carried this comment unchanged since it was written (`git log --oneline -- pipeline_health.sh`
shows exactly one commit, `2b9a2b1`):

```
# Any other heading ends the current section and is ignored, exactly as in
# health_findings: this parser's contract is the documented output shape, and
# an undocumented section is by definition not something the agent promised to
# keep stable. A '(none)' placeholder needs no special case; it does not begin
# with '- '. Nor do the prose notes runs sometimes leave between sections.
```

and the actual matching code, in the `plain` section case (used for Recurring abort causes, Phase
failures, Claim-state drift, and Schedule gaps):

```bash
plain)
  case "$line" in
    '- '*)
      body="${line#- }"
      [ -n "$body" ] && printf '%s\n' "$body"
      ;;
  esac
  ;;
```

The comment's premise — "it does not begin with `- `" — is simply false against the agent's own
worked example one file over. `- (none)` matches `'- '*`, `body` becomes the four characters
`(none)`, `[ -n "$body" ]` is true, and it prints. There is no code path that treats `(none)` as
special; the parser relies entirely on the producer never bulleting it, and the producer's own
documented template bullets it. This is not a hypothetical: it is what happened on 2026-09-02, and
it will happen again on every future 7-day cadence in which any of those four sections is
legitimately empty and the observer follows its own template.

### The sibling parser has the identical, currently-unexercised, hole

`.pipeline/health.sh`'s `health_findings()` carries the exact same comment and an analogous
`case`, in its `wikilinks|backlog` branch:

```bash
wikilinks|backlog)
  case "$line" in
    '- '*)
      body="${line#- }"
      [ -n "$body" ] && printf '%s\n' "$body"
      ;;
  esac
  ;;
```

`.claude/agents/agentlab-health.md`'s template does *not* show a bulleted `(none)` example (its
prose says only "write `(none)` under it," with no worked example either way), so this side is
lower-probability but structurally identical — an ambiguous instruction, next to a parser whose
safety depends entirely on interpreting that ambiguity one particular way. The `examples` section
of the same function is *not* at risk: it only matches `'- FAIL '*`, so a bulleted `(none)` there
can never match.

### Confirmed not covered by the existing test suite

`.pipeline/test_gates.sh` (86 passing on current `main`, checked 2026-09-12) has fixtures for both
parsers' healthy case (`H8`, `PH8`) and both fixtures use the bare, undashed form:

```
## Broken wikilinks
(none)
```

No fixture anywhere in the suite feeds a *dash-bulleted* `(none)` through either parser, so the gap
between the agent's template and the parser's comment has no test standing between it and a repeat.

## Build proposal

**Intent.** Make `(none)` — in the one bulleted form the pipeline-observer's own template
actually produces, and defensively in the equivalent form `health.sh` could produce — never turn
into a filed backlog item, and stop teaching the model the inconsistent form in the first place.
Out of scope: anything about PR #36's artifact-postcondition gate, the reachability probe, the
`(none)` sections that are already safe by construction (`Run outcomes`, and `health.sh`'s
`examples` section), and re-deriving `health_item_subject`'s em-dash splitting rule (unaffected —
this fix stops the finding before subject/detail splitting ever sees it).

**Behavioral spec.**
- Input: an already-open, readable snapshot file passed to `health_findings(path)` or
  `pipeline_findings(path)`; the change is confined to how a line already known to be inside a
  `wikilinks`/`backlog` section (`health.sh`) or a `plain` section (`pipeline_health.sh`) is
  turned into a finding body.
- Output invariant: if a body, after the section's existing marker-stripping, is exactly the
  literal string `(none)` (case-sensitive, no more, no less), it must **not** be emitted as a
  finding — regardless of whether the producing report wrote it as `(none)` or `- (none)`. A body
  that merely starts with `(none)` and continues with real text (e.g. `(none) of the retries
  succeeded — investigate`) **must** still be filed; this is an exact-match guard, not a prefix
  filter, so it cannot swallow a real finding that happens to start with that word.
- Failure modes unchanged: unreadable path still returns 1 with nothing on stdout
  (`H9`/`PH9` must stay green); a genuinely healthy report still returns 0 with empty stdout
  (`H8`/`PH8` must stay green).
- Acceptance criteria (all checkable by running `.pipeline/test_gates.sh`):
  1. A fixture with `## Phase failures` / `- (none)` (the exact form in
     `agentlab-pipeline-observer.md`'s own template, reproducing the 2026-09-02 incident) yields
     zero findings from that section.
  2. The same dash-bulleted `- (none)` form under each of the other three `plain`
     `pipeline_findings` sections (Recurring abort causes, Claim-state drift, Schedule gaps)
     independently yields zero findings.
  3. The same dash-bulleted form under `health_findings`'s `wikilinks` and `backlog` sections
     independently yields zero findings.
  4. A finding body of `(none) of the retries succeeded — investigate` (chosen to start with the
     placeholder word but not equal it) is still filed by both parsers — proves the guard is exact,
     not a prefix strip.
  5. All existing `test_gates.sh` cases (86 today; confirm the number at build time, since PR #36
     or #39 may have merged by then) still pass unmodified.
  6. `agentlab-pipeline-observer.md`'s `## Phase failures` template line no longer reads `- (none)`
     — grep for it and confirm the replacement matches `agentlab-health.md`'s existing convention
     (bare `(none)`, no leading `- `).
  7. The stale `- [ ] fix (health 2026-09-02): (none)` line itself is removed from `BACKLOG.md`
     (it is the artifact of the very bug being fixed and describes no real work — nothing to mark
     `[done #N]` against).

**Interfaces (behavior contract only, no bodies — exact code shape is the builder's call).**
- `.pipeline/health.sh`: `health_findings(path: file) -> (stdout: 0+ finding lines, rc: 0|1)` —
  unchanged signature; the `wikilinks|backlog` case gains the exact-match guard described above.
- `.pipeline/pipeline_health.sh`: `pipeline_findings(path: file) -> (stdout: 0+ finding lines,
  rc: 0|1)` — unchanged signature; the `plain` case gains the same guard. Whether the two files
  share one helper or each inlines its own one-line check is left to the builder — both files'
  header comments currently claim to define a fixed, small number of functions each
  ("two functions and one constant" / "one function and one constant"), so introducing a shared
  helper is a documentation update too if chosen.
- `.claude/agents/agentlab-pipeline-observer.md`: prose-only edit, one line, inside the Output
  section's fenced example.
- `.pipeline/test_gates.sh`: new cases continuing the existing IDs (`H13`+ for the health.sh cases,
  `PH11`+ for the pipeline_health.sh cases — `H1`–`H12` and `PH1`–`PH10` are already taken on
  current `main`).
- `BACKLOG.md`: delete one line under `## Health-check findings`.

**What "it works" means.** `bash .pipeline/test_gates.sh` exits 0 with a strictly higher passing
count than today's 86, including the new IDs above; `grep -n '^- (none)$' .claude/agents/agentlab-pipeline-observer.md`
returns nothing; and `grep -c 'fix (health 2026-09-02): (none)' BACKLOG.md` returns 0.

## Open questions

- Whether `logs/last-pipeline-health.md` from 2026-09-02 itself (the file actually parsed) really
  did say `- (none)` under `## Phase failures` cannot be checked directly — it's git-ignored and
  was overwritten by the next observer run. The inference rests on: (a) the archived sibling log's
  §3 prose is not a `- ` line and could not have produced a finding under the documented contract,
  (b) exactly one extra finding appeared, in exactly §3's position in the filing order, and
  (c) the agent's own template for that exact section is dash-bulleted `(none)`. I could not find
  a fourth, independent confirmation, so this is a strong inference from converging evidence, not
  a directly observed byte-for-byte artifact.
- Whether `health.sh`'s `wikilinks`/`backlog` sections have ever actually produced a live `(none)`
  finding (as opposed to being merely structurally capable of it, as shown above) — I found no
  such line in either archived `logs/lab-health-*.log` or in `BACKLOG.md`'s git history. Proposed
  as a defensive fix by symmetry with the confirmed pipeline_health.sh bug, not as a second
  observed incident.
- Whether other, non-`(none)` placeholder conventions exist elsewhere in either agent's
  instructions that could hit the same "documented-safe-but-actually-bulleted" trap (e.g. a
  section left with only prose notes) was not audited beyond the two `(none)` cases — out of
  scope for today's fix, worth a look if this class of bug resurfaces.
