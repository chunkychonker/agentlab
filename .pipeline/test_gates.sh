#!/bin/bash
# Offline self-test for the pipeline's deterministic gates — every decision
# run.sh makes with no model in the loop:
#
#   .pipeline/verdict.sh  — does the review authorise shipping (review_verdict)
#   .pipeline/health.sh   — what did the health check find (health_findings,
#                           health_item_subject)
#   .pipeline/pipeline_health.sh — what did the pipeline observer find
#                           (pipeline_findings), the sibling of health.sh
#   .pipeline/backlog.sh  — the health-filing half (backlog_has_unresolved,
#                           backlog_file_health_finding), shared by both
#                           observers
#   .pipeline/preflight.sh — what a dirty main means (worktree_disposition),
#                           plus run.sh's stash_strays, extracted and run
#   .pipeline/postcondition.sh — did a phase actually produce the artifact the
#                           next one hard-depends on (artifact_freshness,
#                           research_note_fresh, increment_built,
#                           phase_no_postcondition)
#   run.sh's check_reachable — is the network there (N1-N5), extracted and run
#                           against a local server, not the real internet
#
# plus the call sites they have in .pipeline/run.sh. No ANTHROPIC_API_KEY,
# no `claude`, nothing outside this box — all it touches is a throwaway temp
# dir plus read-only greps of run.sh. Two exceptions, both contained: it runs
# real `git`, but only inside a throwaway repo under that temp dir; and it
# opens a socket, but only on 127.0.0.1. Both are deliberate — the bugs these
# cases cover are git's behaviour and curl's, and a stub would only test the
# stub.
#
# One file rather than one per lib because they are all one invariant: a phase
# leaves something behind — a verdict, a health report, a research note, an
# increment — and something downstream must act on it WITHOUT a model in the
# loop. Splitting the suite would split that invariant across files that could
# drift apart.
#
# On-demand only. It lives outside examples/, so the periodic lab health check
# does not pick it up. Run it by hand after editing run.sh or any lib in
# .pipeline/ (naming them here is how the list above drifted twice):
#
#   bash .pipeline/test_gates.sh
#
# One line per case; exits 0 iff every case passes — same shape as
# .pipeline/test_backlog.sh and eval/run_reviewer_eval.sh.
#
# bash 3.2 only, like everything else in .pipeline/ — see
# knowledge/bash-3.2-testable-scripts.md.

set -uo pipefail

REPO="/Users/steeb/agentlab"
cd "$REPO" || { echo "cannot cd $REPO"; exit 1; }

RUN_SH="$REPO/.pipeline/run.sh"

# --- Harness ---------------------------------------------------------------

PASSED=0
FAILED=0

pass () { echo "PASS  $1: $2"; PASSED=$(( PASSED + 1 )); }
fail () { echo "FAIL  $1: $2"; FAILED=$(( FAILED + 1 )); }

# assert_eq <case> <expected> <actual> <what>
assert_eq () {
  if [ "$2" == "$3" ]; then
    pass "$1" "$4"
  else
    fail "$1" "$4 — expected '$2', got '$3'"
  fi
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/agentlab-gates-test.XXXXXX")" || exit 1
trap 'chmod -R u+rwX "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

# Sourcing must be silent and must not touch anything — the libs are declaration
# files, and run.sh sources them under `set -uo pipefail` before any phase runs.
src_noise="$( { . "$REPO/.pipeline/verdict.sh"; . "$REPO/.pipeline/health.sh"; . "$REPO/.pipeline/pipeline_health.sh"; . "$REPO/.pipeline/backlog.sh"; . "$REPO/.pipeline/preflight.sh"; . "$REPO/.pipeline/postcondition.sh"; } 2>&1 )"
src_rc=$?
assert_eq "INV1" "0|" "$src_rc|$src_noise" \
  "sourcing all six libs exits 0 and prints nothing"

. "$REPO/.pipeline/verdict.sh"
. "$REPO/.pipeline/health.sh"
. "$REPO/.pipeline/pipeline_health.sh"
. "$REPO/.pipeline/backlog.sh"
. "$REPO/.pipeline/preflight.sh"
. "$REPO/.pipeline/postcondition.sh"

# --- verdict.sh ------------------------------------------------------------
#
# The gate fails closed, so every case that is not an unambiguous PASS must
# report something other than PASS. These are the acceptance criteria.

# verdict_case <id> <expected-word> <expected-rc> <what> [file-body...]
# An omitted body means "no file at all".
verdict_case () {
  local id="$1" want_word="$2" want_rc="$3" what="$4"; shift 4
  local f="$WORK/verdict-$id.md"
  if [ "$#" -gt 0 ]; then
    printf '%s\n' "$@" > "$f"
  fi
  local got_word got_rc
  got_word="$(review_verdict "$f")"
  got_rc=$?
  assert_eq "$id" "$want_word|$want_rc" "$got_word|$got_rc" "$what"
}

verdict_case V1 MISSING   2 "an absent verdict file is MISSING, not PASS"

# A truly zero-byte file (the reviewer opened it and wrote nothing) is MISSING;
# a file with a byte in it but no verdict is MALFORMED. Distinguishing them
# only affects the log line — both fail closed — but a caller that could not
# tell "the phase died" from "the phase produced garbage" would be lying in the
# one place someone reads after a night that shipped nothing.
: > "$WORK/verdict-V2.md"
v2_word="$(review_verdict "$WORK/verdict-V2.md")"; v2_rc=$?
assert_eq "V2" "MISSING|2" "$v2_word|$v2_rc" \
  "a zero-byte verdict file is MISSING, not PASS"
verdict_case V2b MALFORMED 3 "a whitespace-only verdict file is MALFORMED, not PASS" "" "   "
verdict_case V3 PASS      0 "a clean PASS verdict parses as PASS" \
  "VERDICT: PASS" "Increment: examples/foo/" "Findings:" "- (nit) whatever"
verdict_case V4 FAIL      1 "a clean FAIL verdict parses as FAIL" \
  "VERDICT: FAIL" "Increment: examples/foo/" "Findings:" "- (blocker) it does not run"
verdict_case V5 MALFORMED 3 "a review with no VERDICT line is MALFORMED, not PASS" \
  "Increment: examples/foo/" "Looks good to me, ship it"
verdict_case V6 MALFORMED 3 "two contradicting VERDICT lines are MALFORMED, not a vote" \
  "VERDICT: PASS" "...on reflection..." "VERDICT: FAIL"
verdict_case V7 MALFORMED 3 "an unrecognised verdict word is MALFORMED" \
  "VERDICT: MAYBE"
verdict_case V8 MALFORMED 3 "a lowercase verdict is MALFORMED (strict by design)" \
  "VERDICT: pass"
verdict_case V9 PASS      0 "no space after the colon still parses" \
  "VERDICT:PASS"
verdict_case V10 MALFORMED 3 "an indented VERDICT line does not count (column 0 contract)" \
  "  VERDICT: PASS"
verdict_case V11 MALFORMED 3 "PASS mentioned in prose does not authorise shipping" \
  "The increment would PASS if the test were fixed." "I am not writing a verdict line."

# The word must come from the VERDICT line, not from anywhere else in the file.
verdict_case V12 FAIL 1 "a FAIL verdict is not flipped by the word PASS appearing later" \
  "VERDICT: FAIL" "The self-test would PASS if the import were fixed."

# V13: the real file in the repo, if one is lying around, must parse cleanly.
# This is the only case that reads outside $WORK, and it is read-only.
if [ -s "$REPO/$REVIEW_VERDICT_FILE" ]; then
  real_word="$(review_verdict "$REPO/$REVIEW_VERDICT_FILE")"
  case "$real_word" in
    PASS|FAIL) pass "V13" "the repo's current $REVIEW_VERDICT_FILE parses as $real_word" ;;
    *) fail "V13" "the repo's current $REVIEW_VERDICT_FILE parses as $real_word — the reviewer's real output does not match the contract" ;;
  esac
