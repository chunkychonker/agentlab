# Stale health findings are starving the backlog: a triage classifier

Date: 2026-10-25. Mode: demo. No external sources apply (this is a defect in the
lab's own pipeline); the hn-search MCP server failed to connect this cycle (missing
venv python), so no HN lookup was attempted. All facts below were read from the repo.

## Question

Every feature item in `BACKLOG.md` is `[done]`, so the topmost `[ ]` is a 2026-09-02
"no run log for 2026-08-17 through 2026-08-28" health finding. Is that actionable,
and what is the underlying problem?

## Findings

- The claimed item cannot be acted on. Its own text says "whether the nightly job ran
  on any of these dates cannot be determined". The dates are 8 to 9 weeks old and a log
  cannot be produced retroactively. `.pipeline/pipeline_health.sh` (`pipeline_gap_date`)
  deliberately does NOT parse this "through" wording (test PH20), and
  `.pipeline/pauses` only covers 2026-10-02..11, so nothing will ever resolve it.
- It is not alone. `grep -c '^- \[ \] ' BACKLOG.md` = 47 and `grep -c '^- \[ \] fix (health'`
  = 47: every unclaimed item is a health finding, almost all historical observations
  (per-run outcomes such as `run-2026-09-13_020004 — shipped 0/2`, "no run log for
  <date>", "phase X exited non-zero — Nx", "session limit — Nx").
- Consequence 1: `backlog_count_unclaimed` (`.pipeline/backlog.sh:32`) counts any line
  starting `- [ ] `. 47 >= cycles, so `backlog_ensure_stocked` never calls the replenish
  phase. The backlog reports itself as stocked while holding no buildable feature work.
- Consequence 2: the researcher is told to take the topmost `[ ]`, so cycles land on
  noise (this one did) instead of work.
- Existing mitigations are on the filing side only: `backlog_has_unresolved` dedupes,
  `.pipeline/pauses` suppresses single-night gaps. Nothing closes an item once filed
  and obsolete. Humans have done it ad hoc with `[done by hand YYYY-MM-DD]`
  (see lines "done by hand 2026-10-16/18").
- Marker choice matters. `backlog_has_unresolved` treats every marker except `[done `
  as "still queued", which suppresses re-filing. `[done by hand ...]` is excluded from
  that check, so a genuine recurrence still re-files (the stated intent: "a finding that
  reappears is new information"). The `[done #N]` PR-mismatch check in health matches
  only `#N`, so `[done by hand` does not trip it.
- Related open work: PR #62 (baseline verify gate, usage-limit stop) addresses the
  session-limit class of findings at the source; this note addresses the leftover items.
- Builder constraint: `PIPELINE.md` (line ~240, 261) says `increment_built` only looks
  under `examples/` and `projects/`; a build landing only in `.pipeline/` goes STALE
  (happened 2026-09-21). So the increment lives in `examples/`, not `.pipeline/`.

## Build proposal

Directory: `examples/backlog-triage/` (checked: not in `ls examples/`, not in open PRs;
only open PR is #62). Python 3 stdlib only, no key, no network.

**Intent.** A deterministic classifier that reads a BACKLOG.md and separates unclaimed
health findings that are historical observations from ones still actionable, and reports
raw vs effective unclaimed count. Out of scope: editing `.pipeline/backlog.sh`, changing
the replenish logic, any model call, closing non-health items, judging whether a fix works.

**Interfaces** (pure core in `triage.py`, shell in `main`):
```python
class Kind(Enum): GAP_NIGHT; RUN_OUTCOME; PHASE_EXIT; REPEATED_MESSAGE; OTHER
@dataclass(frozen=True) class Verdict: line_no:int; kind:Kind; newest:date|None; historical:bool; reason:str
def parse_unclaimed(text:str) -> list[tuple[int,str]]      # only '^- \[ \] ' lines at col 0
def classify(line:str, line_no:int, today:date, max_age_days:int=7) -> Verdict
def triage(text:str, today:date, max_age_days:int=7) -> list[Verdict]
def apply(text:str, verdicts, today:date) -> str   # rewrites '- [ ] ' -> '- [done by hand YYYY-MM-DD: stale <kind>] '
def render_report(verdicts) -> str
```
CLI: `python3 triage.py BACKLOG.md --today 2026-10-25 [--max-age-days 7] [--apply]`;
default is report-only (dry run); `--apply` rewrites the file in place. `today` is a
parameter, never read from a clock inside the core.

**Rules.** Only items matching `fix (health D):` are candidates; all other `[ ]` items
are always actionable. Kind by body: `no run log for` -> GAP_NIGHT; leading
`run-YYYY-MM-DD_HHMMSS` -> RUN_OUTCOME; `phase '...' exited non-zero` -> PHASE_EXIT; a
quoted-message aggregate (contains `Nx`/`N×` plus dates) -> REPEATED_MESSAGE; anything
mentioning `examples/` or a file path (transcript drift, broken links) -> OTHER. Newest
date = max of all YYYY-MM-DD in the body after the filing-date prefix. Historical iff
kind != OTHER and newest is not None and `today - newest > max_age_days`. Items with a
kind but no body date are actionable (fail safe).

**Invariants and failure modes.** Idempotent: a second `--apply` changes nothing (applied
lines no longer start `- [ ] `). Only the marker prefix of a line changes, byte-for-byte
otherwise; continuation lines untouched. Unreadable file or bad `--today` exits 2 with a
message on stderr, nothing written. `--apply` writes only if at least one verdict is
historical, via write-temp-then-rename.

**Acceptance criteria (self-test `test_triage.py`, stdlib unittest, offline).**
1. The 2026-09-02 "no run log for 2026-08-17 through 2026-08-28" line -> GAP_NIGHT,
   newest 2026-08-28, historical at today=2026-10-25.
2. `run-2026-10-22_020001 — external:tardyai opened 0/1 ...` is NOT historical at
   today=2026-10-25, max_age 7, but IS at max_age 2.
3. A `- [ ] fix (health ...): examples/foo/ — README transcript drift` line is OTHER and
   never historical, however old.
4. A non-health `- [ ] Some feature` line is never touched.
5. `apply` is idempotent and changes only line prefixes; `[done #N]`, `[researching]`
   and indented lines are unchanged.
6. Fixture test: copy the real `## Health-check findings` section of this repo's
   BACKLOG.md (committed fixture, 47 `[ ]` lines at today=2026-10-25) and assert raw
   unclaimed = 47 and effective (actionable) count strictly smaller, with the exact
   number pinned in the test once the builder computes it.
7. A marker-compat test: after `apply`, `grep -v '^- \[done '` over the output contains
   none of the closed lines (so `backlog_has_unresolved` semantics are preserved), and
   `grep -c '\[done #'` is unchanged.
8. README with a run block whose output is real and matches (the repo's
   `readme-transcript-check` convention), stating the follow-up: wire triage into
   `backlog_count_unclaimed` / a run.sh pass, a `.pipeline/` change a human should review.

## Open questions

- The 7-day threshold is a judgment call, not derived from data; the CLI exposes it.
- Whether the human wants `--apply` run on the real BACKLOG.md. The builder should NOT
  apply it to the committed BACKLOG.md; ship the tool plus fixture only.
- Why the lab sat 8+ nights (08-17..08-28) without logs remains unknown (logs absent;
  only `pause-resume.log` shows a 08-19 resume).
