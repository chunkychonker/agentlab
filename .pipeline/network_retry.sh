#!/bin/bash
# Retry policy for the agentlab pipeline's network preflight.
#
# This file decides the retry schedule for run.sh's network preflight: given
# which attempt just failed, whether another one is owed and how many seconds
# to wait first. It never runs curl, never sleeps, never reads a clock and
# never aborts — which is what makes it testable offline (see
# .pipeline/test_gates.sh, cases N6 onward). run.sh owns the acting-on-it half
# (network_preflight), because that half is the one that probes and waits.
#
# Why it exists: the preflight used to be one shot. A single failed probe of
# api.anthropic.com or github.com ended the whole night immediately —
# run-2026-08-31_114702.log and run-2026-09-11_020004.log are each a one-line
# log, the abort message and nothing else, a full 0/2 night lost to a network
# that may have been back seconds later. This box's link is VPN-gated, so a
# reconnect blip is the expected failure, not a permanent outage. A bounded
# retry costs a healthy night exactly nothing (the first probe succeeds and
# nothing below is ever called) and costs a genuinely-dead night about a
# minute before it aborts exactly as it does today.
#
# Why hand-rolled rather than curl's own --retry family: curl retries only
# timeouts and a fixed set of HTTP/FTP status codes by default, which does not
# include the DNS failure or refused connection a VPN-down box actually
# produces. The flag that would cover those, --retry-all-errors, is described
# by curl's own documentation as "the sledgehammer of retrying" with an
# explicit "do not use this option by default", and points at handling retry
# in the calling script instead. This is that.
#
# Sourcing this file defines two functions and three constants. It runs
# nothing, prints nothing, and touches no files. run.sh sources it into a shell
# with `set -uo pipefail` on, so every expansion below must be safe under -u.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57) — see
# knowledge/bash-3.2-testable-scripts.md. No float arithmetic exists there, so
# every delay below is whole seconds.

# The tunables, named once. Retune them here if a future run log shows the
# "gave up after N attempts" line recurring — the retry lines run.sh logs on
# the way are what that decision should be made from.
#
# Worst case with these values is two waits (15s + 30s = 45s) plus up to two
# extra rounds of probes, each already bounded by run.sh's RESPONSE_TIMEOUT_S.
# Roughly a couple of minutes before the same abort that happens today.
NETWORK_MAX_ATTEMPTS=3       # 1 initial probe + up to 2 retries
NETWORK_BASE_DELAY_S=15
NETWORK_CAP_DELAY_S=60

# Whether the attempt that just failed (1-based) should be followed by another
# one, on stdout, as exactly one of RETRY / GIVE_UP.
#
# The return code mirrors the word (0=RETRY, 1=GIVE_UP), the same echo-plus-
# return-code contract worktree_disposition uses in preflight.sh, so a caller
# may branch on either.
#
# Failure modes: a malformed budget is a programming error, not a network
# outcome, so it is reported as neither word — message on stderr, return 2,
# which no legitimate outcome uses. attempt < 1, max_attempts < 1, or either
# one non-numeric (including negative, which is not a count) all take that
# path. Nothing here defaults silently.
network_retry_decision () {
  local attempt="$1" max_attempts="$2"

  case "$attempt" in
    '' | *[!0-9]*)
      echo "network_retry_decision: attempt must be a positive integer, got '$attempt'" >&2
      return 2 ;;
  esac
  case "$max_attempts" in
    '' | *[!0-9]*)
      echo "network_retry_decision: max_attempts must be a positive integer, got '$max_attempts'" >&2
      return 2 ;;
  esac

  # 10# so a zero-padded argument is read as decimal rather than octal.
  attempt=$(( 10#$attempt ))
  max_attempts=$(( 10#$max_attempts ))

  if [ "$attempt" -lt 1 ]; then
    echo "network_retry_decision: attempt must be >= 1, got '$attempt'" >&2
    return 2
  fi
  if [ "$max_attempts" -lt 1 ]; then
    echo "network_retry_decision: max_attempts must be >= 1, got '$max_attempts'" >&2
    return 2
  fi

  if [ "$attempt" -lt "$max_attempts" ]; then
    echo "RETRY"
    return 0
  fi
  echo "GIVE_UP"
  return 1
}

# How many whole seconds to wait before the retry that follows failed attempt
# <attempt> (1-based), on stdout: min(base * 2**(attempt-1), cap).
#
# Computed by doubling under the cap rather than by exponentiation, so a large
# attempt number saturates instead of overflowing a 64-bit arithmetic
# expansion into a negative delay.
#
# No jitter, deliberately: jitter exists to desynchronize multiple concurrent
# callers hammering one shared resource, and this is a single unattended nightly
# job with no peers to desynchronize from. The Python sibling
# (examples/tool-error-policy/policy.py) takes a jitter factor because it has
# them.
#
# Failure modes: attempt < 1, base <= 0, cap < base, or any of the three
# non-numeric (including negative) — message on stderr, return 2, same
# distinct-from-a-real-answer code as network_retry_decision. Returns 0 with a
# delay on stdout otherwise.
network_backoff_delay_s () {
  local attempt="$1" base="$2" cap="$3"

  case "$attempt" in
    '' | *[!0-9]*)
      echo "network_backoff_delay_s: attempt must be a positive integer, got '$attempt'" >&2
      return 2 ;;
  esac
  case "$base" in
    '' | *[!0-9]*)
      echo "network_backoff_delay_s: base must be a positive integer, got '$base'" >&2
      return 2 ;;
  esac
  case "$cap" in
    '' | *[!0-9]*)
      echo "network_backoff_delay_s: cap must be a positive integer, got '$cap'" >&2
      return 2 ;;
  esac

  attempt=$(( 10#$attempt ))
  base=$(( 10#$base ))
  cap=$(( 10#$cap ))

  if [ "$attempt" -lt 1 ]; then
    echo "network_backoff_delay_s: attempt must be >= 1, got '$attempt'" >&2
    return 2
  fi
  if [ "$base" -lt 1 ]; then
    echo "network_backoff_delay_s: base must be >= 1 second, got '$base'" >&2
    return 2
  fi
  if [ "$cap" -lt "$base" ]; then
    echo "network_backoff_delay_s: cap ($cap) must be >= base ($base)" >&2
    return 2
  fi

  local delay="$base" doublings=$(( attempt - 1 ))
  while [ "$doublings" -gt 0 ] && [ "$delay" -lt "$cap" ]; do
    delay=$(( delay * 2 ))
    doublings=$(( doublings - 1 ))
  done
  [ "$delay" -gt "$cap" ] && delay="$cap"

  echo "$delay"
  return 0
}
