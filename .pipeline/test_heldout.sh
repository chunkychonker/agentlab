#!/bin/bash
# Offline tests for .pipeline/heldout.sh: the held-out acceptance-test gate
# (research/2026-11-28-held-out-acceptance-tests.md, criteria 1-4 and 6;
# criterion 5 is in test_phase_tools.sh, and run_cycle's wiring is driven end
# to end in test_gates.sh, cases SWH*). No model, no network, no key. Writes
# only under a temp dir. Needs python3 for the unittest fixtures.
# Run: bash .pipeline/test_heldout.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }
# eq <name> <expected> <actual>
eq () { if [ "$2" == "$3" ]; then ok "$1"; else bad "$1 — expected '$2', got '$3'"; fi; }

T="$(mktemp -d "${TMPDIR:-/tmp}/agentlab-heldout-test.XXXXXX")" || exit 1
trap 'chmod -R u+rwX "$T" 2>/dev/null; rm -rf "$T"' EXIT

noise="$( { . "$REPO/.pipeline/verify.sh"; . "$REPO/.pipeline/heldout.sh"; } 2>&1 )"
eq "L0 sourcing verify.sh and heldout.sh prints nothing" "" "$noise"
. "$REPO/.pipeline/verify.sh"
. "$REPO/.pipeline/heldout.sh"

PY="$(command -v python3)"

# A fingerprint of a tree: every path, its type and mode, and every file's
# bytes. Two trees with the same fingerprint are identical for these tests.
fingerprint () {
  ( cd "$1" && find . -print | LC_ALL=C sort | while IFS= read -r p; do
      if [ -f "$p" ]; then printf '%s %s %s\n' "$p" "$(stat -f %Lp "$p")" "$(shasum -a 256 < "$p" | cut -d' ' -f1)"
      else printf '%s %s dir\n' "$p" "$(stat -f %Lp "$p")"; fi
    done )
}

# new_box <name>: a fresh repo-root stand-in with logs/; prints its path.
new_box () { local b="$T/$1"; mkdir -p "$b/logs"; echo "$b"; }

# A suite whose test fails. The sentinel sits on the failing line, and
# unittest prints that source line in its traceback, so the raw output
# contains it: the report must not.
SENTINEL="SENTINEL_FIXTURE_SOURCE_9c41"
write_failing_suite () {
  mkdir -p "$1/heldout/arith"
  cat > "$1/heldout/arith/test_arith.py" <<EOF
import unittest

class Arith(unittest.TestCase):
    def test_adds_two(self):
        self.assertEqual(2 + 2, 5)  # $SENTINEL

    def test_passes(self):
        self.assertTrue(True)

if __name__ == "__main__":
    unittest.main()
EOF
  # Run as a script: -I leaves the cwd off sys.path, so `-m unittest test_arith`
  # could not import the module.
  printf '%s\n' "cd heldout/arith && \"$PY\" -I test_arith.py" > "$1/heldout/arith/run.sh"
}
write_passing_suite () {
  mkdir -p "$1/heldout/$2"
  printf '%s\n' 'echo "Ran 3 tests"; echo OK; exit 0' > "$1/heldout/$2/run.sh"
}

# --- criterion 1: stash and restore are the identity ---------------------

B="$(new_box stash)"
mkdir -p "$B/heldout/a/nested" "$B/heldout/b"
printf 'one\n' > "$B/heldout/a/run.sh"
printf 'two words\n' > "$B/heldout/a/nested/file with spaces.txt"
: > "$B/heldout/b/empty"
printf '#!/bin/sh\n' > "$B/heldout/b/.hidden"; chmod 750 "$B/heldout/b/.hidden"
before="$(fingerprint "$B/heldout")"
mkdir "$B/root"
stashed="$(cd "$B" && heldout_stash "$B/root")"; rc=$?
eq "C1 stash returns 0" "0" "$rc"
check "C1b after stash, heldout/ is gone from the worktree" [ ! -e "$B/heldout" ]
check "C1c the stashed path is under the stash root" [ "${stashed#"$B/root/"}" != "$stashed" ]
eq "C1d the stashed path holds identical files (bytes, names, modes)" "$before" "$(fingerprint "$stashed")"
( cd "$B" && heldout_restore "$stashed" ); rc=$?
eq "C1e restore returns 0" "0" "$rc"
eq "C1f after restore, heldout/ is identical again" "$before" "$(fingerprint "$B/heldout")"
check "C1g the stash root is empty again" [ -z "$(ls -A "$B/root")" ]

