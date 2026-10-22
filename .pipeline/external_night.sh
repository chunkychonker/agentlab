#!/bin/bash
# external_night.sh — runs one `external:<slug>` night: N cycles of
# sync -> baseline gate -> research (or resume) -> build -> protected-path gate
# -> verify gate -> review -> verdict gate -> maintain (open PR) -> bookkeeping.
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
#   - Verify also runs on the PRISTINE checkout before research. A failing base
#     with one of our PRs still open stops the night: that PR is almost
#     certainly the fix, and nothing built tonight can pass until it merges.
#   - A phase that stops on a usage limit stops the night, and leaves a resume
#     marker so the next run reuses the research note instead of paying again.

. "$REPO/.pipeline/external.sh"

# Read by the postcondition below; set once per night by run_external_night.
EXT_WORK=""
EXT_BASE=""
EXT_SELF_EMAIL=""   # target's self_email: agentlab's commit identity there

# run_external_cycle's return code for "stop the whole night, not just this
# cycle" (the baseline gate). Distinct from 1 (cycle failed) and from
# USAGE_LIMIT_RC (usage_limit.sh).
EXT_STOP_NIGHT=3

# Verify output for the pristine checkout. Separate from EXT_VERIFY_FILE, which
# the reviewer reads as the gate result for the increment.
EXT_BASELINE_FILE="logs/baseline-verify.txt"

# ext_after_phase_failure <rc> <marker> <note> <base_sha>
# Called when a phase AFTER research returned non-zero. On a usage limit,
# writes the resume marker and returns USAGE_LIMIT_RC; otherwise returns 1.
ext_after_phase_failure () {
  local rc="$1" marker="$2" note="$3" sha="$4"
  if [ "$rc" -eq "$USAGE_LIMIT_RC" ]; then
    if ext_resume_write "$marker" "$note" "$sha"; then
      echo "--- resume marker written: the next run reuses $note if $EXT_BASE is still at ${sha:0:7} ---" | tee -a "$LOG"
    else
      echo "WARNING: could not write resume marker $marker — the next run re-researches." | tee -a "$LOG"
    fi
    return "$USAGE_LIMIT_RC"
  fi
  return 1
}

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

