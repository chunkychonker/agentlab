# Health-snapshot parsing for the agentlab pipeline.
#
# This file extracts actionable findings from a lab-health snapshot. It reads;
# it never writes, never files anything, and never touches git — turning a
# finding into a backlog item is backlog.sh's job, and running the check is the
# health agent's. That split is what makes this testable offline (see
# .pipeline/test_gates.sh).
#
# Why it exists: .claude/agents/agentlab-health.md correctly forbids the health
# agent from fixing anything, and says fixing rot is "a future build cycle's job
# (a normal backlog item), not yours". PIPELINE.md repeats it. But nothing in
# the pipeline ever filed that backlog item, so every finding was written to
# logs/last-health.md and then dropped: the loop looked closed and was not.
# This is the parsing half of closing it.
#
# Sourcing this file defines two functions and one constant. It runs nothing,
# prints nothing, and touches no files. run.sh sources it into a shell with
# `set -uo pipefail` on, so every expansion below must be safe under -u.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57) — see
# knowledge/bash-3.2-testable-scripts.md.

# The health agent's always-latest snapshot, named once. Same handoff pattern
# as REVIEW_VERDICT_FILE; the dated logs/lab-health-*.log is the permanent
# record and is deliberately not parsed here.
HEALTH_SNAPSHOT_FILE="logs/last-health.md"

# One line per actionable finding in <path>, on stdout, in report order.
#
# Reads ONLY the three sections whose shape .claude/agents/agentlab-health.md
# actually specifies:
#
#   ## Example results        -> the '- FAIL ' lines (PASS and SKIPPED are not
#                                findings; agentlab-health.md is explicit that a
#                                SKIPPED example "is not a finding against the
#                                example")
#   ## Broken wikilinks       -> every '- ' line
#   ## Backlog/PR mismatches  -> every '- ' line
#
# Any other heading ends the current section and is ignored — including the
# free-form "Other drift noted, deliberately not fixed" section a past run
# invented. That is deliberate: this parser's contract is the documented output
# shape, and an undocumented section is by definition not something the agent
# promised to keep stable. A '(none)' placeholder needs no special case; it
# does not begin with '- '. Nor do the prose notes runs sometimes leave between
# sections.
#
# Emitted lines are the finding with its list marker (and the FAIL label and
# the report's alignment padding) removed, otherwise verbatim.
#
# Failure modes:
#   <path> is not a readable regular file -> message on stderr, return 1,
#   nothing on stdout. A readable snapshot with zero findings is NOT a failure:
#   it prints nothing and returns 0, which is the healthy case and by far the
#   common one.
health_findings () {
  local path="$1"
  if [ ! -f "$path" ] || [ ! -r "$path" ]; then
    echo "health_findings: '$path' is not a readable file" >&2
    return 1
  fi

  local line section="" body
  # `|| [ -n "$line" ]` so a final line with no trailing newline is not dropped.
  while IFS= read -r line || [ -n "$line" ]; do
    # Specific headings before the generic one: case takes the first match, and
    # '## '* would otherwise swallow the three we care about.
    case "$line" in
      '## Example results')       section="examples"  ; continue ;;
      '## Broken wikilinks')      section="wikilinks" ; continue ;;
      '## Backlog/PR mismatches') section="backlog"   ; continue ;;
      '## '*)                     section=""          ; continue ;;
    esac
    [ -z "$section" ] && continue

    case "$section" in
      examples)
        case "$line" in
          '- FAIL '*)
            body="${line#- FAIL }"
            # The report right-pads the verdict label to align the paths, so
            # the payload starts an unknown number of spaces in.
            while [ "${body# }" != "$body" ]; do body="${body# }"; done
            [ -n "$body" ] && printf '%s\n' "$body"
            ;;
        esac
        ;;
      wikilinks|backlog)
        case "$line" in
          '- '*)
            body="${line#- }"
            [ -n "$body" ] && printf '%s\n' "$body"
            ;;
        esac
        ;;
    esac
  done < "$path"
  return 0
}

# The subject of <finding> — everything before the first ' — ' — on stdout.
#
# The subject is the finding's identity: the example directory, the broken
# link, the quoted backlog line. It is what stays stable when the health agent
# rewords the reason on a later night, which is exactly the property a dedupe
# key needs. The reason after the em dash is prose and will drift.
#
# A finding with no em-dash separator is its own subject. That is the honest
# answer rather than a guess: an unseparated finding has no reason to strip.
health_item_subject () {
  local finding="$1"
  case "$finding" in
    *' — '*) printf '%s\n' "${finding%% — *}" ;;
    *)       printf '%s\n' "$finding" ;;
  esac
}
