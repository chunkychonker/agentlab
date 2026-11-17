#!/bin/bash
# Offline tests for .pipeline/verify.sh, the deterministic test run between
# build and review. Run: bash .pipeline/test_verify.sh
#
# No model, no network. Runs real `git`, only inside a throwaway repo under a
# temp dir, and real child processes (`true`, `sleep`, `sh -c`) for the
# runner cases: the behaviour under test is the exit code and the kill, and a
# stub would only test the stub.
#
# bash 3.2 only, like everything else in .pipeline/.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=verify.sh
. "$REPO/.pipeline/verify.sh"

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
# eq <name> <expected> <actual>
eq () { if [ "$2" == "$3" ]; then ok "$1"; else bad "$1 — expected '$2', got '$3'"; fi; }

T="$(mktemp -d "${TMPDIR:-/tmp}/agentlab-verify-test.XXXXXX")" || exit 1
trap 'rm -rf "$T"' EXIT
TAB="$(printf '\t')"

# --- verify_plan (B1) --------------------------------------------------------
cat > "$T/conf" <<'CONF'
# a comment, then a blank line

projects/demo/ | demo unit tests | cd projects/demo && run-tests
.pipeline/ | pipeline suites | for t in a b; do x "$t" || exit 1; done | cat
CONF
printf '%s\n' "projects/demo/src/a.py" "projects/demo/tests/t.py" "research/note.md" > "$T/changed-demo"
printf '%s\n' ".pipeline/run.sh" "projects/demo/PLAN.md" > "$T/changed-both"
printf '%s\n' "research/note.md" "docs/projects/demo/x" "xprojects/demo/y" > "$T/changed-none"

eq "P1 a matching prefix plans its rule as label<TAB>command" \
  "demo unit tests${TAB}cd projects/demo && run-tests" "$(verify_plan "$T/conf" "$T/changed-demo")"
eq "P2 a rule is planned once however many paths match" "1" "$(verify_plan "$T/conf" "$T/changed-demo" | wc -l | tr -d ' ')"
eq "P3 two matching rules come out in conf order" "demo unit tests|pipeline suites" \
  "$(verify_plan "$T/conf" "$T/changed-both" | cut -f1 | tr '\n' '|' | sed 's/|$//')"
eq "P4 a command keeps its own pipes and ||" 'for t in a b; do x "$t" || exit 1; done | cat' \
  "$(verify_plan "$T/conf" "$T/changed-both" | sed -n 2p | cut -f2)"
out="$(verify_plan "$T/conf" "$T/changed-none")"; rc=$?
eq "P5 no match plans nothing and is not an error; a prefix matches only at the start of a path" "0|" "$rc|$out"
: > "$T/changed-empty"
out="$(verify_plan "$T/conf" "$T/changed-empty")"; rc=$?
eq "P6 no changed paths plans nothing" "0|" "$rc|$out"

printf '%s\n' "projects/demo/ | demo unit tests | run-tests" "projects/demo/ | only two fields" > "$T/conf-bad"
out="$(verify_plan "$T/conf-bad" "$T/changed-demo" 2>/dev/null)"; rc=$?
eq "P7 one malformed line fails the whole plan, printing no rule (fail closed)" "2|" "$rc|$out"
printf '%s\n' "projects/demo/ |  | run-tests" > "$T/conf-empty-field"
out="$(verify_plan "$T/conf-empty-field" "$T/changed-demo" 2>/dev/null)"; rc=$?
eq "P8 an empty field is malformed" "2|" "$rc|$out"
out="$(verify_plan "$T/no-such-conf" "$T/changed-demo" 2>/dev/null)"; rc=$?
eq "P9 a missing conf is an error, not an empty plan" "2|" "$rc|$out"
out="$(verify_plan "$T/conf" "$T/no-such-list" 2>/dev/null)"; rc=$?
eq "P10 a missing changed-paths file is an error" "2|" "$rc|$out"

# --- verify_run (B2) ---------------------------------------------------------
cd "$T" || exit 1
: > "$T/report"
out="$(verify_run "quiet pass" "echo lots of output; echo more" "$T/report" 10)"; rc=$?
eq "R1 success is one line and its output is swallowed" "0|✓ quiet pass" "$rc|$out"
eq "R1b ...and nothing is added to the report" "0" "$(wc -c < "$T/report" | tr -d ' ')"

out="$(verify_run "loud fail" "echo first; echo the assertion that failed; exit 3" "$T/report" 10)"; rc=$?
eq "R2 failure is one line naming the rc, and the rc is the command's" "3|✗ loud fail (rc 3)" "$rc|$out"
eq "R2b the report names the label, the command, the rc and the output" "1|1|1|1" \
  "$(grep -c 'loud fail' "$T/report")|$(grep -cF 'echo first; echo the assertion that failed; exit 3' "$T/report")|$(grep -c 'exit code: 3' "$T/report")|$(grep -cx 'the assertion that failed' "$T/report")"

: > "$T/report"
verify_run "long fail" "i=1; while [ \$i -le 300 ]; do echo line-\$i; i=\$((i+1)); done; exit 1" "$T/report" 10 >/dev/null
eq "R3 the report keeps the last VERIFY_REPORT_TAIL_LINES lines and says so" "1|0|1" \
  "$(grep -cx 'line-300' "$T/report")|$(grep -cx 'line-100' "$T/report")|$(grep -c "last $VERIFY_REPORT_TAIL_LINES of 300 lines" "$T/report")"
eq "R3b the tail length is the named constant" "200" "$VERIFY_REPORT_TAIL_LINES"

