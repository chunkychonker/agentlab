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
# Case ids are the acceptance criteria from the two notes this file was built
# against. From research/2026-09-17-pipeline-run-log-classifier.md: C1..C10 are
# the ten cases that note enumerates, E1..E9 are edge cases found while
# building, R1 is the syntax check every suite in here carries. From
# research/2026-09-20-pipeline-run-log-classifier-wiring.md: W1..W14 cover
# run_log_in_window, M1..M17 cover run_log_manifest, and R2..R4 pin the wiring
# in run.sh that makes both of them reachable at all.
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

# --- W1..W14: run_log_in_window --------------------------------------------
#
# Pure string logic, so no fixture files at all: these cases hand it filenames
# that need not exist. Every case asserts stdout is EMPTY as well as the return
# code, because the return code being the whole answer is the contract (a
# caller under `set -uo pipefail` branches on `$?`, and a stray line of stdout
# would end up spliced into a phase prompt).

# expect_window <case> <basename> <cutoff> <expected-rc> <what>
expect_window () {
  local id="$1" name="$2" cutoff="$3" want_rc="$4" what="$5"
  local out rc
  out="$(run_log_in_window "$name" "$cutoff" 2>/dev/null)"
  rc=$?
  assert_eq "$id" "|$want_rc" "$out|$rc" "$what"
}

expect_window W1 "run-2026-08-29_114701.log" "ALL" 0 \
  "the ALL sentinel puts every run log in the window"

# The boundary the phase prompt already promises ("dated on or after
# $PIPE_CUTOFF"), and the one a reader is most likely to get wrong.
expect_window W2 "run-2026-08-29_114701.log" "2026-08-29" 0 \
  "a log dated exactly on the cutoff is IN the window (inclusive)"

expect_window W3 "run-2026-09-19_020002.log" "2026-08-29" 0 \
  "a log dated after the cutoff is in the window"

expect_window W4 "run-2026-08-28_114701.log" "2026-08-29" 1 \
  "a log dated before the cutoff is out of the window"

# The ISO-8601-sorts-as-text claim, at the two rollovers where a naive numeric
# or day-of-month comparison would get it backwards.
expect_window W5 "run-2026-09-01_020002.log" "2026-08-31" 0 \
  "a month rollover is compared correctly as a string"
expect_window W5a "run-2026-08-31_114702.log" "2026-09-01" 1 \
  "the same rollover the other way is still out of the window"
expect_window W5b "run-2026-01-01_020002.log" "2025-12-31" 0 \
  "a year rollover is compared correctly as a string"

# A leading path must not change the answer: run_log_manifest has a full path
# in hand and run.sh's prompt talks about `logs/run-*.log`.
expect_window W6 "logs/run-2026-08-29_114701.log" "2026-08-29" 0 \
  "a leading relative path is ignored"
expect_window W6a "/Users/steeb/agentlab/logs/run-2026-08-28_114701.log" "2026-08-29" 1 \
  "a leading absolute path is ignored"

# The five real tagged logs on this machine (run-...-_resume.log,
# _fixcycle.log, _skills-research.log) are run-*.log files with a real date.
# They have to be judged, not rejected, or a dated window is quietly narrower
# than the set run.sh's prompt promises to cover.
expect_window W7 "run-2026-08-06_153448_resume.log" "2026-08-01" 0 \
  "a tagged run log is judged by its date, not rejected"
expect_window W7a "run-2026-07-29_221707_fixcycle.log" "2026-08-29" 1 \
  "a tagged run log before the cutoff is out of the window"

# A name with no parseable date is NOT silently in or out. It is also silent on
# stderr: it is a fact about a filename, and run_log_manifest reports it as a
# manifest line (M12) rather than as noise in the caller's log.
expect_window W8 "run-notes.log" "2026-08-29" 2 \
  "a name with no parseable date cannot be judged against a dated cutoff"
w8_err="$(run_log_in_window "run-notes.log" "2026-08-29" 2>&1 >/dev/null)"
assert_eq "W8a" "" "$w8_err" \
  "an unparseable name is silent on stderr (the manifest reports it as a line)"

# ALL is answered before the name is parsed: the sentinel means there is no
# date to compare, and an ALL window must cover every run log present.
expect_window W9 "run-notes.log" "ALL" 0 \
  "an unparseable name is still IN an ALL window"

