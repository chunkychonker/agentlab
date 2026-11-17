#!/bin/bash
# Offline tests for .pipeline/ci.sh, the "is CI green" half of the auto-merge
# gate, plus its call site in run.sh and the workflow file it waits on.
# Run: bash .pipeline/test_ci.sh
#
# No network: `gh` and `sleep` are shell functions defined here, so ci_wait's
# real loop runs against scripted answers and no time passes.
#
# bash 3.2 only, like everything else in .pipeline/.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=ci.sh
. "$REPO/.pipeline/ci.sh"

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
eq () { if [ "$2" == "$3" ]; then ok "$1"; else bad "$1 — expected '$2', got '$3'"; fi; }

T="$(mktemp -d "${TMPDIR:-/tmp}/agentlab-ci-test.XXXXXX")" || exit 1
trap 'rm -rf "$T"' EXIT

# --- ci_disposition (C2) -----------------------------------------------------
d () { printf '%b' "$1" | ci_disposition; }
eq "D1 every check passed: GREEN" "GREEN" "$(d 'pass\npass\n')"
eq "D2 a skipped check does not block: GREEN" "GREEN" "$(d 'pass\nskipping\n')"
eq "D3 one failure among passes: RED" "RED" "$(d 'pass\nfail\npass\n')"
eq "D4 a cancelled check: RED" "RED" "$(d 'cancel\n')"
eq "D5 still running: PENDING" "PENDING" "$(d 'pass\npending\n')"
eq "D6 red outranks pending (no point waiting for the rest)" "RED" "$(d 'pending\nfail\n')"
eq "D7 no checks at all: NONE, never GREEN" "NONE" "$(d '')"
eq "D8 only skipped checks: NONE (nothing actually ran)" "NONE" "$(d 'skipping\nskipping\n')"
eq "D9 a bucket this code does not know: PENDING, never GREEN" "PENDING" "$(d 'pass\nmystery\n')"
eq "D10 blank lines are ignored" "GREEN" "$(d '\npass\n\n')"
printf 'pass\n' | ci_disposition >/dev/null; eq "D11 the return code is 0 only for GREEN" "0" "$?"
printf 'fail\n' | ci_disposition >/dev/null; rc_red=$?
printf '' | ci_disposition >/dev/null; rc_none=$?
printf 'pending\n' | ci_disposition >/dev/null; rc_pend=$?
eq "D11b ...and distinct for the rest" "1|2|3" "$rc_red|$rc_pend|$rc_none"

# --- ci_wait (C3) ------------------------------------------------------------
# cw <script>: run ci_wait against a fake gh that answers from <script>, one
# line per poll: the buckets space-separated, `-` for "no checks reported",
# `!` for a gh failure. The last line repeats. Prints word|rc|polls|slept.
cw () {
  ( cd "$T" || exit 99
    printf '%b' "$1" > script; : > polls; : > slept; LOG="$T/log"; : > "$LOG"
    gh () {
      echo "$*" >> polls
      local n line
      n="$(wc -l < polls | tr -d ' ')"
      line="$(sed -n "${n}p" script)"; [ -z "$line" ] && line="$(tail -1 script)"
      case "$line" in
        '-') echo "no checks reported on the 'x' branch" >&2; return 1 ;;
        '!') echo "HTTP 502" >&2; return 1 ;;
        *) printf '%s\n' $line; case " $line " in *" fail "*) return 1 ;; *" pending "*) return 8 ;; esac; return 0 ;;
      esac
    }
    sleep () { echo "$1" >> slept; }
    word="$(ci_wait 41)"; rc=$?
    echo "$word|$rc|$(wc -l < polls | tr -d ' ')|$(awk '{s+=$1} END{print s+0}' slept)" )
}
eq "W1 green on the first poll: no waiting" "GREEN|0|1|0" "$(cw 'pass pass\n')"
eq "W2 pending twice then green" "GREEN|0|3|$(( 2 * CI_POLL_S ))" "$(cw 'pending\npass pending\npass pass\n')"
eq "W3 red stops the wait at once" "RED|1|2|$CI_POLL_S" "$(cw 'pending\npass fail\n')"
w4="$(cw 'pending\n')"
eq "W4 still pending at the cap: PENDING, not merged" "PENDING|2" "$(echo "$w4" | cut -d'|' -f1,2)"
eq "W4b ...having waited CI_WAIT_MAX_S and no longer" "$CI_WAIT_MAX_S" "$(echo "$w4" | cut -d'|' -f4)"
w5="$(cw '-\n')"
eq "W5 no checks ever reported: NONE after the grace period, not the full cap" "NONE|3|$CI_NONE_GRACE_S" "$(echo "$w5" | cut -d'|' -f1,2,4)"
eq "W6 checks that appear within the grace period are waited for" "GREEN|0" "$(cw '-\n-\npending\npass\n' | cut -d'|' -f1,2)"
w7="$(cw '!\n')"
eq "W7 gh failing every time: ERROR at the cap, never GREEN" "ERROR|4" "$(echo "$w7" | cut -d'|' -f1,2)"
eq "W8 one gh hiccup is retried" "GREEN|0" "$(cw '!\npass\n' | cut -d'|' -f1,2)"
cw 'pass\n' >/dev/null
eq "W9 it asks gh for this PR's check buckets" "pr checks 41 --json bucket -q .[].bucket" "$(cat "$T/polls")"
out="$( cd "$T" && LOG="$T/log" && gh () { echo called >> "$T/w10-gh"; } && w="$(ci_wait "")"; echo "$w|$?" )$( [ -e "$T/w10-gh" ] && echo " (gh was called)" )"
eq "W10 no PR number: ERROR without calling gh" "ERROR|4" "$out"
eq "W11 the constants are the documented ones" "20|900|120" "$CI_POLL_S|$CI_WAIT_MAX_S|$CI_NONE_GRACE_S"