B="$(new_box stash-none)"; mkdir "$B/root"
out="$(cd "$B" && heldout_stash "$B/root")"; rc=$?
eq "C1h no heldout/: stash prints NONE, rc 0" "NONE|0" "$out|$rc"
check "C1i ...and leaves the stash root empty" [ -z "$(ls -A "$B/root")" ]

B="$(new_box stash-twice)"; write_passing_suite "$B" s; mkdir "$B/root"
first="$(cd "$B" && heldout_stash "$B/root")"
write_passing_suite "$B" t
out="$(cd "$B" && heldout_stash "$B/root" 2>/dev/null)"; rc=$?
eq "C1j a second stash into a root that holds one: rc 2, prints nothing" "2|" "$rc|$out"
check "C1k ...and moves nothing" [ -f "$B/heldout/t/run.sh" ] && [ -f "$first/s/run.sh" ]

B="$(new_box stash-noroot)"; write_passing_suite "$B" s
out="$(cd "$B" && heldout_stash "$B/missing" 2>/dev/null)"; rc=$?
eq "C1l a missing stash root: rc 1, prints nothing" "1|" "$rc|$out"
check "C1m ...and heldout/ stays put" [ -f "$B/heldout/s/run.sh" ]

# --- criterion 2: restore never overwrites --------------------------------

B="$(new_box restore-clash)"; write_passing_suite "$B" s; mkdir "$B/root"
stashed="$(cd "$B" && heldout_stash "$B/root")"
stash_fp="$(fingerprint "$stashed")"
mkdir -p "$B/heldout/x"; printf 'the build wrote this\n' > "$B/heldout/x/run.sh"
wt_fp="$(fingerprint "$B/heldout")"
( cd "$B" && heldout_restore "$stashed" 2>/dev/null ); rc=$?
eq "C2 restore onto an existing heldout/ returns 2" "2" "$rc"
eq "C2b ...the worktree's heldout/ is untouched" "$wt_fp" "$(fingerprint "$B/heldout")"
eq "C2c ...and the stash is untouched" "$stash_fp" "$(fingerprint "$stashed")"
ln -s /nonexistent "$B/dangling"
( cd "$B" && HELDOUT_DIR=dangling heldout_restore "$stashed" 2>/dev/null ); rc=$?
eq "C2d a dangling symlink at heldout/ also counts as existing: rc 2" "2" "$rc"
( cd "$B" && rm -rf heldout && heldout_restore "$B/root/nope/t" 2>/dev/null ); rc=$?
eq "C2e restoring a path that does not exist: rc 1" "1" "$rc"

# --- criterion 3: FAIL, and a report with no source -----------------------

B="$(new_box run-fail)"; write_failing_suite "$B"; mkdir "$B/root"
# Sanity: the raw output really does carry the sentinel, so C3c is not vacuous.
raw_has="$(cd "$B" && bash heldout/arith/run.sh 2>&1 | grep -c "$SENTINEL")"
check "C3-pre unittest's own output quotes the sentinel line" [ "$raw_has" -ge 1 ]
word="$(cd "$B" && heldout_run "$B/log" "$B/root")"; rc=$?
eq "C3 a failing suite: FAIL, rc 1" "FAIL|1" "$word|$rc"
rep="$B/logs/last-heldout.md"
check "C3b the report names the failing test" grep -qF "FAIL: test_adds_two" "$rep"
check "C3c the report has the assertion text" grep -qF "AssertionError: 4 != 5" "$rep"
check "C3d the report does not contain the planted source sentinel" [ "$(grep -c "$SENTINEL" "$rep")" -eq 0 ]
check "C3e the report has no traceback or source lines" [ -z "$(grep -E '^[[:space:]]|^Traceback|^  File ' "$rep")" ]
check "C3f the log says which suite failed" grep -qF "✗ held-out: arith" "$B/log"
check "C3g the log does not contain the sentinel either" [ "$(grep -c "$SENTINEL" "$B/log")" -eq 0 ]
check "C3h no raw output is left under the repo" [ -z "$(cd "$B" && find . -newer "$rep" -type f ! -path ./log)" ]

# heldout_report on its own.
printf '%s\n' "  File \"t.py\", line 5" "    x = secret()  # $SENTINEL" "FAIL: test_a (t.T.test_a)" \
  "AssertionError: 1 != 2" "ERROR: test_b (t.T.test_b)" "ValueError: bad" "FAIL test_c via shell" \
  "FAILED (failures=1)" "timed out after 5s and was killed" > "$T/raw1"
