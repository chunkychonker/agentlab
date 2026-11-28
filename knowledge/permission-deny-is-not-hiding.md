# Permission deny rules do not hide a file from a phase that has Bash

Verified against the Claude Code permissions docs on 2026-11-28.

- `Read(path)` deny applies to built-in file tools. For Grep and Glob it is
  "best-effort". `Grep(path)`, `Glob(path)` and `Write(path)` rules with a path
  don't match anything; use `Read(...)` and `Edit(...)`.
- `//abs/path` anchors at the filesystem root. Bash deny rules match command text
  only, not `sh -c` or a script.
- Consequence: a phase with Bash (the builder) can `cat` a "denied" file. To hold
  a file out, remove it from the worktree for the phase (stash and restore with an
  explicit step, not a trap; see [[bash-3.2-testable-scripts]]) and say it is
  friction, not a boundary. Real enforcement needs sandboxing.
- Related: [[claude-code-allowed-tools-rules]] for rule syntax, and the
  held-out-test design in `research/2026-11-28-held-out-acceptance-tests.md`.
- A research note is also a leak: the builder reads it, so held-out cases must not
  be pasted into it.
