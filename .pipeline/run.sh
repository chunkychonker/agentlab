#!/bin/bash
# agentlab nightly pipeline: a backlog replenishment check, then
# N x (research -> build -> review -> maintain -> merge), then the same
# replenishment check again, then a periodic lab health check.
# Each phase is a headless Claude Code run delegating to one specialist subagent.
# They coordinate through the repo (research/, examples/, BACKLOG.md, git state).
#
# Cycles run STRICTLY SEQUENTIALLY, each cut from a freshly-merged main. That
# ordering is what keeps concurrent-edit conflicts structurally impossible on
# the shared files every cycle touches (BACKLOG.md, knowledge/INDEX.md, and
# backlinks into existing knowledge notes). Do not parallelise this loop
# without first solving item assignment: each researcher picks "the topmost
# unclaimed item" from a working tree the others cannot see, so N parallel
# researchers would all pick the SAME item.
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
#
# The model is passed explicitly per phase rather than inherited from
# ~/.claude/settings.json, so a change to the interactive default can never
# silently re-price the nightly job. See run_phase for which phase gets which.
CLAUDE="claude -p --permission-mode bypassPermissions"

echo "=== agentlab pipeline $TS ===" | tee -a "$LOG"

# The backlog file the demo track draws from, and the library holding the
# replenishment decision logic. The logic lives outside this script so it can be
# unit-tested offline (`bash .pipeline/test_backlog.sh`) instead of only being
# exercised on the rare night the gate fires. Sourcing defines functions only.
BACKLOG_FILE="BACKLOG.md"
BACKLOG_LIB="$REPO/.pipeline/backlog.sh"
if [ ! -r "$BACKLOG_LIB" ]; then
  echo "MISSING $BACKLOG_LIB — required for backlog replenishment. Aborting." | tee -a "$LOG"
  exit 1
fi
. "$BACKLOG_LIB"

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

# Start every cycle from a clean, up-to-date main. Each cycle's branch must be
# cut from main (not from a previous cycle's unmerged branch), or PR diffs
# accumulate across cycles instead of staying independent slices.
reset_to_clean_main () {
  git switch main >/dev/null 2>>"$LOG" && git pull --ff-only origin main >/dev/null 2>>"$LOG"
}
if ! reset_to_clean_main; then
  echo "could not switch to a clean, up-to-date main — resolve manually before the next run. Aborting." | tee -a "$LOG"
  exit 1
fi

# Untracked build artifacts (.venv, __pycache__, example dirs) from a
# previous cycle's unmerged branch survive a plain `git switch main` and
# accumulate on disk — main never had them tracked, so they just sit there.
# Only safe to wipe them if main's working tree is otherwise clean. The
# per-cycle and postflight snapshots below auto-rescue a FAILed cycle's dirty
# main, so this should rarely trip — but it stays as a hard abort (not a
# guess-and-clean) in case main is ever left dirty by something other than
# this script, e.g. manual work done directly in the repo.
if [ -n "$(git status --porcelain)" ]; then
  echo "main has uncommitted or untracked changes (likely a FAILed cycle awaiting a manual fix) — resolve manually before the next run. Aborting." | tee -a "$LOG"
  exit 1
fi
scrub_artifacts () {
  git clean -fdx -e logs/ -e '.claude/settings.local.json' -e '.env*' -e graphify-out/ >>"$LOG" 2>&1
}
scrub_artifacts

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

# How many increments to ship tonight. Config is data, parsed once here at the
# edge; everything below receives an integer. Default 1 preserves the original
# one-increment-per-night behaviour if the file is missing or unreadable.
CYCLES_FILE=".pipeline/cycles"
CYCLES="$(tr -d '[:space:]' < "$CYCLES_FILE" 2>/dev/null)"
[ -z "$CYCLES" ] && CYCLES=1
if ! [[ "$CYCLES" =~ ^[1-9][0-9]*$ ]]; then
  echo "CYCLES '$CYCLES' in $CYCLES_FILE is not a positive integer — fix it. Aborting." | tee -a "$LOG"
  exit 1
