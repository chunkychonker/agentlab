#!/bin/bash
# migrate_backlog_findings.sh — moves the unclaimed findings in BACKLOG.md to
# GitHub issues (spec #6, rollout step 2 of 3).
#
# Not part of the plan since spec #7: findings go to the private
# logs/findings.md, not to issues. Kept with issues.sh; it still refuses to
# run while .pipeline/file-issues says off.
#
# Usage: bash .pipeline/migrate_backlog_findings.sh [--dry-run]
#
# A one-off a human runs by hand, never the nightly pipeline. For every
# `- [ ] fix (health <date>): <subject> — <detail>` line it files an issue
# (issue_file_finding in issues.sh) and, once GitHub has it, deletes the line.
# Claimed (`[building]`) and finished (`[done …]`) lines stay, and so does the
# "Stranded work" section. It edits BACKLOG.md and does not commit: you read
# `git diff BACKLOG.md` and commit it yourself.
#
#   --dry-run  lists what it would file, with the label each line would get.
#              Calls no gh, writes nothing. Works with the switch off.
#
# Safe to re-run: a line is deleted only after its issue exists, so a second
# run finds nothing left to do, and a line whose deletion failed is found
# again and gets a comment on its open issue, never a second issue.
#
# Refuses (rc 1, nothing done) unless .pipeline/file-issues says `on`: the
# issues are public, and that switch is the one place that decision lives.
#
# Exit codes: 0 every candidate is on GitHub and gone from BACKLOG.md (or the
# dry run listed them); 1 at least one line was left in place, or the run was
# refused; 2 bad usage.
#
# Do not run it while a night is running: both edit BACKLOG.md.
#
# bash 3.2 only (macOS system bash).
set -uo pipefail

MIGRATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=review_record.sh
. "$MIGRATE_DIR/review_record.sh"
# shellcheck source=issues.sh
. "$MIGRATE_DIR/issues.sh"
# shellcheck source=backlog.sh
. "$MIGRATE_DIR/backlog.sh"
# shellcheck source=health.sh
. "$MIGRATE_DIR/health.sh"

# The file migrated, relative to the repo root (run.sh's BACKLOG_FILE).
MIGRATE_BACKLOG="BACKLOG.md"
# The heading whose lines are never touched.
MIGRATE_KEEP_SECTION="## Stranded work"
# What the issue says about the run, since the original run is not recorded.
MIGRATE_RUN_NOTE="migrated from BACKLOG.md"
# Seconds between gh writes, to stay clear of GitHub's limit on how fast an
# account may create content. The tests set it to 0.
MIGRATE_PAUSE_SECONDS="${MIGRATE_PAUSE_SECONDS:-2}"

# migrate_candidates <backlog>
#   Prints every unclaimed finding line, in file order: lines that start
#   `- [ ] fix (health ` and are not under MIGRATE_KEEP_SECTION. rc 1 if the
#   file cannot be read.
migrate_candidates () {
  [ -r "$1" ] || return 1
  awk -v p="- [ ] $BACKLOG_HEALTH_PREFIX " -v keep="$MIGRATE_KEEP_SECTION" '
    /^## / { skip = (index($0, keep) == 1) }
    !skip && index($0, p) == 1 { print }
  ' "$1"
}

# migrate_parse_line <line>
#   Prints the line's date, subject and detail, one per line, splitting the
#   subject from the detail exactly as run.sh did when it filed the line
#   (health_item_subject). rc 1 if the line is not `... (health <date>): ...`.
migrate_parse_line () {
  local rest date text subject detail
  rest="${1#- \[ \] $BACKLOG_HEALTH_PREFIX }"
  [ "$rest" != "$1" ] || return 1
  case "$rest" in *'): '*) ;; *) return 1 ;; esac
  date="${rest%%): *}"
  text="${rest#*): }"
  [ -n "$date" ] && [ -n "$text" ] || return 1
  subject="$(health_item_subject "$text")"
  detail="${text#"$subject"}"
  detail="${detail# — }"
  printf '%s\n%s\n%s\n' "$date" "$subject" "$detail"
}

