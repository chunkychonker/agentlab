#!/usr/bin/env python3
"""publish_guard.py: a PreToolUse hook that blocks publishing outside the maintain phase.

The nightly pipeline (.pipeline/run.sh) sets AGENTLAB_PHASE for every phase
and registers this hook through .pipeline/phase_settings.json, so it runs only
in nightly phases, never in an interactive session. For a Bash tool call it
blocks `git push` and every `gh` command that is not on the read-only list
below, unless AGENTLAB_PHASE is `maintain`.

It is a backstop for the deny rules in .pipeline/phase_tools.sh, not a
replacement and not a security boundary: like any text check it can be gotten
around (a script file, `python3 -c`, an alias). It exists because a deny rule
was already bypassed once by accident. The RTK hook rewrites `git push` to
`rtk git push` before permissions are checked, so this guard sees through
`rtk` and the other prefixes listed in _PREFIXES.

The command parsing follows examples/pretooluse-bash-guard (global git
options, nested `bash -c`, fail closed), cut down to the one question asked
here. Python 3.9 stdlib only: it runs under /usr/bin/python3.

Exit codes (Claude Code hook protocol):
  0  allow; prints nothing
  2  block; the reason goes to stderr, which Claude Code shows the model

Failure modes: AGENTLAB_PHASE unset or empty, a payload that is not a JSON
object, or a Bash call with no string command all block (exit 2). A command
the tokenizer cannot parse (an unclosed quote, a heredoc body) blocks only if
its raw text looks like `git ... push` or `gh`, so a builder's heredoc with an
apostrophe is not stopped for nothing.
"""

import json
import os
import re
import shlex
import sys
from typing import List, Optional, Tuple

PUBLISH_PHASE = "maintain"
MAX_NESTING = 3

# Words that run the command after them unchanged.
_PREFIXES = {"rtk", "command", "env", "exec", "nohup", "time", "builtin", "sudo"}
_SHELLS = {"bash", "sh", "zsh", "/bin/bash", "/bin/sh", "/bin/zsh"}
_PUNCTUATION = ";&|()\n"
# git global options that take a value as the next word.
_GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}

# gh calls that only read. Anything else is a write, or might become one in a
# future gh release, so it is treated as one.
_GH_READ_ONLY = {
    ("pr", "list"), ("pr", "view"), ("pr", "status"), ("pr", "checks"), ("pr", "diff"),
    ("issue", "list"), ("issue", "view"), ("issue", "status"),
    ("repo", "view"), ("run", "list"), ("run", "view"), ("auth", "status"),
}

_RAW_PUBLISH = re.compile(r"\bgit\b[^\n;&|]*\bpush\b|(^|[\s;&|(`$])gh\s")


class _Blocked(Exception):
    pass


def _strip_prefixes(words: List[str]) -> List[str]:
    """Drops env assignments and pass-through prefixes from the front."""
    i = 0
    while i < len(words):
        w = words[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            i += 1
        elif os.path.basename(w) in _PREFIXES:
            i += 1
        else:
            break
    return words[i:]


def _git_subcommand(args: List[str]) -> Optional[str]:
    """The first git word after the global options, e.g. `push` in `git -C x push`."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in _GIT_OPTS_WITH_VALUE:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            return a
    return None


def _check_simple(words: List[str], depth: int) -> None:
    """Raises _Blocked if one simple command publishes."""
    words = _strip_prefixes(words)
    if not words:
        return
    prog = os.path.basename(words[0])
    if prog in {os.path.basename(s) for s in _SHELLS}:
        if "-c" in words[1:]:
            idx = words.index("-c", 1)
            if idx + 1 < len(words):
                _check_command(words[idx + 1], depth + 1)
        return
    if prog == "git" and _git_subcommand(words[1:]) == "push":
        raise _Blocked("git push")
    if prog == "gh":
        sub = tuple(w for w in words[1:3])
        if sub not in _GH_READ_ONLY:
            raise _Blocked("gh " + " ".join(sub) if sub else "gh")


def _check_command(command: str, depth: int) -> None:
    """Raises _Blocked if any part of a shell command line publishes."""
    if depth > MAX_NESTING:
        raise _Blocked("shell nesting deeper than %d" % MAX_NESTING)
    # Backticks run a command just like $( ), so they split words the same way.
    command = command.replace("`", " ; ")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION)
        lexer.whitespace_split = True
        lexer.whitespace = " \t\r"
        tokens = list(lexer)
    except ValueError:
        if _RAW_PUBLISH.search(command):
            raise _Blocked("unparseable command that mentions git push or gh")
        return
    current: List[str] = []
    for tok in tokens:
        if tok and set(tok) <= set(_PUNCTUATION):
            _check_simple(current, depth)
            current = []
        else:
            current.append(tok)
    _check_simple(current, depth)


def decide(phase: Optional[str], payload: object) -> Tuple[bool, str]:
    """Returns (allowed, reason) for one PreToolUse payload. Pure."""
    if not phase:
        return False, "AGENTLAB_PHASE is not set; refusing to guess which phase this is"
    if not isinstance(payload, dict):
        return False, "hook payload is not a JSON object"
    if payload.get("tool_name") != "Bash":
        return True, ""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return False, "Bash call has no command string"
    if phase == PUBLISH_PHASE:
        return True, ""
    try:
        _check_command(command, 0)
    except _Blocked as e:
        return False, (
            "publish_guard: '%s' is not allowed in the '%s' phase. Only the "
            "maintain phase may push or write to GitHub; the pipeline does that "
            "for you." % (e, phase)
        )
    return True, ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        payload = None
    allowed, reason = decide(os.environ.get("AGENTLAB_PHASE"), payload)
    if allowed:
        return 0
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
