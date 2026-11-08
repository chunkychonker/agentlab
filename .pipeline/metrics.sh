#!/bin/bash
# metrics.sh — turns one phase's `claude -p --output-format json` output into
# one line of logs/metrics.jsonl, and summarises those lines.
#
# Sourced by run.sh. Pure: reads the files it is handed, writes only to
# stdout, calls no model. Uses /usr/bin/jq, which ships with macOS. Covered
# offline by test_metrics.sh.
#
# Why it exists: as of 2026-11-07 the pipeline had run nightly for weeks and
# nothing could say what a phase costs — how long build takes, how many turns review
# uses, whether research got more expensive after a prompt change. The run
# logs hold the phase's prose answer and nothing else. The CLI already
# measures all of it; this file keeps what it measures, one line per phase.
#
# Metrics are a passenger. Nothing here may change a phase's outcome: run.sh
# writes the line after the outcome is decided, and a phase whose output is
# not JSON is still logged (raw) and recorded (parse_error: true).

METRICS_JQ="/usr/bin/jq"

# metrics_extract_result <json-file>
# Prints the phase's answer text (the CLI's `.result`) followed by a newline.
# Returns 1, printing nothing, if the file is missing, empty, not exactly one
# JSON object, or has no string `.result` — the caller then logs the raw file.
metrics_extract_result () {
  local file="$1"
  [ -s "$file" ] || return 1
  "$METRICS_JQ" -rs 'if length == 1 and (.[0] | type) == "object"
                        and (.[0].result | type) == "string"
                     then .[0].result else error("no result") end' \
    "$file" 2>/dev/null || return 1
}

# metrics_cycle_of <phase-name>
# "cycle 2/3: build" -> 2. Prints nothing for a phase outside the cycle loop
# ("health", "replenish (...)", "pipeline observer"). Always returns 0.
metrics_cycle_of () {
  case "$1" in
    "cycle "[0-9]*/*": "*) local c="${1#cycle }"; echo "${c%%/*}" ;;
  esac
  return 0
}

# metrics_phase_of <phase-name>
# "cycle 2/3: build" -> build; "replenish (unclaimed=0 < 1, target=3)" ->
# replenish; any other name is printed unchanged. Always returns 0.
metrics_phase_of () {
  case "$1" in
    "cycle "[0-9]*/*": "*) echo "${1#*: }" ;;
    "replenish "*|replenish) echo "replenish" ;;
    *) echo "$1" ;;
  esac
}

# metrics_record <json-file> <run_ts> <cycle> <phase> <model> <rc> <post_state>
# Prints one compact JSON line:
#   run_ts, cycle (number or null), phase, model, rc, postcondition_state
#   (string, or null when the postcondition never ran), duration_ms,
#   num_turns, tokens {input, output, cache_read, cache_creation},
#   total_cost_usd, session_id, is_error, parse_error, denials (how many tool
#   calls the phase's permission rules refused; added with spec #3).
# CLI fields the output lacks are null. If the file is not exactly one JSON
# object every CLI field is null and parse_error is true — that is still a
# record, returned 0: "this phase printed something unreadable" is data.
# total_cost_usd is the CLI's API-price estimate; on a subscription it is a
# notional figure, not a bill.
# Returns 2, printing nothing, when <rc> is not an integer or <cycle> is
# neither empty nor a positive integer. Returns 3 if jq itself is missing.
metrics_record () {
  local file="$1" run_ts="$2" cycle="$3" phase="$4" model="$5" rc="$6" post="$7"
  case "$rc" in ''|*[!0-9]*) return 2 ;; esac
  case "$cycle" in '') cycle=null ;; *[!0-9]*|0) return 2 ;; esac
  [ -x "$METRICS_JQ" ] || return 3

  local parsed=true
  [ -s "$file" ] && "$METRICS_JQ" -se 'length == 1 and (.[0] | type) == "object"' \
    "$file" >/dev/null 2>&1 || parsed=false

  local src
  if [ "$parsed" = true ]; then src="$file"; else src=/dev/null; fi
  # With /dev/null as input, -s yields [] and every .[0].x is null.
  "$METRICS_JQ" -cs \
    --arg run_ts "$run_ts" --argjson cycle "$cycle" --arg phase "$phase" \
    --arg model "$model" --argjson rc "$rc" --arg post "$post" \
    --argjson parse_error "$( [ "$parsed" = true ] && echo false || echo true )" \
    '(.[0] // {}) as $o | {
       run_ts: $run_ts, cycle: $cycle, phase: $phase, model: $model, rc: $rc,
       postcondition_state: (if $post == "" then null else $post end),
       duration_ms: $o.duration_ms, num_turns: $o.num_turns,
       tokens: {input: $o.usage.input_tokens, output: $o.usage.output_tokens,
                cache_read: $o.usage.cache_read_input_tokens,
                cache_creation: $o.usage.cache_creation_input_tokens},
       total_cost_usd: $o.total_cost_usd, session_id: $o.session_id,
       is_error: $o.is_error, parse_error: $parse_error,
       denials: ($o.permission_denials | if type == "array" then length else null end)
     }' "$src"
}

