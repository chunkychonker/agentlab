#!/bin/sh
# The veriloom/cli #72 shape: the override IS exported (a grep for the export
# line passes), but the phase runs under `env -i` with an allowlist that omits
# it, so `claude` never sees it.
set -eu
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
env -i HOME="$HOME" PATH="$PATH" TERM="${TERM:-dumb}" LANG="${LANG:-C}" \
  claude -p "review phase"