# A cutoff that is neither ALL nor a date is a caller bug, and must be loud.
expect_window W10 "run-2026-08-29_114701.log" "none" 2 \
  "a malformed cutoff cannot be judged"
w10_err="$(run_log_in_window "run-2026-08-29_114701.log" "none" 2>&1 >/dev/null)"
if [ -n "$w10_err" ]; then
  pass "W10a" "a malformed cutoff says so on stderr"
else
  fail "W10a" "a malformed cutoff was silent"
fi
expect_window W10b "run-2026-08-29_114701.log" "" 2 \
  "an empty cutoff is malformed, not treated as ALL"

# Called from other scripts under `set -u`: a missing argument must be the
# unjudgeable answer, not an unbound-variable crash of the caller's shell.
w11_out="$(run_log_in_window 2>/dev/null)"
w11_rc=$?
assert_eq "W11" "|2" "$w11_out|$w11_rc" \
  "no arguments returns 2 instead of crashing under set -u"

# Near misses on the naming convention. Each would be a silent
# misclassification if the pattern were loose.
expect_window W12 "run-2026-8-29_114701.log" "2026-08-29" 2 \
  "a one-digit month is not a run log name"
expect_window W12a "run-2026-08-29_11470.log" "2026-08-29" 2 \
  "a five-digit timestamp is not a run log name"
expect_window W12b "runner-2026-08-29_114701.log" "2026-08-29" 2 \
  "a different prefix is not a run log name"
expect_window W12c "run-2026-08-29_114701.log.bak" "2026-08-29" 2 \
  "a trailing extension after .log is not a run log name"
expect_window W13 "lab-pipeline-2026-09-15_020003.log" "2026-08-29" 2 \
  "the observer's own report log is not a run log"

# Shape, not calendar validity: an impossible date is compared as text rather
# than aborting. Documented, and pinned so it is not mistaken for a bug later.
expect_window W14 "run-2026-13-45_999999.log" "2026-08-29" 0 \
  "an impossible but well-formed date is compared as text, not validated"

# --- M1..M17: run_log_manifest ---------------------------------------------
#
# Real temp-dir fixtures, same harness as above: a synthetic logs/ directory
# holding copies of the fixtures already classified in C1-C10, named the way
# run.sh names them. The real logs/ is never read — it is git-ignored, so it
# cannot be a committed fixture.
#
# LOGS holds only well-formed, readable logs so the exact-output cases can
# assert an exact block. LOGS_ODD holds the two shapes that cannot be judged
# (an unreadable file, an undated name) so they do not pollute every other
# assertion.

LOGS="$WORK/logs"
mkdir -p "$LOGS"
cp "$OK_LOG"      "$LOGS/run-2026-07-29_221707_fixcycle.log"
cp "$SILENT_LOG"  "$LOGS/run-2026-08-29_114701.log"
cp "$OK_LOG"      "$LOGS/run-2026-09-02_134705.log"
cp "$PARTIAL_LOG" "$LOGS/run-2026-09-03_134704.log"
cp "$OK_LOG"      "$LOGS/run-2026-09-20_020002.log"
CURRENT_LOG="$LOGS/run-2026-09-20_020002.log"
# Non-run-log neighbours that really do live in logs/ and must be ignored.
: > "$LOGS/lab-pipeline-2026-09-15_020003.log"
printf 'CHECKED: 2026-09-15\n' > "$LOGS/last-pipeline-health.md"
printf 'x\n' > "$LOGS/launchd.err.log"

LOGS_ODD="$WORK/logs-odd"
mkdir -p "$LOGS_ODD"
cp "$OK_LOG" "$LOGS_ODD/run-2026-09-05_000000.log"
cp "$OK_LOG" "$LOGS_ODD/run-notes.log"
cp "$OK_LOG" "$LOGS_ODD/run-2026-09-04_000000.log"
chmod 000 "$LOGS_ODD/run-2026-09-04_000000.log"

