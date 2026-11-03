"""The permission policy: decides whether one tool call is permitted.

Pure core. No SDK import, no I/O, no clock, no environment. The two SDK gates
in ``gates.py`` are thin adapters over ``decide``; keeping the rule here means
the hook and the callback can never disagree about what is forbidden.

This is a tripwire, not a sandbox: rules are string checks on the tool input
as written. ``echo X > .env`` through Bash, ``rm -r -f``, a symlink into
``/etc`` and ``/bin/rm`` spelled differently all pass. Real containment needs
OS-level sandboxing (see the README's failure modes).
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from dataclasses import dataclass

SHELL_TOOL = "Bash"
WRITE_TOOLS = frozenset({"Write", "Edit"})
# Every tool ``decide`` can deny. The hook matcher is built from this, so the
# hook can never be registered for fewer tools than the policy has rules for.
GATED_TOOLS: tuple[str, ...] = (SHELL_TOOL, *sorted(WRITE_TOOLS))

PATH_FIELD = "file_path"
COMMAND_FIELD = "command"

PROTECTED_BASENAME = ".env"  # compared case-insensitively
# /etc is a symlink to /private/etc on macOS; both spellings are listed because
# the check is on the string, not the resolved path.
PROTECTED_DIRS: tuple[str, ...] = ("/etc", "/private/etc")
# Matched against the command lowercased with whitespace runs collapsed, so
# "rm  -Rf" is caught. "git push --force" also catches --force-with-lease: a
# deliberate over-match.
BLOCKED_COMMAND_FRAGMENTS: tuple[str, ...] = (
    "rm -rf",
    "rm -fr",
    "git push --force",
    "git push -f",
)


@dataclass(frozen=True)
class Allowed:
    """The policy has no objection. Carries nothing: there is no reason to give."""


@dataclass(frozen=True)
class Denied:
    """The policy forbids the call. ``reason`` is shown to the model.

    Raises:
        ValueError: on an empty or whitespace-only reason. A deny the model
            cannot explain to the user is not a representable state.
    """

    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("Denied requires a non-empty reason")


Verdict = Allowed | Denied
ALLOWED = Allowed()


def decide(tool_name: str, tool_input: Mapping[str, object]) -> Verdict:
    """Decide one tool call.

    Rules:
      * ``Write``/``Edit``: deny when ``file_path`` is missing, not a string,
        relative, has basename ``.env`` (any case), or lies in a protected dir.
      * ``Bash``: deny when ``command`` is missing, not a string, or contains a
        blocked fragment.
      * Any other tool: allowed.

    Relative paths are denied (fail closed) because ``decide`` has no working
    directory to resolve them against, and the ``can_use_tool`` callback is not
    given one. Claude Code's Write/Edit tools take absolute paths.

    Total: never raises for any ``str`` name and ``Mapping`` input.
    """
    if tool_name in WRITE_TOOLS:
        return _decide_write(tool_name, tool_input.get(PATH_FIELD))
    if tool_name == SHELL_TOOL:
        return _decide_shell(tool_input.get(COMMAND_FIELD))
    return ALLOWED


def _decide_write(tool_name: str, raw_path: object) -> Verdict:
    if not isinstance(raw_path, str) or not raw_path:
        return Denied(f"{tool_name} without a string {PATH_FIELD!r} is refused (fail closed)")
    if not raw_path.startswith("/"):
        return Denied(f"{tool_name} to relative path {raw_path!r} is refused: cannot resolve it")
    # normpath keeps a leading "//" (POSIX leaves it implementation-defined);
    # Linux and macOS treat it as "/", so collapse it or "//etc/x" slips past.
    path = "/" + posixpath.normpath(raw_path).lstrip("/")
    if posixpath.basename(path).casefold() == PROTECTED_BASENAME:
        return Denied(f"{tool_name} to {path} is blocked: {PROTECTED_BASENAME} files hold secrets")
    for protected in PROTECTED_DIRS:
        if path == protected or path.startswith(protected + "/"):
            return Denied(f"{tool_name} to {path} is blocked: {protected} is a protected directory")
    return ALLOWED


def _decide_shell(raw_command: object) -> Verdict:
    if not isinstance(raw_command, str) or not raw_command.strip():
        return Denied(f"{SHELL_TOOL} without a string {COMMAND_FIELD!r} is refused (fail closed)")
    normalized = " ".join(raw_command.split()).casefold()
    for fragment in BLOCKED_COMMAND_FRAGMENTS:
        if fragment in normalized:
            return Denied(f"{SHELL_TOOL} command containing {fragment!r} is blocked")
    return ALLOWED
