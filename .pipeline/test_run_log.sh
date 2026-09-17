#!/bin/bash
# Offline self-test for .pipeline/run_log.sh (classify_run_log).
#
# No network, no ANTHROPIC_API_KEY, no `claude`, no git, and — deliberately —
# no reading of the real logs/ directory. logs/ is gitignored, so no real run
# log can ever be a committed fixture; every fixture below is a heredoc written
# into a throwaway temp dir this script creates and removes. The 2026-08-29
# silent-death incident is reproduced synthetically, structure for structure
# (see knowledge/pipeline-run-log-shapes.md).
#
# Case ids are the acceptance criteria from
# research/2026-09-17-pipeline-run-log-classifier.md: C1..C10 are the ten cases
# that note enumerates, E1..E9 are edge cases found while building, R1 is the
# syntax check every suite in here carries.
#
# On-demand only. It lives outside examples/, so the periodic lab health check
# does not pick it up. Run it by hand after editing run_log.sh:
#
#   bash .pipeline/test_run_log.sh
#
# One line per case; exits 0 iff every case passes — same shape as
# .pipeline/test_gates.sh and .pipeline/test_backlog.sh.
#
# bash 3.2 only, like everything else in .pipeline/ — see
# knowledge/bash-3.2-testable-scripts.md.

set -uo pipefail

REPO="/Users/steeb/agentlab"
cd "$REPO" || { echo "cannot cd $REPO"; exit 1; }

LIB="$REPO/.pipeline/run_log.sh"
SELF="$REPO/.pipeline/test_run_log.sh"

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

# assert_contains <case> <needle> <haystack> <what>
assert_contains () {
  case "$3" in
    *"$2"*) pass "$1" "$4" ;;
    *)      fail "$1" "$4 — '$3' does not contain '$2'" ;;
  esac
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/agentlab-runlog-test.XXXXXX")" || exit 1
trap 'chmod -R u+rwX "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

# --- C9: sourcing is silent and inert --------------------------------------
#
# First, because everything below depends on it. A sibling lib in .pipeline/ is
# a declaration file: run.sh sources it under `set -uo pipefail` before any
# phase runs, so sourcing must print nothing, do nothing and leave rc 0. The
# direct-execution tail at the bottom of run_log.sh must take its false branch
# here — if that guard were wrong, sourcing would run the CLI and exit 3.
src_noise="$( { . "$LIB"; } 2>&1 )"
src_rc=$?
assert_eq "C9" "0|" "$src_rc|$src_noise" \
  "sourcing run_log.sh exits 0 and prints nothing"

. "$LIB"

# --- Assertion helpers over the function under test ------------------------

# expect_line <case> <path> <expected-line> <expected-rc> <what>
expect_line () {
  local id="$1" path="$2" want_line="$3" want_rc="$4" what="$5"
  local out rc
  out="$(classify_run_log "$path" 2>/dev/null)"
  rc=$?
  assert_eq "$id" "$want_line|$want_rc" "$out|$rc" "$what"
}

# expect_state <case> <path> <expected-state> <expected-rc> <what>
# Splits on the FIRST delimiter only, which is the documented contract: an
# ABORTED reason may quote a log line that itself contains a `|`.
expect_state () {
  local id="$1" path="$2" want_state="$3" want_rc="$4" what="$5"
  local out rc
  out="$(classify_run_log "$path" 2>/dev/null)"
  rc=$?
  assert_eq "$id" "$want_state|$want_rc" "${out%%|*}|$rc" "$what"
}

# reason_of <path> — the part after the first delimiter.
reason_of () {
  local out
  out="$(classify_run_log "$1" 2>/dev/null)"
  printf '%s\n' "${out#*|}"
}

# --- Fixtures --------------------------------------------------------------
#
# Shaped after real logs/run-*.log output: the banner run.sh opens with, blank
# line + `--- phase: ... ---` per phase, a phase's captured stdout between
# them, and run.sh's single closing line.

OK_LOG="$WORK/run-ok.log"
cat > "$OK_LOG" <<'FIX'
=== agentlab pipeline 2026-09-16_020005 (demo) ===

--- phase: cycle 1/2: research (model: opus) ---
research note written: research/2026-09-16-something.md

--- phase: cycle 1/2: build (model: opus) ---
built examples/something/

--- phase: cycle 1/2: review (model: opus) ---
VERDICT: PASS

