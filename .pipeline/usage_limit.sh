#!/bin/bash
# usage_limit.sh — recognises a phase that died on a Claude usage limit.
#
# Sourced by run.sh. Pure: reads one file it is handed, writes nothing, calls
# no model. Covered offline by test_usage_limit.sh.
#
# Why it exists: on 2026-10-19 two external cycles (145923, 161057) died in the
# build phase with "You've hit your session limit · resets 4:10pm ...". The
# pipeline logged that as an ordinary build failure, went on to the next cycle
# (which could only hit the same limit), and the next run paid for research
# again. A usage limit is not a defect in the increment: it is "come back
# later", and every caller needs to tell the two apart.

# run_phase's return code for "the phase stopped on a usage limit".
# 75 is EX_TEMPFAIL from sysexits.h: a temporary failure, retry later.
USAGE_LIMIT_RC=75

# Extended regex, matched case-insensitively against the phase's own output.
# The first alternative is the exact CLI text seen on 2026-10-19; the second
# covers the wording the CLI uses for plan usage limits.
USAGE_LIMIT_RE="hit your (session|usage|weekly) limit|usage limit reached"

# usage_limit_reset <phase_output_file>
# Returns 0 and prints the reset time (e.g. "4:10pm (America/Chicago)") if the
# file shows a usage limit; prints "unknown" when the message names no reset
# time. Returns 1, printing nothing, if there is no limit message or the file
# is missing or unreadable.
usage_limit_reset () {
  local file="$1" line reset
  [ -r "$file" ] || return 1
  line="$(grep -iE -- "$USAGE_LIMIT_RE" "$file" 2>/dev/null | tail -1)"
  [ -n "$line" ] || return 1
  reset="$(printf '%s\n' "$line" | sed -nE 's/.*[Rr]esets (at )?(.*)$/\2/p' | sed -E 's/[[:space:]]+$//')"
  echo "${reset:-unknown}"
  return 0
}
