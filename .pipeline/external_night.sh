#!/bin/bash
# external_night.sh — runs one `external:<slug>` night: N cycles of
# sync -> research -> build -> protected-path gate -> verify gate -> review ->
# verdict gate -> maintain (open PR) -> bookkeeping.
#
# Sourced by run.sh after its preflights, so LOG, TS, CYCLES, run_phase,
# research_note_fresh, review_verdict and REVIEW_VERDICT_FILE already exist.
#
# Differences from a demo night, each deliberate:
#   - The increment lands in a checkout of someone else's repo under
#     $EXT_WORK_ROOT, never in ~/agentlab.
#   - Two extra mechanical gates run BEFORE the reviewer spends opus tokens:
#     the target's protected/hot files, and the target's own verify script.
#   - Nothing is ever merged. The PR is the deliverable; the repo's owner merges.
#   - A failed cycle's work is snapshotted to a LOCAL branch in the checkout,
#     never pushed: unreviewed branches do not belong in a repo we don't own.

. "$REPO/.pipeline/external.sh"

# Read by the postcondition below; set once per night by run_external_night.
EXT_WORK=""
EXT_BASE=""

# ext_increment_built <floor_epoch>
# Build-phase postcondition for external mode: the checkout differs from
# origin/<base>. (The floor is unused: ext_sync leaves the checkout pristine
# immediately before every cycle, so ANY change is this cycle's.)
ext_increment_built () {
  if [ -n "$(ext_changed_files "$EXT_WORK" "$EXT_BASE")" ]; then
    echo "FRESH"; return 0
  fi
  echo "MISSING"; return 3
}

# Bookkeeping in ~/agentlab itself: this cycle's research note, knowledge notes,
# and projects/<slug>/ (PLAN.md plus the reviewed verify script and checks) —
# except `target`, which sets the base and the protected list and so stays a
# human-only edit. Pushed straight to main, the same precedent as the backlog
# chore commits in run.sh. Anything else dirty is left for snapshot_dirty_main.
ext_commit_bookkeeping () {
  local slug="$1" msg="$2"
  git add research knowledge "projects/$slug" ":(exclude)projects/$slug/target" >>"$LOG" 2>&1
  if git diff --cached --quiet; then
    echo "no agentlab bookkeeping to commit." | tee -a "$LOG"
    return 0
  fi
  if git commit -m "$msg" >>"$LOG" 2>&1 && git push origin main >>"$LOG" 2>&1; then
    echo "agentlab bookkeeping committed and pushed to main." | tee -a "$LOG"
  else
    echo "agentlab bookkeeping commit/push failed — see $LOG; the postflight snapshot will rescue it." | tee -a "$LOG"
  fi
}

# ext_scrub_pr_body <repo> <pr>
# Removes AI attribution lines from the PR description, so the PR reads as the
# account owner's. A failed read/edit is logged loudly but does not unship a
# PR that is already open — the human sees the warning in the run log.
ext_scrub_pr_body () {
  local repo="$1" pr="$2" body clean
  body="$(gh pr view "$pr" -R "$repo" --json body -q .body 2>>"$LOG")" \
    || { echo "WARNING: could not read $repo#$pr body to scrub AI attribution." | tee -a "$LOG"; return 1; }
  clean="$(printf '%s\n' "$body" | ext_strip_ai_attribution)"
  [ "$clean" == "$body" ] && return 0
  if gh pr edit "$pr" -R "$repo" --body "$clean" >>"$LOG" 2>&1; then
    echo "scrubbed AI attribution from $repo#$pr description." | tee -a "$LOG"
  else
    echo "WARNING: failed to scrub AI attribution from $repo#$pr — edit it by hand." | tee -a "$LOG"; return 1
  fi
}

