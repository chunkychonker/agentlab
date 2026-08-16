# Pipeline-health snapshot parsing for the agentlab pipeline.
#
# The sibling of health.sh. That file parses what the *portfolio* observer
# found (do the examples still run, do the wikilinks resolve); this one parses
# what the *pipeline* observer found (did the nights actually run, did a cause
# recur, did a claim get stranded). Both read; neither writes, files, or
# touches git — turning a finding into a backlog item is backlog.sh's job, and
# producing the report is the observer agent's. That split is what makes this
# testable offline (see .pipeline/test_gates.sh).
#
# Why it exists: PR #29 closed the loop for portfolio rot — the health agent
# reports, file_health_findings() files. Nothing did the same for the pipeline
# itself. agentlab-researcher.md's demo mode starts at "read BACKLOG.md, pick
# the topmost unclaimed item"; it never reads logs/ or .pipeline/. So every
# pipeline improvement to date arrived on a human-initiated branch (#28, #29,
# #30, #31), and the one bug the logs would have shown plainly — the
# reachability probe bounding the whole transfer rather than the connection —
# sat misdiagnosed as "VPN off" across several nights before a human read them.
# This is the parsing half of closing that loop.
#
# Sourcing this file defines one function and one constant. It runs nothing,
# prints nothing, and touches no files. run.sh sources it into a shell with
# `set -uo pipefail` on, so every expansion below must be safe under -u.
#
# It deliberately does NOT define its own subject extractor: the dedupe key
# must agree with backlog_has_unresolved or filing silently stops deduping, so
# run.sh reuses health_item_subject() from health.sh for both observers. One
# definition, one behaviour.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57) — see
# knowledge/bash-3.2-testable-scripts.md.

# The pipeline observer's always-latest snapshot, named once. Same handoff
# pattern as HEALTH_SNAPSHOT_FILE and REVIEW_VERDICT_FILE; the dated
# logs/lab-pipeline-*.log is the permanent record and is deliberately not
# parsed here.
PIPELINE_SNAPSHOT_FILE="logs/last-pipeline-health.md"

# One line per actionable finding in <path>, on stdout, in report order.
#
# Reads ONLY the sections whose shape
# .claude/agents/agentlab-pipeline-observer.md actually specifies:
#
#   ## Run outcomes            -> the '- ABORTED ' and '- PARTIAL ' lines
#                                 (a run that reached `=== done ===` having
#                                 shipped every cycle is not a finding, and the
#                                 observer is told to list it as '- OK ' so the
#                                 report stays a complete record rather than a
#                                 findings-only list)
#   ## Recurring abort causes  -> every '- ' line
#   ## Phase failures          -> every '- ' line
#   ## Claim-state drift       -> every '- ' line
#   ## Schedule gaps           -> every '- ' line
#   ## Quarantined strays      -> every '- ' line
#
# Any other heading ends the current section and is ignored, exactly as in
# health_findings: this parser's contract is the documented output shape, and
# an undocumented section is by definition not something the agent promised to
# keep stable. A '(none)' placeholder needs no special case; it does not begin
# with '- '. Nor do the prose notes runs sometimes leave between sections.
#
# Emitted lines are the finding with its list marker (and, in Run outcomes, the
# status label and the report's alignment padding) removed, otherwise verbatim.
#
# Failure modes:
#   <path> is not a readable regular file -> message on stderr, return 1,
#   nothing on stdout. A readable snapshot with zero findings is NOT a failure:
#   it prints nothing and returns 0, which is the healthy case and the one we
#   want to be the common one.
pipeline_findings () {
  local path="$1"
  if [ ! -f "$path" ] || [ ! -r "$path" ]; then
    echo "pipeline_findings: '$path' is not a readable file" >&2
    return 1
  fi

  local line section="" body
  # `|| [ -n "$line" ]` so a final line with no trailing newline is not dropped.
  while IFS= read -r line || [ -n "$line" ]; do
    # Specific headings before the generic one: case takes the first match, and
    # '## '* would otherwise swallow the six we care about.
    case "$line" in
      '## Run outcomes')           section="outcomes" ; continue ;;
      '## Recurring abort causes') section="plain"    ; continue ;;
      '## Phase failures')         section="plain"    ; continue ;;
      '## Claim-state drift')      section="plain"    ; continue ;;
      '## Schedule gaps')          section="plain"    ; continue ;;
      '## Quarantined strays')     section="plain"    ; continue ;;
      '## '*)                      section=""         ; continue ;;
    esac
    [ -z "$section" ] && continue

    case "$section" in
      outcomes)
        # OK runs are recorded for completeness, not filed. Anything else in
        # this section is a night that did not do what it was asked to.
        case "$line" in
          '- ABORTED '*|'- PARTIAL '*)
            body="${line#- }"
            body="${body#ABORTED }"
            body="${body#PARTIAL }"
            # The report right-pads the status label to align the run names, so
            # the payload starts an unknown number of spaces in.
            while [ "${body# }" != "$body" ]; do body="${body# }"; done
            [ -n "$body" ] && printf '%s\n' "$body"
            ;;
        esac
        ;;
      plain)
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