# migrate_label_for <subject>
#   `health` for a finding about the portfolio — an example, a knowledge note,
#   or a [[wikilink]] — and `pipeline` for everything else: run logs, abort
#   causes, claim lines, strays. Both observers filed under one prefix, so the
#   subject is the only evidence; the dry run shows every choice for review.
migrate_label_for () {
  case "$1" in
    examples/*|'`examples/'*|knowledge/*|'`knowledge/'*|*'[['*) echo health ;;
    *) echo pipeline ;;
  esac
}

# migrate_remove_line <path> <line>
#   Deletes the first line of <path> that is exactly <line>, through a temp
#   file and mv. rc 1, leaving <path> as it was, if no line matches or the
#   write fails.
migrate_remove_line () {
  local path="$1" tmp
  tmp="$path.migrate.$$"
  # Through the environment, not -v: awk -v would turn a backslash in the
  # line into an escape sequence, and the line would never match.
  MIGRATE_LINE="$2" awk '!done && $0 == ENVIRON["MIGRATE_LINE"] { done = 1; next } { print } END { exit !done }' \
    "$path" > "$tmp" || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$path" || { rm -f "$tmp"; return 1; }
}

migrate_main () {
  local dry=0 backlog
  case "${1:-}" in
    "") ;;
    --dry-run) dry=1 ;;
    *) echo "usage: $0 [--dry-run]" >&2; return 2 ;;
  esac
  [ "$#" -le 1 ] || { echo "usage: $0 [--dry-run]" >&2; return 2; }
  cd "$MIGRATE_DIR/.." || return 1
  backlog="$MIGRATE_BACKLOG"
  if [ ! -f "$backlog" ] || [ ! -r "$backlog" ] || [ ! -w "$backlog" ]; then
    echo "$backlog is not a readable, writable file." >&2; return 1
  fi
  if [ "$dry" -eq 0 ] && ! issues_enabled "$ISSUES_SWITCH_FILE"; then
    echo "$ISSUES_SWITCH_FILE does not say 'on', so no public issues are filed. Nothing done." >&2
    echo "Run with --dry-run to see what would be filed." >&2
    return 1
  fi

  local lines line parsed date subject detail label n=0 opened=0 dup=0 kept=0 rc
  lines="$(migrate_candidates "$backlog")" || return 1
  [ -n "$lines" ] && n="$(printf '%s\n' "$lines" | wc -l | tr -d ' ')"
  echo "$n unclaimed finding line(s) in $backlog."
  [ "$n" -eq 0 ] && return 0

  while IFS= read -r line; do
    if ! parsed="$(migrate_parse_line "$line")"; then
      echo "  left in place, cannot parse: $line"
      kept=$(( kept + 1 )); continue
    fi
    date="$(printf '%s\n' "$parsed" | sed -n 1p)"
    subject="$(printf '%s\n' "$parsed" | sed -n 2p)"
    detail="$(printf '%s\n' "$parsed" | sed -n 3p)"
    label="$(migrate_label_for "$subject")"
    if [ "$dry" -eq 1 ]; then
      printf '  would file [%s] %s\n' "$label" "$(issue_title_for "$subject")"
      continue
    fi
    issue_file_finding "$label" "$date" "$subject" "$detail" "$MIGRATE_RUN_NOTE"
    rc=$?
    case "$rc" in
      0) opened=$(( opened + 1 )); echo "  opened [$label]: $subject" ;;
      3) dup=$(( dup + 1 ));       echo "  already open, commented [$label]: $subject" ;;
      *) kept=$(( kept + 1 ));     echo "  left in place, not filed (rc=$rc): $subject"; continue ;;
    esac
    if ! migrate_remove_line "$backlog" "$line"; then
      kept=$(( kept + 1 ))
      echo "  filed, but could NOT delete the line (a re-run will comment, not duplicate): $subject"
    fi
    [ "$MIGRATE_PAUSE_SECONDS" -gt 0 ] && sleep "$MIGRATE_PAUSE_SECONDS"
  done <<< "$lines"

  if [ "$dry" -eq 1 ]; then
    echo "Dry run: nothing filed, $backlog unchanged."
    return 0
  fi
  echo "$opened issue(s) opened, $dup already open (commented), $kept line(s) left in place."
  echo "Review 'git diff $backlog', then commit it."
  [ "$kept" -eq 0 ]
}

# Sourced by the tests for its helpers; run directly, it migrates.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  migrate_main "$@"
fi
