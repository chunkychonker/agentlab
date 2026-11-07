#!/bin/bash
# alert.sh — decides whether a finished night needs a human's attention.
#
# Sourced by run.sh. Pure: judges the values it is handed, writes nothing,
# posts nothing. run.sh's EXIT trap does the posting. Covered offline by
# test_auth_alert.sh.
#
# Why it exists: three nights (2026-10-30..11-01) shipped nothing and nobody
# knew until the weekly observer and a human read the logs. A night that
# ships everything it set out to is silent; anything else says so the same
# morning.
#
# Why not classify the log instead (run_log.sh): external mode never prints
# run.sh's `=== done ===` line, so every external night would read ABORTED and
# alert falsely. The exit code and the shipped/cycles counts are known exactly
# at exit, in every mode, without parsing prose.

# night_alert_summary <exit_rc> <shipped|-> <cycles|-> <last_log_line>
# <shipped> and <cycles> are "-" when the night never reached the count (an
# abort before the cycle loop, or external mode, which keeps its own count).
#
# Returns 0 and prints a one-line summary when the night needs attention:
#   - it exited non-zero (an abort, a usage limit, an external night that
#     opened no PR), or
#   - it exited 0 but shipped fewer increments than it ran cycles.
# Returns 1, printing nothing, when it does not: exit 0 with everything
# shipped, or exit 0 with no count (external mode reports failure through its
# exit code).
# Returns 2, printing nothing, when <exit_rc> is not a non-negative integer or
# a count is neither "-" nor one.
night_alert_summary () {
  local rc="${1:-}" shipped="${2:-}" cycles="${3:-}" last="${4:-}"
  case "$rc" in ''|*[!0-9]*) return 2 ;; esac
  case "$shipped" in -) ;; ''|*[!0-9]*) return 2 ;; esac
  case "$cycles" in -) ;; ''|*[!0-9]*) return 2 ;; esac
  [ -n "$last" ] || last="(empty log)"

  local counts=""
  [ "$shipped" != "-" ] && [ "$cycles" != "-" ] && counts="shipped $shipped/$cycles"

  if [ "$rc" -ne 0 ]; then
    local what="exited $rc"
    # 75 is usage_limit.sh's USAGE_LIMIT_RC; spelled out here so this file
    # stays sourceable on its own in tests.
    [ "$rc" -eq 75 ] && what="stopped on a usage limit"
    printf '%s\n' "night $what${counts:+, $counts} — last line: $last"
    return 0
  fi

  if [ -n "$counts" ] && [ "$shipped" -lt "$cycles" ]; then
    printf '%s\n' "night $counts — last line: $last"
    return 0
  fi
  return 1
}
