#!/bin/bash
# Offline self-test for .pipeline/backlog.sh and the two call sites it has in
# .pipeline/run.sh. No network, no ANTHROPIC_API_KEY, no git, no `claude` — all
# it touches is a throwaway temp dir plus read-only greps of run.sh.
#
# On-demand only. It lives outside examples/, so the every-3rd-night lab health
# check does not pick it up. Run it by hand after editing run.sh or backlog.sh:
#
#   bash .pipeline/test_backlog.sh
#
# One line per case; exits 0 iff every case passes — same shape as
# eval/run_reviewer_eval.sh. Case ids (C1..C14, INV1) are the acceptance
# criteria in research/2026-08-12-backlog-replenish-ordering.md.
#
# bash 3.2 only, like everything else in .pipeline/ — see
# knowledge/bash-3.2-testable-scripts.md.

set -uo pipefail

REPO="/Users/steeb/agentlab"
cd "$REPO" || { echo "cannot cd $REPO"; exit 1; }

LIB="$REPO/.pipeline/backlog.sh"
RUN_SH="$REPO/.pipeline/run.sh"
SELF="$REPO/.pipeline/test_backlog.sh"

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

WORK="$(mktemp -d "${TMPDIR:-/tmp}/agentlab-backlog-test.XXXXXX")" || exit 1
trap 'chmod -R u+rwX "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

# A fixture in the shape of the real BACKLOG.md: <n> column-0 unclaimed items
# plus every marker form that must NOT count.
write_fixture () {   # <path> <unclaimed-count>
  local path="$1" n="$2" i=1
  {
    echo "# BACKLOG"
    echo ""
    echo "## Agent engineering"
    echo "- [done #4] a shipped item"
    echo "- [researching] claimed by tonight's cycle 1"
    echo "- [building] claimed by tonight's cycle 2"
    echo "  - [ ] an indented sub-bullet, not a top-level entry"
    echo "  continuation text belonging to the item above"
    echo "-[ ] malformed, no space after the dash"
    echo "- [ ]"
  } > "$path"
  while [ "$i" -le "$n" ]; do
    echo "- [ ] genuinely unclaimed item $i" >> "$path"
    i=$(( i + 1 ))
  done
}

# --- Injected fakes --------------------------------------------------------
#
# Criterion 14: these APPEND TO THE REAL FIXTURE and record every invocation
# (one line per call, holding the target they were passed) in a counter file the
# assertions read back off disk. Nothing below asserts on a variable the test
# itself set — backlog_ensure_stocked has to re-read the fixture to reach its
# verdict, so idempotence is exercised rather than declared.

FAKE_FIXTURE=""   # the file the fakes append to
FAKE_CALLS=""     # the invocation record the assertions read back

fake_appends_target () {   # <target> — a well-behaved agent: appends <target> items
  local target="$1" i=1
  echo "$target" >> "$FAKE_CALLS"
  while [ "$i" -le "$target" ]; do
    echo "- [ ] appended by the fake replenish action ($i)" >> "$FAKE_FIXTURE"
    i=$(( i + 1 ))
  done
}

fake_appends_one () {      # <target> — under-delivers: one item, whatever was asked
  echo "$1" >> "$FAKE_CALLS"
  echo "- [ ] the only item the fake managed to write" >> "$FAKE_FIXTURE"
}

fake_fails () {            # <target> — the phase died (tonight's 529)
  echo "$1" >> "$FAKE_CALLS"
  return 1
}

call_count () { wc -l < "$FAKE_CALLS" | tr -d ' '; }

# --- INV1: sourcing the lib is silent and inert ----------------------------
# Run in a child shell so a stray side effect cannot hide behind state this
# script already has.

source_output="$(/bin/bash -c "set -uo pipefail; . '$LIB'" 2>&1)"
source_rc=$?
assert_eq "INV1" "0|" "$source_rc|$source_output" \
  "sourcing backlog.sh exits 0 and prints nothing"

. "$LIB"

# --- C1: counts only column-0 '- [ ] ' lines -------------------------------

MIXED="$WORK/mixed.md"
write_fixture "$MIXED" 2
assert_eq "C1" "2" "$(backlog_count_unclaimed "$MIXED")" \
  "mixed fixture counts only column-0 '- [ ] ' items"

