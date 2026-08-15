#!/usr/bin/env bash
# Intent: settle, against the real `claude` CLI, whether an `allowed-tools`
# rule that reuses the exact ${CLAUDE_SKILL_DIR}-substituted command from the
# skill body lets a bundled script run in a headless session where no approval
# is possible. Two near-identical skills, one variable between them.
# See README.md for the observed answer and the cost.
#
# Behavioral contract (research/2026-08-16-skill-permission-suppression.md):
#   - fails fast, before invoking `claude`, if the CLI or either test skill is
#     missing, or if mark.sh is not executable
#   - each attempt gets a *fresh* throwaway project dir, so a sentinel file
#     from a previous attempt can never be mistaken for this attempt's result
#   - --permission-mode default is passed explicitly (ambient user settings may
#     set another default mode); assert_transcript.py re-checks the mode the
#     session actually started in and refuses to judge if it isn't `default`
#   - NO --bare: it skips skill auto-discovery, which would skip the thing
#     under test. That also means ambient ~/.claude context is always loaded,
#     so cost matches examples/mcp-connect-claude-code's non-bare figure.
#   - checks BOTH signals every time: the transcript's classification AND the
#     sentinel file's real existence. If they disagree, exits 3 immediately and
#     does not retry -- a disagreement is a finding, not noise.
#   - retries at most MAX_ATTEMPTS times per skill, only for retryable
#     failures (model never issued the command, or `claude` itself failed)
#   - exit code: 0 only if both skills produced their expected outcome
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$SCRIPT_DIR/skills"
VERIFIER="$SCRIPT_DIR/assert_transcript.py"
COMMAND_SUBSTRING="mark.sh"
MAX_ATTEMPTS=2

# Verifier exit codes -- single source of truth is assert_transcript.py's
# docstring; mirrored here as named constants rather than bare integers.
EXIT_PASS=0
EXIT_FAIL=1
EXIT_CONTRADICTION=3
EXIT_INVALID=4

# --- fail fast on missing prerequisites ---
if ! command -v claude >/dev/null 2>&1; then
  echo "FAIL: 'claude' CLI not found on PATH. Install Claude Code first (https://claude.com/claude-code)." >&2
  exit 1
fi

for skill in verify-allow verify-deny; do
  if [ ! -f "$SKILLS_DIR/$skill/SKILL.md" ]; then
    echo "FAIL: $SKILLS_DIR/$skill/SKILL.md is missing." >&2
    exit 1
  fi
  if [ ! -x "$SKILLS_DIR/$skill/scripts/mark.sh" ]; then
    echo "FAIL: $SKILLS_DIR/$skill/scripts/mark.sh is missing or not executable." >&2
    echo "  chmod +x $SKILLS_DIR/$skill/scripts/mark.sh" >&2
    exit 1
  fi
done

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY set: using it for auth." >&2
else
  echo "ANTHROPIC_API_KEY not set: falling back to ambient Claude Code auth." >&2
fi
echo "Note: --bare is deliberately NOT used (it skips skill discovery), so every" >&2
echo "run loads full ambient context -- see README.md 'Cost'." >&2

RUN_DIR="$(mktemp -d -t skill-permission-suppression.XXXXXX)"
echo "Transcripts and scratch project dirs: $RUN_DIR" >&2

# run_one <skill-name> <runs|blocked>
# Returns the verifier's exit code for the last attempt made.
run_one() {
  local skill="$1" expect="$2"
  local attempt=1 verdict_exit=$EXIT_FAIL

  while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
    echo "--- $skill: attempt $attempt/$MAX_ATTEMPTS ---" >&2

    # Fresh project dir per attempt: the sentinel's absence is only evidence
    # if nothing earlier could have created it.
    local proj="$RUN_DIR/$skill-attempt$attempt"
    mkdir -p "$proj/.claude/skills"
    cp -R "$SKILLS_DIR/$skill" "$proj/.claude/skills/$skill"
    local sentinel="$proj/sentinel.txt"
    local transcript="$RUN_DIR/$skill-attempt$attempt.jsonl"

    local claude_exit=0
    (cd "$proj" && claude -p "/$skill $sentinel" \
      --permission-mode default \
      --model haiku \
      --output-format stream-json --verbose \
      < /dev/null > "$transcript") || claude_exit=$?

    if [ "$claude_exit" -ne 0 ]; then
      echo "  claude exited $claude_exit; transcript: $transcript" >&2
      tail -n 3 "$transcript" >&2 || true
      attempt=$((attempt + 1))
      verdict_exit=$EXIT_FAIL
      continue
    fi

    verdict_exit=0
    python3 "$VERIFIER" "$expect" "$sentinel" "$COMMAND_SUBSTRING" \
      < "$transcript" || verdict_exit=$?

    case "$verdict_exit" in
      "$EXIT_PASS")
        echo "  $skill: PASS on attempt $attempt (transcript: $transcript)" >&2
        return "$EXIT_PASS"
        ;;
      "$EXIT_CONTRADICTION"|"$EXIT_INVALID")
        # Not retryable: retrying would only add cost and could hide the
        # inconsistency behind a luckier second attempt.
        echo "  $skill: stopping immediately (exit $verdict_exit). Transcript: $transcript" >&2
        return "$verdict_exit"
        ;;
      *)
        echo "  $skill: attempt $attempt did not pass. Transcript: $transcript" >&2
        attempt=$((attempt + 1))
        ;;
    esac
  done

  echo "  $skill: exhausted $MAX_ATTEMPTS attempts." >&2
  return "$verdict_exit"
}

allow_exit=0
run_one verify-allow runs || allow_exit=$?
if [ "$allow_exit" -eq "$EXIT_CONTRADICTION" ] || [ "$allow_exit" -eq "$EXIT_INVALID" ]; then
  echo "FAIL: verify-allow produced a non-retryable result (exit $allow_exit). See above." >&2
  exit "$allow_exit"
fi

deny_exit=0
run_one verify-deny blocked || deny_exit=$?
if [ "$deny_exit" -eq "$EXIT_CONTRADICTION" ] || [ "$deny_exit" -eq "$EXIT_INVALID" ]; then
  echo "FAIL: verify-deny produced a non-retryable result (exit $deny_exit). See above." >&2
  exit "$deny_exit"
fi

echo >&2
if [ "$allow_exit" -eq 0 ] && [ "$deny_exit" -eq 0 ]; then
  echo "run_e2e.sh: PASS -- allowed-tools suppressed the prompt for the bundled" >&2
  echo "script (verify-allow ran, sentinel written) and its absence did not" >&2
  echo "(verify-deny denied, no sentinel). Both signals agreed in both cases." >&2
  exit 0
fi

echo "run_e2e.sh: FAIL (verify-allow exit $allow_exit, verify-deny exit $deny_exit)." >&2
echo "Transcripts kept in $RUN_DIR" >&2
exit 1
