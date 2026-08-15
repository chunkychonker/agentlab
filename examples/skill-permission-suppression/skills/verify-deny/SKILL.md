---
name: verify-deny
description: Test-only skill for examples/skill-permission-suppression. Runs a bundled script that writes a sentinel file, with NO allowed-tools rule -- the control case for whether allowed-tools is what suppresses the Bash permission prompt. Not for real use.
disable-model-invocation: true
---

Run this command and nothing else, then reply DONE: ${CLAUDE_SKILL_DIR}/scripts/mark.sh $ARGUMENTS
