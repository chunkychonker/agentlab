# Working-tree classification for the agentlab pipeline's preflight.
#
# This file decides what a dirty `main` MEANS. It reads a `git status
# --porcelain` string it is handed; it never runs git, never moves a file, and
# never aborts — which is what makes it testable offline (see
# .pipeline/test_gates.sh). run.sh owns the acting-on-it half.
#
# Why it exists: the preflight used to abort the night on ANY output from
# `git status --porcelain`, on the reasoning that a dirty main means a FAILed
# cycle a human should look at. That reasoning holds for TRACKED changes. It
# does not hold for an untracked stray — a scratch file left in the repo by an
# interactive session is not evidence of a failed cycle, and treating it as one
# silently costs a full night. That is exactly what happened on 2026-08-14
# (an untracked .claude/settings.local.json aborted the run 3 seconds in).
#
# Since PR #18 a FAILed cycle auto-snapshots its dirty main to a
# cycle/*-unshipped-* branch, so genuine wreckage largely clears itself — which
# leaves untracked strays as the LIKELIER trigger of the two. The blunt guard
# now fires mostly on the case it was not written for.
#
# Sourcing this file defines one function and one constant. It runs nothing,
# prints nothing, and touches no files. run.sh sources it into a shell with
# `set -uo pipefail` on, so every expansion below must be safe under -u.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57) — see
# knowledge/bash-3.2-testable-scripts.md.

# Where run.sh parks strays it moves out of the way. Named once here, but TWO
# other places must agree with it and neither can be enforced from this file:
#   - .gitignore must list it, or the moved strays are themselves untracked
#     output and the next preflight reports the tree dirty all over again.
#   - scrub_artifacts in run.sh must pass it to `git clean -fdx -e`, or the
#     scrub deletes the strays seconds after they are rescued (-x removes
#     ignored files, so being in .gitignore is not protection from it).
# Changing this path means changing all three.
STRAY_DIR=".pipeline/strays"

# What the working tree described by <porcelain-text> should do to the night,
# on stdout, as exactly one of CLEAN / STRAY / BLOCKED.
#
# The argument is the literal stdout of `git status --porcelain` (v1 format,
# which is stable by contract — v2 is opt-in via --porcelain=v2). In that
# format a line is an untracked path iff it begins with `??`; every other
# status code describes a path git is already tracking, staged or unstaged,
# including the `UU`/`AA` merge-conflict codes.
#
# The split is deliberately asymmetric, and it fails toward BLOCKED: ANY
# tracked modification present blocks the night even when untracked files sit
# alongside it. A tree holding both is a tree where something edited a file
# git knows about, and no amount of adjacent scratch output makes that safe to
# clean up automatically. Only a tree whose entire dirtiness is untracked can
# be rescued without a human, because nothing that git tracks is at risk.
#
# Path quoting (core.quotePath escapes odd bytes, spaces get quoted) does not
# affect the classification: the status code sits in the first two columns
# before any path, so this function never has to parse a filename. run.sh does
# not parse them either — it re-enumerates with `git ls-files --others -z`.
#
# Failure modes (the return code mirrors the word on stdout, so a caller may
# branch on either):
#   0  CLEAN    no output, or only blank lines — nothing to do
#   1  BLOCKED  at least one tracked change — abort, a human should look
#   2  STRAY    non-empty, and every line is untracked — safe to move aside
worktree_disposition () {
  local status_text="$1"

  # `|| true` is load-bearing on both: grep -c exits 1 on a zero count, which
  # is a real answer here, and `set -o pipefail` would otherwise propagate it.
  local total untracked
  total="$(printf '%s\n' "$status_text" | grep -cv '^[[:space:]]*$' || true)"
  untracked="$(printf '%s\n' "$status_text" | grep -c '^??' || true)"
  [ -z "$total" ] && total=0
  [ -z "$untracked" ] && untracked=0

  if [ "$total" -eq 0 ]; then
    echo "CLEAN"
    return 0
  fi
  if [ "$total" -gt "$untracked" ]; then
    echo "BLOCKED"
    return 1
  fi
  echo "STRAY"
  return 2
}