# One cycle. Returns 0 if a PR was opened on the target, non-zero otherwise.
run_external_cycle () {
  local k="$1" slug="$2" repo="$3" verify="$4" protected="$5" hot_hours="$6"

  rm -f "$REVIEW_VERDICT_FILE" logs/last-pr.txt "$EXT_VERIFY_FILE" "$EXT_CONTEXT_FILE"

  echo "" | tee -a "$LOG"
  echo "--- sync $repo@$EXT_BASE -> $EXT_WORK ---" | tee -a "$LOG"
  if ! ext_sync "$EXT_WORK" "https://github.com/$repo.git" "$EXT_BASE" >>"$LOG" 2>&1; then
    echo "cannot sync $repo — cycle $k aborted." | tee -a "$LOG"
    return 1
  fi
  local hot head
  hot="$(ext_hot_files "$EXT_WORK" "$EXT_BASE" "$hot_hours")"
  head="$(git -C "$EXT_WORK" rev-parse --short HEAD)"

  # The one file every phase reads for "where is the target and what may I
  # touch". Written by the script so no agent has to re-derive it.
  {
    echo "# External cycle context ($TS, cycle $k)"
    echo ""
    echo "- slug: $slug"
    echo "- target repo: $repo (PRs to \`$EXT_BASE\`, never merged)"
    echo "- checkout: $EXT_WORK (pristine at origin/$EXT_BASE = $head)"
    echo "- verify gate: \`bash $verify $EXT_WORK\` — output lands in $EXT_VERIFY_FILE"
    echo ""
    echo "## Protected files — never edit"
    printf '%s\n' "$protected" | sed '/^$/d; s/^/- /'
    echo ""
    echo "## Hot files — changed on $EXT_BASE in the last ${hot_hours}h; another agent may be mid-edit. Do not touch."
    if [ -n "$hot" ]; then printf '%s\n' "$hot" | sed 's/^/- /'; else echo "- (none)"; fi
  } > "$EXT_CONTEXT_FILE"

  run_phase sonnet "cycle $k/$CYCLES: research" research_note_fresh \
    "Use the agentlab-researcher subagent to run this cycle's research end to end, following its instructions exactly." || return 1

  run_phase opus "cycle $k/$CYCLES: build" ext_increment_built \
    "Use the agentlab-builder subagent to build this cycle's increment from the newest research note, following its instructions exactly. Verify it runs." || return 1

  local hits
  if ! hits="$(ext_protected_hits "$(ext_changed_files "$EXT_WORK" "$EXT_BASE")" "$protected" "$hot")"; then
    echo "--- gate: build touched off-limits files — cycle $k does not ship: ---" | tee -a "$LOG"
    printf '%s\n' "$hits" | sed 's/^/  /' | tee -a "$LOG"
    return 1
  fi

  echo "" | tee -a "$LOG"
  echo "--- gate: verify ($verify) ---" | tee -a "$LOG"
  if ! bash "$verify" "$EXT_WORK" > "$EXT_VERIFY_FILE" 2>&1; then
    echo "--- gate: $(tail -1 "$EXT_VERIFY_FILE") — cycle $k does not ship. See $EXT_VERIFY_FILE. ---" | tee -a "$LOG"
    return 1
  fi
  echo "--- gate: VERIFY: PASS ---" | tee -a "$LOG"

  run_phase opus "cycle $k/$CYCLES: review" phase_no_postcondition \
    "Use the agentlab-reviewer subagent to independently review this cycle's diff in the external checkout named in $EXT_CONTEXT_FILE (and $EXT_VERIFY_FILE), and write a PASS/FAIL verdict to logs/last-review.md. Do not modify the increment." || return 1

  local verdict
  verdict="$(review_verdict "$REVIEW_VERDICT_FILE")"
  if [ "$verdict" != "PASS" ]; then
    echo "--- gate: review verdict is $verdict — cycle $k does not ship. See $REVIEW_VERDICT_FILE. ---" | tee -a "$LOG"
    return 1
  fi
  echo "--- gate: review verdict PASS — proceeding to maintain. ---" | tee -a "$LOG"

  run_phase sonnet "cycle $k/$CYCLES: maintain" phase_no_postcondition \
    "Use the agentlab-maintainer subagent. Read logs/last-review.md; ONLY if the verdict is PASS, follow its External mode procedure: commit in the external checkout as Steve Ling <steveylingy@gmail.com>, push a branch to the target repo, open a PR there, and write the PR number to logs/last-pr.txt. Never merge."

  # Trust GitHub, not the phase's word: the PR must exist and be open.
  [ -s logs/last-pr.txt ] || { echo "maintain phase recorded no PR — cycle $k did not ship." | tee -a "$LOG"; return 1; }
  local pr state
  pr="$(tr -d '[:space:]#' < logs/last-pr.txt)"
  state="$(gh pr view "$pr" -R "$repo" --json state -q .state 2>>"$LOG")"
  if [ "$state" != "OPEN" ]; then
    echo "PR '$pr' on $repo is not open (state='$state') — cycle $k did not ship." | tee -a "$LOG"
    return 1
  fi
  echo "PR $repo#$pr opened for human review (not merged, by design)." | tee -a "$LOG"
  ext_scrub_pr_body "$repo" "$pr"
  ext_commit_bookkeeping "$slug" "chore($slug): cycle shipped as $repo#$pr"
  return 0
}

# run_external_night <slug>
# Returns 0 if at least one cycle opened a PR, 1 otherwise or on bad config.
run_external_night () {
  local slug="$1" target="projects/$1/target"
  local repo verify protected hot_hours
  repo="$(ext_target_field "$target" repo)" \
    && EXT_BASE="$(ext_target_field "$target" base)" \
    && verify="$(ext_target_field "$target" verify)" \
    || { echo "$target must define repo, base and verify — see projects/EXTERNAL_MODE.md. Aborting." | tee -a "$LOG"; return 1; }
  protected="$(ext_target_field "$target" protect)"
  hot_hours="$(ext_target_field "$target" hot_hours)" || hot_hours=2
  if ! [[ "$hot_hours" =~ ^[0-9]+$ ]] || [ ! -r "$verify" ]; then
    echo "$target: hot_hours must be an integer and verify must be a readable script. Aborting." | tee -a "$LOG"
    return 1
  fi
  EXT_WORK="$EXT_WORK_ROOT/$slug"

  local k shipped=0
  for (( k=1; k<=CYCLES; k++ )); do
    echo "" | tee -a "$LOG"
    echo "=== external cycle $k of $CYCLES ($repo) ===" | tee -a "$LOG"
    if run_external_cycle "$k" "$slug" "$repo" "$verify" "$protected" "$hot_hours"; then
      shipped=$(( shipped + 1 ))
    else
      echo "cycle $k did not ship." | tee -a "$LOG"
      if ext_snapshot "$EXT_WORK" "cycle/${TS}-unshipped-$k" >>"$LOG" 2>&1; then
        echo "  work (if any) kept on LOCAL branch cycle/${TS}-unshipped-$k in $EXT_WORK." | tee -a "$LOG"
      else
        echo "  could not snapshot $EXT_WORK — the next sync will discard it." | tee -a "$LOG"
      fi
      # A FAILed cycle still produced a research note and maybe PLAN.md notes;
      # keep agentlab main clean for the next preflight.
      ext_commit_bookkeeping "$slug" "chore($slug): unshipped cycle notes ($TS, cycle $k)"
    fi
    snapshot_dirty_main "external cycle $k of $TS"
  done
  echo "" | tee -a "$LOG"
  echo "external night: opened $shipped PR(s) on $repo in $CYCLES cycle(s)." | tee -a "$LOG"
  [ "$shipped" -gt 0 ]
}