else
  pass "V13" "no $REVIEW_VERDICT_FILE present to cross-check (skipped, not a finding)"
fi

# --- health.sh -------------------------------------------------------------

# A fixture in the shape agentlab-health.md specifies, including every line
# form that must NOT be treated as a finding.
HEALTH_FIXTURE="$WORK/last-health.md"
cat > "$HEALTH_FIXTURE" <<'EOF'
CHECKED: 2026-08-14 (run 2026-08-14_024700)
Examples: 11 pass / 2 fail / 1 skipped of 14
Knowledge links: 1 broken of 65
Backlog/PR mismatches: 1 of 13 [done #N] lines

Scope note: `.pipeline/mode` is `demo`, so `projects/` was not in scope.

## Example results
- PASS  examples/mcp-hello-world/
- PASS  examples/minimal-agent-loop/ — offline suite green
- SKIPPED  examples/mcp-connect-claude-code/ — billed path deliberately not run
- FAIL  examples/typed-tool-registry/ — README claims 4 self-tests, suite emits 6
- FAIL  examples/tool-error-policy/ — pytest exits 1: ImportError on httpx

## Broken wikilinks
- knowledge/compaction.md:12 -> [[context-editing-v2]] (no such file)

Note: `knowledge/README.md:26 -> [[wikilink]]` is a syntax illustration, not rot.

## Backlog/PR mismatches
- "- [done #99] a thing that never shipped" — PR #99 is CLOSED, not MERGED

## Other drift noted, deliberately not fixed
- 5 READMEs document `python` where only `python3` exists on this box
EOF

findings="$(health_findings "$HEALTH_FIXTURE")"
h1_rc=$?
h1_count="$(printf '%s\n' "$findings" | grep -c . )"
assert_eq "H1" "0|4" "$h1_rc|$h1_count" \
  "the fixture yields exactly its 4 findings (2 FAIL + 1 link + 1 mismatch)"

assert_eq "H2" "1" "$(printf '%s\n' "$findings" | grep -c '^examples/typed-tool-registry/ ')" \
  "a FAIL example line becomes a finding, label and alignment padding stripped"
assert_eq "H3" "0" "$(printf '%s\n' "$findings" | grep -c 'mcp-hello-world\|minimal-agent-loop')" \
  "PASS lines are never findings"
assert_eq "H4" "0" "$(printf '%s\n' "$findings" | grep -c 'mcp-connect-claude-code')" \
  "SKIPPED is not a finding against the example (agentlab-health.md is explicit)"
assert_eq "H5" "1" "$(printf '%s\n' "$findings" | grep -cF 'knowledge/compaction.md:12')" \
  "a broken wikilink becomes a finding"
assert_eq "H6" "0" "$(printf '%s\n' "$findings" | grep -c 'python3')" \
  "the undocumented 'Other drift' section is ignored — the contract is the documented shape"
assert_eq "H7" "0" "$(printf '%s\n' "$findings" | grep -cF 'syntax illustration')" \
  "prose between sections is not a finding"

# A healthy report: every section present, all '(none)'. The common case.
cat > "$WORK/healthy.md" <<'EOF'
CHECKED: 2026-08-14
Examples: 14 pass / 0 fail / 0 skipped of 14

## Example results
- PASS  examples/mcp-hello-world/

## Broken wikilinks
(none)

## Backlog/PR mismatches
(none)
EOF
healthy="$(health_findings "$WORK/healthy.md")"
assert_eq "H8" "0|" "$?|$healthy" \
  "a clean report yields zero findings and exit 0 (not an error)"

health_findings "$WORK/does-not-exist.md" >/dev/null 2>&1
assert_eq "H9" "1" "$?" "an unreadable snapshot returns 1"

assert_eq "H10" "examples/typed-tool-registry/" \
  "$(health_item_subject 'examples/typed-tool-registry/ — README claims 4, suite emits 6')" \
  "the subject is everything before the first em dash"
assert_eq "H11" "a finding with no separator" \
  "$(health_item_subject 'a finding with no separator')" \
  "a finding with no em dash is its own subject"
assert_eq "H12" "examples/foo/" \
  "$(health_item_subject 'examples/foo/ — first reason — second clause')" \
  "only the FIRST em dash splits, so a multi-clause reason keeps one subject"

# --- pipeline_health.sh ----------------------------------------------------

# A fixture in the shape agentlab-pipeline-observer.md specifies, including
# every line form that must NOT be treated as a finding. The OK line is the
# one that distinguishes this parser from health_findings: this report is a
# complete record of the window, and only the bad outcomes are filed.
PIPE_FIXTURE="$WORK/last-pipeline-health.md"
cat > "$PIPE_FIXTURE" <<'EOF'
CHECKED: 2026-08-23
Runs examined: 7 (2026-08-16 .. 2026-08-22)
Aborted: 2  Partial: 1  Phase failures: 1

Scope note: logs/run-2026-08-23_024702.log is this run and was excluded.

## Run outcomes
- OK       run-2026-08-16_024702 — shipped 2/2
- OK       run-2026-08-17_024701 — shipped 2/2
- PARTIAL  run-2026-08-18_024703 — shipped 1/2, cycle 2 clean VERDICT: FAIL
- ABORTED  run-2026-08-19_024702 — NETWORK UNREACHABLE (api.anthropic.com / github.com)
- ABORTED  run-2026-08-21_024702 — NETWORK UNREACHABLE (api.anthropic.com / github.com)

## Recurring abort causes
- NETWORK UNREACHABLE — 2x on 2026-08-19, 2026-08-21

Note: a single abort is noise; these two share a verbatim cause.

## Phase failures
- run-2026-08-18_024703 cycle 2/2: build — exited non-zero

## Claim-state drift
- "Verify the skill allowed-tools claim" — marked [building], shipped in PR #33 (merged), never advanced

## Schedule gaps
- no run log for 2026-08-20

## Quarantined strays
(none)

## Notes the observer wanted to add
- the 02:47 slot collides with Time Machine on this box
EOF

pfindings="$(pipeline_findings "$PIPE_FIXTURE")"
p1_rc=$?
p1_count="$(printf '%s\n' "$pfindings" | grep -c . )"
assert_eq "PH1" "0|7" "$p1_rc|$p1_count" \
  "the fixture yields exactly its 7 findings (1 partial + 2 aborted + 1 cause + 1 phase + 1 claim + 1 gap, minus 2 OK)"

assert_eq "PH2" "0" "$(printf '%s\n' "$pfindings" | grep -c 'run-2026-08-16\|run-2026-08-17')" \
  "OK runs are recorded in the report but are never findings"
assert_eq "PH3" "1" "$(printf '%s\n' "$pfindings" | grep -c '^run-2026-08-18_024703 — shipped 1/2')" \
  "a PARTIAL line becomes a finding, label and alignment padding stripped"
assert_eq "PH4" "1" "$(printf '%s\n' "$pfindings" | grep -c '^run-2026-08-19_024702 — NETWORK')" \
  "an ABORTED line becomes a finding, label and alignment padding stripped"
assert_eq "PH5" "1" "$(printf '%s\n' "$pfindings" | grep -cF 'no run log for 2026-08-20')" \
  "a schedule gap becomes a finding"
assert_eq "PH6" "0" "$(printf '%s\n' "$pfindings" | grep -c 'Time Machine')" \
  "an undocumented section is ignored — the contract is the documented shape"
assert_eq "PH7" "0" "$(printf '%s\n' "$pfindings" | grep -cF 'single abort is noise')" \
  "prose between sections is not a finding"

# A healthy report: every section present, all '(none)' except the OK record.
# This is the case that must stay silent, and the one we want to be common.
cat > "$WORK/pipe-healthy.md" <<'EOF'
CHECKED: 2026-08-23
Runs examined: 7 (2026-08-16 .. 2026-08-22)

## Run outcomes
- OK       run-2026-08-16_024702 — shipped 2/2

## Recurring abort causes
(none)

## Phase failures
(none)

## Claim-state drift
(none)

## Schedule gaps
(none)

## Quarantined strays
(none)
EOF
pipe_healthy="$(pipeline_findings "$WORK/pipe-healthy.md")"
assert_eq "PH8" "0|" "$?|$pipe_healthy" \
  "a clean pipeline report yields zero findings and exit 0 (not an error)"

pipeline_findings "$WORK/does-not-exist.md" >/dev/null 2>&1
assert_eq "PH9" "1" "$?" "an unreadable pipeline snapshot returns 1"

# The dedupe key is health_item_subject, shared with the portfolio observer
# rather than reimplemented — if these ever disagree, filing silently stops
# deduping and the same finding is queued every cadence.
assert_eq "PH10" "run-2026-08-19_024702" \
  "$(health_item_subject 'run-2026-08-19_024702 — NETWORK UNREACHABLE (api.anthropic.com / github.com)')" \
  "a pipeline finding's subject splits on the first em dash, same rule as the portfolio observer"

# --- backlog.sh: filing ----------------------------------------------------

write_backlog () {   # <path>
  cat > "$1" <<'EOF'
# Backlog

## Coding agents
- [done #4] a shipped item
- [researching] claimed by tonight's cycle
- [ ] a real queued item

## Notes
- Prefer the latest Claude models.
EOF
}

BL="$WORK/BACKLOG.md"
write_backlog "$BL"
before="$(backlog_count_unclaimed "$BL")"

backlog_file_health_finding "$BL" "2026-08-14" "examples/typed-tool-registry/" "README claims 4, suite emits 6"
b1_rc=$?
after="$(backlog_count_unclaimed "$BL")"
assert_eq "B1" "0|1|1" \
  "$b1_rc|$(( after - before ))|$(grep -c "^$BACKLOG_HEALTH_SECTION\$" "$BL")" \
  "filing into a backlog with no health section creates the section and adds one unclaimed item"

assert_eq "B2" "1" \
  "$(grep -cF -- "- [ ] $BACKLOG_HEALTH_PREFIX 2026-08-14): examples/typed-tool-registry/ — README claims 4" "$BL")" \
  "the filed item carries the '- [ ] ' contract prefix, the date, the subject and the detail"

# Idempotence: the same finding next week must change nothing at all.
snapshot="$(cat "$BL")"
backlog_file_health_finding "$BL" "2026-08-21" "examples/typed-tool-registry/" "reworded reason, same rot"
b3_rc=$?
assert_eq "B3" "3|same" "$b3_rc|$( [ "$snapshot" == "$(cat "$BL")" ] && echo same || echo CHANGED )" \
  "re-filing the same subject returns 3 and leaves the file byte-identical (reword-proof)"

backlog_file_health_finding "$BL" "2026-08-14" "examples/tool-error-policy/" "pytest exits 1: ImportError"
b4_rc=$?
assert_eq "B4" "0|1" "$b4_rc|$(grep -c "^$BACKLOG_HEALTH_SECTION\$" "$BL")" \
  "a second, different finding is appended under the SAME section, not a new one"

# Ordering: filed items land under the health heading, after the explanatory
# note, and the heading stays at the end of the file (researcher works top-down,
# so rot fixes queue behind planned work).
last_heading="$(grep '^## ' "$BL" | tail -1)"
assert_eq "B5" "$BACKLOG_HEALTH_SECTION" "$last_heading" \
  "the health section is appended at the end of the backlog, not the top"

# Glob/regex metacharacters in the subject must not be interpreted. A wikilink
# subject contains '[[...]]', which as a glob is a character class.
backlog_file_health_finding "$BL" "2026-08-14" "knowledge/compaction.md:12 -> [[context-editing-v2]]" "no such file"
b6a_rc=$?
backlog_file_health_finding "$BL" "2026-08-14" "knowledge/compaction.md:12 -> [[context-editing-v2]]" "no such file"
b6b_rc=$?
assert_eq "B6" "0|3" "$b6a_rc|$b6b_rc" \
  "a subject full of glob metacharacters files once and dedupes literally the second time"

# '[done #N]' must NOT suppress: a finding that recurs after its fix shipped is
# new information, not a duplicate.
BL2="$WORK/BACKLOG-done.md"
write_backlog "$BL2"
printf '%s\n' "" "$BACKLOG_HEALTH_SECTION" \
  "- [done #99] $BACKLOG_HEALTH_PREFIX 2026-08-01): examples/regressed/ — old rot" >> "$BL2"
backlog_file_health_finding "$BL2" "2026-08-14" "examples/regressed/" "the rot is back"
assert_eq "B7" "0" "$?" \
  "a finding whose earlier fix is [done #N] is re-filed — a recurrence is not a duplicate"

# '[researching]' and '[stranded ...]' must suppress: the work is in flight.
BL3="$WORK/BACKLOG-inflight.md"
write_backlog "$BL3"
printf '%s\n' "" "$BACKLOG_HEALTH_SECTION" \
  "- [researching] $BACKLOG_HEALTH_PREFIX 2026-08-07): examples/inflight/ — claimed tonight" \
  "- [stranded cycle/2026-08-12-unshipped-1] $BACKLOG_HEALTH_PREFIX 2026-08-05): examples/stranded/ — built, unshipped" >> "$BL3"
backlog_file_health_finding "$BL3" "2026-08-14" "examples/inflight/" "x"; b8a_rc=$?
backlog_file_health_finding "$BL3" "2026-08-14" "examples/stranded/" "x"; b8b_rc=$?
assert_eq "B8" "3|3" "$b8a_rc|$b8b_rc" \
  "[researching] and [stranded] items suppress re-filing — the work is already queued"

# A path mentioned in unrelated prose or in a non-health item must NOT suppress.
BL4="$WORK/BACKLOG-prose.md"
write_backlog "$BL4"
printf '%s\n' "- [ ] build something new using examples/mentioned/ as a reference" >> "$BL4"
printf '%s\n' "Prose mentioning examples/mentioned/ outside any item." >> "$BL4"
backlog_file_health_finding "$BL4" "2026-08-14" "examples/mentioned/" "actually broken"
assert_eq "B9" "0" "$?" \
  "a subject mentioned in prose or in a non-health item does not suppress the finding"

# Long details are truncated, not dropped, and the item stays one line.
long_detail="LEADING WORDS THAT MUST SURVIVE$(printf 'x%.0s' $(seq 1 400))"
backlog_file_health_finding "$BL" "2026-08-14" "examples/verbose/" "$long_detail"
filed_line="$(grep -F -- "examples/verbose/" "$BL" | head -1)"
assert_eq "B10" "1|1" \
  "$( [ "${#filed_line}" -lt 300 ] && echo 1 || echo 0 )|$(printf '%s' "$filed_line" | grep -c '\.\.\.$')" \
  "an over-long detail is truncated to one line ending in an ellipsis, not dropped"

# B10b is the regression test for a real bug: truncation was done with
# `printf | cut`, so on any shell where `cut` was not on PATH the command
# substitution yielded "" and the item shipped as a bare '...' — the finding's
# reason silently deleted. B10 above could not catch it (an empty detail is
# still short and still ends in an ellipsis). Asserting the CONTENT survives is
# what distinguishes "truncated" from "destroyed".
assert_eq "B10b" "1" \
  "$(printf '%s' "$filed_line" | grep -cF -- '— LEADING WORDS THAT MUST SURVIVE')" \
  "truncation preserves the start of the detail — it never degrades to a bare ellipsis"

# An unwritable backlog fails loudly and leaves no temp file behind.
BL5="$WORK/BACKLOG-ro.md"
write_backlog "$BL5"
printf '%s\n' "" "$BACKLOG_HEALTH_SECTION" "- [ ] seed" >> "$BL5"
chmod a-w "$BL5"
backlog_file_health_finding "$BL5" "2026-08-14" "examples/ro/" "x" 2>/dev/null
b11_rc=$?
chmod u+w "$BL5"
leftovers="$(ls "$WORK" | grep -c '\.health\.' || true)"
assert_eq "B11" "1|0" "$b11_rc|$leftovers" \
  "an unwritable backlog returns 1 and leaves no .health temp file behind"

# The whole point: a filed item must be visible to the researcher's contract.
assert_eq "B12" "1" \
  "$(grep -c "^- \[ \] $BACKLOG_HEALTH_PREFIX 2026-08-14): examples/tool-error-policy/" "$BL")" \
  "a filed item matches the literal '- [ ] ' prefix the researcher picks on"

# --- preflight.sh: classification ------------------------------------------
#
# The acceptance criteria for "what does a dirty main mean". The asymmetry is
# the whole point and P7 is the case that carries it: untracked-only dirt is
# recoverable, but ONE tracked change blocks the night no matter how much
# untracked noise sits beside it.

# disp_case <id> <expected-word> <expected-rc> <what> [porcelain-line...]
# No lines means "clean tree" (the empty string git actually returns).
disp_case () {
  local id="$1" want_word="$2" want_rc="$3" what="$4"; shift 4
  local text=""
  [ "$#" -gt 0 ] && text="$(printf '%s\n' "$@")"
  local got_word got_rc
  got_word="$(worktree_disposition "$text")"
  got_rc=$?
  assert_eq "$id" "$want_word|$want_rc" "$got_word|$got_rc" "$what"
}

disp_case "P1" "CLEAN"   0 "an empty status is CLEAN"
disp_case "P2" "CLEAN"   0 "a blank-line-only status is CLEAN" ""
disp_case "P3" "STRAY"   2 "one untracked file is a STRAY" "?? scratch.txt"
disp_case "P4" "STRAY"   2 "several untracked files are STRAYs" \
  "?? scratch.txt" "?? notes/" "?? .claude/settings.local.json"
disp_case "P5" "BLOCKED" 1 "an unstaged tracked edit BLOCKS" " M .pipeline/run.sh"
disp_case "P6" "BLOCKED" 1 "a staged tracked edit BLOCKS" "M  BACKLOG.md"
disp_case "P7" "BLOCKED" 1 "one tracked edit BLOCKS even amid untracked files" \
  "?? scratch.txt" " M BACKLOG.md" "?? notes/"
disp_case "P8" "BLOCKED" 1 "a merge conflict BLOCKS" "UU knowledge/INDEX.md"
disp_case "P9" "BLOCKED" 1 "a staged rename BLOCKS" "R  old.md -> new.md"
disp_case "P10" "BLOCKED" 1 "a staged delete BLOCKS" "D  examples/gone.py"
# A `??` path whose name would need parsing to read. The status code is in the
# first two columns regardless, so classification must not care.
disp_case "P11" "STRAY"  2 "a quoted untracked path is still a STRAY" \
  '?? "scratch file.txt"' '?? "odd\nname.txt"'

# --- preflight.sh: the mover it feeds --------------------------------------
#
# stash_strays lives in run.sh (it does I/O, so it is not in the lib), but it
# is the half that can lose someone's file, so it gets a real test rather than
# an ordering grep. Extracted verbatim and run against a REAL throwaway git
# repo: the subtle behaviour is git's own (`ls-files --others` listing files
# individually where `status` collapses a directory, and --exclude-standard
# skipping ignored paths), and a stubbed git would only test the stub.

eval "$(sed -n '/^stash_strays () {/,/^}/p' "$RUN_SH")"
if ! declare -f stash_strays >/dev/null 2>&1; then
  fail "S0" "could not extract stash_strays from run.sh — the tests below are vacuous"
else
  pass "S0" "stash_strays extracted verbatim from run.sh"

  TMPREPO="$WORK/strayrepo"
  mkdir -p "$TMPREPO"
  (
    cd "$TMPREPO" || exit 1
    git -c init.defaultBranch=main init -q . >/dev/null 2>&1
    printf 'tracked\n' > tracked.md
    # Mirrors this repo's own .gitignore, and the strays line is load-bearing
    # rather than scenery: without it the moved strays are themselves untracked
    # output and S4 below fails, because the tree is dirty all over again one
    # line after being rescued. R7 is what asserts the real repo carries it.
    printf 'ignored-here\n' > .gitignore
    printf 'secret.env\n' >> .gitignore
    printf '.pipeline/strays/\n' >> .gitignore
    git add tracked.md .gitignore >/dev/null 2>&1
    git -c user.email=t@t -c user.name=t commit -qm init >/dev/null 2>&1
  ) || fail "S0b" "could not build the temp repo"

  # The strays: a plain file, one nested two deep (status would collapse the
  # whole directory into `?? nested/`), one whose name needs quoting, and one
  # git ignores (which the preflight never sees, so the mover must leave it).
  mkdir -p "$TMPREPO/nested/deeper"
  printf 'x\n' > "$TMPREPO/scratch.txt"
  printf 'x\n' > "$TMPREPO/nested/deeper/note.md"
  printf 'x\n' > "$TMPREPO/stray with spaces.txt"
  printf 'x\n' > "$TMPREPO/secret.env"

  stray_rc=0
  stray_out="$(
    cd "$TMPREPO" || exit 1
    TS="testTS"; LOG="$WORK/stray.log"; STRAY_DIR=".pipeline/strays"
    stash_strays 2>&1
  )" || stray_rc=$?

  assert_eq "S1" "0" "$stray_rc" "stash_strays exits 0 on an untracked-only tree"

  dest="$TMPREPO/.pipeline/strays/testTS"
  moved="$( [ -f "$dest/scratch.txt" ] && [ -f "$dest/nested/deeper/note.md" ] \
    && [ -f "$dest/stray with spaces.txt" ] && echo "all" )"
  assert_eq "S2" "all" "${moved:-missing}" \
    "every stray is moved, nested path and quoted name preserved"

  left="$( [ -e "$TMPREPO/scratch.txt" ] || [ -e "$TMPREPO/nested/deeper/note.md" ] \
    || [ -e "$TMPREPO/stray with spaces.txt" ] && echo "leftover" )"
  assert_eq "S3" "clean" "${left:-clean}" "no stray is left behind in the repo root"

  # The contract the whole guard rests on: after the move, the preflight's own
  # check must agree the tree is clean, or run.sh aborts one line later anyway.
  after="$(cd "$TMPREPO" && git status --porcelain)"
  assert_eq "S4" "CLEAN" "$(worktree_disposition "$after")" \
    "the tree the mover leaves behind classifies CLEAN"

  assert_eq "S5" "kept" \
    "$( [ -f "$TMPREPO/secret.env" ] && echo kept || echo moved )" \
    "a gitignored file is left alone — the preflight never counted it as dirt"

  assert_eq "S6" "1" \
    "$(printf '%s\n' "$stray_out" | grep -c '3 untracked stray')" \
    "the run log reports how many strays moved"

  assert_eq "S7" "tracked" \
    "$( [ -f "$TMPREPO/tracked.md" ] && echo tracked || echo lost )" \
    "a tracked file is never touched by the mover"