out="$(heldout_report suite1 1 "$T/raw1")"; rc=$?
eq "C3i heldout_report keeps exactly the contract's lines" \
  "FAIL: test_a (t.T.test_a)|AssertionError: 1 != 2|ERROR: test_b (t.T.test_b)|ValueError: bad|FAIL test_c via shell|timed out after 5s and was killed" \
  "$(printf '%s\n' "$out" | grep -vE '^##|^\(|^$' | tr '\n' '|' | sed 's/|$//')"
eq "C3j ...returns 0 and heads the section with the suite and rc" "0|## ✗ suite1 (exit code 1)" "$rc|$(printf '%s\n' "$out" | sed -n 1p)"
awk 'BEGIN { for (i = 1; i <= 100; i++) print "FAIL: test_" i }' > "$T/raw2"
out="$(heldout_report many 1 "$T/raw2")"
eq "C3k at most HELDOUT_REPORT_MAX_LINES lines are kept" "$HELDOUT_REPORT_MAX_LINES" "$(printf '%s\n' "$out" | grep -c '^FAIL: ')"
check "C3l ...and the rest are counted, not dropped silently" grep -qF "(40 more matching lines not shown)" <<< "$out"
printf 'FAIL: %s\n' "$(printf 'x%.0s' $(seq 1 1000))" > "$T/raw3"
out="$(heldout_report long 1 "$T/raw3")"
eq "C3m each kept line is cut to HELDOUT_REPORT_MAX_CHARS" "$HELDOUT_REPORT_MAX_CHARS" \
  "$(printf '%s\n' "$out" | grep '^FAIL: ' | awk '{ print length($0) }')"
printf '%s\n' "segfault" "core dumped" > "$T/raw4"
out="$(heldout_report quiet 139 "$T/raw4")"
check "C3n no matching line: says all lines were withheld" grep -qF "all 2 lines withheld" <<< "$out"
out="$(heldout_report gone 1 "$T/no-such-file")"; rc=$?
eq "C3o an unreadable raw file: rc 1, says so" "1|yes" "$rc|$(grep -qF '(output unreadable)' <<< "$out" && echo yes || echo no)"

# Every suite runs, in name order, even after one fails.
B="$(new_box run-order)"; write_failing_suite "$B"; mkdir "$B/root"
mkdir -p "$B/heldout/zz"; printf '%s\n' 'echo ran > zz-ran; exit 0' > "$B/heldout/zz/run.sh"
word="$(cd "$B" && heldout_run "$B/log" "$B/root")"
eq "C3p a later passing suite still runs after an earlier failure" "FAIL|yes" "$word|$( [ -f "$B/zz-ran" ] && echo yes || echo no)"
eq "C3q ...in name order" "arith zz" "$(grep -oE 'held-out: [a-z]+' "$B/log" | sed 's/held-out: //' | tr '\n' ' ' | sed 's/ $//')"
check "C3r ...and only the failing suite is in the report" [ "$(grep -c '^## ✗' "$B/logs/last-heldout.md")" -eq 1 ]

# A timeout is a FAIL, as in verify.sh.
B="$(new_box run-timeout)"; mkdir -p "$B/heldout/slow" "$B/root"
printf '%s\n' 'sleep 30' > "$B/heldout/slow/run.sh"
word="$(cd "$B" && VERIFY_TIMEOUT_S=1 heldout_run "$B/log" "$B/root")"
eq "C3s a suite that outlives VERIFY_TIMEOUT_S is FAIL" "FAIL" "$word"
check "C3t ...and the report says it timed out" grep -qF "timed out after 1s" "$B/logs/last-heldout.md"

# --- criterion 4: PASS, NONE, ERROR ---------------------------------------

B="$(new_box run-pass)"; write_passing_suite "$B" a; write_passing_suite "$B" b; mkdir "$B/root"
printf 'stale\n' > "$B/logs/last-heldout.md"
word="$(cd "$B" && heldout_run "$B/log" "$B/root")"; rc=$?
eq "C4 passing suites: PASS, rc 0" "PASS|0" "$word|$rc"
check "C4b ...and a stale report from an earlier run is removed" [ ! -e "$B/logs/last-heldout.md" ]

