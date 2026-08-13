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

# --- Stranded claims (C15-C23) ---------------------------------------------
#
# Acceptance criteria for the 2026-08-13 stranded-claim fix. The item text below
# is copied verbatim from the real entry the 2026-08-12 failure stranded: it
# carries backticks, parentheses, brackets and a colon, so a key match built
# from a regex rather than a literal comparison fails these cases.

KEY='Server-side compaction (`compact_20260112`, beta `compact-2026-01-12`): the'
BRANCH="cycle/2026-08-12-unshipped-213702-1"

write_claim_fixture () {   # <path> — one claimable item plus decoys
  {
    echo "# BACKLOG"
    echo ""
    echo "## Context & cost"
    echo "- [done #25] Previewing server-side context editing"
    echo "- [ ] $KEY"
    echo "  summarize-don't-prune sibling of context editing. Different response"
    echo "- [ ] an unrelated unclaimed item"
  } > "$1"
}

# --- C15: the claimed line is lifted out of a real-shaped diff -------------

C15_DIFF="$(printf '%s\n' \
  'diff --git a/BACKLOG.md b/BACKLOG.md' \
  '@@ -50,7 +50,7 @@' \
  ' ## Context & cost' \
  "-- [ ] $KEY" \
  "+- [building] $KEY" \
  "   summarize-don't-prune sibling of context editing.")"
assert_eq "C15" "- [building] $KEY" "$(backlog_claimed_line "$C15_DIFF")" \
  "the added claim line is returned without the diff's leading '+'"

# --- C16: a diff with no claim is 'skip', not 'error' ----------------------

c16_out="$(backlog_claimed_line "$(printf '%s\n' '@@ -1 +1 @@' '-old' '+new')")"
c16_rc=$?
assert_eq "C16" "1|" "$c16_rc|$c16_out" \
  "a diff carrying no claim returns 1 with empty stdout"

# --- C17: the key is the item text, and only that --------------------------

assert_eq "C17a" "$KEY" "$(backlog_claim_key "- [building] $KEY")" \
  "backlog_claim_key strips the [building] marker"
assert_eq "C17b" "$KEY" "$(backlog_claim_key "- [researching] $KEY")" \
  "backlog_claim_key strips the [researching] marker"
c17c_out="$(backlog_claim_key "- [done #27] $KEY")"
c17c_rc=$?
assert_eq "C17c" "1|" "$c17c_rc|$c17c_out" \
  "a non-claim marker is rejected rather than mis-parsed as a claim"

# --- C18: the claim is re-applied, and the item leaves the unclaimed count --

STRANDED="$WORK/stranded.md"
write_claim_fixture "$STRANDED"
before_count="$(backlog_count_unclaimed "$STRANDED")"
backlog_apply_stranded "$STRANDED" "$KEY" "$BRANCH"
c18_rc=$?
after_count="$(backlog_count_unclaimed "$STRANDED")"
assert_eq "C18a" "0" "$c18_rc" "re-applying a stranded claim returns 0"
assert_eq "C18b" "2|1" "$before_count|$after_count" \
  "the reconciled item drops out of the unclaimed count"
assert_eq "C18c" "- [stranded $BRANCH] $KEY" \
  "$(grep -F "stranded $BRANCH" "$STRANDED")" \
  "the item now names the branch its work is sitting on"

# --- C19: idempotent — a second pass changes nothing -----------------------

cp "$STRANDED" "$WORK/stranded.before"
backlog_apply_stranded "$STRANDED" "$KEY" "$BRANCH"
c19_rc=$?
if [ "$c19_rc" == "3" ] && diff -q "$WORK/stranded.before" "$STRANDED" >/dev/null; then
  pass "C19" "a second reconcile pass returns 3 and leaves the file byte-identical"
else
  fail "C19" "second pass rc=$c19_rc (want 3) or the file changed"
fi

# --- C20: an item that is no longer there is reported, not invented --------

MISSING="$WORK/missing.md"
write_claim_fixture "$MISSING"
cp "$MISSING" "$WORK/missing.before"
backlog_apply_stranded "$MISSING" "an item nobody ever wrote" "$BRANCH"
c20_rc=$?
if [ "$c20_rc" == "2" ] && diff -q "$WORK/missing.before" "$MISSING" >/dev/null; then
  pass "C20" "an unmatched key returns 2 and leaves the file untouched"
else
  fail "C20" "unmatched key rc=$c20_rc (want 2) or the file changed"
fi

# --- C21: a salvaged item is left alone ------------------------------------
# The 2026-08-13 case: PR #27 shipped the stranded work by hand, so the item
# already reads [done #27]. Re-marking it stranded would be a lie.

SALVAGED="$WORK/salvaged.md"
write_claim_fixture "$SALVAGED"
sed -i '' "s|^- \[ \] $(printf '%s' "$KEY" | sed 's/[[\.*^$/]/\\&/g')|- [done #27] $KEY|" "$SALVAGED" 2>/dev/null
cp "$SALVAGED" "$WORK/salvaged.before"
backlog_apply_stranded "$SALVAGED" "$KEY" "$BRANCH"
c21_rc=$?
if [ "$c21_rc" == "3" ] && diff -q "$WORK/salvaged.before" "$SALVAGED" >/dev/null; then
  pass "C21" "an item already marked [done #N] is left untouched, rc 3"
else
  fail "C21" "salvaged-item rc=$c21_rc (want 3) or the file changed"
fi

# --- C22: an unwritable backlog fails loudly, without a temp file left over -

UNWRITABLE="$WORK/unwritable.md"
write_claim_fixture "$UNWRITABLE"
chmod 444 "$UNWRITABLE"
backlog_apply_stranded "$UNWRITABLE" "$KEY" "$BRANCH" 2>/dev/null
c22_rc=$?
chmod 644 "$UNWRITABLE"
leftovers="$(ls "$WORK" | grep -c 'reconcile\.' || true)"
assert_eq "C22" "1|0" "$c22_rc|$leftovers" \
  "an unwritable backlog returns 1 and leaves no .reconcile temp file behind"

# --- C23: reconcile is wired into run.sh, before the count it changes ------
# Same brittleness caveat as C12, and the same justification: ordering IS the
# fix. Reconciling after stock_backlog would let the night draw an item that is
# already built.

recon_pre="$(grep -n '^reconcile_stranded_claims "pre-loop"' "$RUN_SH" | cut -d: -f1)"
stock_pre="$(grep -n '^stock_backlog "pre-loop"' "$RUN_SH" | cut -d: -f1)"
recon_loop="$(grep -n 'reconcile_stranded_claims "after cycle' "$RUN_SH" | cut -d: -f1)"
if [ -z "$recon_pre" ] || [ -z "$stock_pre" ] || [ -z "$recon_loop" ]; then
  fail "C23" "run.sh is missing a reconcile call site (pre=${recon_pre:-none}, stock=${stock_pre:-none}, in-loop=${recon_loop:-none})"
elif [ "$recon_pre" -lt "$stock_pre" ] && [ "$recon_loop" -gt "$stock_pre" ]; then
  pass "C23" "run.sh reconciles before the pre-loop count (line $recon_pre < $stock_pre) and again inside the loop (line $recon_loop)"
else
  fail "C23" "reconcile is mis-ordered: pre=$recon_pre, stock=$stock_pre, in-loop=$recon_loop"
fi

# --- Summary ---------------------------------------------------------------

echo ""
echo "=== summary: $PASSED passed, $FAILED failed ==="
[ "$FAILED" -eq 0 ] || exit 1
exit 0