fi

# --- run.sh's network preflight --------------------------------------------
#
# check_reachable answers "is the network there", and the 2026-08-15 abort was
# it answering a different question: `--max-time 5` over a full GET bounds the
# whole TRANSFER, so a working-but-slow link failed the gate mid-body
# (`curl: (28) ... with 435800 bytes received`) and the night was logged as
# NETWORK UNREACHABLE.
#
# Reproduced here rather than asserted about: a local server that answers HEAD
# at once but trickles a GET body models exactly that shape — reachable, slow
# to finish. Localhost only, no outside network, so this stays offline.

SLOW_PID=""
trap 'kill "$SLOW_PID" 2>/dev/null; chmod -R u+rwX "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

# Constants and function together: check_reachable reads the two timeouts, so
# extracting the function alone would leave it undefined under `set -u`.
eval "$(sed -n '/^CONNECT_TIMEOUT_S=/,/^}/p' "$RUN_SH")"
if ! declare -f check_reachable >/dev/null 2>&1; then
  fail "N0" "could not extract check_reachable from run.sh — the tests below are vacuous"
else
  pass "N0" "check_reachable extracted verbatim from run.sh"

  # A connection that is refused outright is the case that MUST still abort the
  # night. Port 1 on loopback refuses instantly — no waiting on a timeout.
  n1_rc=0
  check_reachable "http://127.0.0.1:1" >/dev/null 2>&1 || n1_rc=$?
  assert_eq "N1" "unreachable" "$( [ "$n1_rc" -ne 0 ] && echo unreachable || echo reachable )" \
    "a refused connection is still UNREACHABLE — the gate has not been defanged"

  cat > "$WORK/slowserver.py" <<'PY'
