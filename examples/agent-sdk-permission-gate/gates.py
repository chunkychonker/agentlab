"""Adapters that put ``policy.decide`` behind the Agent SDK's two gate shapes.

``PreToolUse`` hook: runs first in Claude Code's permission order, on every
call whose tool name its matcher matches, in every permission mode.
``can_use_tool``: runs last, only for calls nothing earlier resolved.

Both report each decision to an injected audit sink. The audit sink is the
only side effect, and it is the caller's: this module holds no state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from claude_agent_sdk import (
    CanUseTool,
    HookCallback,
    HookContext,
    HookJSONOutput,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from policy import Allowed, Denied, Verdict, decide

HOOK_EVENT = "PreToolUse"
DENY = "deny"


class Checkpoint(Enum):
    """Which gate made a decision."""

    HOOK = "PreToolUse hook"
    CALLBACK = "can_use_tool"


@dataclass(frozen=True)
class AuditEntry:
    checkpoint: Checkpoint
    tool_name: str
    verdict: Verdict


AuditSink = Callable[[AuditEntry], None]


@dataclass(frozen=True)
class Gates:
    """One policy, two adapters. Build with ``build_gates``."""

    hook: HookCallback
    can_use_tool: CanUseTool


def build_gates(audit: AuditSink) -> Gates:
    """Return the hook and callback adapters over ``policy.decide``.

    ``audit`` is called once per decision, after the verdict is known. If it
    raises, the exception propagates out of the adapter: for the hook, the
    matcher's ``on_failure="block"`` (see ``options.py``) turns that into a
    blocked call; for ``can_use_tool`` the SDK sends the CLI an error reply,
    and what the CLI then does is not documented.
    """

    async def pre_tool_use_hook(
        input_data: Any, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """PreToolUse callback. Never raises on a bad payload: it denies.

        Returns ``{}`` (no opinion, the call continues down the permission
        order) when the policy allows, else the documented deny output. A
        payload without a string ``tool_name`` and a mapping ``tool_input``
        is denied, so a malformed request fails closed instead of reaching
        the SDK's error path, which by default counts as "no decision".
        """
        try:
            tool_name = input_data["tool_name"]
            tool_input = input_data["tool_input"]
            if not isinstance(tool_name, str) or not isinstance(tool_input, Mapping):
                raise TypeError("tool_name must be str and tool_input a mapping")
            verdict = decide(tool_name, tool_input)
        except Exception as exc:  # fail closed on any malformed payload
            tool_name = _best_effort_name(input_data)
            verdict = Denied(f"permission gate failed closed: {type(exc).__name__}: {exc}")
        audit(AuditEntry(Checkpoint.HOOK, tool_name, verdict))
        if isinstance(verdict, Allowed):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": HOOK_EVENT,
                "permissionDecision": DENY,
                "permissionDecisionReason": verdict.reason,
            }
        }

    async def can_use_tool(
        tool_name: str, input_data: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResult:
        """can_use_tool callback. The SDK guarantees the argument types.

        Allow always echoes ``updated_input``: CLIs before v2.1.207 rejected
        an allow without it.
        """
        verdict = decide(tool_name, input_data)
        audit(AuditEntry(Checkpoint.CALLBACK, tool_name, verdict))
        if isinstance(verdict, Denied):
            return PermissionResultDeny(message=verdict.reason)
        return PermissionResultAllow(updated_input=input_data)

    return Gates(hook=pre_tool_use_hook, can_use_tool=can_use_tool)


def _best_effort_name(input_data: Any) -> str:
    """A label for the audit log when the payload is malformed. Never raises."""
    name = input_data.get("tool_name") if isinstance(input_data, Mapping) else None
    return name if isinstance(name, str) else "<malformed payload>"