fi
echo "cycles tonight: $CYCLES" | tee -a "$LOG"

# Every phase names its model. Two phases carry real judgment and stay on opus:
# the builder (writes the increment) and the reviewer (the gate on code that
# ships under Steve's name — downgrading the gate would be the one change that
# costs correctness rather than tokens). The rest are mechanical or
# search-shaped — the maintainer reads a verdict and runs git/gh, the researcher
# does web search and writes a note, replenish appends checklist lines, health
# runs test suites and reports — and run on sonnet.
#
# An unrecognised model aborts the phase rather than falling through to a
# default: a typo here would otherwise quietly restore the full-opus bill this
# split exists to avoid, and nothing downstream would ever surface it.
run_phase () {
  local model="$1" name="$2" prompt="$3"
  case "$model" in
    opus|sonnet) ;;
    *) echo "run_phase: bad model '$model' for phase '$name' — aborting phase." | tee -a "$LOG"
       return 1 ;;
  esac
  echo "" | tee -a "$LOG"
  echo "--- phase: $name (model: $model) ---" | tee -a "$LOG"
  if ! $CLAUDE --model "$model" "$prompt" >>"$LOG" 2>&1; then
    echo "phase '$name' exited non-zero — see $LOG" | tee -a "$LOG"
    return 1
  fi
}

# Rescue anything left uncommitted onto a clearly-named, pushed branch, then
# return to a clean main. Nothing here discards work. Called after EVERY cycle
# (not just at exit) because with N>1 a FAILed cycle's dirty tree would
# otherwise be inherited by the next cycle, which would then commit two
# cycles' work as one PR. Ends on main, clean if the snapshot committed.
SNAP_SEQ=0
snapshot_dirty_main () {
  local reason="$1"
  [ -z "$(git status --porcelain)" ] && return 0
  # The suffix must be unique per call, not just per second: two cycles that
  # both fail fast land in the same second, and the second `switch -c` would
  # then collide with the first cycle's recovery branch.
  SNAP_SEQ=$(( SNAP_SEQ + 1 ))
  local branch="cycle/${TS%%_*}-unshipped-$(date +%H%M%S)-$SNAP_SEQ"
  echo "" | tee -a "$LOG"
  echo "main left dirty after $reason — snapshotting to $branch instead of leaving it to be tripped over." | tee -a "$LOG"
  local committed=0 created=0
  if git switch -c "$branch" >>"$LOG" 2>&1; then
    created=1
    if git add -A >>"$LOG" 2>&1 \
      && git commit -m "wip: $reason left uncommitted (see logs/run-$TS.log, logs/last-review.md)" >>"$LOG" 2>&1; then
      committed=1
      git push -u origin "$branch" >>"$LOG" 2>&1 \
        || echo "push of $branch failed — work is still safe locally on that branch." | tee -a "$LOG"
    else
      echo "snapshot commit on $branch failed — leaving main dirty; resolve manually before the next run." | tee -a "$LOG"
    fi
  else
    echo "could not create recovery branch $branch — leaving main dirty; resolve manually before the next run." | tee -a "$LOG"
  fi
  # Whichever step above failed (or none did), get back onto main: `git switch`
  # carries any still-uncommitted changes with it rather than discarding them,
  # so main ends up either clean (snapshot committed) or genuinely dirty
  # (snapshot failed) — never silently left on the recovery branch instead.
  git switch main >>"$LOG" 2>&1
  # An empty branch left by a failed add/commit is just clutter — the real diff
  # is still on main, dirty, so there's nothing on it worth keeping. Gated on
  # `created` so a name collision can never delete a DIFFERENT cycle's
  # recovery branch, which is real work and the whole point of this function.
  if [ "$created" -eq 1 ] && [ "$committed" -eq 0 ]; then
    git branch -D "$branch" >>"$LOG" 2>&1 || true
  fi
}