import http.server, socketserver, sys, time

BODY_LEN = 1_000_000  # promised in the header, never fully delivered

class H(http.server.BaseHTTPRequestHandler):
    def _headers(self):
        self.send_response(200)
        self.send_header("Content-Length", str(BODY_LEN))
        self.end_headers()

    def do_HEAD(self):
        self._headers()          # answered immediately, like a real server

    def do_GET(self):
        self._headers()
        for _ in range(4):       # body trickles and never completes
            try:
                self.wfile.write(b"x" * 1000)
                self.wfile.flush()
            except Exception:
                return
            time.sleep(1.5)

    def log_message(self, *a):
        pass

srv = socketserver.TCPServer(("127.0.0.1", 0), H)
with open(sys.argv[1], "w") as f:
    f.write(str(srv.server_address[1]))
srv.serve_forever()
PY

  # Launched from a subshell so the server is not a job of THIS shell: bash
  # otherwise prints its own "Terminated: 15" line when it reaps the kill
  # below, which lands in the middle of the suite's one-line-per-case output.
  ( python3 "$WORK/slowserver.py" "$WORK/port" >/dev/null 2>&1 & echo $! > "$WORK/pid" )
  SLOW_PID="$(cat "$WORK/pid" 2>/dev/null)"
  slow_port=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -s "$WORK/port" ] && slow_port="$(cat "$WORK/port")" && break
    sleep 0.3
  done

  if [ -z "$slow_port" ]; then
    fail "N2" "the slow local server never came up — cannot reproduce the 2026-08-15 shape"
    fail "N3" "skipped: no slow server"
  else
    SLOW_URL="http://127.0.0.1:$slow_port/"

    # The regression. This is the night that was lost: reachable, just slow.
    n2_rc=0
    check_reachable "$SLOW_URL" >/dev/null 2>&1 || n2_rc=$?
    assert_eq "N2" "reachable" "$( [ "$n2_rc" -eq 0 ] && echo reachable || echo unreachable )" \
      "a reachable host with a slow body is REACHABLE — the 2026-08-15 abort does not recur"

    # And the old shape against the same server, to keep this honest: if this
    # ever starts passing, the server stopped reproducing the bug and N2 has
    # gone vacuous. A tighter cap than run.sh's former 5s, purely so the suite
    # does not sit here for five seconds proving a known-broken thing broken.
    n3_rc=0
    curl -sS --max-time 2 -o /dev/null "$SLOW_URL" >/dev/null 2>&1 || n3_rc=$?
    assert_eq "N3" "times out" "$( [ "$n3_rc" -ne 0 ] && echo "times out" || echo "completes" )" \
      "a full GET under a total-time cap still fails here — N2 is testing something real"
  fi

  kill "$SLOW_PID" 2>/dev/null
  SLOW_PID=""
