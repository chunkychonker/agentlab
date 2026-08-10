#!/usr/bin/env bash
# Intent: prove a real `claude` CLI session (the actual host, not the SDK's
# in-memory Client) discovers, connects to, and calls a locally-configured
# MCP server -- reusing examples/mcp-hello-world/server.py's `count_words`
# tool rather than duplicating server code. See README.md for what this
# proves and its cost.
#
# Behavioral contract (research/2026-08-08-mcp-connect-claude-code.md):
#   - fails fast, before invoking `claude`, if the `claude` binary is
#     missing or examples/mcp-hello-world's venv hasn't been built
#   - builds a throwaway --mcp-config file with an absolute, run-time-
#     resolved path to that venv's python (portable across checkouts, never
#     hardcoded)
#   - --strict-mcp-config so this repo's own .mcp.json (hn-search) can't
#     leak into or pollute the test
#   - retries up to MAX_ATTEMPTS times, bounded (never an infinite loop),
#     to absorb the model's tool-choice non-determinism -- not to paper
#     over a real connection failure, which fails on attempt 1 every time
#   - exit code: 0 only if assert_stream.py's four acceptance checks all
#     passed on some attempt; non-zero otherwise
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HELLO_WORLD_DIR="$REPO_ROOT/examples/mcp-hello-world"
PYBIN="$HELLO_WORLD_DIR/.venv/bin/python"
SERVER="$HELLO_WORLD_DIR/server.py"
TOOL_ALLOW="mcp__hello-world__count_words"
TEST_STRING="the quick brown fox jumps over"
MAX_ATTEMPTS=2

# --- fail fast on missing prerequisites, per the spec's explicit failure modes ---
if ! command -v claude >/dev/null 2>&1; then
  echo "FAIL: 'claude' CLI not found on PATH. Install Claude Code first (https://claude.com/claude-code)." >&2
  exit 1
fi

if [ ! -x "$PYBIN" ]; then
  echo "FAIL: $PYBIN not found." >&2
  echo "Build examples/mcp-hello-world's venv first:" >&2
  echo "  cd $HELLO_WORLD_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Single source of truth for the expected count: computed here with
# str.split(), the same operation the tool itself performs, never
# hardcoded a second time anywhere in this example.
EXPECTED_WORD_COUNT="$(python3 -c "print(len('$TEST_STRING'.split()))")"

CONFIG_FILE="$(mktemp -t mcp-connect-claude-code-config.XXXXXX.json)"
trap 'rm -f "$CONFIG_FILE" "$TRANSCRIPT_FILE"' EXIT

cat > "$CONFIG_FILE" <<EOF
{"mcpServers": {"hello-world": {"type": "stdio", "command": "$PYBIN", "args": ["$SERVER"]}}}
EOF

CLAUDE_ARGS=(
  --strict-mcp-config --mcp-config "$CONFIG_FILE"
  --allowedTools "$TOOL_ALLOW"
  --model haiku
  --output-format stream-json --verbose
)

# --bare requires ANTHROPIC_API_KEY (bare mode doesn't use subscription/OAuth
# login -- verified empirically during this build: `claude --bare -p ...`
# with no key set prints "Not logged in" and exits non-zero). Without a key,
# fall back to ambient Claude Code auth. This makes the run reproducible on
# a machine with a subscription login but *not* machine-independent, and
# it costs more (full ambient skills/context get loaded instead of --bare's
# minimal context) -- see README's cost section.
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY set: running with --bare (isolated, minimal context)." >&2
  CLAUDE_ARGS=(--bare "${CLAUDE_ARGS[@]}")
else
  echo "ANTHROPIC_API_KEY not set: falling back to ambient Claude Code auth (no --bare)." >&2
  echo "See README.md 'Cost and prerequisites' -- this run will cost more than the --bare estimate." >&2
fi

PROMPT="Use the count_words tool to count the exact number of whitespace-separated words in this string: \"$TEST_STRING\". Report only the integer the tool returns."

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  echo "--- attempt $attempt/$MAX_ATTEMPTS ---" >&2
  TRANSCRIPT_FILE="$(mktemp -t mcp-connect-claude-code-transcript.XXXXXX.jsonl)"

  claude_exit=0
  claude "${CLAUDE_ARGS[@]}" -p "$PROMPT" > "$TRANSCRIPT_FILE" || claude_exit=$?

  if [ "$claude_exit" -ne 0 ]; then
    echo "FAIL: claude exited $claude_exit (see transcript below)" >&2
    cat "$TRANSCRIPT_FILE" >&2
    rm -f "$TRANSCRIPT_FILE"
    attempt=$((attempt + 1))
    continue
  fi

  verdict_exit=0
  python3 "$SCRIPT_DIR/assert_stream.py" "$EXPECTED_WORD_COUNT" < "$TRANSCRIPT_FILE" || verdict_exit=$?

  if [ "$verdict_exit" -eq 0 ]; then
    echo "run_e2e.sh: PASS on attempt $attempt/$MAX_ATTEMPTS." >&2
    rm -f "$TRANSCRIPT_FILE"
    exit 0
  fi

  echo "Attempt $attempt failed assert_stream.py's checks. Transcript: $TRANSCRIPT_FILE" >&2
  attempt=$((attempt + 1))
done

echo "FAIL: exhausted $MAX_ATTEMPTS attempts without a passing transcript." >&2
exit 1