--- phase: cycle 1/2: auto-merge (PR #41) ---
merged.
shipped 2 of 2 cycles.

=== done 2026-09-16_020005 === (shipped 2/2, full log: logs/run-2026-09-16_020005.log)
FIX

PARTIAL_LOG="$WORK/run-partial.log"
cat > "$PARTIAL_LOG" <<'FIX'
=== agentlab pipeline 2026-09-16_020005 (demo) ===

--- phase: cycle 2/2: review (model: opus) ---
VERDICT: FAIL
review FAILed — not shipping this cycle.
shipped 1 of 2 cycles.

=== done 2026-09-16_020005 === (shipped 1/2, full log: logs/run-2026-09-16_020005.log)
FIX

# The 2026-08-29 incident, reproduced structurally: the log's last content is
# the announcement of a phase, with absolutely nothing after it — no verdict,
# no "exited non-zero", no line ending in "Aborting.".
SILENT_LOG="$WORK/run-silent.log"
cat > "$SILENT_LOG" <<'FIX'
=== agentlab pipeline 2026-08-29_114701 (demo) ===

--- phase: cycle 1/2: review (model: opus) ---
VERDICT: FAIL
review FAILed — not shipping this cycle.

--- phase: cycle 2/2: research (model: opus) ---
Background tasks still running after 600s; terminating.
research note written.

--- phase: cycle 2/2: review (model: opus) ---
FIX

NONZERO_LOG="$WORK/run-nonzero.log"
cat > "$NONZERO_LOG" <<'FIX'
=== agentlab pipeline 2026-09-16_020005 (demo) ===

--- phase: cycle 2/2: research (model: opus) ---
some output from the phase
phase 'cycle 2/2: research' exited non-zero — see logs/run-2026-09-16_020005.log
FIX

PREFLIGHT_LOG="$WORK/run-preflight.log"
cat > "$PREFLIGHT_LOG" <<'FIX'
=== agentlab pipeline 2026-09-16_020005 (demo) ===
NETWORK UNREACHABLE (api.anthropic.com / github.com) — check VPN. Aborting.
FIX

EMPTY_LOG="$WORK/run-empty.log"
: > "$EMPTY_LOG"

# Two model-phase headers where the FIRST looks like it completed. Only the
# last one describes what was running when the log stopped.
TWOPHASE_LOG="$WORK/run-twophase.log"
cat > "$TWOPHASE_LOG" <<'FIX'
=== agentlab pipeline 2026-09-16_020005 (demo) ===

--- phase: cycle 1/2: build (model: opus) ---
built examples/something/ — done.

--- phase: cycle 2/2: build (model: sonnet) ---
FIX

# --- C1..C8: the enumerated cases ------------------------------------------

expect_line C1 "$OK_LOG" "OK|shipped 2/2" 0 \
  "a log ending in the closing line with shipped 2/2 is OK"

expect_line C2 "$PARTIAL_LOG" "PARTIAL|shipped 1/2" 1 \
  "a log ending in the closing line with shipped 1/2 is PARTIAL"

expect_state C3 "$SILENT_LOG" "ABORTED" 2 \
  "a log whose last content is a phase announcement is ABORTED"
c3_reason="$(reason_of "$SILENT_LOG")"
assert_contains "C3a" "cycle 2/2: review" "$c3_reason" \
  "the silent-death reason names the phase that was running"
assert_contains "C3b" "nothing followed" "$c3_reason" \
  "the silent-death reason says nothing followed the announcement"

expect_state C4 "$NONZERO_LOG" "ABORTED" 2 \
  "a phase that printed a failure line and never reached the closing line is ABORTED"
c4_reason="$(reason_of "$NONZERO_LOG")"
assert_contains "C4a" \
  "phase 'cycle 2/2: research' exited non-zero — see logs/run-2026-09-16_020005.log" \
  "$c4_reason" "the reason quotes the log's last line verbatim"
assert_contains "C4b" "cycle 2/2: research" "$c4_reason" \
  "the reason also names the last phase that started"

expect_state C5 "$PREFLIGHT_LOG" "ABORTED" 2 \
  "a preflight death with no phase header at all is ABORTED"
c5_reason="$(reason_of "$PREFLIGHT_LOG")"
assert_contains "C5a" "no model phase started" "$c5_reason" \
  "the reason says no model phase started"
assert_contains "C5b" "NETWORK UNREACHABLE (api.anthropic.com / github.com) — check VPN. Aborting." \
  "$c5_reason" "the reason quotes the preflight line verbatim"

# An unreadable log is not evidence about a run. It must be distinguishable
# from every classification, and must not print a classification.
c6_out="$(classify_run_log "$WORK/does-not-exist.log" 2>/dev/null)"
c6_rc=$?
c6_err="$(classify_run_log "$WORK/does-not-exist.log" 2>&1 >/dev/null)"
assert_eq "C6" "|3" "$c6_out|$c6_rc" \
  "a nonexistent path prints nothing on stdout and returns 3"
if [ -n "$c6_err" ]; then
  pass "C6a" "a nonexistent path says so on stderr"
else
  fail "C6a" "a nonexistent path said nothing on stderr"
fi

expect_line C7 "$EMPTY_LOG" "ABORTED|(empty log)" 2 \
  "a zero-byte log is ABORTED with a non-empty reason"

expect_state C8 "$TWOPHASE_LOG" "ABORTED" 2 \
  "a log with two phase headers is still ABORTED"
c8_reason="$(reason_of "$TWOPHASE_LOG")"
assert_contains "C8a" "cycle 2/2: build" "$c8_reason" \
  "classification uses the LAST phase header, not the first"
case "$c8_reason" in
  *"cycle 1/2: build"*) fail "C8b" "the reason mentions the earlier, superseded phase" ;;
  *)                    pass "C8b" "the reason does not mention the earlier phase" ;;