# One full increment: research -> build -> review -> maintain -> auto-merge.
# Returns non-zero if the increment did not ship; the caller continues to the
# next cycle regardless, so one bad increment costs one slot, not the night.
run_cycle () {
  local k="$1"

  # Both handoff files are per-cycle state. last-review.md MUST be cleared
  # before the reviewer runs: if the review phase itself dies, a stale PASS
  # from the previous cycle would otherwise authorise the maintainer to ship
  # this cycle's work unreviewed.
  rm -f logs/last-review.md logs/last-pr.txt

  run_phase sonnet "cycle $k/$CYCLES: research" \
    "Use the agentlab-researcher subagent to run this cycle's research end to end, following its instructions exactly." || return 1

  run_phase opus "cycle $k/$CYCLES: build" \
    "Use the agentlab-builder subagent to build this cycle's increment from the newest research note, following its instructions exactly. Verify it runs." || return 1

  run_phase opus "cycle $k/$CYCLES: review" \
    "Use the agentlab-reviewer subagent to independently review this cycle's working-tree diff, run its tests/lint, and write a PASS/FAIL verdict to logs/last-review.md. Do not modify the increment." || return 1

  run_phase sonnet "cycle $k/$CYCLES: maintain" \
    "Use the agentlab-maintainer subagent. Read logs/last-review.md; ONLY if the verdict is PASS, commit this cycle's work as Steve Ling <steveylingy@gmail.com>, push a branch, and open a PR. On FAIL or missing verdict, do nothing and say why."

  # Auto-merge gate: mechanical, not an LLM judgment call. The maintainer never
  # merges (see agentlab-maintainer.md) — it only opens the PR and, on success,
  # writes the PR number to logs/last-pr.txt. Here we ask GitHub itself whether
  # the PR is a clean, conflict-free merge and act only on that flag. Anything
  # else (a real conflict, e.g. the #19/#20 same-file collision on 2026-08-10,
  # or GitHub still computing) is left open for a human to resolve — conflict
  # resolution needs judgment about intent that no part of this pipeline has.
  [ -s logs/last-pr.txt ] || return 1
  local pr_num mergeable="UNKNOWN"
  pr_num="$(tr -d '[:space:]' < logs/last-pr.txt)"
  echo "" | tee -a "$LOG"
  echo "--- phase: cycle $k/$CYCLES: auto-merge (PR #$pr_num) ---" | tee -a "$LOG"
  for _ in 1 2 3 4 5; do
    mergeable="$(gh pr view "$pr_num" --json mergeable -q .mergeable 2>>"$LOG")"
    [ "$mergeable" != "UNKNOWN" ] && break
    sleep 2
  done
  if [ "$mergeable" != "MERGEABLE" ]; then
    echo "PR #$pr_num left open for manual merge (mergeable=$mergeable)." | tee -a "$LOG"
    return 1
  fi
  if ! gh pr merge "$pr_num" --merge --delete-branch >>"$LOG" 2>&1; then
    echo "PR #$pr_num was MERGEABLE but 'gh pr merge' failed (branch protection? re-check manually) — left open." | tee -a "$LOG"
    return 1
  fi
  echo "PR #$pr_num auto-merged (clean, no conflicts)." | tee -a "$LOG"
  return 0
}

# Backlog replenishment: throughput is only useful while there is real work
# queued. At N increments a night an unattended backlog drains in days, and the
# researcher's empty-backlog fallback is to re-pick a stale item — which buys
# churn, not portfolio. Top up when fewer than one night's worth remain.
#
# Reconciled TWICE, before the loop and after it. Before, because a night that
# starts short leaves its later cycles with nothing to claim — topping up after
# the loop is too late for the very cycles that needed it. After, so tomorrow
# starts stocked. The decision reads BACKLOG.md from disk each time and acts at
# most once, so the second pass is a no-op unless the loop actually drained it.
# Demo mode only: project mode draws its work from projects/<slug>/PLAN.md
# milestones, not BACKLOG.md, so there is nothing here to replenish.