# --- C2: zero matches is 0, not an error -----------------------------------

EMPTY="$WORK/empty.md"
write_fixture "$EMPTY" 0
c2_out="$(backlog_count_unclaimed "$EMPTY")"
c2_rc=$?
assert_eq "C2" "0|0" "$c2_out|$c2_rc" \
  "backlog with no unclaimed items prints 0 and exits 0"

# --- C3: missing path fails, with a clean stdout ---------------------------

c3_out="$(backlog_count_unclaimed "$WORK/does-not-exist.md" 2>/dev/null)"
c3_rc=$?
if [ "$c3_rc" -ne 0 ] && [ -z "$c3_out" ]; then
  pass "C3" "missing path exits non-zero ($c3_rc) with empty stdout"
else
  fail "C3" "missing path should exit non-zero with empty stdout — got rc=$c3_rc, stdout='$c3_out'"
fi

# --- C4: target is three nights' worth -------------------------------------

assert_eq "C4" "6|3" "$(backlog_replenish_target 2)|$(backlog_replenish_target 1)" \
  "replenish target is CYCLES * 3"

# --- C5: the short-of-a-night predicate ------------------------------------

backlog_should_replenish 1 2; c5a=$?
backlog_should_replenish 2 2; c5b=$?
backlog_should_replenish 0 1; c5c=$?
assert_eq "C5" "0|1|0" "$c5a|$c5b|$c5c" \
  "should_replenish: 1<2 yes, 2>=2 no, 0<1 yes"

# --- C6: the gate fires when the backlog is short --------------------------

FAKE_FIXTURE="$WORK/short.md"
FAKE_CALLS="$WORK/short.calls"
write_fixture "$FAKE_FIXTURE" 1
: > "$FAKE_CALLS"

backlog_ensure_stocked "$FAKE_FIXTURE" 2 fake_appends_target
c6_rc=$?
assert_eq "C6" "0|1|6" "$c6_rc|$(call_count)|$(head -1 "$FAKE_CALLS")" \
  "1 unclaimed, cycles=2: action invoked once with target 6, exit 0"

# --- C7: idempotence — a second pass on the now-stocked file does nothing ---
# Same fixture, same counter file, no intervening consumption. The reconciler
# must re-read the 7 items the fake really wrote and decide to do nothing.

backlog_ensure_stocked "$FAKE_FIXTURE" 2 fake_appends_target
c7_rc=$?
assert_eq "C7" "0|1|7" "$c7_rc|$(call_count)|$(backlog_count_unclaimed "$FAKE_FIXTURE")" \
  "second pass invokes the action zero more times and exits 0"

# --- C8: no-op when already stocked ----------------------------------------

FAKE_FIXTURE="$WORK/stocked.md"
FAKE_CALLS="$WORK/stocked.calls"
write_fixture "$FAKE_FIXTURE" 5
: > "$FAKE_CALLS"

backlog_ensure_stocked "$FAKE_FIXTURE" 2 fake_appends_target
c8_rc=$?
assert_eq "C8" "0|0" "$c8_rc|$(call_count)" \
  "5 unclaimed, cycles=2: action never invoked, exit 0"

# --- C9: under-delivery is detected, not assumed away ----------------------

FAKE_FIXTURE="$WORK/underdeliver.md"
FAKE_CALLS="$WORK/underdeliver.calls"
write_fixture "$FAKE_FIXTURE" 0
: > "$FAKE_CALLS"

backlog_ensure_stocked "$FAKE_FIXTURE" 2 fake_appends_one
c9_rc=$?
assert_eq "C9" "1|1|1" "$c9_rc|$(call_count)|$(backlog_count_unclaimed "$FAKE_FIXTURE")" \
  "action appended only 1 item for cycles=2: exit 1 (ran, still short)"

# --- C10: a failed action is surfaced, and does not abort the run ----------
# "Does not abort" is demonstrated structurally: this script has `set -u` but
# not `set -e`, the reconciler returns rather than exits, and the cases below
# this one still run. If it aborted, the summary would never print.

