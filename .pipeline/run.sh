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

# Preflight: both api.anthropic.com (claude -p) and github.com (git/gh) must be
# reachable. This box's network is VPN-gated; the 403 auth and "can't reach
# main" failures on 2026-08-02/03/04 were VPN-off, not credential or repo
# problems — check connectivity first so that shows up as one clear message.
# No -f: any HTTP response (even 404) means the connection succeeded, which is
# all this checks. Only DNS/timeout/refused failures should trip it.
check_reachable () { curl -sS --max-time 5 -o /dev/null "$1"; }
if ! check_reachable "https://api.anthropic.com" || ! check_reachable "https://github.com"; then
  echo "NETWORK UNREACHABLE (api.anthropic.com / github.com) — check VPN. Aborting." | tee -a "$LOG"
  exit 1
fi

# Preflight: the maintainer needs a GitHub remote + auth to push and open a PR.
if ! git remote get-url origin >/dev/null 2>&1; then
  echo "NO GIT REMOTE 'origin' — set it up first (see setup notes). Aborting." | tee -a "$LOG"
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "gh NOT AUTHENTICATED — run 'gh auth login' first. Aborting." | tee -a "$LOG"
  exit 1
fi

# Start every cycle from a clean, up-to-date main. Each day's branch must be
# cut from main (not from yesterday's unmerged cycle branch), or PR diffs
# accumulate across days instead of staying independent daily slices.
if ! git switch main >/dev/null 2>>"$LOG" || ! git pull --ff-only origin main >/dev/null 2>>"$LOG"; then
  echo "could not switch to a clean, up-to-date main — resolve manually before the next run. Aborting." | tee -a "$LOG"
  exit 1
fi

# Untracked build artifacts (.venv, __pycache__, example dirs) from a
# previous day's unmerged cycle branch survive a plain `git switch main` and
# accumulate on disk — main never had them tracked, so they just sit there.
# Only safe to wipe them if main's working tree is otherwise clean. The
# postflight below now auto-snapshots a FAILed cycle's dirty main onto a
# recovery branch, so this should rarely trip — but it stays as a hard abort
# (not a guess-and-clean) in case main is ever left dirty by something other
# than this script, e.g. manual work done directly in the repo.
if [ -n "$(git status --porcelain)" ]; then
  echo "main has uncommitted or untracked changes (likely a FAILed cycle awaiting a manual fix) — resolve manually before the next run. Aborting." | tee -a "$LOG"
  exit 1
fi
git clean -fdx -e logs/ -e '.claude/settings.local.json' -e '.env*' -e graphify-out/ >>"$LOG" 2>&1

# Mode is the single source of truth for demo vs. project work, read by both
# this script (to fail fast on a bad config) and each subagent itself (each
# reads .pipeline/mode as step 0 of its own procedure) — not duplicated into
# the phase prompts below, so run.sh and the agent defs can't drift out of
# sync on which mode is active.
MODE_FILE=".pipeline/mode"
MODE="$(tr -d '[:space:]' < "$MODE_FILE" 2>/dev/null)"
[ -z "$MODE" ] && MODE="demo"
if [[ "$MODE" == project:* ]]; then
  SLUG="${MODE#project:}"
  if [ ! -f "projects/$SLUG/PLAN.md" ]; then
    echo "MODE is 'project:$SLUG' but projects/$SLUG/PLAN.md is missing — fix .pipeline/mode or create the plan (see projects/README.md). Aborting." | tee -a "$LOG"
    exit 1
  fi
  echo "mode: project:$SLUG (projects/$SLUG/PLAN.md)" | tee -a "$LOG"
elif [ "$MODE" == "demo" ]; then
  echo "mode: demo (BACKLOG.md)" | tee -a "$LOG"
else
  echo "MODE '$MODE' in .pipeline/mode is neither 'demo' nor 'project:<slug>' — fix it. Aborting." | tee -a "$LOG"
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

# Postflight: guarantee main is clean before this script exits, no matter what
# happened above. On FAIL, the maintainer deliberately leaves today's diff
# uncommitted on main for a human to inspect — correct in isolation, but it
# means the *next* run's preflight (line ~67) aborts on "main not clean",
# which is what happened on 2026-08-02/03/07. Nothing here discards work: it
# snapshots whatever's dirty onto a clearly-named, pushed recovery branch (so
# it survives even a lost laptop), then resets main so tomorrow's run isn't
# blocked by today's failure. If the push fails (offline/auth), the commit
# still exists locally on the recovery branch — nothing is lost either way.
if [ -n "$(git status --porcelain)" ]; then
  RECOVERY_BRANCH="cycle/${TS%%_*}-unshipped-$(date +%H%M%S)"
  echo "" | tee -a "$LOG"
  echo "main left dirty after this cycle — snapshotting to $RECOVERY_BRANCH instead of leaving it for the next run to trip over." | tee -a "$LOG"
  snapshot_committed=0
  if ! git switch -c "$RECOVERY_BRANCH" >>"$LOG" 2>&1; then
    echo "could not create recovery branch $RECOVERY_BRANCH — leaving main dirty; resolve manually before the next run." | tee -a "$LOG"
  elif ! git add -A >>"$LOG" 2>&1 || ! git commit -m "wip: cycle $TS left uncommitted (see logs/run-$TS.log, logs/last-review.md)" >>"$LOG" 2>&1; then
    echo "snapshot commit on $RECOVERY_BRANCH failed — leaving main dirty; resolve manually before the next run." | tee -a "$LOG"
  else
    snapshot_committed=1
    git push -u origin "$RECOVERY_BRANCH" >>"$LOG" 2>&1 \
      || echo "push of $RECOVERY_BRANCH failed — work is still safe locally on that branch." | tee -a "$LOG"
  fi
  # Whichever step above failed (or none did), get back onto main: `git
  # switch` carries any still-uncommitted changes with it rather than
  # discarding them, so main ends up either clean (snapshot committed) or
  # genuinely dirty (snapshot failed) — never silently left on the recovery
  # branch instead.
  git switch main >>"$LOG" 2>&1
  # A recovery branch that never got a commit (switch/add/commit failed
  # partway) is just clutter left by `switch -c` — the real diff is still on
  # main, dirty, so there's nothing on it worth keeping. Drop it locally.
  if [ "$snapshot_committed" -eq 0 ] && git show-ref --verify --quiet "refs/heads/$RECOVERY_BRANCH"; then
    git branch -D "$RECOVERY_BRANCH" >>"$LOG" 2>&1 || true
  fi
fi

echo "" | tee -a "$LOG"
echo "=== done $TS === (full log: $LOG)" | tee -a "$LOG"