B="$(new_box run-none)"; mkdir "$B/root"
word="$(cd "$B" && heldout_run "$B/log" "$B/root")"; rc=$?
eq "C4c no heldout/: NONE, rc 0" "NONE|0" "$word|$rc"
check "C4d ...and the log says the gate checked nothing" grep -qF "checked nothing" "$B/log"

B="$(new_box run-unrestored)"; write_passing_suite "$B" a; mkdir "$B/root"
( cd "$B" && heldout_stash "$B/root" >/dev/null )
word="$(cd "$B" && heldout_run "$B/log" "$B/root")"; rc=$?
eq "C4e a stash not yet restored: ERROR, rc 2 (never NONE or PASS)" "ERROR|2" "$word|$rc"

B="$(new_box run-noroot)"; write_passing_suite "$B" a
eq "C4f a stash root that does not exist: ERROR" "ERROR" "$(cd "$B" && heldout_run "$B/log" "$B/missing")"

if [ "$(id -u)" -eq 0 ]; then
  ok "C4g (skipped: root reads a mode-000 directory anyway)"
else
  B="$(new_box run-unreadable)"; write_passing_suite "$B" a; mkdir "$B/root"
  chmod 000 "$B/heldout"
  eq "C4g an unreadable heldout/: ERROR" "ERROR" "$(cd "$B" && heldout_run "$B/log" "$B/root")"
  chmod 755 "$B/heldout"
  chmod 000 "$B/heldout/a/run.sh"
  eq "C4h an unreadable run.sh: ERROR (not a FAIL the builder could never fix)" "ERROR" "$(cd "$B" && heldout_run "$B/log" "$B/root")"
  chmod 644 "$B/heldout/a/run.sh"
fi

B="$(new_box run-empty)"; mkdir -p "$B/heldout" "$B/root"
eq "C4i an empty heldout/: ERROR" "ERROR" "$(cd "$B" && heldout_run "$B/log" "$B/root")"
B="$(new_box run-norunsh)"; write_passing_suite "$B" a; mkdir -p "$B/heldout/b" "$B/root"; : > "$B/heldout/b/test_x.py"
eq "C4j a suite with no run.sh: ERROR" "ERROR" "$(cd "$B" && heldout_run "$B/log" "$B/root")"
B="$(new_box run-badname)"; mkdir -p "$B/heldout/bad name" "$B/root"; echo 'exit 0' > "$B/heldout/bad name/run.sh"
eq "C4k a suite name with a space: ERROR" "ERROR" "$(cd "$B" && heldout_run "$B/log" "$B/root")"
B="$(new_box run-file)"; mkdir "$B/root"; : > "$B/heldout"
eq "C4l heldout is a file: ERROR" "ERROR" "$(cd "$B" && heldout_run "$B/log" "$B/root")"
B="$(new_box run-link)"; write_passing_suite "$B" a; mv "$B/heldout" "$B/real"; ln -s "$B/real" "$B/heldout"; mkdir "$B/root"
eq "C4m heldout is a symlink: ERROR" "ERROR" "$(cd "$B" && heldout_run "$B/log" "$B/root")"
B="$(new_box run-noverify)"; write_passing_suite "$B" a; mkdir "$B/root"
eq "C4n verify_run not defined: ERROR" "ERROR" "$(cd "$B" && unset -f verify_run && heldout_run "$B/log" "$B/root")"

# --- heldout_hidden: the wrapper run.sh uses around build and fix ---------

B="$(new_box hidden)"; write_failing_suite "$B"; mkdir "$B/root"
before="$(fingerprint "$B/heldout")"
probe () { [ -e heldout ] && echo visible > seen || echo hidden > seen; ls -A "$1" > root-during; return 7; }
( cd "$B" && heldout_hidden "$B/root" "$B/log" probe "$B/root" ); rc=$?
eq "H1 heldout_hidden returns the command's rc" "7" "$rc"
eq "H1b heldout/ is absent while the command runs" "hidden" "$(cat "$B/seen")"
eq "H1c ...and identical afterwards, though the command failed" "$before" "$(fingerprint "$B/heldout")"
stash_name="$(cat "$B/root-during")"
check "H1d the log never names where the tests were" [ -n "$stash_name" ] && [ "$(grep -cF "$stash_name" "$B/log")" -eq 0 ]

