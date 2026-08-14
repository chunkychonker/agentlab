# Reviewer verdict parsing for the agentlab pipeline.
#
# This file decides whether a review verdict file authorises shipping. It reads;
# it never writes, never runs a phase, and never touches git — which is what
# makes it testable offline (see .pipeline/test_gates.sh).
#
# Why it exists: .claude/agents/agentlab-maintainer.md instructs the maintainer
# to read the verdict and ship only on PASS, and that instruction is correct —
# but "an LLM read a file and decided" is not a gate. The maintainer is the
# phase that commits under a human's name; whether it runs at all must be a
# mechanical decision, in the same spirit as the auto-merge gate in run.sh
# asking GitHub itself rather than asking a model. This is the review-side half
# of that principle, which was missing.
#
# Sourcing this file defines one function and one constant. It runs nothing,
# prints nothing, and touches no files. run.sh sources it into a shell with
# `set -uo pipefail` on, so every expansion below must be safe under -u.
#
# bash 3.2 ONLY (macOS system bash is 3.2.57) — see
# knowledge/bash-3.2-testable-scripts.md.

# The reviewer's handoff file, named once. run.sh clears it before the review
# phase and reads it after; nothing else may write it.
REVIEW_VERDICT_FILE="logs/last-review.md"

# The verdict recorded in <path>, on stdout, as exactly one of
# PASS / FAIL / MISSING / MALFORMED.
#
# The contract is the output shape in .claude/agents/agentlab-reviewer.md:
# exactly one line at column 0 reading `VERDICT: PASS` or `VERDICT: FAIL`.
# The match is deliberately strict — uppercase word, one VERDICT line, nothing
# else accepted — because this gate FAILS CLOSED. A verdict this function
# cannot read with certainty is not a licence to ship; it is a night that
# doesn't ship and says why in the log. The reverse default would let a garbled
# or half-written review authorise a PR under someone's real name, which is the
# one outcome the whole pipeline is built to prevent.
#
# Failure modes (the return code mirrors the word on stdout, so a caller may
# branch on either, and the three not-PASS cases stay distinguishable in logs):
#   0  PASS       exactly one VERDICT line, and it reads PASS
#   1  FAIL       exactly one VERDICT line, and it reads FAIL
#   2  MISSING    <path> is absent, unreadable, or empty — the review phase
#                 died, or run.sh's pre-review `rm -f` was never followed by a
#                 reviewer that wrote anything
#   3  MALFORMED  no VERDICT line, more than one, or an unrecognised word
review_verdict () {
  local path="$1"
  if [ ! -f "$path" ] || [ ! -r "$path" ] || [ ! -s "$path" ]; then
    echo "MISSING"
    return 2
  fi

  # More than one VERDICT line is ambiguous, not a majority vote: a reviewer
  # that emitted both has contradicted itself, and guessing which it meant is
  # exactly the judgment call this function exists to avoid. `|| true` is
  # load-bearing — grep -c exits 1 on zero matches, which is a real answer here.
  local count
  count="$(grep -c '^VERDICT:' "$path" || true)"
  [ -z "$count" ] && count=0
  if [ "$count" -ne 1 ]; then
    echo "MALFORMED"
    return 3
  fi

  local word
  word="$(sed -n 's/^VERDICT:[[:space:]]*\([A-Za-z]*\).*/\1/p' "$path")"
  case "$word" in
    PASS) echo "PASS"; return 0 ;;
    FAIL) echo "FAIL"; return 1 ;;
    *)    echo "MALFORMED"; return 3 ;;
  esac
}
