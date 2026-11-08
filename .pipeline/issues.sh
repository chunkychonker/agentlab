#!/bin/bash
# issues.sh — files a health or pipeline finding as a GitHub issue.
#
# Sourced by run.sh (and by migrate_backlog_findings.sh). The helpers above
# issue_file_finding are pure; issue_file_finding itself calls `gh`. Covered
# offline, with gh faked, by test_issues.sh.
#
# Why it exists (spec #6): findings were filed into BACKLOG.md, the same queue
# the researcher picks portfolio work from, so the two kinds of work competed
# for the same slots and the backlog filled with rot reports. Findings move to
# GitHub issues labeled `health` or `pipeline`; BACKLOG.md keeps topics.
#
# Rollout step 1 of 3: run.sh files to issues AND to the backlog, and only when
# .pipeline/file-issues says `on`. It ships `off`, because the issues are
# public and turning that on is the repo owner's call.
#
# Needs review_record.sh sourced first (for review_record_secret_hit): every
# body is scanned before it is posted, since the issues are public.
#
# bash 3.2 only (macOS system bash); safe under `set -uo pipefail`.

# GitHub's limit on an issue title, in characters.
ISSUE_TITLE_MAX=256
# The labels a finding may carry. One per observer.
ISSUE_LABELS="health pipeline"
# How many issues per label are fetched to look for a matching title. An open
# match older than this many issues would be missed and a duplicate opened.
ISSUE_LIST_LIMIT=1000
# The switch: `on` files issues, `off` (or a missing file) does not.
ISSUES_SWITCH_FILE=".pipeline/file-issues"
# Where a finding goes when gh cannot take it. Under logs/, so git-ignored.
UNFILED_FINDINGS_FILE="logs/unfiled-findings.md"
ISSUE_JQ="/usr/bin/jq"

# issues_enabled <switch-file>
#   rc 0 if the file holds `on`, 1 if it holds `off` or does not exist, 2 if
#   it holds anything else. Callers treat 2 as off: a typo must not publish.
issues_enabled () {
  local v
  [ -e "$1" ] || return 1
  v="$(tr -d '[:space:]' < "$1" 2>/dev/null)" || return 2
  case "$v" in
    on)  return 0 ;;
    off) return 1 ;;
    *)   return 2 ;;
  esac
}

# issue_title_for <subject>
#   Prints the issue title for a finding: the subject with every run of
#   whitespace (newlines and tabs included) squeezed to one space and the ends
#   trimmed. Over ISSUE_TITLE_MAX it is cut and ends in "...". Lengths are
#   counted in bytes, never fewer than GitHub's characters, and the cut never
#   splits a multi-byte character.
#   The title is the dedupe key, so this must give the same title for the same
#   subject every night: it depends on nothing but its argument.
#   rc 1, printing nothing, if nothing is left after trimming.
issue_title_for () {
  local t n
  t="$(printf '%s' "$1" | tr '\t\n\r' '   ' | tr -s ' ')"
  t="${t# }"; t="${t% }"
  [ -n "$t" ] || return 1
  n="$(printf '%s' "$t" | LC_ALL=C wc -c | tr -d ' ')"
  if [ "$n" -gt "$ISSUE_TITLE_MAX" ]; then
    t="$(printf '%s' "$t" | head -c $(( ISSUE_TITLE_MAX - 3 )) | LC_ALL=C iconv -c -f UTF-8 -t UTF-8 2>/dev/null)..."
  fi
  printf '%s\n' "$t"
}

# issue_body_for <label> <date> <detail> <run-ts> [previous-issue-number]
#   Prints the body of a new issue: the detail, when and in which run it was
#   seen, and which report it came from. With a previous issue number, adds a
#   line linking it — a finding that comes back after its issue was closed is
#   new information, so it gets a new issue rather than reopening the old one.
issue_body_for () {
  local label="$1" date="$2" detail="$3" ts="$4" prev="${5:-}" report
  case "$label" in
    pipeline) report="logs/last-pipeline-health.md" ;;
    *)        report="logs/last-health.md" ;;
  esac
  printf '%s\n\n' "${detail:-(no detail given)}"
  printf 'Observed: %s (run %s)\n' "$date" "${ts:-unknown}"
  printf 'Filed by `.pipeline/run.sh` from `%s`. Full detail is in that night'"'"'s log on the runner.\n' "$report"
  [ -n "$prev" ] && printf '\nThis came back after #%s was closed.\n' "$prev"
  return 0
}