# --- run.sh call site (C3) and the workflow (C1) -----------------------------
RUN_SH="$REPO/.pipeline/run.sh"
ci_ln="$(grep -n 'ci_word="\$(ci_wait "\$pr_num")"' "$RUN_SH" | head -1 | cut -d: -f1)"
merge_ln="$(grep -n 'gh pr merge "\$pr_num"' "$RUN_SH" | head -1 | cut -d: -f1)"
record_ln="$(grep -n 'if ! attach_review_to_pr "\$pr_num"' "$RUN_SH" | head -1 | cut -d: -f1)"
if [ -n "$ci_ln" ] && [ -n "$merge_ln" ] && [ -n "$record_ln" ] && [ "$record_ln" -lt "$ci_ln" ] && [ "$ci_ln" -lt "$merge_ln" ]; then
  ok "S1 run.sh waits for CI after the review record and before gh pr merge"
else
  bad "S1 CI gate missing or mis-ordered (record=${record_ln:-none}, ci=${ci_ln:-none}, merge=${merge_ln:-none})"
fi
s2="$(awk -v s="${ci_ln:-0}" 'NR>s && NR<=s+6 && /return 1/ {print "guarded"; exit}' "$RUN_SH")"
eq "S2 anything but GREEN returns before the merge" "guarded" "${s2:-unguarded}"
eq "S3 run.sh sources ci.sh alongside the other libs" "1" "$(grep -c '^for lib in .* ci[ ;]' "$RUN_SH" || true)"

WF="$REPO/.github/workflows/ci.yml"
eq "F1 the workflow exists" "yes" "$( [ -r "$WF" ] && echo yes || echo no )"
eq "F2 it runs on pull requests and on push to main" "1|1" \
  "$(grep -c '^  pull_request:' "$WF" 2>/dev/null)|$(grep -c 'branches: \[main\]' "$WF" 2>/dev/null)"
eq "F3 it runs every pipeline suite and nightloop's tests" "1|1" \
  "$(grep -c '\.pipeline/test_\*\.sh' "$WF" 2>/dev/null)|$(grep -c 'unittest discover' "$WF" 2>/dev/null)"
unpinned="$(grep -E '^\s*-?\s*uses:' "$WF" 2>/dev/null | grep -vcE '@[0-9a-f]{40}( |$)' || true)"
eq "F4 every action is pinned to a full commit hash" "0" "$unpinned"
eq "F5 the job's token is read-only" "1" "$(grep -c '^  contents: read' "$WF" 2>/dev/null)"

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