esac
assert_contains "C8c" "sonnet" "$c8_reason" \
  "the reason carries the model of the last phase"

# C9 ran first, above.

# --- C10: direct execution --------------------------------------------------
#
# `bash .pipeline/run_log.sh <path>` must behave exactly like the function, so
# the thing a human runs by hand and the thing run.sh would source cannot
# disagree. Both the line and the exit code are checked.
c10_out="$(bash "$LIB" "$OK_LOG" 2>/dev/null)"
c10_rc=$?
assert_eq "C10" "OK|shipped 2/2|0" "$c10_out|$c10_rc" \
  "direct execution prints the same line and exit code as the function"

c10b_out="$(bash "$LIB" "$SILENT_LOG" 2>/dev/null)"
c10b_rc=$?
assert_eq "C10a" "$(classify_run_log "$SILENT_LOG" 2>/dev/null)|2" "$c10b_out|$c10b_rc" \
  "direct execution agrees with the function on an aborted log too"

c10c_out="$(bash "$LIB" 2>/dev/null)"
c10c_rc=$?
assert_eq "C10b" "|3" "$c10c_out|$c10c_rc" \
  "direct execution with no argument prints nothing on stdout and exits 3"

# --- E1..E9: edge cases found while building --------------------------------

# E1 A directory is not a log. Same fail-closed answer as a missing path.
e1_out="$(classify_run_log "$WORK" 2>/dev/null)"
e1_rc=$?
assert_eq "E1" "|3" "$e1_out|$e1_rc" "a directory path returns 3, not a classification"

# E2 Present but unreadable. Root bypasses permission bits, so this only means
# anything as a normal user (same guard as test_backlog.sh's C11b).
if [ "$(id -u)" -ne 0 ]; then
  E2_LOG="$WORK/run-noperm.log"
  cp "$OK_LOG" "$E2_LOG"
  chmod 000 "$E2_LOG"
  e2_out="$(classify_run_log "$E2_LOG" 2>/dev/null)"
  e2_rc=$?
  chmod 644 "$E2_LOG"
  assert_eq "E2" "|3" "$e2_out|$e2_rc" "a chmod-000 log returns 3, not a classification"
else
  echo "SKIP  E2: running as root, permission bits are unenforceable"
fi

# E3 No argument at all. The function is called from other scripts; a missing
# argument must be the input error, not an unbound-variable crash of the
# caller's shell under `set -u`.
e3_out="$(classify_run_log 2>/dev/null)"
e3_rc=$?
assert_eq "E3" "|3" "$e3_out|$e3_rc" "no argument returns 3 instead of crashing under set -u"

# E4 A log that is not empty but has no content. Distinct reason from a
# zero-byte file (the same distinction verdict.sh draws between a reviewer that
# wrote nothing and one that wrote whitespace), and still never an empty reason.
E4_LOG="$WORK/run-blank.log"
printf '\n   \n\t\n' > "$E4_LOG"
expect_state E4 "$E4_LOG" "ABORTED" 2 "a whitespace-only log is ABORTED"
e4_reason="$(reason_of "$E4_LOG")"
if [ -n "$e4_reason" ] && [ "$e4_reason" != "(empty log)" ]; then
  pass "E4a" "a whitespace-only log gets its own non-empty reason"
else
  fail "E4a" "whitespace-only reason was empty or indistinguishable from (empty log) — got '$e4_reason'"
fi

# E5 run.sh prints a blank line before its closing line, and a log may collect
# trailing blank lines. The closing line is the last CONTENT line, not the last
# byte.
E5_LOG="$WORK/run-trailing-blanks.log"
cp "$OK_LOG" "$E5_LOG"
printf '\n\n   \n' >> "$E5_LOG"
expect_line E5 "$E5_LOG" "OK|shipped 2/2" 0 \
  "trailing blank lines after the closing line do not hide it"

# E6 The pipeline-observer phase reads OLD run logs and echoes their closing
# lines into the log of the run it is part of. A closing line anywhere but the
# last content line is not evidence this run finished.
E6_LOG="$WORK/run-quoted-done.log"
cat > "$E6_LOG" <<'FIX'
=== agentlab pipeline 2026-09-17_020005 (demo) ===

