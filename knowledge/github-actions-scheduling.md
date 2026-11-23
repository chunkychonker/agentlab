# GitHub Actions as a night scheduler

Distilled 2026-11-23 for nightloop m10 (see `research/2026-11-23-nightloop-actions-scheduler.md`). Sibling of [[launchd-scheduling]].

- `schedule`: UTC unless `timezone:` is set, default branch only, can be delayed or dropped under load (avoid `:00`), public repos auto-disable after 60 days without activity. A late run is normal, so keep the run's own window check ([[nightloop-gate-stats]], m7) and never pass `--ignore-window`.
- Auth: `ANTHROPIC_API_KEY` outranks `CLAUDE_CODE_OAUTH_TOKEN` in `claude -p`. Export only one, or a "subscription" job silently bills the API. `--bare` ignores the OAuth token. See [[claude-p-phase-contract]].
- `GITHUB_TOKEN` pushes and PRs do not trigger other workflows; creating a PR also needs the "Allow GitHub Actions to create and approve pull requests" repo setting (secondary sources).
- Listing any `permissions:` sets the rest to none. Concurrency: never `cancel-in-progress` for a night loop.
- Ephemeral VM: upload metrics as an artifact (`if: always()`); recovery branches must be pushed ([[shipping-a-cycle]]). Shallow checkout breaks `merge-base`-based claim tracking, so use `fetch-depth: 0`.
- Pin the CLI version and the tool's source SHA for an unattended job; native installs auto-update unless `DISABLE_AUTOUPDATER=1`.
- Budget check: `cycles x phases x timeout_s` can exceed the hosted-job cap at default timeouts; refuse at generation time. (Cap value unconfirmed.)
