#!/bin/sh
# Sets the override to the empty string: present, but not "0".
set -eu
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=""
claude -p "build phase"