# The exact block, asserted in full rather than line by line: the manifest IS
# its output format, and a missing or extra line is the failure mode that
# matters (a silently short manifest is worse than reading the logs by eye).
m1_want="run-2026-08-29_114701.log|ABORTED|phase 'cycle 2/2: review' (model: opus) started, nothing followed
run-2026-09-02_134705.log|OK|shipped 2/2
run-2026-09-03_134704.log|PARTIAL|shipped 1/2"
m1_got="$(run_log_manifest "$LOGS" "2026-08-29" "$CURRENT_LOG" 2>/dev/null)"
m1_rc=$?
assert_eq "M1" "$m1_want|0" "$m1_got|$m1_rc" \
  "a dated window prints one classified line per in-window log, and nothing else"

# The 2026-08-29 silent death is the incident this whole file exists for: the
# manifest must carry the shape the agent doc's old instructions got wrong.
assert_contains "M1a" "nothing followed" "$m1_got" \
  "the silent-death shape survives into the manifest"

m2_want="run-2026-07-29_221707_fixcycle.log|OK|shipped 2/2
$m1_want"
m2_got="$(run_log_manifest "$LOGS" "ALL" "$CURRENT_LOG" 2>/dev/null)"
m2_rc=$?
assert_eq "M2" "$m2_want|0" "$m2_got|$m2_rc" \
  "an ALL window adds the older tagged log and still excludes the current run"

# No new classification logic: every line's tail must be byte-for-byte what
# classify_run_log printed for that file. This is the invariant the manifest's
# whole coupling story rests on.
m3_bad=""
while IFS= read -r m3_line; do
  [ -n "$m3_line" ] || continue
  m3_base="${m3_line%%|*}"
  m3_tail="${m3_line#*|}"
  m3_direct="$(classify_run_log "$LOGS/$m3_base" 2>/dev/null)"
  [ "$m3_tail" = "$m3_direct" ] || m3_bad="$m3_bad $m3_base"
done <<M3EOF
$m2_got
M3EOF
assert_eq "M3" "" "$m3_bad" \
  "every manifest line's state and reason is exactly classify_run_log's own output"

m4_order="$(printf '%s\n' "$m2_got" | cut -d'|' -f1 | tr '\n' ' ')"
assert_eq "M4" \
  "run-2026-07-29_221707_fixcycle.log run-2026-08-29_114701.log run-2026-09-02_134705.log run-2026-09-03_134704.log " \
  "$m4_order" "lines are sorted by filename, i.e. chronologically, oldest first"

# The exclusion must survive a relative-vs-absolute spelling mismatch on either
# side — run.sh passes `logs/run-$TS.log` and could equally pass a full path.
m5_rel_dir="$( cd "$WORK" && run_log_manifest "logs" "ALL" "$CURRENT_LOG" 2>/dev/null )"
assert_eq "M5" "$m2_want" "$m5_rel_dir" \
  "a relative logs dir with an absolute exclude path still excludes the current run"
m5_rel_ex="$( cd "$WORK" && run_log_manifest "$LOGS" "ALL" "logs/run-2026-09-20_020002.log" 2>/dev/null )"
assert_eq "M5a" "$m2_want" "$m5_rel_ex" \
  "an absolute logs dir with a relative exclude path still excludes the current run"

# The counter-case, so M1/M2/M5 are not passing for some other reason: with
# nothing excluded, the current run's log IS listed.
m6_got="$(run_log_manifest "$LOGS" "ALL" "" 2>/dev/null)"
m6_rc=$?
assert_contains "M6" "run-2026-09-20_020002.log|OK|shipped 2/2" "$m6_got" \
  "an empty exclude path excludes nothing, proving the exclusion is what removed it"
assert_eq "M6a" "4|0" "$(printf '%s\n' "$m2_got" | grep -c . )|$m6_rc" \
  "the excluded manifest is exactly one line shorter than the unexcluded one"
assert_eq "M6b" "5" "$(printf '%s\n' "$m6_got" | grep -c . )" \
  "the unexcluded manifest lists all five run logs"

# An empty window is a result, not a failure.
m7_got="$(run_log_manifest "$LOGS" "2026-12-01" "$CURRENT_LOG" 2>/dev/null)"
m7_rc=$?
assert_eq "M7" "|0" "$m7_got|$m7_rc" \
  "a window no log falls into prints nothing and succeeds"

