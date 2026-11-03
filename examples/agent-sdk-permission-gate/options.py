"""Builds the ``ClaudeAgentOptions`` for one gate layout.

The two layouts differ in one thing only: whether the ``PreToolUse`` hook is
registered. Both always pass the same ``can_use_tool`` callback, so any
difference in what gets through is the hook's doing.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from blind_spots import ModeledMode
from gates import HOOK_EVENT, Gates
from policy import GATED_TOOLS

# Model id in one constant; see knowledge/anthropic-models.md (cheapest current).
MODEL = "claude-haiku-4-5"
MAX_TURNS = 4
# Matchers match tool NAMES only (path and command checks live in the policy).
# Built from the policy's own list so the two cannot drift apart.
HOOK_MATCHER = "|".join(GATED_TOOLS)
# The only built-ins the model gets. Everything else is removed, not just unapproved.
BUILTIN_TOOLS: tuple[str, ...] = ("Read", *GATED_TOOLS)
# A failing hook (raises, returns None, or times out) blocks the call instead
# of counting as "no decision". The SDK refuses to connect to a CLI that
# cannot honour this, so it can't silently fail open.
HOOK_ON_FAILURE = "block"


class Layout(Enum):
    CALLBACK_ONLY = "callback-only"
    HOOK_AND_CALLBACK = "hook"


def build_options(
    gates: Gates, layout: Layout, permission_mode: ModeledMode, cwd: Path
) -> ClaudeAgentOptions:
    """Hermetic options for one layout. Pure: builds a value, starts nothing.

    No settings files (``setting_sources=[]``), so no allow rule outside this
    function can shadow the callback. No MCP servers. No ``allowed_tools``,
    so the only pre-callback approvals come from ``permission_mode``.
    """
    hooks = None
    if layout is Layout.HOOK_AND_CALLBACK:
        hooks = {
            HOOK_EVENT: [
                HookMatcher(matcher=HOOK_MATCHER, hooks=[gates.hook], on_failure=HOOK_ON_FAILURE)
            ]
        }
    return ClaudeAgentOptions(
        model=MODEL,
        max_turns=MAX_TURNS,
        cwd=cwd,
        tools=list(BUILTIN_TOOLS),
        allowed_tools=[],
        permission_mode=permission_mode,
        can_use_tool=gates.can_use_tool,
        hooks=hooks,
        setting_sources=[],
        strict_mcp_config=True,
    )
