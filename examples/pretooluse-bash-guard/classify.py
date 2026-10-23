"""Pure classifier: decides whether one Bash command string hits the deny-list.

No I/O, no environment reads, no clock. `classify` is the only export that
callers need; `Allow`, `Deny`, `Verdict`, and `Rule` are the result types.

This is a guardrail against *mistakes*, not a security boundary. It tokenizes
the command with `shlex`, splits it into simple commands, strips a few
transparent prefixes (`sudo`, `env`, `VAR=x`, ...), recurses into `bash -c` /
`eval` strings, and checks each simple command against two named rules. Any
command it cannot tokenize is denied (fail closed). See README.md for what it
does not catch.
"""

from __future__ import annotations

import enum
import os
import re
import shlex
from dataclasses import dataclass


class Rule(enum.Enum):
    """Why a command was denied. Each value has one explanation in _EXPLANATIONS."""

    RECURSIVE_RM_ROOT_OR_HOME = "recursive-rm-root-or-home"
    FORCE_PUSH_PROTECTED_BRANCH = "force-push-protected-branch"
    UNPARSEABLE_COMMAND = "unparseable-command"
    NESTING_TOO_DEEP = "nesting-too-deep"


# (what was blocked, safer alternative) per rule. Defined once.
_EXPLANATIONS: dict[Rule, tuple[str, str]] = {
    Rule.RECURSIVE_RM_ROOT_OR_HOME: (
        "recursive rm of the filesystem root or the home directory",
        "Delete a specific subdirectory by path instead (e.g. `rm -rf ./build`).",
    ),
    Rule.FORCE_PUSH_PROTECTED_BRANCH: (
        "force push to a protected branch (main/master)",
        "Push to a feature branch and open a pull request, or ask the user to do it.",
    ),
    Rule.UNPARSEABLE_COMMAND: (
        "command could not be tokenized (unbalanced quotes?), so it cannot be checked",
        "Fix the quoting so the command parses as shell words.",
    ),
    Rule.NESTING_TOO_DEEP: (
        "too many nested `bash -c` / `eval` layers to check",
        "Run the inner command directly instead of wrapping it in shells.",
    ),
}

# Every Rule must have an explanation; checked once at import, not per call.
if set(_EXPLANATIONS) != set(Rule):
    raise RuntimeError("every Rule needs an entry in _EXPLANATIONS")


@dataclass(frozen=True)
class Allow:
    """The command hit no rule. The hook says nothing (no decision)."""


@dataclass(frozen=True)
class Deny:
    """The command hit `rule`; `segment` is the simple command that triggered it."""

    rule: Rule
    segment: str

    @property
    def reason(self) -> str:
        """Human/model-readable reason: the rule, the offending segment, an alternative."""
        what, alternative = _EXPLANATIONS[self.rule]
        return (
            f"Blocked by agentlab bash-guard ({self.rule.value}): {what}. "
            f"Offending command: `{self.segment}`. {alternative}"
        )


Verdict = Allow | Deny

_ALLOW = Allow()

# How many `bash -c` / `eval` layers we will look inside before giving up (deny).
MAX_NESTING = 3

# Shell operators that end one simple command. `\n` and backtick are included so
# multi-line scripts and `...` substitutions are split too; `(`/`)` split `$(...)`.
_PUNCTUATION_CHARS = "();<>|&\n`"
_WHITESPACE = " \t\r"

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Commands that run the rest of the line as another command, mapped to the
# flags of theirs that consume the following word.
_PREFIX_COMMANDS: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-C", "-h", "-p", "-U", "-r", "-t", "-D", "-R", "-T"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n"}),
    "nohup": frozenset(),
    "time": frozenset(),
    "command": frozenset(),
    "builtin": frozenset(),
    "exec": frozenset({"-a"}),
    # Shell grammar words that can precede a command after a split.
    "{": frozenset(),
    "!": frozenset(),
    "if": frozenset(),
    "then": frozenset(),
    "else": frozenset(),
    "elif": frozenset(),
    "while": frozenset(),
    "until": frozenset(),
    "do": frozenset(),
}

_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})

# Targets that, after normalization, mean "root" or "home" to a recursive rm.
_DANGEROUS_RM_TARGETS = frozenset(
    {"/", "/*", "~", "~/*", "$HOME", "$HOME/*", "${HOME}", "${HOME}/*"}
)
_RM_RECURSIVE_SHORT = frozenset({"r", "R"})
_RM_RECURSIVE_LONG = "recursive"  # GNU accepts unambiguous prefixes: --rec, --recur, ...

_PROTECTED_BRANCHES = frozenset({"main", "master"})
_GIT_GLOBAL_OPTS_WITH_ARG = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_PUSH_FORCE_LONG = frozenset({"force", "force-with-lease"})
_HEADS_PREFIX = "refs/heads/"


def classify(command: str) -> Verdict:
    """Return Deny if any simple command inside `command` hits a rule, else Allow.

    Never raises for any `str` input: tokenization failures become
    Deny(UNPARSEABLE_COMMAND) and excessive nesting becomes
    Deny(NESTING_TOO_DEEP). The first rule hit (left to right) wins.
    """
    return _classify(command, depth=0)


