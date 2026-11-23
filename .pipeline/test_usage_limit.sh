#!/bin/bash
# Offline tests for .pipeline/usage_limit.sh. Run: bash .pipeline/test_usage_limit.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=usage_limit.sh
. .pipeline/usage_limit.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

# The two real messages from 2026-10-19 (run-2026-10-19_145923.log, _161057.log).
printf '%s\n' "--- phase: cycle 1/1: build (model: opus) ---" \
  "You've hit your session limit · resets 4:10pm (America/Chicago)" > "$T/real1"
printf '%s\n' "You've hit your session limit · resets 8:10pm (America/Chicago)" > "$T/real2"
check "L1a real 10-19 message is a limit, with its reset time" \
  [ "$(usage_limit_reset "$T/real1")" == "4:10pm (America/Chicago)" ]
check "L1b second real message" [ "$(usage_limit_reset "$T/real2")" == "8:10pm (America/Chicago)" ]

# The real message from 2026-11-17 (runner log run-2026-11-17_020001.log): the
# CLI names the window, not the kind of limit.
printf '%s\n' "--- phase: cycle 1/1: research (model: sonnet) ---" \
  "You've hit your 5-hour limit · resets 3:20am (America/Chicago)" > "$T/real3"
check "L1d real 11-17 '5-hour limit' message is a limit, with its reset time" \
  [ "$(usage_limit_reset "$T/real3")" == "3:20am (America/Chicago)" ]
printf '%s\n' "You've hit your limit · resets 9am (Europe/London)" > "$T/bare"
check "L1e 'hit your limit' with no qualifier is a limit" \
  [ "$(usage_limit_reset "$T/bare")" == "9am (Europe/London)" ]

printf '%s\n' "Claude usage limit reached." > "$T/noreset"
check "L1c a limit with no reset time prints unknown" [ "$(usage_limit_reset "$T/noreset")" == "unknown" ]

# Ordinary failures: text that mentions limits or sessions but is not one.
printf '%s\n' "Error: rate limit of the GitHub API exceeded" "npm ERR! session expired" \
  "VERIFY: FAIL (1): routes stay in corridors" "token limit for this example is 4096" \
  "you hit your head on the rate limit" > "$T/ordinary"
usage_limit_reset "$T/ordinary" >/dev/null
check "L5a an ordinary build failure is not a usage limit" [ $? -eq 1 ]
usage_limit_reset "$T/missing" >/dev/null
check "L5b a missing output file is not a usage limit" [ $? -eq 1 ]
: > "$T/empty"
usage_limit_reset "$T/empty" >/dev/null
check "L5c empty output is not a usage limit" [ $? -eq 1 ]
check "L6 the return code is EX_TEMPFAIL and distinct from 1" [ "$USAGE_LIMIT_RC" -eq 75 ]

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
