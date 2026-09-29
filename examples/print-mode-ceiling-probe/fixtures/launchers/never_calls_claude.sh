#!/bin/sh
# Runs a hard-coded binary path instead of bare `claude`, and exits 0.
# The stub is never reached, so nothing about the environment can be claimed.
set -eu
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
/usr/bin/true -p "research phase"