# ... but a directory that could not be listed must NOT look like an empty
# window. This is the distinction the whole return code exists for.
m8_got="$(run_log_manifest "$WORK/nope" "ALL" "" 2>/dev/null)"
m8_rc=$?
m8_err="$(run_log_manifest "$WORK/nope" "ALL" "" 2>&1 >/dev/null)"
assert_eq "M8" "|1" "$m8_got|$m8_rc" \
  "a missing logs dir returns 1 with nothing on stdout, not an empty manifest"
if [ -n "$m8_err" ]; then
  pass "M8a" "a missing logs dir says so on stderr"
else
  fail "M8a" "a missing logs dir was silent"
fi

m9_got="$(run_log_manifest "$OK_LOG" "ALL" "" 2>/dev/null)"
m9_rc=$?
assert_eq "M9" "|1" "$m9_got|$m9_rc" "a file path as the logs dir returns 1"

# Readable but not searchable: the glob would expand to nothing and an empty
# manifest would claim the window was checked. Root bypasses permission bits,
# so this only means anything as a normal user (same guard as E2).
if [ "$(id -u)" -ne 0 ]; then
  M10_DIR="$WORK/logs-noperm"
  mkdir -p "$M10_DIR"
  cp "$OK_LOG" "$M10_DIR/run-2026-09-06_000000.log"
  chmod 000 "$M10_DIR"
  m10_got="$(run_log_manifest "$M10_DIR" "ALL" "" 2>/dev/null)"
  m10_rc=$?
  chmod 755 "$M10_DIR"
  assert_eq "M10" "|1" "$m10_got|$m10_rc" \
    "an unsearchable logs dir returns 1, not an empty manifest"
else
  echo "SKIP  M10: running as root, permission bits are unenforceable"
fi

# A file the classifier cannot read gets its own line rather than vanishing,
# and an undated name gets its own line too. Both are "the manifest could not
# judge this one — open it yourself", which is information; a short manifest is
# not.
if [ "$(id -u)" -ne 0 ]; then
  m11_want="run-2026-09-04_000000.log|UNREADABLE|-
run-2026-09-05_000000.log|OK|shipped 2/2
run-notes.log|UNDATED|-"
  m11_got="$(run_log_manifest "$LOGS_ODD" "2026-09-01" "" 2>/dev/null)"
  m11_rc=$?
  assert_eq "M11" "$m11_want|0" "$m11_got|$m11_rc" \
    "an unreadable file and an undated name each get their own line, not a drop"
else
  echo "SKIP  M11: running as root, permission bits are unenforceable"
fi

# Under ALL there is no date to judge, so the undated name is classified like
# any other log — the same precedence W9 pins for the window function.
m12_got="$(run_log_manifest "$LOGS_ODD" "ALL" "" 2>/dev/null)"
assert_contains "M12" "run-notes.log|OK|shipped 2/2" "$m12_got" \
  "an undated name is classified normally in an ALL window"
case "$m12_got" in
  *UNDATED*) fail "M12a" "an ALL window still reported a name as UNDATED" ;;
  *)         pass "M12a" "an ALL window reports nothing as UNDATED" ;;
esac

# Wrong arity is a caller bug, reported, never guessed at.
for m13_args in 0 2 4; do
  case "$m13_args" in
    0) m13_got="$(run_log_manifest 2>/dev/null)" ;;
    2) m13_got="$(run_log_manifest "$LOGS" "ALL" 2>/dev/null)" ;;
    4) m13_got="$(run_log_manifest "$LOGS" "ALL" "" "extra" 2>/dev/null)" ;;
  esac
  m13_rc=$?
  assert_eq "M13-$m13_args" "|1" "$m13_got|$m13_rc" \
    "$m13_args arguments returns 1 with nothing on stdout"
done

m14_got="$(run_log_manifest "$LOGS" "yesterday" "" 2>/dev/null)"
m14_rc=$?
m14_err="$(run_log_manifest "$LOGS" "yesterday" "" 2>&1 >/dev/null)"
assert_eq "M14" "|1" "$m14_got|$m14_rc" \
  "a malformed cutoff returns 1 with nothing on stdout"
if [ -n "$m14_err" ]; then
  pass "M14a" "a malformed cutoff says so on stderr"
else
  fail "M14a" "a malformed cutoff was silent"
fi

