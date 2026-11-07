#!/bin/bash
# Offline tests for .pipeline/auth.sh and .pipeline/alert.sh.
# Run: bash .pipeline/test_auth_alert.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=auth.sh
. .pipeline/auth.sh
# shellcheck source=alert.sh
. .pipeline/alert.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

# --- auth_token_disposition: which login the night uses ---

check "K1 a readable, non-empty token is used" \
  [ "$(auth_token_disposition 0 108)" == "USE" ]
check "K2 no Keychain item falls back to the interactive login" \
  [ "$(auth_token_disposition 44 0)" == "FALLBACK" ]
auth_token_disposition 44 0 >/dev/null
check "K2b fallback is not an error" [ $? -eq 0 ]
check "K3 an item that exists but cannot be read aborts (locked Keychain)" \
  [ "$(auth_token_disposition 36 0)" == "ABORT" ]
auth_token_disposition 36 0 >/dev/null
check "K3b abort returns 1" [ $? -eq 1 ]
check "K4 rc 0 with an empty token aborts, never runs tokenless" \
  [ "$(auth_token_disposition 0 0)" == "ABORT" ]
auth_token_disposition "" 5 >/dev/null
check "K5a a missing rc is a usage error" [ $? -eq 2 ]
auth_token_disposition 0 abc >/dev/null
check "K5b a non-numeric length is a usage error" [ $? -eq 2 ]

# --- auth_failure_reason: did the probe die on the login ---

# The two real messages (run-2026-10-30_020001.log, run-2026-08-01_120004.log).
printf '%s\n' "Failed to authenticate: OAuth session expired and could not be refreshed" > "$T/expired"
check "A1 the 10-30 expiry is an auth failure, quoted back" \
  [ "$(auth_failure_reason "$T/expired")" == "Failed to authenticate: OAuth session expired and could not be refreshed" ]
printf '%s\n' "some preamble" "  Failed to authenticate. API Error: 403 Request not allowed  " > "$T/403"
check "A2 the 08-01 403 is an auth failure, trimmed" \
  [ "$(auth_failure_reason "$T/403")" == "Failed to authenticate. API Error: 403 Request not allowed" ]

# Text near the subject that is not a Claude auth failure.
printf '%s\n' "gh NOT AUTHENTICATED — run 'gh auth login' first. Aborting." \
  "You've hit your session limit · resets 4:10pm (America/Chicago)" \
  "npm ERR! session expired" > "$T/ordinary"
auth_failure_reason "$T/ordinary" >/dev/null
check "A3 gh auth, a usage limit and an npm session are not Claude auth failures" [ $? -eq 1 ]
auth_failure_reason "$T/missing" >/dev/null
check "A4 a missing probe output is not an auth failure" [ $? -eq 1 ]

# --- night_alert_summary: does the night need a human ---

night_alert_summary 0 1 1 "=== done X === (shipped 1/1, full log: L)" >/dev/null
check "N1 a night that shipped everything is silent" [ $? -eq 1 ]
check "N2 shipped 0/1 with exit 0 alerts (the 10-30 shape)" \
  [ "$(night_alert_summary 0 0 1 "=== done X === (shipped 0/1, full log: L)")" == \
    "night shipped 0/1 — last line: === done X === (shipped 0/1, full log: L)" ]
check "N3 an abort before the loop alerts with its last line" \
  [ "$(night_alert_summary 1 - - "AUTH FAILED: x. Aborting.")" == \
    "night exited 1 — last line: AUTH FAILED: x. Aborting." ]
check "N4 a usage limit is named as one, with the counts" \
  [ "$(night_alert_summary 75 0 2 "=== done X ===")" == \
    "night stopped on a usage limit, shipped 0/2 — last line: === done X ===" ]
night_alert_summary 0 - - "external night: opened 1 PR(s) on o/r in 1 cycle(s)." >/dev/null
check "N5 external mode exiting 0 is silent (it never prints === done ===)" [ $? -eq 1 ]
night_alert_summary 1 - - "external night: opened 0 PR(s) on o/r in 1 cycle(s)." >/dev/null
check "N6 external mode exiting non-zero alerts" [ $? -eq 0 ]
check "N7 an empty log still produces a readable summary" \
  [ "$(night_alert_summary 1 - - "")" == "night exited 1 — last line: (empty log)" ]
night_alert_summary x 0 1 "l" >/dev/null
check "N8a a non-numeric exit code is a usage error" [ $? -eq 2 ]
night_alert_summary 0 "" 1 "l" >/dev/null
check "N8b an empty count is a usage error, not a silent night" [ $? -eq 2 ]
check "N9 a summary is exactly one line" \
  [ "$(night_alert_summary 1 0 1 "x" | wc -l | tr -d ' ')" == "1" ]

# --- run.sh wiring (structure only: no model, no Keychain) ---

RUN=".pipeline/run.sh"
trap_line="$(grep -n '^trap on_exit EXIT' "$RUN" | cut -d: -f1)"
window_exit="$(grep -n 'Set \$SCHEDULE_OVERRIDE_VAR=1 to run anyway' "$RUN" | cut -d: -f1)"
probe_line="$(grep -n '^auth_preflight$' "$RUN" | cut -d: -f1)"
first_phase="$(grep -n '^  run_phase ' "$RUN" | head -1 | cut -d: -f1)"
check "W1 the alert trap is armed after the run-window gate (a skipped wake stays silent)" \
  [ -n "$trap_line" ] && [ -n "$window_exit" ] && [ "$trap_line" -gt "$window_exit" ]
check "W2 the auth probe runs before the first phase" \
  [ -n "$probe_line" ] && [ -n "$first_phase" ] && [ "$probe_line" -lt "$first_phase" ]
on_exit_body="$(sed -n '/^on_exit () {/,/^}/p' "$RUN")"
send_alert_body="$(sed -n '/^send_alert () {/,/^}/p' "$RUN")"
if printf '%s\n%s\n' "$on_exit_body" "$send_alert_body" | grep -q 'LOG"\? *$\|tee -a\|>> *"\$LOG"'; then
  bad "W3 the exit trap never writes to \$LOG (=== done === must stay its last line)"
else
  ok "W3 the exit trap never writes to \$LOG (=== done === must stay its last line)"
fi
if grep -nE 'echo[^|]*\$(token|CLAUDE_CODE_OAUTH_TOKEN)' "$RUN" >/dev/null; then
  bad "W4 run.sh never echoes the token"
else
  ok "W4 run.sh never echoes the token"
fi

# --- the trap's exit code survives (bash 3.2 semantics the wiring relies on) ---

/bin/bash -c '
  night_alert_summary () { return 1; }
  on_exit () { local rc=$?; night_alert_summary "$rc" - - x || return 0; }
  trap on_exit EXIT
  exit 7' >/dev/null 2>&1
check "X1 an exit trap that returns 0 leaves the script's exit code alone" [ $? -eq 7 ]

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
