#!/bin/bash
# agentlab daily pipeline: research -> build -> maintain (open PR).
# Each phase is a headless Claude Code run delegating to one specialist subagent.
# They coordinate through the repo (research/, examples/, BACKLOG.md, git state).
#
# Run manually first to shake out PATH/auth:  bash .pipeline/run.sh
# Scheduled via launchd job com.steeb.agentlab.daily.

set -uo pipefail

# launchd gives a minimal PATH; these are the real tool locations on this Mac.
export PATH="/Users/steeb/.local/bin:/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.13/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO="/Users/steeb/agentlab"
cd "$REPO" || { echo "cannot cd $REPO"; exit 1; }

mkdir -p logs
TS="$(date +%Y-%m-%d_%H%M%S)"
LOG="logs/run-$TS.log"

# Headless runs can't answer permission prompts. This job operates only inside
# its own repo, and nothing reaches main without a human merging the PR, so we
# let it run non-interactively. Review the PR before merging.
CLAUDE="claude -p --permission-mode bypassPermissions"

echo "=== agentlab pipeline $TS ===" | tee -a "$LOG"

# Preflight: the maintainer needs a GitHub remote + auth to push and open a PR.
if ! git remote get-url origin >/dev/null 2>&1; then
  echo "NO GIT REMOTE 'origin' — set it up first (see setup notes). Aborting." | tee -a "$LOG"
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "gh NOT AUTHENTICATED — run 'gh auth login' first. Aborting." | tee -a "$LOG"
  exit 1
fi

run_phase () {
  local name="$1" prompt="$2"
  echo "" | tee -a "$LOG"
  echo "--- phase: $name ---" | tee -a "$LOG"
  if ! $CLAUDE "$prompt" >>"$LOG" 2>&1; then
    echo "phase '$name' exited non-zero — see $LOG" | tee -a "$LOG"
    return 1
  fi
}

run_phase "research" \
  "Use the agentlab-researcher subagent to run today's research cycle end to end, following its instructions exactly." || exit 1

run_phase "build" \
  "Use the agentlab-builder subagent to build today's increment from the newest research note, following its instructions exactly. Verify it runs." || exit 1

run_phase "review" \
  "Use the agentlab-reviewer subagent to independently review today's working-tree diff, run its tests/lint, and write a PASS/FAIL verdict to logs/last-review.md. Do not modify the increment." || exit 1

run_phase "maintain" \
  "Use the agentlab-maintainer subagent. Read logs/last-review.md; ONLY if the verdict is PASS, commit today's work as Steve Ling <steveylingy@gmail.com>, push a branch, and open a PR. On FAIL or missing verdict, do nothing and say why."

echo "" | tee -a "$LOG"
echo "=== done $TS === (full log: $LOG)" | tee -a "$LOG"
