#!/bin/bash
# metrics_report.sh — per-phase medians from logs/metrics.jsonl.
# Usage: bash .pipeline/metrics_report.sh [nights]   (default 7)
#
# Read-only. The numbers come from run_phase's one-line-per-phase records
# (see metrics.sh); a night with no records simply does not count.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=metrics.sh
. .pipeline/metrics.sh

nights="${1:-7}"
file="logs/metrics.jsonl"
case "$nights" in ''|*[!0-9]*|0) echo "usage: $0 [nights]  (a positive integer)" >&2; exit 2 ;; esac
if [ ! -s "$file" ]; then
  echo "no metrics yet: $file is missing or empty — it fills in as phases run." >&2
  exit 1
fi
echo "last $nights night(s) in $file"
metrics_summary "$file" "$nights" | column -t -s "$(printf '\t')"
exit "${PIPESTATUS[0]}"
