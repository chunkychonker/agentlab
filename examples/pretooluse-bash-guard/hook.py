#!/usr/bin/env python3
"""Claude Code PreToolUse hook: maps one hook payload to the hook's exit/stdout/stderr.

Contract (Claude Code hooks reference, fetched 2026-10-23):
- stdin is one JSON object with `hook_event_name`, `tool_name`, `tool_input`, ...
  For Bash, `tool_input.command` is the shell string.
- Deny = exit 0 and stdout is exactly one JSON object:
  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
   "permissionDecision": "deny", "permissionDecisionReason": "..."}}
- No decision = exit 0 with empty stdout. This hook never emits "allow",
  so it can never skip a permission prompt the user would otherwise see.
- Exit 1 and crashes do NOT block (fail open), so every failure we can
  detect is turned into an explicit deny here.

`decide` is pure (text in, HookResult out). `main` is the only I/O.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import NamedTuple

from classify import Allow, Deny, Verdict, classify

HOOK_EVENT_NAME = "PreToolUse"
GUARDED_TOOL = "Bash"
DECISION_DENY = "deny"
EXIT_OK = 0
EXIT_BLOCK = 2  # last-resort block if even writing the JSON fails

REASON_MALFORMED = (
    "Blocked by agentlab bash-guard (malformed-payload): the hook could not read "
    "a Bash command from its input ({detail}), so the call is denied rather than "
    "allowed unchecked."
)
REASON_INTERNAL_ERROR = (
    "Blocked by agentlab bash-guard (internal-error): the classifier raised "
    "{detail}; denying instead of failing open."
)


class HookResult(NamedTuple):
    """What the hook process should do: exit code, stdout text, stderr text."""

    exit_code: int
    stdout: str
    stderr: str


_NO_DECISION = HookResult(EXIT_OK, "", "")


def _deny(reason: str) -> HookResult:
    body = {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT_NAME,
            "permissionDecision": DECISION_DENY,
            "permissionDecisionReason": reason,
        }
    }
    return HookResult(EXIT_OK, json.dumps(body) + "\n", "")


def _command_from_payload(payload_text: str) -> str | None:
    """Return the Bash command, or None if the payload is for another tool.

    Raises ValueError (with a short description) if the payload is not valid
    JSON, not an object, or is a Bash call without a string `tool_input.command`.
    """
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stdin is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("stdin JSON is not an object")
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str):
        raise ValueError("`tool_name` is missing or not a string")
    if tool_name != GUARDED_TOOL:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        raise ValueError("`tool_input.command` is missing or not a string")
    return tool_input["command"]


def decide(payload_text: str, classifier: Callable[[str], Verdict] = classify) -> HookResult:
    """Map one PreToolUse payload to the hook's output.

    - Non-Bash tool: no decision (exit 0, empty stdout).
    - Bash, classifier returns Allow: no decision.
    - Bash, classifier returns Deny: exit 0 + deny JSON with the rule's reason.
    - Malformed payload (bad JSON, not an object, no tool_name, Bash without a
      string command): deny JSON naming the problem (fail closed).
    - Classifier raises: deny JSON naming the exception (fail closed).
    Never raises.
    """
    try:
        command = _command_from_payload(payload_text)
    except ValueError as exc:
        return _deny(REASON_MALFORMED.format(detail=exc))
    if command is None:
        return _NO_DECISION
    try:
        verdict = classifier(command)
    except Exception as exc:  # fail closed: a crash here would otherwise let the call through
        return _deny(REASON_INTERNAL_ERROR.format(detail=f"{type(exc).__name__}: {exc}"))
    if isinstance(verdict, Deny):
        return _deny(verdict.reason)
    if isinstance(verdict, Allow):
        return _NO_DECISION
    return _deny(REASON_INTERNAL_ERROR.format(detail=f"unknown verdict {verdict!r}"))


def main() -> int:
    """Read stdin, write the decision. Falls back to exit 2 + stderr if output fails."""
    try:
        payload_text = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        result = _deny(REASON_MALFORMED.format(detail=f"stdin is not UTF-8: {exc.reason}"))
    else:
        result = decide(payload_text)
    try:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        sys.stdout.flush()
    except OSError as exc:
        # exit 2 blocks the call and shows stderr to Claude.
        sys.stderr.write(f"agentlab bash-guard could not write its decision ({exc}); blocking.\n")
        return EXIT_BLOCK
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