def _classify(command: str, depth: int) -> Verdict:
    if depth > MAX_NESTING:
        return Deny(Rule.NESTING_TOO_DEEP, command)
    try:
        tokens = _tokenize(command)
    except ValueError:
        return Deny(Rule.UNPARSEABLE_COMMAND, command)
    for words in _split_simple_commands(tokens):
        verdict = _classify_simple(words, depth)
        if isinstance(verdict, Deny):
            return verdict
    return _ALLOW


def _tokenize(command: str) -> list[str]:
    """Split into shell words and operator tokens. Raises ValueError on bad quoting."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION_CHARS)
    lexer.whitespace = _WHITESPACE
    lexer.whitespace_split = True
    lexer.commenters = ""  # treat `#` literally: may over-deny, never under-deny
    return list(lexer)


def _is_operator(token: str) -> bool:
    return token != "" and all(ch in _PUNCTUATION_CHARS for ch in token)


def _split_simple_commands(tokens: list[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if _is_operator(token):
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def _strip_prefixes(words: list[str]) -> list[str]:
    """Drop leading `VAR=x` assignments and transparent prefix commands with their flags."""
    words = list(words)
    changed = True
    while changed and words:
        changed = False
        while words and _ASSIGNMENT.match(words[0]):
            words.pop(0)
            changed = True
        if words and os.path.basename(words[0]) in _PREFIX_COMMANDS:
            takes_arg = _PREFIX_COMMANDS[os.path.basename(words.pop(0))]
            changed = True
            while words and words[0].startswith("-") and words[0] != "-":
                flag = words.pop(0)
                if flag == "--":
                    break
                if flag in takes_arg and words:
                    words.pop(0)
    return words


def _classify_simple(words: list[str], depth: int) -> Verdict:
    words = _strip_prefixes(words)
    if not words:
        return _ALLOW
    name = os.path.basename(words[0])
    args = words[1:]
    segment = " ".join(words)

    if name in _SHELLS:
        script = _shell_c_script(args)
        if script is not None:
            return _classify(script, depth + 1)
    if name == "eval":
        return _classify(" ".join(args), depth + 1)
    if name == "rm" and _is_recursive_rm_of_root_or_home(args):
        return Deny(Rule.RECURSIVE_RM_ROOT_OR_HOME, segment)
    if name == "git" and _is_force_push_to_protected(args):
        return Deny(Rule.FORCE_PUSH_PROTECTED_BRANCH, segment)
    return _ALLOW


def _shell_c_script(args: list[str]) -> str | None:
    """Return the script string of `sh -c SCRIPT` (also `-lc`, `-ec`, ...), else None."""
    for i, arg in enumerate(args):
        if arg.startswith("--"):
            continue
        if not arg.startswith("-"):
            return None  # a script file or operand: not a -c invocation
        if "c" in arg[1:]:
            return args[i + 1] if i + 1 < len(args) else None
    return None


def _normalize_path(target: str) -> str:
    target = re.sub(r"/{2,}", "/", target)
    while True:
        if target.endswith("/."):
            target = target[:-2] or "/"
        elif target.endswith("/") and len(target) > 1:
            target = target[:-1]
        else:
            return target


def _is_recursive_rm_of_root_or_home(args: list[str]) -> bool:
    recursive = False
    targets: list[str] = []
    options_done = False
    for arg in args:
        if options_done:
            targets.append(arg)
        elif arg == "--":
            options_done = True
        elif arg.startswith("--"):
            if arg[2:] and _RM_RECURSIVE_LONG.startswith(arg[2:]):
                recursive = True
        elif arg.startswith("-") and len(arg) > 1:
            if _RM_RECURSIVE_SHORT & set(arg[1:]):
                recursive = True
        else:
            targets.append(arg)
    return recursive and any(_normalize_path(t) in _DANGEROUS_RM_TARGETS for t in targets)


def _git_push_args(args: list[str]) -> list[str] | None:
    """Return the arguments after `push` if the git subcommand is push, else None."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _GIT_GLOBAL_OPTS_WITH_ARG:
            i += 2
        elif arg.startswith("-"):
            i += 1
        else:
            return args[i + 1 :] if arg == "push" else None
    return None


def _refspec_destination(refspec: str) -> str:
    dst = refspec.lstrip("+").rsplit(":", 1)[-1]
    return dst[len(_HEADS_PREFIX) :] if dst.startswith(_HEADS_PREFIX) else dst


def _is_force_push_to_protected(args: list[str]) -> bool:
    push_args = _git_push_args(args)
    if push_args is None:
        return False
    force = False
    positionals: list[str] = []
    for arg in push_args:
        if arg.startswith("--") and len(arg) > 2:
            if arg[2:].split("=", 1)[0] in _PUSH_FORCE_LONG:
                force = True
        elif arg.startswith("-") and len(arg) > 1:
            if "f" in arg[1:]:
                force = True
        else:
            positionals.append(arg)
    # The remote name is checked too; a remote literally named `main` over-denies.
    return any(
        _refspec_destination(p) in _PROTECTED_BRANCHES and (force or p.startswith("+"))
        for p in positionals
    )
