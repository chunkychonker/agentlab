#!/usr/bin/env bash
# Intent: prove mechanically how the real `claude` CLI host surfaces an stdio
# MCP server's *resources* -- reusing examples/mcp-resources-vs-tools/server.py
# (the `notes` server: static resource notes://index, template notes://{note_id},
# tool create_note) unchanged, exactly as examples/mcp-connect-claude-code does
# with mcp-hello-world. See README.md for what this proves and its cost.
#
# Behavioral contract (research/2026-09-05-mcp-resources-claude-code.md):
#   - fails fast, before invoking `claude`, if the `claude` binary is missing
#   - reconciles the notes server's venv toward existing (builds it if absent,
#     idempotent and safe to re-run); fails loudly with the manual command if
#     that build fails
#   - builds a throwaway --mcp-config file with an absolute, run-time-resolved
#     path to that venv's python -- portable across checkouts, never hardcoded
#   - --strict-mcp-config so this repo's own .mcp.json (hn-search) cannot leak
#     into the test
#   - ASSERTED path: up to MAX_ATTEMPTS bounded attempts of one `-p` run that
#     asks for the resource by URI, each handed to assert_stream.py. The retry
#     absorbs the model's tool-choice non-determinism, not a real failure --
#     a disconnected server fails on attempt 1 every time.
#   - RECORDED-NOT-ASSERTED path: after the asserted path passes, exactly one
#     un-retried `-p` run whose prompt contains the literal token
#     `@notes:notes://index`, saved to fixtures/at_mention_transcript.jsonl and
#     summarized in one line. Whether `@`-mention resolution even runs in
#     headless mode is the open question this captures; it must never gate the
#     exit code.
#   - exit code: 0 iff assert_stream.py's six acceptance checks all passed on
#     some attempt; non-zero otherwise.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVER_DIR="$REPO_ROOT/examples/mcp-resources-vs-tools"
PYBIN="$SERVER_DIR/.venv/bin/python"
SERVER="$SERVER_DIR/server.py"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
AT_MENTION_FIXTURE="$FIXTURES_DIR/at_mention_transcript.jsonl"

# Single source of truth for both the --allowedTools value and the URI in the
# prompts. The tool *names* are the host's built-ins, re-asserted independently
# by assert_stream.py's LIST_TOOL_NAMES/READ_TOOL_NAMES.
LIST_TOOL="ListMcpResourcesTool"
READ_TOOL="ReadMcpResourceTool"
RESOURCE_URI="notes://index"
SERVER_NAME="notes"
MAX_ATTEMPTS=2

# --- fail fast on missing prerequisites, per the spec's explicit failure modes ---
if ! command -v claude >/dev/null 2>&1; then
  echo "FAIL: 'claude' CLI not found on PATH. Install Claude Code first (https://claude.com/claude-code)." >&2
  exit 1
fi

if [ ! -f "$SERVER" ]; then
  echo "FAIL: $SERVER not found -- this example reuses the notes server from examples/mcp-resources-vs-tools." >&2
  exit 1
fi

# Declare desired state, reconcile toward it (Protocol section 5): the venv is
# a build artifact of the *other* example, so build it here rather than making
# the reviewer read an error message and type it themselves. Re-running is a
# no-op.
if [ ! -x "$PYBIN" ]; then
  echo "notes server venv missing; building $SERVER_DIR/.venv ..." >&2
  if ! (python3 -m venv "$SERVER_DIR/.venv" && "$PYBIN" -m pip install -q -r "$SERVER_DIR/requirements.txt"); then
    echo "FAIL: could not build $SERVER_DIR/.venv. Build it manually:" >&2
    echo "  cd $SERVER_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
  fi
fi

CONFIG_FILE="$(mktemp -t mcp-resources-claude-code-config.XXXXXX.json)"
TRANSCRIPT_FILE=""
trap 'rm -f "$CONFIG_FILE" "$TRANSCRIPT_FILE"' EXIT

cat > "$CONFIG_FILE" <<EOF
{"mcpServers": {"$SERVER_NAME": {"type": "stdio", "command": "$PYBIN", "args": ["$SERVER"]}}}
EOF

CLAUDE_ARGS=(
  --strict-mcp-config --mcp-config "$CONFIG_FILE"
  --allowedTools "$LIST_TOOL $READ_TOOL"
  --model haiku
  --output-format stream-json --verbose
)

# --bare requires ANTHROPIC_API_KEY (bare mode never reads the keychain/OAuth
# login -- verified in examples/mcp-connect-claude-code's build). Without a
# key, fall back to ambient Claude Code auth, which works but loads the full
# session context and therefore costs more. See README's cost section.
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY set: running with --bare (isolated, minimal context)." >&2
  CLAUDE_ARGS=(--bare "${CLAUDE_ARGS[@]}")