# One cycle. Returns 0 if a PR was opened on the target; EXT_STOP_NIGHT if the # baseline gate says no cycle can ship tonight; USAGE_LIMIT_RC if a phase # stopped on a usage limit; 1 for any other failure.
run_external_cycle () {
  local k="$1" slug="$2" repo="$3" verify="$4" protected="$5" hot_hours="$6"

  rm -f "$REVIEW_VERDICT_FILE" logs/last-pr.txt "$EXT_VERIFY_FILE" "$EXT_CONTEXT_FILE" "$EXT_BASELINE_FILE"

  echo "" | tee -a "$LOG"
  echo "--- sync $repo@$EXT_BASE -> $EXT_WORK ---" | tee -a "$LOG"
  if ! ext_sync "$EXT_WORK" "https://github.com/$repo.git" "$EXT_BASE" >>"$LOG" 2>&1; then
    echo "cannot sync $repo — cycle $k aborted." | tee -a "$LOG"
    return 1
  fi
  local hot head base_sha
  hot="$(ext_hot_files "$EXT_WORK" "$EXT_BASE" "$hot_hours" "$EXT_SELF_EMAIL")"
  head="$(git -C "$EXT_WORK" rev-parse --short HEAD)"
  base_sha="$(git -C "$EXT_WORK" rev-parse HEAD)"

  # Baseline gate: can the base pass verify at all? Model-free, before any
  # tokens are spent. The checkout is reset afterwards (no fetch), so the
  # build starts from the exact commit that was verified, with no build output.
  echo "" | tee -a "$LOG"
  echo "--- baseline: verify on pristine $EXT_BASE ($head) ---" | tee -a "$LOG"
  local base_rc prs="" prs_rc=0 decision base_summary
  bash "$verify" "$EXT_WORK" > "$EXT_BASELINE_FILE" 2>&1
  base_rc=$?
  base_summary="$(tail -1 "$EXT_BASELINE_FILE")"
  if ! ext_reset_pristine "$EXT_WORK" "$EXT_BASE" >>"$LOG" 2>&1; then
    echo "cannot reset $EXT_WORK after the baseline verify — cycle $k aborted." | tee -a "$LOG"
    return 1
  fi
  if [ "$base_rc" -ne 0 ]; then
    prs="$(ext_open_agentlab_prs "$repo" 2>>"$LOG")"
    prs_rc=$?
  fi
  decision="$(ext_baseline_decision "$base_rc" "$prs_rc" "$prs")"
  case "$decision" in
    "$EXT_BASELINE_STOP_OPEN_PR")
      echo "--- gate: baseline $base_summary while our PR(s) $(printf '%s\n' "$prs" | sed 's/^/#/' | tr '\n' ' ')are open on $repo — stopping the night until they merge. See $EXT_BASELINE_FILE. ---" | tee -a "$LOG"
      return "$EXT_STOP_NIGHT" ;;
    "$EXT_BASELINE_STOP_UNKNOWN")
      echo "--- gate: baseline $base_summary and the open PRs on $repo could not be listed — stopping the night. See $EXT_BASELINE_FILE and $LOG. ---" | tee -a "$LOG"
      return "$EXT_STOP_NIGHT" ;;
    "$EXT_BASELINE_MUST_FIX")
      echo "--- gate: baseline $base_summary, none of our PRs open — continuing; this cycle must make verify pass. ---" | tee -a "$LOG" ;;
    *)
      echo "--- gate: baseline VERIFY: PASS ---" | tee -a "$LOG" ;;
  esac

  # Resume after a usage limit: read only now, after the baseline gate, so a
  # night the gate stops does not consume the marker.
  local marker="logs/ext-resume-$slug" note=""
  if [ -e "$marker" ]; then
    if note="$(ext_resume_read "$marker" "$base_sha")"; then
      echo "--- resume: the last cycle stopped on a usage limit; reusing $note ($EXT_BASE unchanged at $head) ---" | tee -a "$LOG"
    else
      note=""
      echo "--- resume: marker discarded ($EXT_BASE moved, or the note is gone) — researching afresh ---" | tee -a "$LOG"
    fi
  fi

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
    echo ""
    echo "## Baseline — verify on pristine $EXT_BASE ($head), output in $EXT_BASELINE_FILE"
    if [ "$decision" == "$EXT_BASELINE_MUST_FIX" ]; then
      echo "- **$base_summary** — $EXT_BASE fails verify on its own. This cycle's increment must make verify pass, or it cannot ship."
    else
      echo "- VERIFY: PASS"
    fi
    if [ -n "$note" ]; then
      echo ""
      echo "## Resumed cycle"
      echo "- Research was skipped: the last cycle stopped on a usage limit. Build from \`$note\`."
    fi
  } > "$EXT_CONTEXT_FILE"

  if [ -z "$note" ]; then
    run_phase sonnet "cycle $k/$CYCLES: research" research_note_fresh \
      "Use the agentlab-researcher subagent to run this cycle's research end to end, following its instructions exactly." || return $?
    note="$(ls -t "$RESEARCH_DIR"/*.md 2>/dev/null | head -1)"
  fi

  local rc
  run_phase opus "cycle $k/$CYCLES: build" ext_increment_built \
    "Use the agentlab-builder subagent to build this cycle's increment from the research note $note, following its instructions exactly. Verify it runs."
  rc=$?
  [ "$rc" -eq 0 ] || { ext_after_phase_failure "$rc" "$marker" "$note" "$base_sha"; return $?; }

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
    "Use the agentlab-reviewer subagent to independently review this cycle's diff in the external checkout named in $EXT_CONTEXT_FILE (and $EXT_VERIFY_FILE), and write a PASS/FAIL verdict to logs/last-review.md. Do not modify the increment."
  rc=$?
  [ "$rc" -eq 0 ] || { ext_after_phase_failure "$rc" "$marker" "$note" "$base_sha"; return $?; }

  local verdict
  verdict="$(review_verdict "$REVIEW_VERDICT_FILE")"
  if [ "$verdict" != "PASS" ]; then
    echo "--- gate: review verdict is $verdict — cycle $k does not ship. See $REVIEW_VERDICT_FILE. ---" | tee -a "$LOG"
    return 1
  fi
  echo "--- gate: review verdict PASS — proceeding to maintain. ---" | tee -a "$LOG"

  run_phase sonnet "cycle $k/$CYCLES: maintain" phase_no_postcondition \
    "Use the agentlab-maintainer subagent. Read logs/last-review.md; ONLY if the verdict is PASS, follow its External mode procedure: commit in the external checkout as Steve Ling <steveylingy@gmail.com>, push a branch to the target repo, open a PR there, and write the PR number to logs/last-pr.txt. Never merge."
  rc=$?
  # A limit after the PR was recorded is not a lost cycle: verify it below as
  # usual. Only a limit with no PR on record stops the night.
  if [ "$rc" -eq "$USAGE_LIMIT_RC" ] && [ ! -s logs/last-pr.txt ]; then
    ext_after_phase_failure "$rc" "$marker" "$note" "$base_sha"; return $?
  fi

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
# Returns 0 if at least one cycle opened a PR; USAGE_LIMIT_RC if a phase stopped # on a usage limit; 1 otherwise or on bad config.
run_external_night () {
  local slug="$1" target="projects/$1/target"
  local repo verify protected hot_hours
  repo="$(ext_target_field "$target" repo)" \
    && EXT_BASE="$(ext_target_field "$target" base)" \
    && verify="$(ext_target_field "$target" verify)" \
    || { echo "$target must define repo, base and verify — see projects/EXTERNAL_MODE.md. Aborting." | tee -a "$LOG"; return 1; }
  protected="$(ext_target_field "$target" protect)"
  hot_hours="$(ext_target_field "$target" hot_hours)" || hot_hours=2
  EXT_SELF_EMAIL="$(ext_target_field "$target" self_email)" || EXT_SELF_EMAIL=""
  if ! [[ "$hot_hours" =~ ^[0-9]+$ ]] || [ ! -r "$verify" ]; then
    echo "$target: hot_hours must be an integer and verify must be a readable script. Aborting." | tee -a "$LOG"
    return 1
  fi
  EXT_WORK="$EXT_WORK_ROOT/$slug"

  local k rc shipped=0 limited=0
  for (( k=1; k<=CYCLES; k++ )); do
    echo "" | tee -a "$LOG"
    echo "=== external cycle $k of $CYCLES ($repo) ===" | tee -a "$LOG"
    run_external_cycle "$k" "$slug" "$repo" "$verify" "$protected" "$hot_hours"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      shipped=$(( shipped + 1 ))
    elif [ "$rc" -eq "$EXT_STOP_NIGHT" ]; then
      # Nothing was built, so there is nothing to snapshot or commit.
      echo "night stopped by the baseline gate at cycle $k." | tee -a "$LOG"
      break
    else
      [ "$rc" -eq "$USAGE_LIMIT_RC" ] && limited=1
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
    if [ "$limited" -eq 1 ]; then
      echo "--- night stopped: usage limit — later cycles would hit the same limit ---" | tee -a "$LOG"
      break
    fi
  done
  echo "" | tee -a "$LOG"
  echo "external night: opened $shipped PR(s) on $repo in $CYCLES cycle(s)." | tee -a "$LOG"
  [ "$limited" -eq 1 ] && return "$USAGE_LIMIT_RC"
  [ "$shipped" -gt 0 ]
}