# The side effect injected into backlog_ensure_stocked: run the replenish phase
# for <target> unclaimed items, then commit and push BACKLOG.md if the agent
# changed it. Returns the phase's status.
#
# A failed commit/push is logged but NOT propagated: the new items are already
# on disk where the next cycle's researcher can claim them, and the postflight
# snapshot_dirty_main rescues the edit. A git hiccup must never cost the night
# its increments.
replenish_action () {
  local target="$1"
  local unclaimed
  if ! unclaimed="$(backlog_count_unclaimed "$BACKLOG_FILE")"; then
    echo "cannot read $BACKLOG_FILE — skipping the replenish phase." | tee -a "$LOG"
    return 1
  fi

  run_phase sonnet "replenish (unclaimed=$unclaimed < $CYCLES, target=$target)" \
    "The agentlab BACKLOG.md has only $unclaimed unclaimed items left and the pipeline consumes $CYCLES a night. Append enough new items to reach at least $target unclaimed, under the appropriate existing section heading (create one if genuinely needed). Rules: use the exact '- [ ] ' prefix — the researcher matches on it literally, and an item without it is invisible. Each item must be a single agent-engineering increment buildable and testable in one cycle, in the style of the existing entries, and must NOT duplicate anything already marked [done], [researching], or [building]. Ground them in what this lab already has: read README.md, PIPELINE.md, knowledge/INDEX.md and examples/ first, and prefer items that extend or harden existing work over unrelated greenfield topics. Edit ONLY BACKLOG.md. Do not commit, push, or touch git — the pipeline commits for you." \
    || return 1

  # The script commits, not the agent: deterministic message and identity,
  # and it keeps main clean for tomorrow's preflight either way.
  if [ -z "$(git status --porcelain "$BACKLOG_FILE")" ]; then
    echo "replenish phase made no $BACKLOG_FILE changes." | tee -a "$LOG"
    return 0
  fi
  local new_count
  if ! new_count="$(backlog_count_unclaimed "$BACKLOG_FILE")"; then
    echo "cannot re-read $BACKLOG_FILE after the replenish phase — leaving the edit for the postflight snapshot." | tee -a "$LOG"
    return 0
  fi
  if git add "$BACKLOG_FILE" >>"$LOG" 2>&1 \
    && git commit -m "chore(backlog): replenish to $new_count unclaimed items" >>"$LOG" 2>&1 \
    && git push origin main >>"$LOG" 2>&1; then
    echo "backlog replenished: $unclaimed -> $new_count unclaimed, pushed to main." | tee -a "$LOG"
  else
    echo "backlog replenish commit/push failed — see $LOG; the postflight snapshot will rescue the edit." | tee -a "$LOG"
  fi
  return 0
}

