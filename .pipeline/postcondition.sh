# Phase postcondition checks for the agentlab pipeline.
#
# This file decides whether a phase actually produced the artifact the next
# phase depends on. It reads; it never writes, never runs a phase, and never
# touches git — which is what makes it testable offline (see
# .pipeline/test_gates.sh).
#
# Why it exists: run_phase in run.sh treats the `claude` CLI's exit status as
# proof the phase did its job. It is not. On 2026-09-01 the 600s
# CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS ceiling killed the researcher mid-flight;
# the CLI still exited 0, so the phase "succeeded" having written no note and the
# builder ran against empty state. The log of that run said it plainly: "A phase
# whose output the next one hard-depends on should abort the cycle rather than
# pass half-written state forward." That is this file.
#
# Note the shape of the real failure: research/ was not empty. It was full of
# notes from previous nights. "Does an artifact exist" would have passed. Only
# "is there one newer than the moment this phase started" catches it — hence
# STALE is a distinct outcome from MISSING, and hence the floor is the PHASE's
# start time and not the RUN's. With CYCLES=2 a run-start floor would let cycle
# 1's note satisfy cycle 2's gate, and the bug would survive its own fix.
#
# Fails closed, the same posture as verdict.sh: an artifact this file cannot
# confirm with certainty is not a licence to advance to the next phase.
#
# Sourcing this file defines functions and constants. It runs nothing, prints
# nothing, and touches no files. run.sh sources it into a shell with
# `set -uo pipefail` on, so every expansion below must be safe under -u.
#
# macOS only for mtime: `stat -f %m` is BSD. GNU's `stat -c %Y` is not available
# on this box and is not shimmed — the pipeline runs on one Mac under launchd.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57) — see
# knowledge/bash-3.2-testable-scripts.md.

# Where each phase's artifact lands. Named once so a path change is a diff here
# rather than a silently-passing gate somewhere else.
RESEARCH_DIR="research"
RESEARCH_GLOB="*.md"

# The builder writes under examples/ in demo mode and projects/ in project mode
# (.claude/agents/agentlab-builder.md). Both are checked so one predicate covers
# both modes; only one of them is live on any given night.
INCREMENT_DIRS="examples projects"

# Any file counts as an increment. The builder is not required to produce a
# named file, only to produce something runnable, so the glob does not narrow.
INCREMENT_GLOB="*"

# artifact_freshness <dir> <glob> <floor_epoch>
#
# Is there a file under <dir> matching <glob> that was written at or after
# <floor_epoch>? Recursive: `find -name`, so a flat dir (research/) and a nested
# one (examples/<slug>/...) are both handled by one implementation.
#
# Prints exactly one word; the return code mirrors it:
#   0  FRESH    at least one match has mtime >= floor
#   2  STALE    matches exist, but every one predates the floor — the phase left
#               a previous cycle's artifact behind and wrote nothing new
#   3  MISSING  no match, or <dir> is absent/unreadable, or <floor_epoch> is not
#               a non-negative integer
#
# A match whose mtime cannot be read counts toward "found" but contributes no
# timestamp, so a directory of unreadable files reports STALE, not FRESH. That
# is deliberate: unreadable is not confirmation.
#
# Generated and vendored trees (.venv, __pycache__, node_modules, .git) are
# pruned: a phase that produced nothing but re-ran an existing example's
# self-test would drop fresh .pyc files under examples/ and forge a FRESH.
#
# Assumes no newlines in tracked filenames (true of this repo, and `find` output
# is read line-wise).
artifact_freshness () {
  local dir="$1" glob="$2" floor="$3"

  case "$floor" in
    ''|*[!0-9]*) echo "MISSING"; return 3 ;;
  esac
  if [ ! -d "$dir" ] || [ ! -r "$dir" ] || [ ! -x "$dir" ]; then
    echo "MISSING"; return 3
  fi

  local found=0 newest=0 mtime path
  # Heredoc rather than a pipe: a pipe runs the loop in a subshell under bash
  # 3.2 and `found` would not survive it.
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    found=1
    mtime="$(stat -f %m "$path" 2>/dev/null || true)"
    case "$mtime" in ''|*[!0-9]*) continue ;; esac
    [ "$mtime" -gt "$newest" ] && newest="$mtime"
  done <<EOF
$(find "$dir" \( -name .venv -o -name __pycache__ -o -name node_modules -o -name .git \) -prune \
     -o -type f -name "$glob" -print 2>/dev/null)
EOF

  if [ "$found" -eq 0 ]; then
    echo "MISSING"; return 3
  fi
  if [ "$newest" -ge "$floor" ]; then
    echo "FRESH"; return 0
  fi
  echo "STALE"; return 2
}

# research_note_fresh <floor_epoch>
#
# The research phase's postcondition: a dated note under research/, written by
# this phase. Both demo and project mode write there
# (.claude/agents/agentlab-researcher.md, demo step 3 / project step 4), so one
# check covers both. Prints and returns exactly as artifact_freshness does.
research_note_fresh () {
  local floor="$1" state rc
  state="$(artifact_freshness "$RESEARCH_DIR" "$RESEARCH_GLOB" "$floor")"
  rc=$?
  echo "$state"
  return $rc
}

# increment_built <floor_epoch>
#
# The build phase's postcondition: a file written under one of INCREMENT_DIRS at
# or after <floor_epoch>. Prints one word and mirrors it in the return code,
# same convention as research_note_fresh.
#
# Why not "the working tree is dirty": by the time this runs the researcher has
# already written into research/, and the builder is instructed to mark
# BACKLOG.md (demo) or PLAN.md (project) as its FIRST bookkeeping step. A dirty
# tree is therefore guaranteed even when no increment exists, so that test would
# pass vacuously on exactly the failure this gate is for.
#
# FRESH in either root is enough — only one root is live per mode, and a
# demo-mode night must not be failed for an untouched projects/. The negative
# answer still distinguishes the two cases for the log: STALE if either root
# holds work that all predates this phase (the builder ran and added nothing),
# MISSING if neither root has a single countable file. Both are non-zero; the
# distinction is diagnostic, not permission.
increment_built () {
  local floor="$1" dir rc saw_stale=0
  # Unquoted on purpose: INCREMENT_DIRS is a space-separated list to split.
  for dir in $INCREMENT_DIRS; do
    artifact_freshness "$dir" "$INCREMENT_GLOB" "$floor" >/dev/null
    rc=$?
    case "$rc" in
      0) echo "FRESH"; return 0 ;;
      2) saw_stale=1 ;;
    esac
  done
  if [ "$saw_stale" -eq 1 ]; then
    echo "STALE"; return 2
  fi
  echo "MISSING"; return 3
}

# phase_no_postcondition <floor_epoch>
#
# For phases nothing downstream hard-depends on (maintain, replenish, health,
# the observer). Explicit rather than optional: run_phase requires a
# postcondition argument, so a phase added later gets one by decision instead of
# inheriting "none" by omission. Ignores its argument; always succeeds.
phase_no_postcondition () {
  echo "NONE"
  return 0
}
