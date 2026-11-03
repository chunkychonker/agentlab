"""Names the gated tools a rule enforced only in ``can_use_tool`` would never see.

Pure. Encodes the documented evaluation order (hooks, deny rules, ask rules,
permission mode, allow rules, ``can_use_tool``) for the permission modes it
models. A tool in the returned set has calls the callback never sees, so a
deny rule enforced only in the callback is silently skipped for them.
``unguarded`` then subtracts the tools a ``PreToolUse`` hook covers. Imports
the SDK's options type only; no I/O.

Source: https://code.claude.com/docs/en/agent-sdk/permissions (read
2026-11-03). Over-reporting is the safe direction: for ``acceptEdits`` a tool
is listed when *some* of its calls (those inside the working directory) are
auto-approved, not all of them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Literal, get_args

from claude_agent_sdk import ClaudeAgentOptions

# The modes this module models. "plan" and "auto" are left out on purpose:
# plan mode's behaviour depends on whether auto mode is available to the
# session, and auto mode's classifier is a runtime decision. Neither can be
# answered from options alone.
ModeledMode = Literal["default", "acceptEdits", "bypassPermissions", "dontAsk"]
MODELED_MODES: frozenset[str] = frozenset(get_args(ModeledMode))

# acceptEdits auto-approves Edit/Write (and notebook edits) plus the Bash
# filesystem commands mkdir, touch, rm, rmdir, mv, cp, sed, all inside the
# working directory. That makes "rm -rf ./build" a Bash call the callback
# never sees.
ACCEPT_EDITS_APPROVES: frozenset[str] = frozenset({"Write", "Edit", "NotebookEdit", "Bash"})


def approved_before_callback(
    permission_mode: str,
    allowed_tools: Sequence[str],
    gated_tools: Iterable[str],
) -> frozenset[str]:
    """Return the members of ``gated_tools`` that can be approved before ``can_use_tool``.

    * ``bypassPermissions``: every tool.
    * ``acceptEdits``: the tools in ``ACCEPT_EDITS_APPROVES``, plus allow rules.
    * ``default`` and ``dontAsk``: only tools an ``allowed_tools`` entry
      allows outright (``"X"``, ``"X()"``, ``"X(*)"``). ``dontAsk`` never calls
      the callback either, but it *denies* what would have prompted, so only
      allow rules approve anything there.

    Scoped entries such as ``"Bash(ls *)"`` approve only matching calls; the
    rest still reach the callback, so they are not reported. Allow rules from
    settings files are invisible here (pass ``setting_sources=[]``).

    Raises:
        ValueError: ``permission_mode`` is not one of ``MODELED_MODES``.
    """
    if permission_mode not in MODELED_MODES:
        raise ValueError(
            f"permission mode {permission_mode!r} is not modeled; "
            f"expected one of {sorted(MODELED_MODES)}"
        )
    gated = frozenset(gated_tools)
    if permission_mode == "bypassPermissions":
        return gated
    by_rule = {tool for entry in allowed_tools if (tool := _whole_tool(entry)) is not None}
    by_mode = ACCEPT_EDITS_APPROVES if permission_mode == "acceptEdits" else frozenset()
    return gated & (by_rule | by_mode)


def _whole_tool(entry: str) -> str | None:
    """The tool an ``allowed_tools`` entry allows outright, or None if it is scoped."""
    entry = entry.strip()
    if not entry:
        return None
    open_paren = entry.find("(")
    if open_paren == -1:
        return entry
    if open_paren == 0 or not entry.endswith(")"):
        return None
    return entry[:open_paren] if entry[open_paren + 1 : -1] in ("", "*") else None


def unguarded(options: ClaudeAgentOptions, gated_tools: Iterable[str]) -> frozenset[str]:
    """Gated tools whose calls can be approved with no policy check at all.

    That is: approved before ``can_use_tool`` (see ``approved_before_callback``)
    and not matched by any ``PreToolUse`` hook, which runs before every other
    step. An empty result means every gated call meets the policy somewhere.

    Matcher semantics follow Claude Code: ``None``, ``""`` and ``"*"`` match
    every tool; anything else is a regex that must match the whole tool name.

    Raises:
        ValueError: ``options.permission_mode`` is not modeled (``None`` is
            read as ``"default"``).
    """
    gated = frozenset(gated_tools)
    mode = options.permission_mode or "default"
    exposed = approved_before_callback(mode, options.allowed_tools, gated)
    hooked = {tool for tool in gated if _hook_matches(options, tool)}
    return exposed - hooked


def _hook_matches(options: ClaudeAgentOptions, tool_name: str) -> bool:
    for matcher in (options.hooks or {}).get("PreToolUse", []):
        if not matcher.hooks:
            continue
        pattern = matcher.matcher
        if pattern in (None, "", "*") or re.fullmatch(pattern, tool_name):
            return True
    return False
