# launchd scheduling for an unattended night job

Distilled 2026-11-22 for nightloop m9 (`research/2026-11-22-nightloop-launchd-scheduler.md`).

- `StartCalendarInterval`: omitted keys are wildcards, so a dict without
  `Minute` fires every minute of that hour. Always set both `Hour` and `Minute`.
- A calendar job missed during sleep runs at the next wake, and several missed
  runs coalesce into one. The slot is a request, so the job itself must check the
  hour against the run window (see [[nightloop-gate-stats]] for the dry-run/CLI
  side, `cli/window.py` for the check). A schedule must never pass `--ignore-window`.
- Power state is not guaranteed: per an Apple Developer Forums thread (Feb 2026)
  a lid-open Mac can Dark Wake and run the job; lid-closed waits. Apple DTS: no
  guaranteed power state. launchd cannot wake a Mac; `pmset repeat wakeorpoweron`
  does, and needs sudo.
- Modern commands: `launchctl bootstrap gui/<uid> <plist>`, `bootout`, `print`.
  `bootstrap` on a loaded job and `bootout` on an unloaded one both fail with
  status 5, so an idempotent installer reads state first and plans a diff
  rather than ignoring errors.
- Never mutate a RUNNING job's definition; refuse and say why.
- Render with stdlib `plistlib` (escapes for you, deterministic with
  `sort_keys=True`), instead of a heredoc that must reject `& < > " '`.
- Related: [[cycle-orchestration-gates]], [[claude-code-plugins]].
