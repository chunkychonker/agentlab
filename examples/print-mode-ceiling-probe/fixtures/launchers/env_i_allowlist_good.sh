#!/bin/sh
# Same `env -i` allowlist, with the override added to it (the #72 fix).
set -eu
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
env -i HOME="$HOME" PATH="$PATH" TERM="${TERM:-dumb}" LANG="${LANG:-C}" \
  CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="$CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS" \
  claude -p "review phase"
