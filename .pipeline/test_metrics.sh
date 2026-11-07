#!/bin/bash
# Offline tests for .pipeline/metrics.sh. Run: bash .pipeline/test_metrics.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=metrics.sh
. .pipeline/metrics.sh

PASS=0; FAIL=0
ok ()  { PASS=$((PASS+1)); echo "ok   $1"; }
bad () { FAIL=$((FAIL+1)); echo "FAIL $1"; }
check () { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }
field () { printf '%s\n' "$1" | "$METRICS_JQ" -c "$2"; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

# Shapes captured from claude 2026-11-07: a haiku success, and a call with a
# bad CLAUDE_CODE_OAUTH_TOKEN (exit 1, is_error true, the error in .result).
cat > "$T/ok.json" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"duration_ms":1172,"num_turns":1,"result":"OK\nsecond line","stop_reason":"end_turn","session_id":"5063166f-ae1d-42d3-8a1a-9581cb162ae3","total_cost_usd":0.00287775,"usage":{"input_tokens":2,"cache_creation_input_tokens":13924,"cache_read_input_tokens":9075,"output_tokens":4},"modelUsage":{"claude-haiku-5-5":{}}}
EOF
cat > "$T/auth.json" <<'EOF'
{"type":"result","subtype":"success","is_error":true,"duration_ms":300,"num_turns":1,"result":"Failed to authenticate. API Error: 401 Invalid bearer token","session_id":"x","total_cost_usd":0,"usage":{"input_tokens":0,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}
EOF
printf '%s\n' "Traceback: KeyError door" > "$T/text"
printf '%s\n' "You've hit your session limit · resets 4:10pm (America/Chicago)" > "$T/limit-text"
printf '%s' '{"type":"result","result":"cut off' > "$T/torn.json"
: > "$T/empty"

# --- metrics_extract_result: what goes into the run log ---

check "E1 a success prints the answer text, newlines intact" \
  [ "$(metrics_extract_result "$T/ok.json")" == "$(printf 'OK\nsecond line')" ]
check "E2 an auth failure's message reaches the log, so log greps still see it" \
  [ "$(metrics_extract_result "$T/auth.json")" == "Failed to authenticate. API Error: 401 Invalid bearer token" ]
for f in text torn.json empty missing; do
  out="$(metrics_extract_result "$T/$f")"; rc=$?
  check "E3 $f is not a result: returns 1, prints nothing" [ "$rc|$out" == "1|" ]
done
printf '%s\n' '{"type":"result","subtype":"error_max_turns","is_error":true}' > "$T/noresult.json"
metrics_extract_result "$T/noresult.json" >/dev/null
check "E4 JSON with no .result returns 1 (caller logs it raw)" [ $? -eq 1 ]

# --- metrics_cycle_of / metrics_phase_of: run_phase names -> fields ---

check "P1 cycle name" [ "$(metrics_cycle_of 'cycle 2/3: build')|$(metrics_phase_of 'cycle 2/3: build')" == "2|build" ]
check "P2 health has no cycle" [ "$(metrics_cycle_of health)|$(metrics_phase_of health)" == "|health" ]
check "P3 replenish loses its counts, so it groups as one phase" \
  [ "$(metrics_phase_of 'replenish (unclaimed=0 < 1, target=3)')" == "replenish" ]
check "P4 pipeline observer" [ "$(metrics_phase_of 'pipeline observer')" == "pipeline observer" ]

# --- metrics_record: the line itself ---

line="$(metrics_record "$T/ok.json" 2026-11-08_020001 1 research sonnet 0 FRESH)"
check "C1a a success record is one line" [ "$(printf '%s\n' "$line" | wc -l | tr -d ' ')" -eq 1 ]
printf '%s\n' "$line" | "$METRICS_JQ" -e . >/dev/null
check "C1b ...and it parses as JSON" [ $? -eq 0 ]
check "R1 run fields come from the arguments" \
  [ "$(field "$line" '[.run_ts,.cycle,.phase,.model,.rc,.postcondition_state]')" == '["2026-11-08_020001",1,"research","sonnet",0,"FRESH"]' ]
check "R2 CLI fields come from the JSON" \
  [ "$(field "$line" '[.duration_ms,.num_turns,.total_cost_usd,.session_id,.is_error,.parse_error]')" == '[1172,1,0.00287775,"5063166f-ae1d-42d3-8a1a-9581cb162ae3",false,false]' ]
check "R3 token counts" \
  [ "$(field "$line" '.tokens')" == '{"input":2,"output":4,"cache_read":9075,"cache_creation":13924}' ]
check "R4 the answer text is not copied into metrics" [ "$(field "$line" 'has("result")')" == "false" ]

line="$(metrics_record "$T/auth.json" ts "" health sonnet 1 "")"
check "R5 no cycle and no postcondition are null, is_error carried" \
  [ "$(field "$line" '[.cycle,.postcondition_state,.is_error,.rc]')" == '[null,null,true,1]' ]

# C3: stdout that is not JSON is still recorded, flagged, rc untouched.
for f in text limit-text torn.json empty missing; do
  line="$(metrics_record "$T/$f" ts 2 build opus 75 "")"; rc=$?
  check "C3 $f: recorded with parse_error true, rc kept, CLI fields null" \
    [ "$rc|$(field "$line" '[.parse_error,.rc,.duration_ms,.tokens.output,.phase]')" == '0|[true,75,null,null,"build"]' ]
done
check "R6 a phase name with quotes and backslashes stays valid JSON" \
  [ "$(field "$(metrics_record "$T/ok.json" ts "" 'say "hi" \ there' sonnet 0 "")" .phase)" == '"say \"hi\" \\ there"' ]

metrics_record "$T/ok.json" ts 1 build opus abc NONE >/dev/null
check "V1 a non-numeric rc is a usage error" [ $? -eq 2 ]
metrics_record "$T/ok.json" ts 0 build opus 0 NONE >/dev/null
check "V2 cycle 0 is a usage error" [ $? -eq 2 ]
metrics_record "$T/ok.json" ts x build opus 0 NONE >/dev/null
check "V3 a non-numeric cycle is a usage error" [ $? -eq 2 ]

# --- metrics_summary: per-phase medians over the last N nights (C4) ---

mk () { # mk <run_ts> <phase> <rc> <duration_ms> <turns> <out_tokens> <cost>
  printf '{"run_ts":"%s","cycle":1,"phase":"%s","rc":%s,"duration_ms":%s,"num_turns":%s,"tokens":{"output":%s},"total_cost_usd":%s,"parse_error":false}\n' "$@"
}
{
  mk 2026-11-01_020001 build 0 6000000 99 99999 9.99   # oldest night: outside N=3
  mk 2026-11-02_020001 build 0 120000 10 1000 1.00
  mk 2026-11-02_020001 research 0 60000 4 300 0.20
  mk 2026-11-03_020001 build 1 240000 30 3000 3.00
  echo 'not json at all'
  mk 2026-11-04_020001 build 0 180000 20 2000 2.00
  mk 2026-11-04_020001 research 0 120000 6 500 0.40
  printf '%s\n' '{"run_ts":"2026-11-04_020001","cycle":1,"phase":"research","rc":1,"duration_ms":null,"num_turns":null,"tokens":{"output":null},"total_cost_usd":null,"parse_error":true}'
} > "$T/m.jsonl"
out="$(metrics_summary "$T/m.jsonl" 3)"; rc=$?
check "C4a the summary succeeds despite a torn line" [ "$rc" -eq 0 ]
check "C4b header row" [ "$(printf '%s\n' "$out" | head -1)" == "$(printf 'phase\tn\tok\tmedian_min\tmedian_turns\tmedian_out_tokens\tmedian_cost_usd')" ]
check "C4c build: last 3 nights only, odd-count median" \
  [ "$(printf '%s\n' "$out" | grep '^build')" == "$(printf 'build\t3\t2\t3\t20\t2000\t2')" ]
check "C4d research: even-count median, parse-error record counted but not averaged" \
  [ "$(printf '%s\n' "$out" | grep '^research')" == "$(printf 'research\t3\t2\t1.5\t5\t400\t0.3')" ]
check "C4e N=1 keeps only the newest night" \
  [ "$(metrics_summary "$T/m.jsonl" 1 | grep '^build' | cut -f2)" == "1" ]
metrics_summary "$T/m.jsonl" 0 >/dev/null
check "C4f N=0 is a usage error" [ $? -eq 2 ]
metrics_summary "$T/nope.jsonl" 3 >/dev/null
check "C4g a missing file returns 1" [ $? -eq 1 ]

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
