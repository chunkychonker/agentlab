# "Is CI green" for the agentlab pipeline's auto-merge gate.
#
# Why it exists: until 2026-11-15 nothing outside the pipeline checked a PR
# before it merged. Every test run was one the pipeline itself (or one of its
# agents) chose to do, on the same machine, in the same working tree. The
# workflow in .github/workflows/ci.yml runs the suites on a clean checkout
# somewhere else, and this file makes the merge wait for it.
#
# Fails closed, like verdict.sh: only GREEN merges. A red run, a run still
# going at the cap, no run at all, and a `gh` that cannot answer all leave
# the PR open for a human.
#
# Failure domain: if GitHub Actions is down or slow, PRs still open but do
# not auto-merge. Nothing is lost; the morning merge is by hand.
#
# Sourcing this file defines functions and constants. It runs nothing, prints
# nothing, and touches no files. Safe under `set -uo pipefail`.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57).

# Seconds between polls, and the longest the gate waits for a verdict. The
# suites take about a minute once a runner picks the job up; 15 minutes
# covers a slow queue without holding the night open.
CI_POLL_S=20
CI_WAIT_MAX_S=900
# How long "no checks reported" is believed to mean "not registered yet".
# GitHub lists a PR's checks within seconds of the push; after two minutes
# none are coming, and waiting the full cap would only delay the same answer.
CI_NONE_GRACE_S=120

# ci_disposition
#
# Reads check buckets on stdin, one per line (the `bucket` field of
# `gh pr checks --json`: pass, fail, pending, skipping, cancel). Pure.
# Prints exactly one word; the return code mirrors it:
#   0  GREEN    at least one check passed, none failed, none still running
#   1  RED      any check failed or was cancelled, whatever the others say
#   2  PENDING  no failure yet, but a check is still running, or has a bucket
#               this function does not know (unknown is never a pass)
#   3  NONE     no checks, or only skipped ones: nothing actually ran
ci_disposition () {
  local bucket pass=0 red=0 pending=0
  while IFS= read -r bucket || [ -n "$bucket" ]; do
    case "$bucket" in
      '') ;;
      pass) pass=$(( pass + 1 )) ;;
      skipping) ;;
      fail|cancel) red=$(( red + 1 )) ;;
      *) pending=$(( pending + 1 )) ;;
    esac
  done
  if [ "$red" -gt 0 ]; then echo "RED"; return 1; fi
  if [ "$pending" -gt 0 ]; then echo "PENDING"; return 2; fi
  if [ "$pass" -gt 0 ]; then echo "GREEN"; return 0; fi
  echo "NONE"; return 3
}

# ci_wait <pr-number>
#
# Polls the PR's checks with `gh` every CI_POLL_S seconds until they are
# GREEN or RED, or until the wait runs out: CI_NONE_GRACE_S while no check
# has been reported, CI_WAIT_MAX_S otherwise. Run from the repo, so `gh`
# resolves the PR. Appends what `gh` said on a failure to $LOG if set.
#
# Prints exactly one word, the last disposition seen; the return code mirrors
# it. Only 0 authorises a merge:
#   0  GREEN
#   1  RED
#   2  PENDING  still running at the cap
#   3  NONE     no checks were ever reported
#   4  ERROR    <pr-number> is empty, or `gh` could not answer on the last
#               poll (a failed poll before that is simply retried)
# Idempotent: it only reads.
ci_wait () {
  local pr="${1:-}" log="${LOG:-/dev/null}"
  if [ -z "$pr" ]; then echo "ERROR"; return 4; fi
  local waited=0 word buckets err
  err="$(mktemp)" || { echo "ERROR"; return 4; }
  while :; do
    # gh exits non-zero for failing and for pending checks too, so its output
    # decides, not its status.
    buckets="$(gh pr checks "$pr" --json bucket -q '.[].bucket' 2>"$err")"
    if [ -n "$buckets" ]; then
      word="$(printf '%s\n' "$buckets" | ci_disposition)"
    elif grep -q "no checks reported" "$err"; then
      word="NONE"
    else
      word="ERROR"
      { echo "ci_wait: gh pr checks $pr failed:"; cat "$err"; } >> "$log"
    fi
    case "$word" in
      GREEN|RED) break ;;
      NONE) [ "$waited" -ge "$CI_NONE_GRACE_S" ] && break ;;
      *) [ "$waited" -ge "$CI_WAIT_MAX_S" ] && break ;;
    esac
    sleep "$CI_POLL_S"
    waited=$(( waited + CI_POLL_S ))
  done
  rm -f "$err"
  echo "$word"
  case "$word" in
    GREEN) return 0 ;; RED) return 1 ;; PENDING) return 2 ;; NONE) return 3 ;;
  esac
  return 4
}