# issue_comment_for <date> <detail> <run-ts>
#   Prints the comment added when an open issue's finding is seen again.
issue_comment_for () {
  printf 'Re-observed %s (run %s).\n\n%s\n' "$1" "${3:-unknown}" "${2:-(no detail given)}"
}

# issue_match <title>
#   Reads `gh issue list --json number,title,state` output on stdin and prints
#   the issue whose title is exactly <title>: `OPEN <n>` if any open one
#   matches (the newest), else `CLOSED <n>` (the newest closed one), else
#   `NONE`. The match is exact, never GitHub's fuzzy search.
#   rc 1, printing nothing, if stdin is not a JSON array.
issue_match () {
  "$ISSUE_JQ" -r --arg t "$1" '
    if type != "array" then error("not an array") else . end
    | [ .[] | select(.title == $t) ] | sort_by(-.number)
    | (map(select(.state == "OPEN")) + map(select(.state != "OPEN"))) | first
    | if . == null then "NONE" elif .state == "OPEN" then "OPEN \(.number)" else "CLOSED \(.number)" end
  ' 2>/dev/null
}

# issue_file_finding <label> <date> <subject> <detail> [run-ts]
#   Reconciles GitHub toward one open issue for this finding:
#     - an open issue with the same title exists: adds a "Re-observed" comment
#     - only a closed one exists: opens a new issue that links the old one
#     - none exists: opens a new issue
#   <label> must be one of ISSUE_LABELS. Reasons for failing go to stderr.
#   Return codes:
#     0  a new issue was opened
#     3  a comment was added to the open issue
#     1  nothing was posted: a bad label, an empty subject, the body matched a
#        credential pattern, or a gh call failed. If `gh issue list` worked
#        and the create or comment failed, GitHub is unchanged.
#   Not idempotent within a night: run twice, an open issue gets two comments.
issue_file_finding () {
  local label="$1" date="$2" subject="$3" detail="$4" ts="${5:-}"
  local title list match body rc=1
  case " $ISSUE_LABELS " in
    *" $label "*) ;;
    *) echo "issue_file_finding: label '$label' is not one of: $ISSUE_LABELS" >&2; return 1 ;;
  esac
  title="$(issue_title_for "$subject")" || { echo "issue_file_finding: empty subject" >&2; return 1; }
  if ! list="$(gh issue list --label "$label" --state all --limit "$ISSUE_LIST_LIMIT" \
                  --json number,title,state)"; then
    echo "issue_file_finding: 'gh issue list' failed" >&2; return 1
  fi
  if ! match="$(printf '%s' "$list" | issue_match "$title")" || [ -z "$match" ]; then
    echo "issue_file_finding: could not read gh's issue list" >&2; return 1
  fi
  body="$(mktemp)" || return 1
  case "$match" in
    "OPEN "*) issue_comment_for "$date" "$detail" "$ts" > "$body" ;;
    "CLOSED "*) issue_body_for "$label" "$date" "$detail" "$ts" "${match#CLOSED }" > "$body" ;;
    *) issue_body_for "$label" "$date" "$detail" "$ts" > "$body" ;;
  esac
  # The issues are public: anything that looks like a credential stays local.
  if review_record_secret_hit "$body" || [ $? -ne 1 ]; then
    echo "issue_file_finding: the text matches a credential pattern — not posted" >&2
  else
    case "$match" in
      "OPEN "*)
        if gh issue comment "${match#OPEN }" --body-file "$body" >/dev/null; then rc=3
        else echo "issue_file_finding: 'gh issue comment' failed on #${match#OPEN }" >&2; fi ;;
      *)
        if gh issue create --title="$title" --label "$label" --body-file "$body" >/dev/null; then rc=0
        else echo "issue_file_finding: 'gh issue create' failed" >&2; fi ;;
    esac
  fi
  rm -f "$body"
  return "$rc"
}

# issue_record_unfiled <file> <label> <date> <subject> <detail>
#   Appends one line for a finding that could not be filed, so it is not lost:
#   `- [<label>] <date>: <subject> — <detail>`, with a heading if the file is
#   new. rc 1 if the file cannot be written.
issue_record_unfiled () {
  local file="$1" line
  line="- [$2] $3: $4"
  [ -n "$5" ] && line="$line — $5"
  if [ ! -s "$file" ]; then
    printf '# Findings that could not be filed as GitHub issues\n\nFile each by hand, then delete its line.\n\n' > "$file" || return 1
  fi
  printf '%s\n' "$line" >> "$file"
}