: > "$T/report"
start="$(date +%s)"
out="$(verify_run "hung" "sh -c 'sleep 60' & echo \$! > '$T/child'; wait" "$T/report" 1)"; rc=$?
took=$(( $(date +%s) - start ))
eq "R4 a command still running at the timeout is a failure with rc 124" "124|✗ hung (rc 124)" "$rc|$out"
eq "R4b ...within a few seconds of the limit" "yes" "$( [ "$took" -le 8 ] && echo yes || echo "${took}s" )"
sleep 3
eq "R4c ...and its child process is gone too" "gone" "$(kill -0 "$(cat "$T/child")" 2>/dev/null && echo alive || echo gone)"
eq "R4d the report says it timed out" "1" "$(grep -c 'timed out after 1s' "$T/report")"

# --- verify_increment (B3) ---------------------------------------------------
# A throwaway repo with one tracked file per area and a conf of its own.
BOX="$T/box"
mkdir -p "$BOX/projects/demo" "$BOX/other" "$BOX/logs" "$BOX/.pipeline"
(
  cd "$BOX" || exit 1
  git init -q . && git config user.email t@example.invalid && git config user.name t
  echo one > projects/demo/a.txt; echo one > other/b.txt; echo "logs/" > .gitignore
  git add -A && git commit -q -m init
) || { echo "FAIL could not build the throwaway repo"; exit 1; }
# vi <conf-body>: run verify_increment in the box with that conf; prints word|rc.
vi () {
  ( cd "$BOX" || exit 99
    printf '%b' "$1" > .pipeline/verify.conf
    VERIFY_CONF=".pipeline/verify.conf" VERIFY_REPORT_FILE="logs/last-verify.md" VERIFY_TIMEOUT_S=10
    word="$(verify_increment "$BOX/log")"; echo "$word|$?" )
}
GOOD='projects/demo/ | demo tests | test -f projects/demo/a.txt\n'
BADT='projects/demo/ | demo tests | echo assertion failed here; exit 1\n'

git -C "$BOX" status --porcelain | grep -q . && bad "I0 the box should start clean" || ok "I0 the box starts clean (the conf is untracked noise outside every rule)"
eq "I1 nothing changed under any rule: NONE" "NONE|0" "$(vi "$GOOD")"
echo two > "$BOX/other/b.txt"
eq "I2 a change outside every rule: NONE" "NONE|0" "$(vi "$GOOD")"
echo two > "$BOX/projects/demo/a.txt"
eq "I3 a tracked change under a rule whose command succeeds: PASS" "PASS|0" "$(vi "$GOOD")"
eq "I3b ...and the log has the one-line result" "1" "$(grep -c '✓ demo tests' "$BOX/log")"
eq "I4 a failing command: FAIL" "FAIL|1" "$(vi "$BADT")"
eq "I4b ...with the output in the report" "1" "$(grep -cx 'assertion failed here' "$BOX/logs/last-verify.md")"
eq "I5 a PASS after a FAIL leaves no stale report" "PASS|0|gone" "$(vi "$GOOD")|$( [ -e "$BOX/logs/last-verify.md" ] && echo present || echo gone )"
git -C "$BOX" checkout -q -- projects/demo/a.txt
echo new > "$BOX/projects/demo/untracked.txt"
eq "I6 an untracked new file under a rule counts as a change" "FAIL|1" "$(vi "$BADT")"
eq "I7 two rules, the second failing: FAIL, and both ran" "FAIL|1|1" \
  "$(vi "projects/demo/ | first | true\nprojects/ | second | false\n")|$(grep -c '✓ first' "$BOX/log" | sed 's/[2-9]/1/')"
eq "I8 a malformed conf: ERROR, never PASS" "ERROR|2" "$(vi 'projects/demo/ | no command\n')"
rm -f "$BOX/.pipeline/verify.conf"
eq "I9 a missing conf: ERROR" "ERROR|2" \
  "$( cd "$BOX" && VERIFY_CONF=".pipeline/verify.conf" VERIFY_REPORT_FILE="logs/last-verify.md" && w="$(verify_increment "$BOX/log")"; echo "$w|$?" )"
eq "I10 outside a git repo: ERROR" "ERROR|2" \
  "$( mkdir -p "$T/nogit/logs" && cd "$T/nogit" && printf '%b' "$GOOD" > conf && VERIFY_CONF=conf VERIFY_REPORT_FILE=logs/v.md && w="$(GIT_CEILING_DIRECTORIES="$T" verify_increment "$T/nogit/log")"; echo "$w|$?" )"

# --- the shipped conf (B5) ---------------------------------------------------
printf '%s\n' "projects/nightloop/nightloop/cli.py" > "$T/changed-nl"
printf '%s\n' ".claude/hooks/publish_guard.py" > "$T/changed-hook"
printf '%s\n' "examples/foo/main.py" "research/x.md" "BACKLOG.md" > "$T/changed-demo-mode"
eq "C1 the shipped conf plans nightloop's tests for a nightloop change" "1" \
  "$(verify_plan "$REPO/.pipeline/verify.conf" "$T/changed-nl" | grep -c 'unittest')"
eq "C2 ...the pipeline suites for a hook change" "1" \
  "$(verify_plan "$REPO/.pipeline/verify.conf" "$T/changed-hook" | grep -c 'test_\*\.sh')"
out="$(verify_plan "$REPO/.pipeline/verify.conf" "$T/changed-demo-mode")"; rc=$?
eq "C3 ...and nothing for a demo-mode example (no common test command)" "0|" "$rc|$out"

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