fi

# The flags ARE the contract (see the comment block above check_reachable), and
# nothing else in the suite would notice them silently reverting to a body
# download.
assert_eq "N4" "1" \
  "$(grep -c 'curl -sS -I --connect-timeout "\$CONNECT_TIMEOUT_S" --max-time "\$RESPONSE_TIMEOUT_S"' "$RUN_SH")" \
  "check_reachable still issues HEAD bounded by --connect-timeout, not a bare GET"

# --max-time is a hang backstop; if it ever drops near the connect timeout it
# silently becomes the gate again, which is the whole 2026-08-15 failure.
if [ "${RESPONSE_TIMEOUT_S:-0}" -gt "${CONNECT_TIMEOUT_S:-0}" ]; then
  pass "N5" "the response backstop (${RESPONSE_TIMEOUT_S}s) stays above the connect timeout (${CONNECT_TIMEOUT_S}s)"
else
  fail "N5" "response timeout ${RESPONSE_TIMEOUT_S:-unset}s is not above connect timeout ${CONNECT_TIMEOUT_S:-unset}s — the payload gate is back"
fi

# --- postcondition.sh ------------------------------------------------------
#
# The gate that closes the 2026-09-01 failure: the 600s
# CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS ceiling killed the researcher mid-phase,
# the `claude` CLI still exited 0, and run.sh advanced to build against a
# research/ whose newest note was the previous night's. An exit code is not
# evidence that an artifact exists.
#
# Two properties carry the whole fix, and each is a case below:
#
#   STALE is not MISSING (PC2 against PC3). research/ was not empty on the
#   failure night, it was full of the wrong nights' notes. A "does the directory
#   have anything in it" check passes on exactly the failure it is for. Only
#   "is there something newer than this phase started" catches it.
#
#   Build noise cannot forge freshness (PC11, PC12). Re-running an old example's
#   self-test rewrites .venv/ and __pycache__/ mtimes, so without the -prune any
#   night that merely ran the health check would report an increment it never
#   built. The prune is load-bearing, so it is tested — and PC13 checks it does
#   not swallow real work sitting in the same tree.
#
# Every non-FRESH case asserts a NON-ZERO return code. That is the fails-closed
# posture PIPELINE.md states: a state the postcondition cannot confirm is not a
# licence to advance. STALE and MISSING are diagnostic, not permission.
#
# The lib reads RELATIVE paths (RESEARCH_DIR="research"), exactly as run.sh
# calls it from the repo root, so each case runs with its own sandbox as the
# working directory. Old files get a real 2026-01-01 mtime rather than a
# doctored floor: the comparison under test is against mtimes on disk, and
# moving the floor instead would exercise arithmetic this lib does not do.