# logs/ holds the observer's own reports, the launchd logs and two .md
# snapshots. None of them is a run log.
m15_bad=""
for m15_n in lab-pipeline last-pipeline-health launchd; do
  case "$m6_got" in
    *"$m15_n"*) m15_bad="$m15_bad $m15_n" ;;
  esac
done
assert_eq "M15" "" "$m15_bad" "non-run-log files in the same directory are ignored"

# A reason can contain the delimiter (E9). The consumer splits on the first two
# only, so basename and state must survive and the reason must arrive intact.
M16_DIR="$WORK/logs-pipe"
mkdir -p "$M16_DIR"
cp "$E9_LOG" "$M16_DIR/run-2026-09-07_000000.log"
m16_line="$(run_log_manifest "$M16_DIR" "2026-09-07" "" 2>/dev/null)"
m16_base="${m16_line%%|*}"
m16_rest="${m16_line#*|}"
m16_state="${m16_rest%%|*}"
m16_reason="${m16_rest#*|}"
assert_eq "M16" "run-2026-09-07_000000.log|ABORTED" "$m16_base|$m16_state" \
  "basename and state are fields one and two even when the reason contains a delimiter"
assert_contains "M16a" "| phase | model | outcome |" "$m16_reason" \
  "the delimiter-bearing reason arrives intact"

# An existing directory with no run logs at all: the unmatched glob must not
# become a phantom line (bash 3.2 leaves it literal, and this file will not set
# nullglob on its caller's shell).
M17_DIR="$WORK/logs-empty"
mkdir -p "$M17_DIR"
m17_got="$(run_log_manifest "$M17_DIR" "ALL" "" 2>/dev/null)"
m17_rc=$?
assert_eq "M17" "|0" "$m17_got|$m17_rc" \
  "a logs dir with no run logs prints nothing and succeeds"

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

# --- R2..R4: the wiring in run.sh -------------------------------------------
#
# Every case above is dead code if run.sh never sources this library or never
# calls the manifest, and nothing else in either suite would notice — which is
# exactly what happened to this file between 2026-09-17 and 2026-09-20 (see
# knowledge/pipeline-run-log-shapes.md, "Not yet wired in"). These three cases
# live here rather than in test_gates.sh on purpose: run_log.sh's own tests are
# this file, so the pin belongs next to the thing it protects instead of adding
# a ninth lib's wording to test_gates.sh. Same brittleness caveat as
# test_gates.sh's own run.sh call-site cases, and the same justification: the
# wiring IS the behaviour here.
RUN_SH="$REPO/.pipeline/run.sh"

assert_eq "R2" "1" "$(grep -c '^for lib in .*run_log' "$RUN_SH" || true)" \
  "run.sh sources run_log.sh in its library loop"

mf_ln="$(grep -n 'run_log_manifest ' "$RUN_SH" | head -1 | cut -d: -f1)"
obs_ln="$(grep -n 'run_phase sonnet "pipeline observer"' "$RUN_SH" | head -1 | cut -d: -f1)"
if [ -z "$mf_ln" ] || [ -z "$obs_ln" ]; then
  fail "R3" "run.sh is missing a call site (manifest=${mf_ln:-none}, observer phase=${obs_ln:-none})"
elif [ "$mf_ln" -lt "$obs_ln" ]; then
  pass "R3" "run.sh builds the manifest (line $mf_ln) before the pipeline-observer phase (line $obs_ln)"
else
  fail "R3" "the manifest is built AFTER the observer phase: manifest=$mf_ln, observer=$obs_ln"
fi

# The prompt this increment edits also carries the observer's prohibitions.
# Adding information to it must never cost that sentence.
# Scoped to the observer's own prompt line, not counted across the file: the
# health phase prompt above it carries the same sentence, and a count would
# pass while the observer's copy was gone.
obs_prompt="$(grep -F 'agentlab-pipeline-observer subagent' "$RUN_SH" | head -1)"
case "$obs_prompt" in
  *"must not modify anything under examples/"*)
    pass "R4" "the observer phase prompt still forbids touching examples/ and its siblings" ;;
  *)
    fail "R4" "the observer phase prompt lost its hard-rule sentence" ;;
esac

# --- Summary ---------------------------------------------------------------

echo ""
echo "=== summary: $PASSED passed, $FAILED failed ==="
[ "$FAILED" -eq 0 ] || exit 1
exit 0
