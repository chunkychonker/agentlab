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

# --- Stranded claims -------------------------------------------------------
#
# A claim ('[ ]' -> '[researching]' -> '[building]') lives only in the working
# tree until a PR merges. When a cycle fails, run.sh's snapshot_dirty_main
# carries that working tree — claim included — onto a cycle/*-unshipped-* branch
# and returns to a main whose BACKLOG.md reads '- [ ] ' again. The work exists;
# the claim pointing at it left with it, so the next cycle re-picks the topic and
# rebuilds what is already built. Observed 2026-08-12 (a 529 in maintain).
#
# The fix is to re-apply the claim to main in a form that says where the work
# went: '- [stranded <branch>] '. That is not '- [ ] ', so it is invisible to
# both backlog_count_unclaimed and the researcher's pick, and it names the branch
# a human must salvage. See knowledge/pipeline-claim-lifecycle.md.

# The marker a reconciled item carries. Named once: run.sh logs it, the
# self-test asserts on it, and neither restates the literal.
BACKLOG_STRANDED_MARKER="stranded"

# The claimed BACKLOG.md line inside <diff-text>, on stdout, without the diff's
# leading '+'.
#
# <diff-text> is a unified diff of BACKLOG.md between a branch and its merge base
# with main. A claim shows up there as an added line carrying a claimed marker.
# Only the FIRST such line is reported: one cycle claims one item, and a branch
# carrying two claims is a state this function refuses to guess at.
#
# Failure modes:
#   no added claim line in <diff-text> -> return 1, nothing on stdout. That is
#   the normal case for a snapshot branch that failed before the researcher
#   edited BACKLOG.md, so callers treat it as "skip", not "error".
backlog_claimed_line () {
  local diff="$1" line found=""
  # A here-string, not a pipeline: `... | while` would run the loop in a subshell
  # and $found would not survive it. bash 3.2 has <<< .
  while IFS= read -r line; do
    [ -n "$found" ] && continue
    case "$line" in
      '+- [researching] '*|'+- [building] '*) found="${line#+}" ;;
    esac
  done <<< "$diff"
  [ -z "$found" ] && return 1
  printf '%s\n' "$found"
}

# The item text of <claimed-line>, on stdout — everything after the marker.
#
# That text is the item's identity: it is byte-identical on main (where the line
# still reads '- [ ] <text>') and on the branch, so callers match on it
# literally. Literal matching is deliberate — item text contains backticks,
# parentheses and brackets, so any regex built from it would misfire.
#
# Failure modes:
#   <claimed-line> carries no recognised claim marker -> return 1, no stdout.
backlog_claim_key () {
  local line="$1"
  # The brackets MUST be escaped here. The word after '#' is a glob pattern, so
  # an unescaped [building] is a character class matching ONE character from
  # {b,u,i,l,d,n,g} — it silently strips nothing and the whole line comes back
  # as the key. (The case patterns above are quoted, so they are already
  # literal; only the expansions need this.)
  case "$line" in
    '- [researching] '*) printf '%s\n' "${line#- \[researching\] }" ;;
    '- [building] '*)    printf '%s\n' "${line#- \[building\] }" ;;
    *) return 1 ;;
  esac
}

# Rewrite the item whose text is <key> in <path> to '- [stranded <branch>] <key>'.
#
# This is a reconciler: it decides from the file's current contents, and calling
# it twice changes nothing the second time. It rewrites <path> in place only when
# there is something to change, so a no-op leaves the file's mtime alone and
# `git status` stays clean.
#
# Failure modes (distinct codes — run.sh logs them differently, and "already
# handled" must never be reported as "could not find"):
#   0  rewritten: the item read '- [ ] <key>' and now reads stranded
#   1  <path> is not a readable regular file, or is not writable
#   2  no line in <path> has the item text <key> — the item was reworded or
#      removed since the branch was cut; a human has to look
#   3  nothing to do: already stranded, or already '[done #N]' (salvaged), or
#      claimed by a cycle in flight right now
backlog_apply_stranded () {
  local path="$1" key="$2" branch="$3"
  if [ ! -f "$path" ] || [ ! -r "$path" ] || [ ! -w "$path" ]; then
    echo "backlog_apply_stranded: '$path' is not a readable, writable file" >&2
    return 1
  fi

  local tmp line rest changed=0 seen=0
  tmp="$path.reconcile.$$"
  : > "$tmp" || return 1

  # `|| [ -n "$line" ]` so a final line with no trailing newline is not dropped.
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$line" = "- [ ] $key" ]; then
      seen=1; changed=1
      printf '%s\n' "- [$BACKLOG_STRANDED_MARKER $branch] $key" >> "$tmp"
      continue
    fi
    # Any other marker carrying the same item text: already reconciled, already
    # shipped, or in flight. All three mean "leave it alone", and all three are
    # matched by suffix so no marker's spelling is hard-coded here beyond the
    # '- [' the format itself guarantees.
    case "$line" in
      '- ['*"] $key")
        seen=1
        printf '%s\n' "$line" >> "$tmp"
        continue
        ;;
    esac
    printf '%s\n' "$line" >> "$tmp"
  done < "$path"

  if [ "$seen" -eq 0 ]; then
    rm -f "$tmp"
    return 2
  fi
  if [ "$changed" -eq 0 ]; then
    rm -f "$tmp"
    return 3
  fi
  mv "$tmp" "$path" || return 1
  return 0
}
