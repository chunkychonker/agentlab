#!/bin/sh
# Two phases: the first runs with the override, the second under `env -i`
# without it. Only the second is exposed to the 600s default.
set -eu
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
claude -p "research phase"
env -i HOME="$HOME" PATH="$PATH" claude -p "review phase"
