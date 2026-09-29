#!/bin/sh
# Exports the override, then runs the phase. The variable reaches `claude`.
set -eu
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
claude -p "research phase"
