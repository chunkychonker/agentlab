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
# Sourcing this file defines three functions and two constants. It runs nothing,
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
# keep stable. The prose notes runs sometimes leave between sections are
# dropped for a different reason: they do not begin with '- '.
#
# The '(none)' an empty section carries is NOT safe by that second argument,
# and is dropped by an explicit guard below instead. This comment used to
# claim the placeholder "needs no special case; it does not begin with '- '".
# That was false: agentlab-pipeline-observer.md's own worked example writes
# '## Phase failures' / '- (none)', which strips to a four-character body and
# files as a contentless backlog item — exactly what happened on 2026-09-02.
# The guard is an EXACT match, so a real finding that merely opens with the
# word still files. See
# research/2026-09-12-health-finding-none-placeholder.md.
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
            # Empty body, or the empty-section placeholder in its bulleted
            # form. Quoted patterns, so both are literal exact matches: a
            # finding like '(none) of the retries succeeded — ...' is real
            # text that happens to start with the word, and must still file.
            case "$body" in
              ''|'(none)') ;;
              *) printf '%s\n' "$body" ;;
            esac
            ;;
        esac
        ;;
    esac
  done < "$path"
  return 0
}

# --- Declared pauses --------------------------------------------------------
#
# The observer's Schedule gaps section lists every date in its window with no
# run log, and says only that the log is absent — it cannot see why. When the
# job was taken down on purpose, every one of those dates used to file as a
# 'fix (health ...)' item: 2026-09-28 filed six for a pause, and the queue they
# built up sat above nothing else, so the top-down claim would have spent the
# first night after a resume investigating the pause itself. The fix is the
# usual split: the observer keeps reporting (its log stays complete), and the
# filer drops gaps a human declared in advance.

# Where deliberate pauses are declared, named once. Tracked, not under logs/:
# it is desired state a human edits, and it must survive a fresh clone.
PIPELINE_PAUSES_FILE=".pipeline/pauses"

# The date of a single-night schedule-gap finding, on stdout.
#
# Matches the shape agentlab-pipeline-observer.md specifies, 'no run log for
# YYYY-MM-DD', alone or followed by ' — <detail>'. Anything else — a run
# outcome, an abort cause, or the free-form range wording an older report used
# ('... through YYYY-MM-DD') — is not a single-night gap and is left for the
# filer to file as before.
#
# Failure modes: returns 1 with nothing on stdout when <finding> is not a
# single-night gap. That is the common case, not an error, so nothing goes to
# stderr.
pipeline_gap_date () {
  local finding="$1" date
  case "$finding" in
    'no run log for '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]|\
    'no run log for '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' — '*)
      date="${finding#no run log for }"
      printf '%s\n' "${date:0:10}"
      return 0 ;;
  esac
  return 1
}

# Return 0 iff <date> (YYYY-MM-DD) falls inside a pause declared in <path>.
#
# <path> holds one range per line, 'START END', both YYYY-MM-DD and inclusive,
# optionally followed by '# note'. Blank lines and whole-line '#' comments are
# ignored.
#
# Failure modes (distinct codes — the caller files the gap on anything but 0,
# so a broken pauses file costs a noisy backlog, never a silently hidden gap):
#   0  <date> is inside a declared range
#   1  it is not — including when <path> does not exist, which is the normal
#      state of a lab that has never been paused, so nothing goes to stderr
#   2  <path> has a line that is not a valid range; message on stderr naming it
pipeline_date_paused () {
  local path="$1" date="$2"
  [ -f "$path" ] || return 1

  local line start end extra hit=1 bad=0 ymd='[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
  # `|| [ -n "$line" ]` so a final line with no trailing newline is not dropped.
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    # Unquoted on purpose: word splitting trims and separates the fields.
    read -r start end extra <<< "$line"
    [ -z "$start" ] && continue
    # YYYY-MM-DD compares correctly as a string, so no date(1) is needed — and
    # BSD and GNU date disagree on parsing flags anyway.
    # shellcheck disable=SC2053
    if [ -n "$extra" ] || [[ "$start" != $ymd ]] || [[ "$end" != $ymd ]] \
       || [[ "$end" < "$start" ]]; then
      echo "pipeline_date_paused: $path: not a 'START END' range: $line" >&2
      bad=1
      continue
    fi
    [[ ! "$date" < "$start" && ! "$date" > "$end" ]] && hit=0
  done < "$path"
  # A bad line wins over a match: a typo must be fixed, not quietly outvoted.
  [ "$bad" -eq 1 ] && return 2
  return "$hit"
}