PC_ROOT="$WORK/postcond"
PC_FLOOR="$(date +%s)"   # every file created below this line is at or above it
PC_OLD=202601010000      # touch -t stamp, comfortably under any real floor

# pc_box <name> — create a sandbox directory and echo its path.
pc_box () {
  mkdir -p "$PC_ROOT/$1"
  echo "$PC_ROOT/$1"
}

# pc_run <sandbox> <fn> [args...] — run <fn> with <sandbox> as the working
# directory; print "<word>|<rc>" the way assert_eq wants it.
pc_run () {
  local box="$1"; shift
  local word rc
  word="$( cd "$PC_ROOT/$box" && "$@" )"
  rc=$?
  echo "$word|$rc"
}

if declare -f artifact_freshness >/dev/null 2>&1 \
   && declare -f research_note_fresh >/dev/null 2>&1 \
   && declare -f increment_built >/dev/null 2>&1; then
  pass "PC0" "postcondition.sh defined its functions — the cases below are live"
else
  fail "PC0" "postcondition.sh defined nothing — every case below is vacuous"
fi

# The research phase.
pc_b="$(pc_box nodir)"
assert_eq "PC1" "MISSING|3" "$(pc_run nodir research_note_fresh "$PC_FLOOR")" \
  "an absent research/ is MISSING, not a quiet pass"

pc_b="$(pc_box emptydir)"; mkdir -p "$pc_b/research"
assert_eq "PC2" "MISSING|3" "$(pc_run emptydir research_note_fresh "$PC_FLOOR")" \
  "an empty research/ is MISSING"

pc_b="$(pc_box oldnote)"; mkdir -p "$pc_b/research"
: > "$pc_b/research/2026-08-31-yesterday.md"
touch -t "$PC_OLD" "$pc_b/research/2026-08-31-yesterday.md"
assert_eq "PC3" "STALE|2" "$(pc_run oldnote research_note_fresh "$PC_FLOOR")" \
  "a research/ holding only previous nights' notes is STALE — the 2026-09-01 failure"

pc_b="$(pc_box freshnote)"; mkdir -p "$pc_b/research"
: > "$pc_b/research/2026-09-02-tonight.md"
assert_eq "PC4" "FRESH|0" "$(pc_run freshnote research_note_fresh "$PC_FLOOR")" \
  "a note written after the phase started is FRESH"

