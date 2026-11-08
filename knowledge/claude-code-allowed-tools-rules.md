# Claude Code allowedTools rule gotchas

Verified 2026-11-08 against the [permissions docs](https://code.claude.com/docs/en/permissions).

- `Bash(git log *)` and `Bash(git log:*)` are equivalent. `:*` is only recognised at the end of the pattern.
- Allow globs in the tool-name position work only after a literal `mcp__<server>__` prefix. `"*"`, `"B*"` and `"mcp__*"` are skipped and approve nothing, without an error.
- Settings files skip `mcp__` rules that have parentheses. Unconfirmed for `--allowedTools`.
- A `*` before the subcommand (`Bash(git * main)`) matches `git -c ...` and triggers a startup warning.
- Bare `Bash` allows every command. `dontAsk` mode auto-denies whatever is not pre-approved, so an unattended phase needs an explicit allowlist.
- Compound commands need every subcommand allowed.

Used by the nightloop config validator (see `research/2026-11-08-nightloop-config-schema.md`). Related: [[claude-code-hooks]], [[claude-code-plugins]].

Also tomllib: `TOMLDecodeError.lineno/colno/pos` exist only on Python 3.14+. `bool` passes `isinstance(x, int)`, so validators must reject it explicitly.