# metrics_denials <json-file>
# Prints one line per tool call the phase's permission rules refused, as
# "<tool>: <command or path>", cut to 200 characters. These are the calls
# spec #3's per-phase allow lists turned away; a denial is not an error, but a
# phase that keeps hitting one is either misbehaving or missing a rule.
# Prints nothing, and returns 0, when there were none or the file is not one
# JSON object.
metrics_denials () {
  local file="$1"
  [ -s "$file" ] || return 0
  "$METRICS_JQ" -rs 'if length == 1 and (.[0] | type) == "object" then
      (.[0].permission_denials // [])[]? |
      "\(.tool_name // "?"): \((.tool_input.command // .tool_input.file_path // .tool_input.url // (.tool_input | tostring)) | tostring | .[0:200])"
    else empty end' "$file" 2>/dev/null
  return 0
}

# metrics_summary <metrics.jsonl> <nights>
# Prints a tab-separated table, one row per phase, of medians over the last
# <nights> distinct run_ts values in the file (run_ts sorts by time):
#   phase  n  ok  median_min  median_turns  median_out_tokens  median_cost_usd
# n counts records, ok counts rc 0. A median ignores records whose field is
# null (parse errors), and is "-" when none remain. Lines that are not JSON
# are skipped, never fatal: one torn line must not hide a month of data.
# Returns 2 when <nights> is not a positive integer, 1 if the file is missing.
metrics_summary () {
  local file="$1" nights="$2"
  case "$nights" in ''|*[!0-9]*|0) return 2 ;; esac
  [ -r "$file" ] || return 1
  [ -x "$METRICS_JQ" ] || return 3
  "$METRICS_JQ" -R 'fromjson? | objects' "$file" | "$METRICS_JQ" -rs --argjson n "$nights" '
    def median: map(select(. != null)) | sort |
      if length == 0 then null
      elif length % 2 == 1 then .[length / 2 | floor]
      else (.[length / 2 - 1] + .[length / 2]) / 2 end;
    def show(f): if . == null then "-" else f end;
    ([.[].run_ts] | unique | .[-$n:]) as $keep
    | map(select(.run_ts | IN($keep[])))
    | (["phase", "n", "ok", "median_min", "median_turns", "median_out_tokens", "median_cost_usd"] | @tsv),
      (group_by(.phase)[] | [
        .[0].phase, length, (map(select(.rc == 0)) | length),
        (map(.duration_ms) | median | show(. / 60000 * 10 | round / 10)),
        (map(.num_turns) | median | show(.)),
        (map(.tokens.output) | median | show(round)),
        (map(.total_cost_usd) | median | show(. * 100 | round / 100))
      ] | map(tostring) | @tsv)'
}
