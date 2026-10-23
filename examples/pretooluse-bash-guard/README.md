# PreToolUse hook that guards Bash

A Claude Code `PreToolUse` hook that vetoes a short, named deny-list of Bash
commands before they run, and tells the model why. It catches a recursive `rm`
of `/` or `~`, and a force push to `main` or `master`. Everything else passes
through with **no decision**, so the normal permission flow still applies.
Python 3 standard library only. No dependencies.

The skills and MCP examples in this lab cover what the model *can* do. This one
shows the deterministic layer over what it actually *does*.

From the research note:
[`research/2026-10-23-pretooluse-bash-guard-hook.md`](../../research/2026-10-23-pretooluse-bash-guard-hook.md).

> **This is a guardrail against mistakes, not a security boundary.** A model
> (or a person) trying to get around it can, see [Known bypasses](#known-bypasses).
> For hard enforcement, use permission `deny` rules. Hooks are the programmable
> layer on top of them.

## Design (layers 1-3)

- **Intent:** a deterministic veto over a small set of destructive Bash commands,
  with the reason returned to Claude. Out of scope: Edit/Write guards, rewriting
  input (`updatedInput`), `ask`/`defer` decisions, http hooks, and any claim to
  be a security boundary.
- **Spec:**
  - Bash call that hits a rule: exit 0, and stdout is exactly one JSON object
    `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "<rule id, offending command, safer alternative>"}}`.
  - Bash call that hits no rule, or any other tool: exit 0 with empty stdout.
    This means "no decision", not approval. The hook **never** emits `allow`,
    because `allow` could skip a permission prompt the user would otherwise see.
  - Fails closed. Per the docs, exit 1, a crash, or a timeout does *not* block
    a PreToolUse call. So every failure the script can detect becomes an
    explicit deny: malformed JSON, a payload that isn't an object, a missing
    `tool_name`, a Bash call without a string `tool_input.command`, stdin that
    isn't UTF-8, quoting that won't tokenize, too many nested `bash -c` layers,
    or an exception inside the classifier. If writing stdout fails, the script
    exits 2, which blocks and shows stderr to Claude.
- **Interfaces:**
  - `classify.classify(command: str) -> Verdict`, where `Verdict = Allow | Deny`.
    `Deny(rule: Rule, segment: str)` has a `.reason` property. `Rule` is an enum.
    It is pure and never raises on any `str`.
  - `hook.decide(payload_text: str, classifier=classify) -> HookResult(exit_code, stdout, stderr)`.
    It is pure and never raises.
  - `hook.main() -> int` is the only I/O: stdin in, stdout/stderr out.

| File | What it is |
|------|-----------|
| `classify.py` | Pure deny-list classifier for one command string. No I/O. |
| `hook.py` | Maps the hook payload to exit code and output (`decide`), plus the stdin/stdout shell (`main`). |
| `fixtures/` | Recorded payloads in the documented shape (two denies, one allow, one non-Bash tool, one malformed). |
| `test_guard.py` | Offline self-test of the acceptance criteria, plus a subprocess smoke test per fixture. |
| `settings.example.json` | How to register the hook in `.claude/settings.json`. |

## The deny-list

| Rule id | Fires when | Examples denied |
|---------|-----------|-----------------|
| `recursive-rm-root-or-home` | `rm` with a recursive flag (`-r`, `-R`, clusters such as `-rf`/`-fr`, split `-r -f`, `--recursive` or an unambiguous prefix like `--rec`) and a target that normalizes to `/`, `/*`, `~`, `~/*`, `$HOME`, `$HOME/*`, `${HOME}`, or `${HOME}/*`. Repeated slashes and trailing `/` or `/.` are normalized first. | `rm -rf /`, `rm -fr ~`, `rm -r -f $HOME`, `rm --recursive --force /*`, `rm -Rf //`, `rm -rf -- /` |
| `force-push-protected-branch` | `git push` with `--force`, `--force-with-lease[=...]`, or a short cluster containing `f`, **or** a `+refspec`, where a refspec destination is `main` or `master` (`refs/heads/` prefix and `src:` part handled). | `git push --force origin main`, `git push -f origin master`, `git push origin +main`, `git push origin HEAD:refs/heads/main --force` |
| `unparseable-command` | `shlex` cannot tokenize the command (e.g. unbalanced quotes). | `echo 'unterminated` |
| `nesting-too-deep` | More than `MAX_NESTING` (3) layers of `bash -c` / `eval`. | six nested `bash -c` |

Before the rules run, the command is split into simple commands on `;`, `&&`,
`||`, `|`, `&`, newlines, `(`/`)` (so `$(...)` is split out too), backticks, and
redirections. Leading `VAR=x` assignments are stripped, and so are the prefix
commands `sudo`, `doas`, `env`, `nice`, `nohup`, `time`, `command`, `builtin`,
and `exec` (with their argument-taking flags, e.g. `sudo -u root`). Shell
keywords such as `then`, `do`, `{`, and `!` are stripped the same way. Command
paths go through basename, so `/bin/rm` counts as `rm`. `sh|bash|zsh|dash|ksh -c
SCRIPT` (also `-lc`, `-ec`, ...) and `eval ARGS` are classified recursively.

Pinned choices (the tests lock these in):
- `echo "rm -rf /"` is **allowed**. A quoted string handed to another command is
  data.
- `git push origin main` (no force) is **allowed**. It's an ordinary
  fast-forward.
- `rm ~` (not recursive) is **allowed**. `rm` refuses directories without `-r`.

## Run the self-test (no API key, no Claude Code needed)

```bash
cd examples/pretooluse-bash-guard
python3 -m unittest -v test_guard
```

Expected: `Ran 16 tests ... OK`. Each deny-list and allow-list case runs as a
`subTest`. Requires Python 3.10+ (it uses `X | Y` type unions at runtime).

To drive the hook by hand the way Claude Code does:

```bash
python3 hook.py < fixtures/deny_force_push_main.json; echo "exit=$?"
# {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", ...}}
# exit=0
python3 hook.py < fixtures/allow_rm_node_modules.json; echo "exit=$?"
# (no output)
# exit=0
```

## Install it in Claude Code

Merge `settings.example.json` into `.claude/settings.json` (project, shared),
`.claude/settings.local.json` (project, personal), or `~/.claude/settings.json`
(all projects):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3",
            "args": ["${CLAUDE_PROJECT_DIR}/examples/pretooluse-bash-guard/hook.py"],
            "onFailure": "block"
          }
        ]
      }
    ]
  }
}
```

- `"matcher": "Bash"` is an exact match, because it contains only letters.
- The exec form (`command` + `args`) and the `${CLAUDE_PROJECT_DIR}` placeholder
  are copied from the docs' own `onFailure` example.
- `"onFailure": "block"` makes Claude Code block the call if the script can't
  start (wrong path, missing `python3`, import error) or times out. Without it,
  those failures **fail open**. The script's own fail-closed handling only
  covers errors that happen after it is running. The docs say this field needs
  Claude Code v2.1.295 or later. On older versions, drop it and accept that the
  hook fails open.

## Known bypasses

These are not covered. Every one of them deletes or force-pushes without
tripping the guard:

1. **Other interpreters:** `python -c "import shutil; shutil.rmtree('/')"`,
   `node -e ...`, `perl -e ...`.
2. **Variable indirection:** `D=/; rm -rf $D`, `rm -rf "$(echo /)"`,
   `cmd=rm; $cmd -rf ~`. The classifier never evaluates variables.
3. **Other deleters:** `find / -delete`, `find ~ -exec rm -rf {} +`,
   `xargs rm -rf <<< /`, `rsync --delete`, `shred`, `dd`, `mkfs`.
4. **Absolute home paths:** `rm -rf /Users/alice` and `rm -rf /home/alice`.
   The classifier is pure and doesn't know the user's home path, so it only
   matches `~` and `$HOME`. The same goes for system directories like
   `rm -rf /usr` and for `..` tricks (`rm -rf /tmp/..`).
5. **Git without an explicit protected branch:** a bare `git push -f` (or
   `git push -f origin`) while on `main`. Telling which branch is current needs
   repo state, and the classifier is stateless. Also not covered: `git push
   --mirror`, deleting main (`git push origin :main` / `--delete main`), and
   pushes that run through aliases or a configured `push.default` with `+`
   refspecs.
6. **Scripts:** `./cleanup.sh` whose contents do any of the above.

Known over-denials (fail-closed direction): POSIX `shlex` drops quoting, so
`rm -rf '~'` (a literal directory named `~`) is denied, `echo ";" rm -rf /` is
split at the quoted `;`, and `#` is not treated as a comment. A remote that is
literally named `main` can also trip the push rule.

## Verified vs. unverified

Verified against the Claude Code hooks reference (fetched 2026-10-23) and by the
offline tests here: the payload fields, the deny JSON shape, "exit 0 with empty
stdout = no decision", exit 1 and crashes being non-blocking, the
`onFailure: "block"` config shape, and the matcher semantics.

Unverified: no live Claude Code session was run in this build. Specifically,
these are untested:
- whether a hook deny still holds under `--dangerously-skip-permissions` /
  `bypassPermissions` (secondary blogs say it does; the official sections the
  researcher read don't say);
- `onFailure: "block"` on a real install;
- the exact way the reason is rendered to Claude in the transcript.

A cheap, safe live check: run it in a throwaway repo with no remote and ask
Claude to run `git push --force origin main`. If the hook doesn't fire, the
push just fails for lack of a remote. Never live-test the `rm` rule with a
real target.
