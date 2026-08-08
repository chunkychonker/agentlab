#!/bin/bash
# Regression harness for the agentlab-reviewer subagent: proves it still
# catches specific bug classes it has already caught once, after any edit
# to .claude/agents/agentlab-reviewer.md.
#
# On-demand only. NOT wired into .pipeline/run.sh or launchd — run by hand
# after touching the reviewer's agent definition:
#
#   bash eval/run_reviewer_eval.sh
#
# Each fixture under eval/fixtures/<slug>/ is materialized as a throwaway
# increment at examples/_eval_<slug>/, reviewed with the exact same
# invocation run.sh uses for the real review phase, then removed. Costs one
# real (opus-model) reviewer call per fixture.

set -uo pipefail

export PATH="/Users/steeb/.local/bin:/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.13/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO="/Users/steeb/agentlab"
cd "$REPO" || { echo "cannot cd $REPO"; exit 1; }

CLAUDE="claude -p --permission-mode bypassPermissions"
FIXTURES_DIR="eval/fixtures"
REVIEW_PROMPT="Use the agentlab-reviewer subagent to independently review today's working-tree diff, run its tests/lint, and write a PASS/FAIL verdict to logs/last-review.md. Do not modify the increment."

# --- Preflight ---

check_reachable () { curl -sS --max-time 5 -o /dev/null "$1"; }
if ! check_reachable "https://api.anthropic.com"; then
  echo "NETWORK UNREACHABLE (api.anthropic.com) — check VPN. Aborting."
  exit 1
fi

if pgrep -f '\.pipeline/run\.sh' >/dev/null 2>&1; then
  echo "A real pipeline cycle looks to be running (run.sh in process list)." \
       "Aborting to avoid racing it — re-run once it finishes."
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree is not clean — resolve or stash before running the eval. Aborting."
  echo "(This eval temporarily stages fixture files as an uncommitted diff; it refuses" \
       "to run against a tree that already has uncommitted changes of its own.)"
  exit 1
fi

if [ ! -d "$FIXTURES_DIR" ] || [ -z "$(ls -A "$FIXTURES_DIR" 2>/dev/null)" ]; then
  echo "No fixtures found under $FIXTURES_DIR. Nothing to run."
  exit 1
fi

# --- Cleanup guarantee ---

REVIEW_BACKUP=""
if [ -f logs/last-review.md ]; then
  REVIEW_BACKUP="$(mktemp)"
  cp logs/last-review.md "$REVIEW_BACKUP"
fi

STAGED_PATH=""

cleanup () {
  if [ -n "$STAGED_PATH" ]; then
    rm -rf "$STAGED_PATH"
  fi
  git clean -fd examples/ >/dev/null 2>&1 || true
  if [ -n "$REVIEW_BACKUP" ]; then
    cp "$REVIEW_BACKUP" logs/last-review.md
    rm -f "$REVIEW_BACKUP"
  else
    rm -f logs/last-review.md
  fi
}
trap cleanup EXIT

# --- Run each fixture ---

declare -a RESULT_LINES
overall_status=0

for fixture_dir in "$FIXTURES_DIR"/*/; do
  slug="$(basename "$fixture_dir")"
  expect_file="${fixture_dir}expect.txt"

  if [ ! -f "$expect_file" ]; then
    echo "skip $slug: no expect.txt"
    continue
  fi
  if [ ! -d "${fixture_dir}increment" ]; then
    echo "skip $slug: no increment/ directory"
    continue
  fi

  EXPECT_VERDICT="$(grep '^VERDICT:' "$expect_file" | head -1 | sed 's/^VERDICT:[[:space:]]*//')"

  STAGED_PATH="examples/_eval_${slug}"
  rm -rf "$STAGED_PATH"
  cp -r "${fixture_dir}increment" "$STAGED_PATH"
  rm -f logs/last-review.md

  echo ""
  echo "=== $slug ==="
  RUN_LOG="$(mktemp)"
  if ! $CLAUDE "$REVIEW_PROMPT" >"$RUN_LOG" 2>&1; then
    echo "reviewer invocation failed — see $RUN_LOG"
    RESULT_LINES+=("$slug: ERROR (invocation failed, log: $RUN_LOG)")
    overall_status=1
    rm -rf "$STAGED_PATH"; STAGED_PATH=""
    continue
  fi

  if [ ! -f logs/last-review.md ]; then
    echo "$slug: MISSED — reviewer produced no verdict"
    RESULT_LINES+=("$slug: MISSED (no verdict written, log: $RUN_LOG)")
    overall_status=1
  else
    ACTUAL_VERDICT_LINE="$(grep '^VERDICT:' logs/last-review.md | head -1)"
    verdict_ok=0
    echo "$ACTUAL_VERDICT_LINE" | grep -q "$EXPECT_VERDICT" && verdict_ok=1

    patterns_ok=1
    while IFS= read -r pattern; do
      [ -z "$pattern" ] && continue
      grep -qiE "$pattern" logs/last-review.md || patterns_ok=0
    done < <(grep '^PATTERN:' "$expect_file" | sed 's/^PATTERN:[[:space:]]*//')

    if [ "$verdict_ok" -eq 1 ] && [ "$patterns_ok" -eq 1 ]; then
      echo "$slug: CAUGHT"
      RESULT_LINES+=("$slug: CAUGHT")
    elif [ "$verdict_ok" -eq 1 ]; then
      echo "$slug: MISDIAGNOSED — verdict matched ($ACTUAL_VERDICT_LINE) but findings don't cite the seeded bug"
      RESULT_LINES+=("$slug: MISDIAGNOSED (verdict ok, findings don't match: $(cp logs/last-review.md "/tmp/agentlab-eval-${slug}-review.md"; echo "/tmp/agentlab-eval-${slug}-review.md"))")
      overall_status=1
    else
      echo "$slug: MISSED — expected VERDICT: $EXPECT_VERDICT, got: $ACTUAL_VERDICT_LINE"
      RESULT_LINES+=("$slug: MISSED (expected $EXPECT_VERDICT, got: $ACTUAL_VERDICT_LINE)")
      overall_status=1
    fi
  fi

  rm -f "$RUN_LOG"
  rm -rf "$STAGED_PATH"; STAGED_PATH=""
  git clean -fd examples/ >/dev/null 2>&1 || true
done

echo ""
echo "=== summary ==="
for line in "${RESULT_LINES[@]}"; do
  echo "$line"
done

exit $overall_status