# Re-apply claims that a failed cycle carried off to a cycle/*-unshipped-*
# branch, so no cycle re-picks a topic that is already built. <label> names the
# call site in the log.
#
# The git half of the stranded-claim fix; the decisions live in backlog.sh where
# they are unit-tested offline. Always returns 0 — a reconciliation problem is
# reported loudly but never aborts the night. It never opens a PR for a stranded
# branch and never deletes one: salvaging that work is a judgment call about
# intent, which is exactly what this pipeline does not automate.
reconcile_stranded_claims () {
  local label="$1"
  local refs ref base diff claimed key changed=0 rc

  # for-each-ref, not `git branch --list`: the glob is matched against the
  # refname by git itself, and both local and origin copies are found. A push
  # that failed leaves the branch local-only, which still holds a real claim.
  refs="$(git for-each-ref --format='%(refname:short)' \
    'refs/heads/cycle/*-unshipped-*' 'refs/remotes/origin/cycle/*-unshipped-*' 2>>"$LOG")"
  [ -z "$refs" ] && return 0

  echo "" | tee -a "$LOG"
  echo "--- phase: reconcile stranded claims ($label) ---" | tee -a "$LOG"

  while IFS= read -r ref; do
    [ -z "$ref" ] && continue
    if ! base="$(git merge-base main "$ref" 2>>"$LOG")"; then
      echo "  $ref: no merge base with main — skipping." | tee -a "$LOG"
      continue
    fi
    if ! diff="$(git diff "$base" "$ref" -- "$BACKLOG_FILE" 2>>"$LOG")"; then
      echo "  $ref: cannot diff $BACKLOG_FILE — skipping." | tee -a "$LOG"
      continue
    fi
    if ! claimed="$(backlog_claimed_line "$diff")"; then
      echo "  $ref: carries no backlog claim — nothing to reconcile." | tee -a "$LOG"
      continue
    fi
    if ! key="$(backlog_claim_key "$claimed")"; then
      echo "  $ref: claim line has no recognised marker — skipping." | tee -a "$LOG"
      continue
    fi

    backlog_apply_stranded "$BACKLOG_FILE" "$key" "$ref"
    rc=$?
    case "$rc" in
      0) changed=$(( changed + 1 ))
         echo "  $ref: claim re-applied — item marked [$BACKLOG_STRANDED_MARKER $ref]." | tee -a "$LOG" ;;
      2) echo "  $ref: its item is no longer in $BACKLOG_FILE (reworded or removed) — a human needs to look." | tee -a "$LOG" ;;
      3) echo "  $ref: already reconciled, shipped, or in flight — left alone." | tee -a "$LOG" ;;
      *) echo "  $ref: cannot rewrite $BACKLOG_FILE — skipping." | tee -a "$LOG" ;;
    esac
  done <<< "$refs"

  [ "$changed" -eq 0 ] && return 0

  if git add "$BACKLOG_FILE" >>"$LOG" 2>&1 \
    && git commit -m "chore(backlog): reconcile $changed claim(s) stranded on unshipped branches" >>"$LOG" 2>&1 \
    && git push origin main >>"$LOG" 2>&1; then
    echo "reconciled $changed stranded claim(s), pushed to main." | tee -a "$LOG"
  else
    echo "reconcile commit/push failed — see $LOG; the postflight snapshot will rescue the edit." | tee -a "$LOG"
  fi
  return 0
}

# One reconcile pass, logged. <label> names the call site in the log so the two
# passes are distinguishable. Always returns 0: a replenishment problem is
# reported loudly but never aborts the night, which does not depend on it.
stock_backlog () {
  local label="$1"
  [ "$MODE" == "demo" ] || return 0

  local before after
  before="$(backlog_count_unclaimed "$BACKLOG_FILE" 2>/dev/null)" || before="?"
  backlog_ensure_stocked "$BACKLOG_FILE" "$CYCLES" replenish_action
  local rc=$?
  after="$(backlog_count_unclaimed "$BACKLOG_FILE" 2>/dev/null)" || after="?"

  local msg
  case "$rc" in
    0) msg="stocked, $before -> $after unclaimed (need >= $CYCLES/night)" ;;
    1) msg="replenish ran but $BACKLOG_FILE is STILL short: $before -> $after unclaimed, need >= $CYCLES — see $LOG" ;;
    2) msg="replenish phase FAILED (still $after unclaimed) — continuing anyway, see $LOG" ;;
    3) msg="cannot read $BACKLOG_FILE — replenishment skipped, see $LOG" ;;
    *) msg="unexpected reconcile status $rc — see $LOG" ;;
  esac
  echo "" | tee -a "$LOG"
  echo "--- backlog reconcile ($label): $msg ---" | tee -a "$LOG"
  return 0
}

# Must run here, not up next to the "cycles tonight:" echo: it needs the parsed
# MODE/CYCLES *and* run_phase, and a function is only callable once its
# definition line has been executed — a call placed earlier would fail with
# "run_phase: command not found" on exactly the nights the gate fires.
# Before stock_backlog, never after: reconciling REMOVES items from the
# unclaimed count (a stranded item stops reading '- [ ] '), so counting first
# would let the night draw work that is already built and about to disappear
# from the queue.
reconcile_stranded_claims "pre-loop"
stock_backlog "pre-loop"