# A floor that is not a timestamp cannot be compared, so it is not a pass.
assert_eq "PC5" "MISSING|3" "$(pc_run freshnote research_note_fresh "not-a-number")" \
  "a non-numeric floor fails closed instead of comparing garbage"

assert_eq "PC6" "MISSING|3" "$(pc_run freshnote research_note_fresh "-1")" \
  "a negative floor fails closed"

# The phase writes notes; a fresh scratch file of another type is not the artifact.
pc_b="$(pc_box wrongglob)"; mkdir -p "$pc_b/research"
: > "$pc_b/research/note.md"; touch -t "$PC_OLD" "$pc_b/research/note.md"
: > "$pc_b/research/scratch.txt"
assert_eq "PC7" "STALE|2" "$(pc_run wrongglob research_note_fresh "$PC_FLOOR")" \
  "a fresh non-.md file does not satisfy the research postcondition"

# The build phase, which sweeps both increment roots and takes FRESH in either.
pc_b="$(pc_box noroots)"
assert_eq "PC8" "MISSING|3" "$(pc_run noroots increment_built "$PC_FLOOR")" \
  "neither examples/ nor projects/ existing is MISSING"

pc_b="$(pc_box oldwork)"; mkdir -p "$pc_b/examples/old-demo"
: > "$pc_b/examples/old-demo/main.py"
touch -t "$PC_OLD" "$pc_b/examples/old-demo/main.py"
assert_eq "PC9" "STALE|2" "$(pc_run oldwork increment_built "$PC_FLOOR")" \
  "a builder that shipped nothing leaves only pre-floor work — STALE"

pc_b="$(pc_box otherroot)"; mkdir -p "$pc_b/examples/old-demo" "$pc_b/projects/live"
: > "$pc_b/examples/old-demo/main.py"
touch -t "$PC_OLD" "$pc_b/examples/old-demo/main.py"
: > "$pc_b/projects/live/step-3.md"
assert_eq "PC10" "FRESH|0" "$(pc_run otherroot increment_built "$PC_FLOOR")" \
  "FRESH in projects/ is enough — project mode must not fail for an untouched examples/"

# The -prune, tested from both sides.
pc_b="$(pc_box venvnoise)"
mkdir -p "$pc_b/examples/old-demo/__pycache__" "$pc_b/examples/old-demo/.venv/lib"
: > "$pc_b/examples/old-demo/main.py"
touch -t "$PC_OLD" "$pc_b/examples/old-demo/main.py"
: > "$pc_b/examples/old-demo/__pycache__/main.cpython-313.pyc"
assert_eq "PC11" "STALE|2" "$(pc_run venvnoise increment_built "$PC_FLOOR")" \
  "a fresh __pycache__ entry does not forge an increment"

: > "$pc_b/examples/old-demo/.venv/lib/pyvenv.cfg"
assert_eq "PC12" "STALE|2" "$(pc_run venvnoise increment_built "$PC_FLOOR")" \
  "a fresh .venv entry does not forge an increment"

mkdir -p "$pc_b/examples/new-demo"; : > "$pc_b/examples/new-demo/main.py"
assert_eq "PC13" "FRESH|0" "$(pc_run venvnoise increment_built "$PC_FLOOR")" \
  "one real new file in the same tree is FRESH — the prune skips noise, not work"

# The phases that are gated elsewhere say so explicitly rather than by omission.
assert_eq "PC14" "NONE|0" "$(pc_run nodir phase_no_postcondition "$PC_FLOOR")" \
  "phase_no_postcondition prints NONE and succeeds, ignoring the floor"

# --- run.sh call sites -----------------------------------------------------
#
# Same brittleness caveat as test_backlog.sh's C12/C23, and the same
# justification: ordering IS the fix. A verdict parsed after the maintain phase
# would gate nothing.