--- phase: pipeline observer (model: opus) ---
reading logs/run-2026-09-16_020005.log:
=== done 2026-09-16_020005 === (shipped 2/2, full log: logs/run-2026-09-16_020005.log)
that one ended cleanly. Next:

--- phase: cycle 1/2: build (model: opus) ---
FIX
expect_state E6 "$E6_LOG" "ABORTED" 2 \
  "a closing line quoted mid-log does not make an aborted run look OK"
assert_contains "E6a" "cycle 1/2: build" "$(reason_of "$E6_LOG")" \
  "the reason still names the phase that was actually running"

# E7 A closing line the parser cannot read the counts out of is not a licence
# to call the night OK. Fails closed, and says what it saw.
E7_LOG="$WORK/run-malformed-done.log"
cat > "$E7_LOG" <<'FIX'
=== agentlab pipeline 2026-09-16_020005 (demo) ===

--- phase: cycle 1/2: build (model: opus) ---
built something.
=== done 2026-09-16_020005 ===
FIX
expect_state E7 "$E7_LOG" "ABORTED" 2 \
  "a closing line with no parseable shipped counts is ABORTED, not OK"
assert_contains "E7a" "=== done 2026-09-16_020005 ===" "$(reason_of "$E7_LOG")" \
  "the reason quotes the unparseable closing line"

# E8 A phase's captured output can contain a NUL byte, at which point grep
# without -a answers "Binary file ... matches" instead of the line. Verified to
# actually happen on this machine's grep.
E8_LOG="$WORK/run-nul.log"
printf '=== agentlab pipeline 2026-09-16_020005 (demo) ===\n\n--- phase: cycle 1/2: build (model: opus) ---\nweird \0 output\n\n=== done 2026-09-16_020005 === (shipped 2/2, full log: logs/run-2026-09-16_020005.log)\n' > "$E8_LOG"
expect_line E8 "$E8_LOG" "OK|shipped 2/2" 0 \
  "a NUL byte earlier in the log does not defeat classification"

# E9 A phase's output can contain the delimiter (a markdown table, a shell
# pipeline). The documented contract is split-on-first, and the state field
# must survive it.
E9_LOG="$WORK/run-pipe.log"
cat > "$E9_LOG" <<'FIX'
=== agentlab pipeline 2026-09-16_020005 (demo) ===

--- phase: cycle 1/2: research (model: opus) ---
| phase | model | outcome |
FIX
expect_state E9 "$E9_LOG" "ABORTED" 2 \
  "a last line containing the delimiter still yields the state as field one"
assert_contains "E9a" "| phase | model | outcome |" "$(reason_of "$E9_LOG")" \
  "the reason carries the delimiter-bearing line through intact"

# --- Invariants across every fixture ---------------------------------------
#
# Two properties the classifier promises for ALL input, asserted over the whole
# fixture set rather than case by case: exactly one line on stdout, and — for
# every ABORTED — a non-empty reason. The second is the whole point of the
# ABORTED branch: the incident that motivated this file was a log the observer
# could not say anything structural about.
ALL_FIXTURES="$OK_LOG $PARTIAL_LOG $SILENT_LOG $NONZERO_LOG $PREFLIGHT_LOG"
ALL_FIXTURES="$ALL_FIXTURES $EMPTY_LOG $TWOPHASE_LOG $E4_LOG $E5_LOG $E6_LOG"
ALL_FIXTURES="$ALL_FIXTURES $E7_LOG $E8_LOG $E9_LOG"

bad_lines=""
bad_reason=""
for f in $ALL_FIXTURES; do
  out="$(classify_run_log "$f" 2>/dev/null)"
  n="$(printf '%s\n' "$out" | wc -l | tr -d ' ')"
  [ "$n" = "1" ] || bad_lines="$bad_lines $(basename "$f"):$n"
  case "${out%%|*}" in
    ABORTED) [ -n "${out#*|}" ] || bad_reason="$bad_reason $(basename "$f")" ;;
    OK|PARTIAL) ;;
    *) bad_reason="$bad_reason $(basename "$f"):unknown-state" ;;
  esac
done
assert_eq "INV1" "" "$bad_lines" "every fixture yields exactly one line on stdout"
assert_eq "INV2" "" "$bad_reason" "every ABORTED reason is non-empty, and no state is unknown"

# --- R1: syntax -------------------------------------------------------------

syntax_bad="$(for f in "$LIB" "$SELF"; do bash -n "$f" 2>&1; done)"
assert_eq "R1" "" "$syntax_bad" "bash -n is clean on run_log.sh and this test"

# --- Summary ---------------------------------------------------------------

echo ""
echo "=== summary: $PASSED passed, $FAILED failed ==="
[ "$FAILED" -eq 0 ] || exit 1
exit 0
