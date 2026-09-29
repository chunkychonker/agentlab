#!/bin/sh
# `env -i` without PATH in the allowlist: bare `claude` cannot be found.
set -eu
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
env -i HOME="$HOME" CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 claude -p "research phase"