gate_ln="$(grep -n 'verdict="\$(review_verdict' "$RUN_SH" | head -1 | cut -d: -f1)"
review_ln="$(grep -n 'cycle \$k/\$CYCLES: review' "$RUN_SH" | head -1 | cut -d: -f1)"
maintain_ln="$(grep -n 'cycle \$k/\$CYCLES: maintain' "$RUN_SH" | head -1 | cut -d: -f1)"
if [ -z "$gate_ln" ] || [ -z "$review_ln" ] || [ -z "$maintain_ln" ]; then
  fail "R1" "run.sh is missing a call site (review=${review_ln:-none}, gate=${gate_ln:-none}, maintain=${maintain_ln:-none})"
elif [ "$review_ln" -lt "$gate_ln" ] && [ "$gate_ln" -lt "$maintain_ln" ]; then
  pass "R1" "run.sh parses the verdict after review (line $gate_ln > $review_ln) and before maintain (< $maintain_ln)"
else
  fail "R1" "verdict gate is mis-ordered: review=$review_ln, gate=$gate_ln, maintain=$maintain_ln"
fi

r2="$(awk -v s="$gate_ln" 'NR>=s && NR<=s+8 && /return 1/ {print "guarded"; exit}' "$RUN_SH")"
assert_eq "R2" "guarded" "${r2:-unguarded}" \
  "a non-PASS verdict returns from run_cycle rather than falling through to maintain"

file_ln="$(grep -n '^    file_health_findings$' "$RUN_SH" | head -1 | cut -d: -f1)"
health_ln="$(grep -n 'agentlab-health subagent' "$RUN_SH" | head -1 | cut -d: -f1)"
if [ -z "$file_ln" ] || [ -z "$health_ln" ]; then
  fail "R3" "run.sh is missing a health call site (phase=${health_ln:-none}, file=${file_ln:-none})"
elif [ "$file_ln" -gt "$health_ln" ]; then
  pass "R3" "run.sh files health findings after the health phase (line $file_ln > $health_ln)"
else
  fail "R3" "file_health_findings runs before the health phase: phase=$health_ln, file=$file_ln"
fi

# The health agent must stay observational: run.sh does the filing, so the
# agent definition must still forbid itself from touching BACKLOG.md.
assert_eq "R4" "1" \
  "$(grep -c 'Never.*edit anything under' "$REPO/.claude/agents/agentlab-health.md")" \
  "agentlab-health.md still forbids the agent from editing BACKLOG.md itself"

pfile_ln="$(grep -n '^    file_pipeline_findings$' "$RUN_SH" | head -1 | cut -d: -f1)"
pobs_ln="$(grep -n 'agentlab-pipeline-observer subagent' "$RUN_SH" | head -1 | cut -d: -f1)"
if [ -z "$pfile_ln" ] || [ -z "$pobs_ln" ]; then
  fail "R9" "run.sh is missing a pipeline-observer call site (phase=${pobs_ln:-none}, file=${pfile_ln:-none})"
elif [ "$pfile_ln" -gt "$pobs_ln" ]; then
  pass "R9" "run.sh files pipeline findings after the observer phase (line $pfile_ln > $pobs_ln)"
else
  fail "R9" "file_pipeline_findings runs before the observer phase: phase=$pobs_ln, file=$pfile_ln"
fi

# Same rule as R4, and for the same reason: run.sh does the filing, so the
# agent must stay observational or the two would both write BACKLOG.md.
assert_eq "R10" "1" \
  "$(grep -c 'Never\*\* edit anything under' "$REPO/.claude/agents/agentlab-pipeline-observer.md")" \
  "agentlab-pipeline-observer.md still forbids the agent from editing BACKLOG.md itself"

# The observer runs at the end of the night, so this run's own log has not had
# its `=== done ===` line written yet. Handing it to the observer would make
# every night report itself as ABORTED — a finding filed on every cadence,
# forever. The exclusion lives in the phase prompt, so pin it there.
assert_eq "R11" "1" \
  "$(grep -c 'EXCLUDE logs/run-\$TS.log' "$RUN_SH")" \
  "the observer phase prompt excludes this run's own in-progress log"

# The preflight must classify BEFORE scrub_artifacts runs: the scrub is
# `git clean -fdx`, so a stray still lying in the tree when it fires is deleted
# rather than rescued. Ordering IS the fix, same as R1.
disp_ln="$(grep -n 'worktree_disposition "\$(git status --porcelain)"' "$RUN_SH" | head -1 | cut -d: -f1)"
scrub_ln="$(grep -n '^scrub_artifacts$' "$RUN_SH" | head -1 | cut -d: -f1)"
if [ -z "$disp_ln" ] || [ -z "$scrub_ln" ]; then
  fail "R6" "run.sh is missing a preflight call site (disposition=${disp_ln:-none}, scrub=${scrub_ln:-none})"
elif [ "$disp_ln" -lt "$scrub_ln" ]; then
  pass "R6" "run.sh classifies the tree (line $disp_ln) before scrub_artifacts deletes anything (line $scrub_ln)"
else
  fail "R6" "scrub_artifacts runs before the disposition check: disp=$disp_ln, scrub=$scrub_ln"
fi

# Two out-of-file agreements STRAY_DIR cannot enforce from preflight.sh, both
# of which silently undo the rescue if they drift: .gitignore keeps the moved
# strays from re-dirtying the tree, and the scrub's -e keeps `git clean -fdx`
# (which deletes ignored files too) from erasing them seconds later.
assert_eq "R7" "1" "$(grep -c '^\.pipeline/strays/$' "$REPO/.gitignore")" \
  ".gitignore lists .pipeline/strays/, so rescued strays do not re-dirty main"
assert_eq "R8" "1" \
  "$(grep -c 'git clean -fdx .*-e "\$STRAY_DIR"' "$RUN_SH")" \
  "scrub_artifacts excludes \$STRAY_DIR from git clean -fdx"

# Every phase names a postcondition, and every name it uses exists. A typo would
# turn the gate into a silent no-op on that phase — run_phase catches that at
# runtime (R13); this catches it before the night runs.
pc_sites="$(grep -n '^[[:space:]]*\(if \)\{0,1\}run_phase ' "$RUN_SH" | grep -v 'run_phase ()')"
pc_bad=""
pc_n=0
while IFS= read -r pc_line; do
  [ -n "$pc_line" ] || continue
  pc_n=$(( pc_n + 1 ))
  pc_name="$(echo "$pc_line" | sed 's/.*run_phase [a-z][a-z]*  *"[^"]*"  *//' | awk '{print $1}')"
  declare -f "$pc_name" >/dev/null 2>&1 \
    || pc_bad="$pc_bad ${pc_line%%:*}:${pc_name:-<none>}"
done <<EOF
$pc_sites
EOF
# A floor, not a count: it only guards against the grep above silently matching
# nothing, which would make the assertion pass by finding no call sites at all.
if [ "$pc_n" -lt 5 ]; then
  fail "R12" "found only $pc_n run_phase call sites — the extraction broke, this case is vacuous"
else
  assert_eq "R12" "" "$pc_bad" \
    "all $pc_n run_phase call sites name a postcondition function that exists"
fi

# run_phase itself, extracted verbatim. Both its guards run BEFORE the floor is
# taken and before the CLI is invoked, which is what makes this runnable offline
# with no key: a bad postcondition never reaches `claude`.
eval "$(sed -n '/^run_phase () {/,/^}/p' "$RUN_SH")"
if ! declare -f run_phase >/dev/null 2>&1; then
  fail "R13" "could not extract run_phase from run.sh — this case is vacuous"
else
  rp_out="$( LOG="$WORK/run-phase.log" run_phase sonnet "test phase" no_such_postcondition_xyz "prompt" 2>&1 )"
  rp_rc=$?
  case "$rp_out" in
    *"is not a defined function"*) rp_saw="yes" ;;
    *) rp_saw="no ($rp_out)" ;;
  esac
  assert_eq "R13" "1|yes" "$rp_rc|$rp_saw" \
    "run_phase aborts the phase when its postcondition names no defined function"
fi

# The floor is per PHASE, not per run. With CYCLES=2 both cycles' artifacts carry
# the same date, so a run-scoped floor would let cycle 1's note satisfy cycle 2's
# gate and the 2026-09-01 bug would survive its own fix.
floor_in_body="$(awk '
  /^run_phase \(\)/ { inbody=1; next }
  inbody && /^\}/ { exit }
  inbody && /floor=/ && /date/ { print "yes"; exit }' "$RUN_SH")"
floor_at_run_scope="$(grep -c '^floor=' "$RUN_SH")"
assert_eq "R14" "yes|0" "$floor_in_body|$floor_at_run_scope" \
  "the freshness floor is taken inside run_phase, so every phase gets its own"

syntax_bad="$(for f in "$RUN_SH" "$REPO/.pipeline/verdict.sh" "$REPO/.pipeline/health.sh" \
  "$REPO/.pipeline/pipeline_health.sh" "$REPO/.pipeline/backlog.sh" \
  "$REPO/.pipeline/preflight.sh" "$REPO/.pipeline/postcondition.sh" \
  "$REPO/.pipeline/test_gates.sh"; do
    bash -n "$f" 2>&1
  done)"
assert_eq "R5" "" "$syntax_bad" \
  "bash -n is clean on run.sh, the six libs, and this test"

# --- Summary ---------------------------------------------------------------

echo ""
echo "=== summary: $PASSED passed, $FAILED failed ==="
[ "$FAILED" -eq 0 ] || exit 1
exit 0
