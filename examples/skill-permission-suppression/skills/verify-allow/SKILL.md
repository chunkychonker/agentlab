---
name: verify-allow
description: Test-only skill for examples/skill-permission-suppression. Runs a bundled script that writes a sentinel file, to check whether a matching allowed-tools rule suppresses the Bash permission prompt. Not for real use.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/mark.sh *)
---

Run this command and nothing else, then reply DONE: ${CLAUDE_SKILL_DIR}/scripts/mark.sh $ARGUMENTS
