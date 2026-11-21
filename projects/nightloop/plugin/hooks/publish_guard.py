#!/usr/bin/env python3
"""publish_guard.py: a PreToolUse hook that blocks publishing from any nightloop model phase.

nightloop's phases (research, build, review) never publish: the Shipper
pushes the cycle's branch and opens the PR outside any model run, after the
review gate passes. So this guard needs no phase. For every Bash tool call
it blocks `git push` and every `gh` command that is not on the read-only
list below, whichever agent is running.

It is a backstop for the phases' `--allowedTools` rules, not a security
boundary: like any text check it can be gotten around (a script file,
`python3 -c`, an alias). While the plugin is enabled it also guards an
interactive session; disable the plugin to push by hand from Claude Code.

Ported from agentlab's `.claude/hooks/publish_guard.py` (2026-11-21) with
its two agentlab-specific parts removed: the `AGENTLAB_PHASE` requirement
and the `maintain` phase exemption. Everything else, including the parser,
is the same. It is a copy that is meant to diverge, never an import: the
plugin must stand alone. Python 3.9 stdlib only, because `python3` on PATH
may be macOS's /usr/bin/python3.

Exit codes (Claude Code hook protocol):
  0  allow; prints nothing
  2  block; the reason goes to stderr, which Claude Code shows the model
Exit 1 or a crash would be a non-blocking error, i.e. fail open, so main()
turns every exception into exit 2.

Failure modes: a payload that is not a JSON object, or a Bash call with no
string command, blocks. A command the tokenizer cannot parse (an unclosed
quote) blocks only if its raw text looks like `git ... push` or `gh`. The
text of a quoted heredoc fed to `cat` or `tee` is dropped before checking,
because it is written to a file, not run; see _drop_data_heredocs.
"""

import json
import os
import re
import shlex
import sys
from typing import List, Optional, TextIO, Tuple

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

# Commands that only copy a heredoc's text to a file or to stdout.
_HEREDOC_DATA_SINKS = {"cat", "tee"}
# A heredoc operator with a quoted end word: <<'EOF', <<"EOF", <<-'EOF'.
# Quoting matters: the shell expands $( ) inside an unquoted heredoc, so its
# text can run a command. `<<<` (a here-string) is excluded.
_QUOTED_HEREDOC = re.compile(r"""(?<!<)<<(-?)[ \t]*(?:'([^'\n]+)'|"([^"\n]+)")""")
_ANY_HEREDOC = re.compile(r"(?<!<)<<(?!<)")

_RAW_PUBLISH = re.compile(r"\bgit\b[^\n;&|]*\bpush\b|(^|[\s;&|(`$])gh\s")

_BLOCK_MESSAGE = (
    "publish_guard: '%s' is not allowed. No nightloop phase may push or write to "
    "GitHub; nightloop's shipper does that after the review passes."
)


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


def _tokens(text: str) -> List[str]:
    """Splits shell text into words and punctuation runs. Raises ValueError on an unclosed quote."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    # shlex's own comment handling eats the newline that ends the comment, so
    # `ls # note` followed by `git push` on the next line would read as one
    # harmless command. With it off, a comment's words are checked like any others.
    lexer.commenters = ""
    return list(lexer)


def _is_data_heredoc_line(line: str, match: "re.Match[str]") -> bool:
    """True if the heredoc opened on this line only feeds text to cat or tee.

    The command that owns the heredoc must be `cat` or `tee` (after the usual
    prefixes), and nothing after the operator may pipe its output onward.
    Any doubt, including a line that does not tokenize, is False. So is a `#`
    before the operator: a commented-out operator opens no heredoc.
    """
    if "#" in line[: match.start()]:
        return False
    try:
        before = _tokens(line[: match.start()])
        after = _tokens(line[match.end():])
    except ValueError:
        return False
    if any("|" in t for t in after if t and set(t) <= set(_PUNCTUATION)):
        return False
    owner: List[str] = []
    for tok in before:
        if tok and set(tok) <= set(_PUNCTUATION):
            owner = []
        else:
            owner.append(tok)
    owner = _strip_prefixes(owner)
    return bool(owner) and os.path.basename(owner[0]) in _HEREDOC_DATA_SINKS


def _drop_data_heredocs(command: str) -> str:
    """Returns the command without the text of heredocs that are only data.

    A heredoc's text is dropped only when all of these hold: the line opens
    exactly one heredoc, its end word is quoted, _is_data_heredoc_line is
    true, the lines above it leave no quote or backslash open (so the line
    really starts a command), and the end word is found on a later line. The
    opening line itself and everything after the end word stay, and are
    checked as usual. A heredoc that fails any condition is left in place
    untouched. Pure; never raises.
    """
    lines = command.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        if len(_ANY_HEREDOC.findall(line)) != 1:
            continue
        match = _QUOTED_HEREDOC.search(line)
        if match is None or not _is_data_heredoc_line(line, match):
            continue
        above = "\n".join(out[:-1])
        if above.endswith("\\"):
            continue
        try:
            _tokens(above)
        except ValueError:
            continue
        strip_tabs = match.group(1) == "-"
        end_word = match.group(2) or match.group(3)
        for j in range(i, len(lines)):
            candidate = lines[j].lstrip("\t") if strip_tabs else lines[j]
            if candidate == end_word:
                i = j + 1
                break
    return "\n".join(out)


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
    command = _drop_data_heredocs(command)
    # Backticks run a command just like $( ), so they split words the same way.
    command = command.replace("`", " ; ")
    try:
        tokens = _tokens(command)
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


def decide(payload: object) -> Tuple[bool, str]:
    """Returns (allowed, reason) for one PreToolUse payload. Pure; takes no phase.

    Non-object payload: blocked. Any tool other than Bash: allowed. Bash with
    no string command: blocked. Otherwise blocked if the command publishes.
    """
    if not isinstance(payload, dict):
        return False, "publish_guard: hook payload is not a JSON object"
    if payload.get("tool_name") != "Bash":
        return True, ""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return False, "publish_guard: Bash call has no command string"
    try:
        _check_command(command, 0)
    except _Blocked as e:
        return False, _BLOCK_MESSAGE % e
    return True, ""


def main(stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr) -> int:
    """Reads one payload from stdin; returns 0 (allow) or 2 (block, reason on stderr).

    Never raises and never returns 1: empty stdin, invalid JSON and any
    exception inside decide() all block, because a crashing hook fails open.
    """
    try:
        try:
            payload = json.loads(stdin.read())
        except ValueError:
            payload = None
        allowed, reason = decide(payload)
    except BaseException as e:  # noqa: BLE001 - fail closed on anything
        allowed, reason = False, "publish_guard: internal error, blocking: %s: %s" % (
            type(e).__name__, e)
    if allowed:
        return 0
    try:
        print(reason, file=stderr)
    except BaseException:  # noqa: BLE001 - the exit code alone still blocks
        pass
    return 2


if __name__ == "__main__":
    sys.exit(main())