else
  echo "ANTHROPIC_API_KEY not set: falling back to ambient Claude Code auth (no --bare)." >&2
  echo "See README.md 'Cost and prerequisites' -- this run will cost more than the --bare estimate." >&2
fi

# Two explicit steps. The obvious one-step phrasing ("read $RESOURCE_URI and
# report the title") was tried first during this example's build and the model
# skipped the listing tool entirely both times -- correctly, since the prompt
# already handed it the URI. Criterion 2/3 asserts that `resources/list` really
# runs and really surfaces the URI, so the prompt has to actually require the
# discovery step rather than assume the model will take it.
READ_PROMPT="Two steps, both using the MCP resource tools. Step 1: list the resources the '$SERVER_NAME' MCP server exposes. Step 2: read the resource $RESOURCE_URI from that server. Then report only the title of the first note in that resource. Do not skip step 1."
AT_MENTION_PROMPT="Quote the contents of @$SERVER_NAME:$RESOURCE_URI verbatim."

# --- asserted path -------------------------------------------------------
passed=0
attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  echo "--- attempt $attempt/$MAX_ATTEMPTS (asserted: resource tools) ---" >&2
  TRANSCRIPT_FILE="$(mktemp -t mcp-resources-claude-code-transcript.XXXXXX.jsonl)"

  claude_exit=0
  claude "${CLAUDE_ARGS[@]}" -p "$READ_PROMPT" > "$TRANSCRIPT_FILE" || claude_exit=$?

  if [ "$claude_exit" -ne 0 ]; then
    echo "FAIL: claude exited $claude_exit (see transcript below)" >&2
    cat "$TRANSCRIPT_FILE" >&2
    rm -f "$TRANSCRIPT_FILE"
    TRANSCRIPT_FILE=""
    attempt=$((attempt + 1))
    continue
  fi

  verdict_exit=0
  python3 "$SCRIPT_DIR/assert_stream.py" < "$TRANSCRIPT_FILE" || verdict_exit=$?

  if [ "$verdict_exit" -eq 0 ]; then
    echo "run_e2e.sh: PASS on attempt $attempt/$MAX_ATTEMPTS." >&2
    rm -f "$TRANSCRIPT_FILE"
    TRANSCRIPT_FILE=""
    passed=1
    break
  fi

  echo "Attempt $attempt failed assert_stream.py's checks. Transcript kept at: $TRANSCRIPT_FILE" >&2
  TRANSCRIPT_FILE=""   # keep it on disk for inspection; do not rm on exit
  attempt=$((attempt + 1))
done

if [ "$passed" -ne 1 ]; then
  echo "FAIL: exhausted $MAX_ATTEMPTS attempts without a passing transcript." >&2
  exit 1
fi

# --- recorded, NOT asserted: the @-mention path --------------------------
# One un-retried run. Its outcome is documentation (README + knowledge note),
# never a gate: whether a `@server:uri` token in a `-p` prompt resolves at all
# is precisely the open question, so a failure here is a finding, not a bug.
echo "--- recording the @-mention path (not asserted) ---" >&2
mkdir -p "$FIXTURES_DIR"
at_mention_exit=0
claude "${CLAUDE_ARGS[@]}" -p "$AT_MENTION_PROMPT" > "$AT_MENTION_FIXTURE" || at_mention_exit=$?

if [ "$at_mention_exit" -ne 0 ]; then
  echo "@-mention run: claude exited $at_mention_exit; transcript kept at $AT_MENTION_FIXTURE (not asserted)." >&2
else
  # One-line mechanical summary, by grep over the raw JSONL -- deliberately
  # not in assert_stream.py, which verifies the asserted path only.
  marker_seen="no"
  grep -q "Welcome" "$AT_MENTION_FIXTURE" && marker_seen="yes"
  read_tool_seen="no"
  grep -q "\"$READ_TOOL\"" "$AT_MENTION_FIXTURE" && read_tool_seen="yes"
  literal_token_seen="no"
  grep -q "@$SERVER_NAME:$RESOURCE_URI" "$AT_MENTION_FIXTURE" && literal_token_seen="yes"
  final_is_error="$(grep -o '"is_error":[a-z]*' "$AT_MENTION_FIXTURE" | tail -1 || true)"
  echo "@-mention run (recorded, not asserted): resource content ('Welcome') present=$marker_seen; $READ_TOOL called=$read_tool_seen; literal '@$SERVER_NAME:$RESOURCE_URI' token echoed=$literal_token_seen; final ${final_is_error:-<none>}" >&2
  echo "@-mention transcript saved to $AT_MENTION_FIXTURE" >&2
fi

exit 0