FAKE_FIXTURE="$WORK/actionfails.md"
FAKE_CALLS="$WORK/actionfails.calls"
write_fixture "$FAKE_FIXTURE" 0
: > "$FAKE_CALLS"

backlog_ensure_stocked "$FAKE_FIXTURE" 2 fake_fails
c10_rc=$?
assert_eq "C10" "2|1|0" "$c10_rc|$(call_count)|$(backlog_count_unclaimed "$FAKE_FIXTURE")" \
  "failing action gives exit 2, leaves the backlog untouched, run continues"

# --- C11: unreadable backlog path ------------------------------------------

backlog_ensure_stocked "$WORK/nowhere.md" 2 fake_appends_target 2>/dev/null
c11_rc=$?
assert_eq "C11" "3" "$c11_rc" "missing backlog path gives exit 3"

# A file that exists but cannot be read is the same failure mode. Root bypasses
# permission bits, so this only means anything as a normal user.
if [ "$(id -u)" -ne 0 ]; then
  CHMODDED="$WORK/noperm.md"
  write_fixture "$CHMODDED" 0
  chmod 000 "$CHMODDED"
  backlog_ensure_stocked "$CHMODDED" 2 fake_appends_target 2>/dev/null
  c11b_rc=$?
  chmod 644 "$CHMODDED"
  assert_eq "C11b" "3" "$c11b_rc" "chmod-000 backlog gives exit 3"
else
  echo "SKIP  C11b: running as root, permission bits are unenforceable"
fi

# --- C12: the fix is actually wired into run.sh ----------------------------
# The most brittle assertion here by far: it is grep- and line-number-based, so
# renaming stock_backlog or reflowing the `for` header breaks it even though the
# behaviour is fine. It earns its keep anyway — it is the only check that binds
# a correct library to a correct *ordering* inside run.sh, which is the entire
# point of this change. If it fails, read run.sh before "fixing" the test.

loop_line="$(grep -n 'for (( k=1; k<=CYCLES' "$RUN_SH" | head -1 | cut -d: -f1)"
pre_line="$(grep -n '^stock_backlog "pre-loop"' "$RUN_SH" | head -1 | cut -d: -f1)"
post_line="$(grep -n '^stock_backlog "post-loop"' "$RUN_SH" | head -1 | cut -d: -f1)"

if [ -z "$loop_line" ] || [ -z "$pre_line" ] || [ -z "$post_line" ]; then
  fail "C12" "run.sh is missing the cycle loop or a stock_backlog call site (loop=${loop_line:-none}, pre=${pre_line:-none}, post=${post_line:-none})"
elif [ "$pre_line" -lt "$loop_line" ] && [ "$post_line" -gt "$loop_line" ]; then
  pass "C12" "run.sh reconciles before (line $pre_line) and after (line $post_line) the loop (line $loop_line)"
else
  fail "C12" "call sites are mis-ordered around the loop: pre=$pre_line, loop=$loop_line, post=$post_line"
fi

# --- C13: everything still parses under this box's bash --------------------

syntax_bad=""
for f in "$RUN_SH" "$LIB" "$SELF"; do
  /bin/bash -n "$f" 2>/dev/null || syntax_bad="$syntax_bad $(basename "$f")"
done
assert_eq "C13" "" "$syntax_bad" "bash -n is clean on run.sh, backlog.sh and this test"

# --- C14: covered by construction ------------------------------------------
# Criterion 14 is a property of C6-C10 rather than a separate case: the fakes
# write to the fixture and record calls on disk, and the assertions above read
# both back. Asserted here so a future edit that turns them into bare counters
# trips a named case.

if grep -q 'FAKE_CALLS' "$SELF" \
  && grep -q '>> "\$FAKE_FIXTURE"' "$SELF" \
  && [ -s "$WORK/short.calls" ]; then
  pass "C14" "fakes mutate the real fixture and record invocations to a file the assertions re-read"
else
  fail "C14" "fakes no longer mutate a real fixture / record invocations on disk"
fi

# --- Summary ---------------------------------------------------------------

echo ""
echo "=== summary: $PASSED passed, $FAILED failed ==="
[ "$FAILED" -eq 0 ] || exit 1
exit 0
