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

# --- Health findings -------------------------------------------------------
#
# The health check reports portfolio rot and is forbidden from fixing it
# (.claude/agents/agentlab-health.md), on the grounds that fixing it is "a
# future build cycle's job (a normal backlog item)". Nothing filed that item,
# so findings were written to logs/last-health.md and dropped. run.sh — not the
# agent — files them here, the same split as replenish_action: the agent
# produces the report, the script commits the consequence.

# Where filed findings land, and the tag that identifies them. Named once:
# run.sh logs them, the self-test asserts on them, the dedupe below scopes to
# them, and none of those restate the literal.
BACKLOG_HEALTH_SECTION="## Health-check findings"
BACKLOG_HEALTH_PREFIX="fix (health"

# How much of a finding's reason survives into the one-line backlog item. The
# full text is in the dated logs/lab-health-*.log; the item only has to be
# identifiable and claimable.
BACKLOG_HEALTH_DETAIL_MAX=200

# Return 0 iff <path> carries an UNRESOLVED item containing every <substr>.
#
# Unresolved means any claim marker except '[done #N]': '[ ]', '[researching]',
# '[building]' and '[stranded <branch>]' all mean the work is still queued or in
# flight, so re-filing would duplicate it. '[done #N]' deliberately does NOT
# suppress — a finding that reappears after its fix shipped is new information
# (the fix regressed, or never addressed the real cause), not a duplicate.
#
# Matching is literal (grep -F) and confined to item lines. Both matter: item
# text routinely contains '[[wikilink]]', backticks, parentheses and '*', so any
# pattern built from it would misfire — the same trap backlog_claim_key warns
# about — and a bare substring search over the whole file would let a prose
# paragraph that merely mentions a path suppress a real finding.
#
# Failure modes: an unreadable <path> returns 1 (i.e. "not present"), because
# every caller here validates the file first and would rather see the write
# fail loudly than have this function invent an error path of its own.
backlog_has_unresolved () {
  local path="$1"; shift
  local out s
  out="$(grep '^- \[' "$path" 2>/dev/null | grep -v '^- \[done ')" || return 1
  [ -z "$out" ] && return 1
  for s in "$@"; do
    out="$(printf '%s\n' "$out" | grep -F -- "$s")" || return 1
    [ -z "$out" ] && return 1
  done
  return 0
}

# Reconcile <path> toward carrying an unclaimed item for the health finding
# <subject> / <detail>, dated <date>.
#
# The item is written as '- [ ] fix (health <date>): <subject> — <detail>' under
# BACKLOG_HEALTH_SECTION, which is created at the end of the file if absent.
# End of file, not the top: the researcher works top-down, so rot fixes queue
# behind planned work rather than pre-empting it. Promoting one is a human's
# call.
#
# This is a reconciler, not a one-shot. It decides from the file's current
# contents and dedupes on <subject> scoped to health-filed items, so the same
# finding re-reported next week changes nothing and `git status` stays clean.
# It rewrites <path> only when there is something to add.
#
# Failure modes (distinct codes — run.sh logs them differently, and "already
# filed" must never be reported as "could not write"):
#   0  filed: an unclaimed item for <subject> now exists
#   1  <path> is not a readable, writable file, or the rewrite failed
#   3  nothing to do: an unresolved item for <subject> is already there
backlog_file_health_finding () {
  local path="$1" date="$2" subject="$3" detail="$4"
  if [ ! -f "$path" ] || [ ! -r "$path" ] || [ ! -w "$path" ]; then
    echo "backlog_file_health_finding: '$path' is not a readable, writable file" >&2
    return 1
  fi
  [ -n "$subject" ] || { echo "backlog_file_health_finding: empty subject" >&2; return 1; }

  backlog_has_unresolved "$path" "$BACKLOG_HEALTH_PREFIX" "$subject" && return 3

  local text="$BACKLOG_HEALTH_PREFIX $date): $subject"
  if [ -n "$detail" ]; then
    # Substring expansion, not `cut`: bash 3.2 has ${var:off:len}, and a
    # subprocess here would have a failure mode this cannot afford. If `cut`
    # were ever missing or failed, the command substitution would yield the
    # empty string and the item would silently ship as a bare '...' — the
    # finding's reason deleted, with nothing anywhere saying so. Protocol §4:
    # never a quiet default in place of the real value.
    if [ "${#detail}" -gt "$BACKLOG_HEALTH_DETAIL_MAX" ]; then
      detail="${detail:0:$BACKLOG_HEALTH_DETAIL_MAX}..."
    fi
    text="$text — $detail"
  fi

  # One awk, exact whole-line match, no glob and no head/cut pipeline: the
  # section heading is data, and a `case` or grep pattern built from it would
  # be interpreted.
  local head_ln
  head_ln="$(awk -v s="$BACKLOG_HEALTH_SECTION" '$0 == s {print NR; exit}' "$path")"

  # No section yet: append it plus the item. The leading newline separates it
  # from whatever the file currently ends with.
  if [ -z "$head_ln" ]; then
    {
      printf '\n%s\n' "$BACKLOG_HEALTH_SECTION"
      printf '%s\n' "Filed automatically by \`.pipeline/run.sh\` from \`logs/last-health.md\`."
      printf '%s\n' "Full detail is in the dated \`logs/lab-health-*.log\` for that date."
      printf '%s\n' "- [ ] $text"
    } >> "$path" || return 1
    return 0
  fi

  # Insert after the section's last non-blank line, so items accumulate in
  # filing order under the heading and the blank line before the next heading
  # survives.
  local next_ln insert_at
  next_ln="$(awk -v s="$head_ln" 'NR>s && /^## / {print NR; exit}' "$path")"
  insert_at="$(awk -v s="$head_ln" -v e="${next_ln:-0}" \
    'NR>s && (e==0 || NR<e) && $0 !~ /^[[:space:]]*$/ {last=NR} END {print last+0}' "$path")"
  [ "$insert_at" -eq 0 ] && insert_at="$head_ln"

  local tmp n=0 line
  tmp="$path.health.$$"
  : > "$tmp" || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    n=$(( n + 1 ))
    printf '%s\n' "$line" >> "$tmp"
    [ "$n" -eq "$insert_at" ] && printf '%s\n' "- [ ] $text" >> "$tmp"
  done < "$path"
  mv "$tmp" "$path" || { rm -f "$tmp"; return 1; }
  return 0
}
