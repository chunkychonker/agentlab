#!/bin/bash
# review_record.sh — turns the reviewer's verdict file into the section run.sh
# adds to the PR body before it can merge.
#
# Sourced by run.sh. Pure: reads the files it is handed, writes only to
# stdout, calls no model and no network. Covered offline by
# test_review_record.sh.
#
# Why it exists: the reviewer's findings lived only in logs/last-review.md,
# which is git-ignored and overwritten every cycle. A PR merged with warnings
# and nits that nobody outside the runner could ever read. Spec #5 makes them
# part of the public PR, before the merge, so the record is part of the gate.
#
# bash 3.2 only (macOS system bash); safe under `set -uo pipefail`.

# The hidden line that marks the section as already added. Named once.
REVIEW_RECORD_MARKER="<!-- agentlab-review -->"
# GitHub refuses a PR body longer than this many characters.
REVIEW_RECORD_BODY_LIMIT=65536

# review_record_section <verdict-file>
#   Prints the markdown section: the marker, an "## Independent review"
#   heading, the verdict word, and the verdict file's Ran and Findings blocks
#   verbatim. Ran is every line from `Ran:` up to `Findings:`; Findings is
#   every line after `Findings:` to the end of the file.
#   rc 1, printing nothing, if the file is missing or has no line starting
#   `Findings:`. A verdict without findings is not one the reviewer's output
#   contract allows, so it is not posted as if it were.
review_record_section () {
  local file="$1"
  [ -r "$file" ] || return 1
  grep -q '^Findings:' "$file" || return 1
  local verdict ran findings
  verdict="$(sed -n 's/^VERDICT:[[:space:]]*\([A-Za-z]*\).*/\1/p' "$file" | head -1)"
  ran="$(awk '/^Findings:/ { exit } on { print } /^Ran:/ { sub(/^Ran:[[:space:]]*/, ""); if ($0 != "") print; on = 1 }' "$file")"
  findings="$(awk 'on { print } /^Findings:/ { sub(/^Findings:[[:space:]]*/, ""); if ($0 != "") print; on = 1 }' "$file")"
  printf '%s\n' "$REVIEW_RECORD_MARKER"
  printf '## Independent review\n\n'
  printf 'Written by the pipeline'"'"'s reviewer, a separate model run that did not write this code, before the PR was opened. Posted by `run.sh`; the merge waits for it.\n\n'
  printf '**Verdict:** %s\n\n' "${verdict:-unknown}"
  printf '**Ran:**\n\n%s\n\n' "${ran:-(not stated)}"
  printf '**Findings:**\n\n%s\n' "${findings:-(none listed)}"
}

# review_record_has_marker <body-file>
#   rc 0 if the body already holds the section's marker, 1 otherwise
#   (including when the file is missing).
review_record_has_marker () {
  [ -r "$1" ] && grep -qF -- "$REVIEW_RECORD_MARKER" "$1"
}

# review_record_secret_hit <text-file>
#   rc 0 if the text matches a credential pattern, 1 if not, 2 if the file
#   cannot be read (callers treat anything but 1 as "do not post").
#   The patterns are the reviewer's list made concrete: Anthropic (sk-ant-),
#   other sk- keys, GitHub tokens (ghp_, gho_, ghs_, ghu_, ghr_, github_pat_),
#   AWS access key ids (AKIA), Slack tokens (xox?-), PEM private keys, and
#   `KEY=value` lines for names that hold secrets.
#   Not matched: bare long hex. Commit SHAs are long hex, and findings cite
#   them; a scan that fired on every SHA would block every PR.
review_record_secret_hit () {
  local file="$1"
  [ -r "$file" ] || return 2
  grep -qE -- 'sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|xox[abposr]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(API_KEY|OAUTH_TOKEN|SECRET|PASSWORD|ACCESS_TOKEN)[A-Z_]*[[:space:]]*=[[:space:]]*[^[:space:]$<]{8,}' "$file"
}

# review_record_compose <body-file> <section-file>
#   Prints the new PR body: the existing body, a blank line, then the section.
#   If the result would exceed REVIEW_RECORD_BODY_LIMIT, only the section is
#   cut — never the maintainer's text above it — and ends with a note saying
#   so. Lengths are counted in bytes, which is never less than GitHub's
#   character count, so the result always fits; the cut never splits a
#   multi-byte character.
#   rc 1, printing nothing, if the existing body alone leaves no room for the
#   section's heading and the note, or either file cannot be read.
review_record_compose () {
  local body="$1" section="$2"
  [ -r "$body" ] && [ -r "$section" ] || return 1
  local note
  note="$(printf '\n\n_(Truncated to fit GitHub'"'"'s %s-character PR body limit. The full verdict is in the run log.)_' "$REVIEW_RECORD_BODY_LIMIT")"
  local body_n section_n note_n room
  body_n="$(wc -c < "$body" | tr -d ' ')"
  section_n="$(wc -c < "$section" | tr -d ' ')"
  note_n="$(printf '%s' "$note" | wc -c | tr -d ' ')"
  # 2 bytes for the blank line between body and section.
  room=$(( REVIEW_RECORD_BODY_LIMIT - 1 - body_n - 2 ))
  if [ "$section_n" -le "$room" ]; then
    cat "$body"; printf '\n\n'; cat "$section"
    return 0
  fi
  local keep=$(( room - note_n ))
  # Too little room for even the marker and heading: refuse rather than post
  # a section that is mostly a truncation note.
  [ "$keep" -ge 200 ] || return 1
  cat "$body"; printf '\n\n'
  head -c "$keep" "$section" | LC_ALL=C iconv -c -f UTF-8 -t UTF-8 2>/dev/null
  printf '%s' "$note"
}
