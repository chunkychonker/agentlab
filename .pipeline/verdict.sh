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
# Sourcing this file defines two functions and two constants. It runs nothing,
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

# What the reviewer writes on its Spec line when its prompt named no spec (the
# reviewer eval calls it that way). Named once; the prompt spells it out too.
REVIEW_SPEC_NONE="none given"

# review_spec_check <verdict-file> <expected-spec-path>
#
# Did the reviewer find the build meets its spec (spec #9)? Reads the Spec block
# the reviewer writes between `Increment:` and `Ran:` in its verdict file:
#
#   Spec: research/2026-11-11-some-note.md
#   - MET B1 — test: tests/test_x.py::test_one
#   - CUT B7 — explained in README.md "What was cut": <reason>
#   - UNMET B3 — <what is missing>
#
# The block is every line after `Spec:` up to the first `Ran:` line. Inside it,
# a blank line is allowed and every other line must be `- MET|CUT|UNMET
# <label> — <text>`, with a MET line's text starting `test: `. Strict for the
# same reason review_verdict is: this gate fails closed, so a block it cannot
# read with certainty does not ship.
#
# Prints the result word on line 1 (the return code mirrors it). On OK, line 2
# is "<n> met, <m> cut"; on UNMET, line 2 is the UNMET labels, comma-separated.
#   0  OK          at least one MET, no UNMET, every line well formed
#   1  UNMET       at least one UNMET line, even under VERDICT: PASS
#   2  NONE_MET    well formed, but no MET line (all CUT, or no criteria lines)
#   3  NO_SPEC     no Spec line, or `Spec: none given`
#   4  WRONG_SPEC  the Spec path is not <expected-spec-path>
#   5  MALFORMED   two Spec lines, a line breaking the grammar, a MET line
#                  without `test: `, or the file is missing or unreadable
# MALFORMED outranks UNMET: one unreadable line means the rest can't be trusted.
review_spec_check () {
  local path="$1" expected="$2"
  if [ ! -f "$path" ] || [ ! -r "$path" ]; then
    echo "MALFORMED"
    return 5
  fi

  local count
  count="$(grep -c '^Spec:' "$path" || true)"
  [ -z "$count" ] && count=0
  if [ "$count" -eq 0 ]; then
    echo "NO_SPEC"
    return 3
  fi
  if [ "$count" -gt 1 ]; then
    echo "MALFORMED"
    return 5
  fi

  local named
  named="$(sed -n 's/^Spec:[[:space:]]*//p' "$path" | sed 's/[[:space:]]*$//')"
  if [ "$named" = "$REVIEW_SPEC_NONE" ]; then
    echo "NO_SPEC"
    return 3
  fi
  if [ "$named" != "$expected" ]; then
    echo "WRONG_SPEC"
    return 4
  fi

  # The regex lives in a variable: since bash 3.2 a quoted pattern after =~ is
  # matched as a literal string, not a regex.
  local re='^- (MET|CUT|UNMET) ([^ ]+) — (.+)$'
  local line in_block=0 bad=0 met=0 cut=0 unmet=""
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$in_block" -eq 0 ]; then
      case "$line" in Spec:*) in_block=1 ;; esac
      continue
    fi
    case "$line" in Ran:*) break ;; esac
    [ -z "$line" ] && continue
    if [[ "$line" =~ $re ]]; then
      case "${BASH_REMATCH[1]}" in
        MET)
          case "${BASH_REMATCH[3]}" in
            "test: "?*) met=$(( met + 1 )) ;;
            *) bad=1 ;;
          esac ;;
        CUT) cut=$(( cut + 1 )) ;;
        UNMET) unmet="${unmet:+$unmet, }${BASH_REMATCH[2]}" ;;
      esac
    else
      bad=1
    fi
  done < "$path"

  if [ "$bad" -eq 1 ]; then
    echo "MALFORMED"
    return 5
  fi
  if [ -n "$unmet" ]; then
    printf 'UNMET\n%s\n' "$unmet"
    return 1
  fi
  if [ "$met" -eq 0 ]; then
    echo "NONE_MET"
    return 2
  fi
  printf 'OK\n%s met, %s cut\n' "$met" "$cut"
  return 0
}