SHIPPED=0
for (( k=1; k<=CYCLES; k++ )); do
  echo "" | tee -a "$LOG"
  echo "=== cycle $k of $CYCLES ===" | tee -a "$LOG"
  if run_cycle "$k"; then
    SHIPPED=$(( SHIPPED + 1 ))
  else
    echo "cycle $k did not ship — continuing to the next cycle." | tee -a "$LOG"
  fi
  # Hand the next cycle exactly what the preflight guarantees the first one:
  # a clean, current main with no leftover build artifacts. Without this, a
  # FAILed cycle's diff would be swept into the next cycle's PR.
  snapshot_dirty_main "cycle $k of $TS"
  if ! reset_to_clean_main; then
    echo "could not return to a clean, up-to-date main after cycle $k — stopping the loop here." | tee -a "$LOG"
    break
  fi
  scrub_artifacts
  # The high-value slot: if this cycle just stranded its claim on a snapshot
  # branch, reconcile before the NEXT cycle's researcher picks a topic. Without
  # it, cycle k+1 re-researches and rebuilds exactly what cycle k just built —
  # the 2026-08-12 failure.
  reconcile_stranded_claims "after cycle $k"
done
echo "" | tee -a "$LOG"
echo "shipped $SHIPPED of $CYCLES cycles." | tee -a "$LOG"

# Leave tomorrow's draw stocked. Usually a no-op: the pre-loop pass already
# guaranteed one night's worth, and the loop consumes at most one item a cycle.
stock_backlog "post-loop"

# Lab health check: a separate concern from tonight's increments — it re-verifies
# the whole accumulated portfolio (every example still runs, every knowledge
# wikilink resolves, every BACKLOG.md [done #N] matches a merged PR), not just
# one diff. Runs once per night after the last cycle, and is gated to a fixed
# calendar cadence since it reruns every example's test suite in a fresh venv —
# real time/cost that grows with the number of increments. A finding here never
# blocks or alters the cycles above; it only reports. See agentlab-health.md.
#
# One constant, read by both the gate and the skip message, so the two can't
# drift into claiming different cadences.
HEALTH_CADENCE_DAYS=7
LAST_HEALTH_LOG="$(ls -t logs/lab-health-*.log 2>/dev/null | head -1)"
RUN_HEALTH=1
DAYS_SINCE="n/a"
if [ -n "$LAST_HEALTH_LOG" ]; then
  LAST_DATE="$(basename "$LAST_HEALTH_LOG" | sed -E 's/^lab-health-([0-9]{4}-[0-9]{2}-[0-9]{2}).*/\1/')"
  LAST_EPOCH="$(date -j -f "%Y-%m-%d" "$LAST_DATE" +%s 2>/dev/null || echo 0)"
  NOW_EPOCH="$(date +%s)"
  DAYS_SINCE=$(( (NOW_EPOCH - LAST_EPOCH) / 86400 ))
  [ "$DAYS_SINCE" -lt "$HEALTH_CADENCE_DAYS" ] && RUN_HEALTH=0
fi
if [ "$RUN_HEALTH" -eq 1 ]; then
  run_phase sonnet "health" \
    "Use the agentlab-health subagent to run a full lab-scope health check. This cycle's timestamp is $TS — write the dated report to logs/lab-health-$TS.log and the latest-snapshot to logs/last-health.md, following the subagent's instructions exactly. This must not modify anything under examples/, knowledge/, research/, projects/, or BACKLOG.md, and must not affect tonight's PRs or merges above."
else
  echo "" | tee -a "$LOG"
  echo "--- phase: health check skipped (last run $LAST_DATE, ${DAYS_SINCE}d ago, cadence=${HEALTH_CADENCE_DAYS}d) ---" | tee -a "$LOG"
fi

# Postflight: guarantee main is clean before this script exits, no matter what
# happened above — the per-cycle snapshots cover the loop, this covers the
# replenish and health phases and anything else unexpected. On FAIL the
# maintainer deliberately leaves a diff uncommitted on main for a human to
# inspect — correct in isolation, but it means the *next* run's preflight
# aborts on "main not clean", which is what happened on 2026-08-02/03/07.
snapshot_dirty_main "cycle run $TS"

echo "" | tee -a "$LOG"
echo "=== done $TS === (shipped $SHIPPED/$CYCLES, full log: $LOG)" | tee -a "$LOG"
