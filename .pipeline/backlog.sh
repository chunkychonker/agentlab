# Backlog replenishment decisions for the agentlab pipeline.
#
# This file decides *whether* the backlog needs topping up; it never does the
# topping up itself. The expensive part (a `claude -p` phase, a git push) is
# injected by the caller as the name of a shell function, which is what makes
# the decision testable offline — see .pipeline/test_backlog.sh and
# knowledge/bash-3.2-testable-scripts.md.
#
# Sourcing this file defines functions and a constant. It runs nothing, prints
# nothing, and touches no files. .pipeline/run.sh sources it into a shell that
# has `set -uo pipefail` on, so every expansion below must be safe under -u.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57 and is the only bash on this box):
# no `declare -A`, no mapfile/readarray, no ${var,,}, no &>>.

# How many nights' worth of work to aim for when topping up. Named once here
# rather than restated as a bare `* 3` at each call site.
BACKLOG_TARGET_NIGHTS=3

# Number of unclaimed items in <path>, on stdout.
#
# An item counts iff its line begins with the literal '- [ ] ' at column 0 —
# that prefix is the documented contract between BACKLOG.md and the researcher
# (PIPELINE.md). Indented lines, continuation text, '- [done #N]',
# '- [researching]' and '- [building]' never count.
#
# Failure modes:
#   <path> is not a readable regular file -> message on stderr, return 1,
#   nothing on stdout. A file with zero matching lines is NOT a failure: it
#   prints 0 and returns 0. (`grep -c` exits 1 on zero matches, which is why
#   the `|| true` below is load-bearing rather than dead code.)
backlog_count_unclaimed () {
  local path="$1"
  if [ ! -f "$path" ] || [ ! -r "$path" ]; then
    echo "backlog_count_unclaimed: '$path' is not a readable file" >&2
    return 1
  fi
  local count
  count="$(grep -c '^- \[ \] ' "$path" || true)"
  [ -z "$count" ] && count=0
  echo "$count"
}

# Desired unclaimed count for a night of <cycles>, on stdout.
#
# Assumes <cycles> is a validated positive integer: run.sh parses and validates
# it once at its boundary, so nothing here re-validates it.
backlog_replenish_target () {
  echo $(( $1 * BACKLOG_TARGET_NIGHTS ))
}

# Return 0 iff <unclaimed> is short of one night's draw of <cycles>.
#
# One night consumes at most one item per cycle, so `unclaimed >= cycles` at the
# start of the loop is exactly the condition under which every cycle has
# something to claim.
backlog_should_replenish () {
  [ "$1" -lt "$2" ]
}

# Reconcile <path> toward the state ">= <cycles> unclaimed items" by invoking
# `<action> <target>` at most once, where <target> is backlog_replenish_target
# <cycles>. <action> is the NAME of a shell function: run.sh injects the one
# that runs the replenish phase and commits, the self-test injects a fake.
#
# This is a reconciler, not a one-shot: it reads the file, acts only if the file
# says it must, then re-reads to verify. Calling it twice with no intervening
# consumption is therefore a no-op the second time, and it is safe to retry.
#
# Failure modes (deliberately distinct return codes — callers log them
# differently, and "the phase died" must not be confused with "the phase ran and
# under-delivered"):
#   0  stocked — either it already was, or <action> stocked it
#   1  <action> succeeded but <path> is STILL short of <cycles>
#   2  <action> returned non-zero (e.g. the replenish phase died)
#   3  <path> is not a readable file, before or after <action> ran
backlog_ensure_stocked () {
  local path="$1" cycles="$2" action="$3"

  local before
  before="$(backlog_count_unclaimed "$path")" || return 3
  backlog_should_replenish "$before" "$cycles" || return 0

  "$action" "$(backlog_replenish_target "$cycles")" || return 2

  local after
  after="$(backlog_count_unclaimed "$path")" || return 3
  backlog_should_replenish "$after" "$cycles" && return 1
  return 0
}
