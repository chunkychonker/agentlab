#!/bin/sh
# Exports the override inside a subshell only; it dies with the subshell.
set -eu
( export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 )
claude -p "build phase"
