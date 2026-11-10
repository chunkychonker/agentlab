#!/bin/bash
# findings.sh — saves a health or pipeline finding to the private findings
# file, through findings_store.py.
#
# Sourced by run.sh. Spec #7: findings go to $FINDINGS_FILE on the runner
# (under logs/, so git-ignored and never pushed), where they stay until the
# repo owner reads them and marks them `[x]`. The line format and the
# seen-again / reopen rules live in findings_store.py; this file only names
# the paths and the return codes.
#
# Rollout step 1 of 3: run.sh saves each finding here AND files it to
# BACKLOG.md as before, so the two can be compared for a night or two.
#
# Covered by test_findings_store.sh. bash 3.2 only (macOS system bash); safe
# under `set -uo pipefail`.

FINDINGS_FILE="logs/findings.md"
FINDINGS_STORE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/findings_store.py"
# The stdlib-only store runs under the system Python, never a venv's.
FINDINGS_PYTHON="/usr/bin/python3"

# findings_store.py's exit codes, as record_finding returns them.
FINDING_ADDED=0
FINDING_SEEN_AGAIN=3
FINDING_REOPENED=4

# record_finding <label> <date> <subject> <detail>
#   Saves one finding to $FINDINGS_FILE. <label> is health or pipeline,
#   <date> is YYYY-MM-DD. The store's reason for failing goes to stderr.
#   Return codes: $FINDING_ADDED a new line, $FINDING_SEEN_AGAIN an open line
#   updated, $FINDING_REOPENED a fixed line reopened, anything else nothing
#   written (the file is left as it was).
#   Safe to repeat within a night: the same finding on the same date never
#   counts twice.
record_finding () {
  # --opt=value, so a subject or detail that starts with '-' is not read as
  # an option.
  "$FINDINGS_PYTHON" "$FINDINGS_STORE" record --file "$FINDINGS_FILE" \
    --label "$1" --date "$2" --subject="$3" --detail="$4"
}
