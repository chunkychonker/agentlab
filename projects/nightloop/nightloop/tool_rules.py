"""Classifies one Claude Code ``--allowedTools`` rule as acceptable or not.

Pure: no I/O. The rules encoded here come from the Claude Code permissions
docs (read 2026-11-08, see research/2026-11-08-nightloop-config-schema.md).
Each rejection targets a rule that Claude Code would either silently ignore
(so the phase is denied a tool its author thought it had) or that is far
broader than it reads (so an unattended loop gets more than intended).
"""

from __future__ import annotations

import re

_MCP_PREFIX = "mcp__"
_MCP_SEPARATOR = "__"
_BASH = "Bash"
_PREFIX_SUFFIX = ":*"  # Bash(git log:*) == Bash(git log *), only at the end
_WILDCARD = "*"

# Bare tool names: Read, WebFetch, NotebookEdit, ...
_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
# mcp__<server>__<tool>: server is literal, tool may carry * globs.
_MCP_SERVER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_MCP_TOOL = re.compile(r"[A-Za-z0-9_*-]+")

# Bash specifiers that approve every command.
_UNRESTRICTED_BASH_SPECIFIERS = frozenset({_WILDCARD, _PREFIX_SUFFIX})


def rule_violation(rule: str, *, allow_unrestricted_bash: bool) -> str | None:
    """Return why ``rule`` must be rejected, or ``None`` if it is acceptable.

    ``None`` is the "no violation" result, not a swallowed error. Never raises
    for any ``str`` input. ``allow_unrestricted_bash`` is the phase's explicit
    opt-in for ``Bash`` / ``Bash(*)`` / ``Bash(:*)``.
    """
    if rule == "":
        return "must be a non-empty rule"
    if rule != rule.strip():
        return "has leading or trailing whitespace, which Claude Code will not match"

    name, specifier = _split(rule)
    if name is None:
        return "has '(' without a closing ')' at the end"
    if specifier == "":
        return "has an empty specifier '()'"

    if name.startswith(_MCP_PREFIX):
        return _mcp_violation(name, specifier)
    if _WILDCARD in name:
        return (
            "a wildcard in the tool name approves nothing: Claude Code skips "
            "unanchored tool-name globs (only mcp__<server>__* is honoured)"
        )
    if not _TOOL_NAME.fullmatch(name):
        return f"tool name {name!r} is not a valid tool name"
    if name == _BASH:
        return _bash_violation(specifier, allow_unrestricted_bash)
    return None


def _split(rule: str) -> tuple[str | None, str | None]:
    """Split ``Tool(spec)`` into ``(Tool, spec)``; ``Tool`` into ``(Tool, None)``.

    Returns ``(None, None)`` when there is a ``(`` but the rule does not end
    with ``)``.
    """
    if "(" not in rule:
        return rule, None
    name, rest = rule.split("(", 1)
    if not rest.endswith(")"):
        return None, None
    return name, rest[:-1]


def _mcp_violation(name: str, specifier: str | None) -> str | None:
    if specifier is not None:
        return (
            "mcp__ rules with a parenthesised specifier are skipped by Claude "
            "Code; use mcp__<server>__<tool> or mcp__<server>__*"
        )
    server, separator, tool = name[len(_MCP_PREFIX):].partition(_MCP_SEPARATOR)
    if server == "" or _WILDCARD in server:
        return (
            "unanchored mcp__ wildcard approves nothing; name the server, "
            "e.g. mcp__<server>__*"
        )
    if not _MCP_SERVER.fullmatch(server):
        return f"MCP server name {server!r} is not valid"
    if separator and tool == "":
        return "ends with '__' but names no tool; use mcp__<server> or mcp__<server>__*"
    if separator and not _MCP_TOOL.fullmatch(tool):
        return f"MCP tool name {tool!r} is not valid"
    return None


def _bash_violation(specifier: str | None, allow_unrestricted: bool) -> str | None:
    if specifier is None or specifier in _UNRESTRICTED_BASH_SPECIFIERS:
        if allow_unrestricted:
            return None
        return (
            "allows every shell command; set allow_unrestricted_bash = true "
            "in this phase to opt in explicitly"
        )

    body = specifier
    if body.endswith(_PREFIX_SUFFIX):
        body = body[: -len(_PREFIX_SUFFIX)]
    if _PREFIX_SUFFIX in body:
        return (
            "':*' is only recognised at the end of a pattern; elsewhere the "
            "colon is literal and the rule will not match what it looks like"
        )

    words = body.split(" ")
    if "" in words:
        return "has an empty word (leading, trailing, or repeated spaces)"
    if _WILDCARD in words[0]:
        return "a wildcard in the command position matches arbitrary commands; name the command"
    if len(words) > 2 and _WILDCARD in words[1]:
        return (
            "a wildcard before the subcommand (the 'git * main' shape) matches "
            "far more than it reads, e.g. 'git -c <opt> ... main'; name the subcommand"
        )
    return None