B="$(new_box hidden-clash)"; write_passing_suite "$B" a; mkdir "$B/root"
writer () { mkdir -p heldout/mine && echo 'exit 0' > heldout/mine/run.sh; }
( cd "$B" && heldout_hidden "$B/root" "$B/log" writer ); rc=$?
eq "H2 a command that writes heldout/ itself: rc HELDOUT_RESTORE_RC" "$HELDOUT_RESTORE_RC" "$rc"
check "H2b ...its heldout/ is left as it wrote it" [ -f "$B/heldout/mine/run.sh" ] && [ ! -e "$B/heldout/a" ]
check "H2c ...and the real tests stay in the stash, not lost" [ -n "$(find "$B/root" -name run.sh)" ]
failing_writer () { writer; return 75; }
rm -rf "$B/heldout"; mv "$(find "$B/root" -mindepth 2 -maxdepth 2 -name t)" "$B/heldout"; rm -rf "$B/root"/*
( cd "$B" && heldout_hidden "$B/root" "$B/log" failing_writer ); rc=$?
eq "H2d ...but a command's own failure (a usage limit) wins over the restore error" "75" "$rc"

B="$(new_box hidden-blocked)"; write_passing_suite "$B" a; mkdir -p "$B/root/leftover"
ran () { echo yes > ran; }
( cd "$B" && heldout_hidden "$B/root" "$B/log" ran ); rc=$?
eq "H3 a stash that cannot be made: rc 1 and the command never runs" "1|no" "$rc|$( [ -f "$B/ran" ] && echo yes || echo no)"

B="$(new_box hidden-none)"; mkdir "$B/root"
( cd "$B" && heldout_hidden "$B/root" "$B/log" ran ); rc=$?
eq "H4 no heldout/: the command runs as usual" "0|yes" "$rc|$( [ -f "$B/ran" ] && echo yes || echo no)"

# --- criterion 6: the combined gate, with injected steps ------------------
# Each fake step records that it ran (and with which arguments) in $CALLS,
# then prints the word it was given.

CALLS="$T/calls"
fake_verify ()  { echo "verify $*" >> "$CALLS"; echo "$FAKE_V"; }
fake_heldout () { echo "heldout $*" >> "$CALLS"; echo "$FAKE_H"; }
gate () {
  : > "$CALLS"
  local out rc
  out="$(FAKE_V="$1" FAKE_H="$2" heldout_gate "$T/glog" "$T/groot" fake_verify fake_heldout)"; rc=$?
  echo "$out|$rc"
}
eq "G1 own suite PASS, held-out FAIL: FIX_HELDOUT, never REVIEW (criterion 6)" "FIX_HELDOUT PASS FAIL|1" "$(gate PASS FAIL)"
eq "G1b ...and both steps ran, verify first" "verify $T/glog|heldout $T/glog $T/groot" "$(tr '\n' '|' < "$CALLS" | sed 's/|$//')"
eq "G2 own suite NONE, held-out FAIL: FIX_HELDOUT" "FIX_HELDOUT NONE FAIL|1" "$(gate NONE FAIL)"
eq "G3 own suite FAIL: FIX_VERIFY" "FIX_VERIFY FAIL SKIPPED|1" "$(gate FAIL PASS)"
eq "G3b ...and the held-out step never ran" "verify $T/glog" "$(cat "$CALLS")"
eq "G4 PASS + PASS: REVIEW" "REVIEW PASS PASS|0" "$(gate PASS PASS)"
eq "G4b NONE + NONE: REVIEW" "REVIEW NONE NONE|0" "$(gate NONE NONE)"
eq "G5 held-out ERROR: STOP" "STOP PASS ERROR|2" "$(gate PASS ERROR)"
eq "G5b verify ERROR: STOP, held-out not run" "STOP ERROR SKIPPED|2|1" "$(gate ERROR PASS)|$(wc -l < "$CALLS" | tr -d ' ')"
eq "G5c an empty held-out word: STOP" "STOP PASS INVALID|2" "$(gate PASS "")"
eq "G5d a multi-word answer cannot split the gate line" "STOP PASS INVALID|2" "$(gate PASS "PASS FAIL")"
eq "G5e a lower-case pass is not a pass" "STOP pass SKIPPED|2" "$(gate pass PASS | sed 's/INVALID/pass/')"

# The real heldout_run as the injected step, against a real failing suite.
B="$(new_box gate-real)"; write_failing_suite "$B"; mkdir "$B/root"
pass_verify () { echo PASS; }
out="$(cd "$B" && heldout_gate "$B/log" "$B/root" pass_verify heldout_run)"; rc=$?
eq "G6 own suite PASS, real held-out suite failing: FIX_HELDOUT" "FIX_HELDOUT PASS FAIL|1" "$out|$rc"

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
