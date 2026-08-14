#!/bin/bash
# Offline self-test for the pipeline's two deterministic gates:
#
#   .pipeline/verdict.sh  — does the review authorise shipping (review_verdict)
#   .pipeline/health.sh   — what did the health check find (health_findings,
#                           health_item_subject)
#   .pipeline/backlog.sh  — the health-filing half (backlog_has_unresolved,
#                           backlog_file_health_finding)
#
# plus the call sites all three have in .pipeline/run.sh. No network, no
# ANTHROPIC_API_KEY, no git, no `claude` — all it touches is a throwaway temp
# dir plus read-only greps of run.sh.
#
# One file rather than three because the two changes are one feature: the
# review verdict and the health report are both things an agent writes and
# something downstream must act on WITHOUT a model in the loop. Splitting the
# suite would split that invariant across files that could drift apart.
#
# On-demand only. It lives outside examples/, so the periodic lab health check
# does not pick it up. Run it by hand after editing run.sh, verdict.sh,
# health.sh, or backlog.sh's health section:
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
src_noise="$( { . "$REPO/.pipeline/verdict.sh"; . "$REPO/.pipeline/health.sh"; . "$REPO/.pipeline/backlog.sh"; } 2>&1 )"
src_rc=$?
assert_eq "INV1" "0|" "$src_rc|$src_noise" \
  "sourcing verdict.sh, health.sh and backlog.sh exits 0 and prints nothing"

. "$REPO/.pipeline/verdict.sh"
. "$REPO/.pipeline/health.sh"
. "$REPO/.pipeline/backlog.sh"

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

syntax_bad="$(for f in "$RUN_SH" "$REPO/.pipeline/verdict.sh" "$REPO/.pipeline/health.sh" \
  "$REPO/.pipeline/backlog.sh" "$REPO/.pipeline/test_gates.sh"; do
    bash -n "$f" 2>&1
  done)"
assert_eq "R5" "" "$syntax_bad" \
  "bash -n is clean on run.sh, the three libs, and this test"

# --- Summary ---------------------------------------------------------------

echo ""
echo "=== summary: $PASSED passed, $FAILED failed ==="
[ "$FAILED" -eq 0 ] || exit 1
exit 0
