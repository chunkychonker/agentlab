# Running nightloop on GitHub Actions

`nightloop schedule-gha` writes one workflow file that runs `nightloop run`
once a night on a GitHub-hosted runner, inside your config's run window.
Milestone 10; research note:
[`research/2026-11-23-nightloop-actions-scheduler.md`](../../../research/2026-11-23-nightloop-actions-scheduler.md).

**This workflow has never run on GitHub.** The tests check its structure
line by line, a golden file pins its bytes, and `actionlint` 1.7.12 accepts
it, but no test talks to GitHub. Do the three-step check below before you
trust it.

## Generate it

```sh
cd projects/nightloop
python3 -m nightloop schedule-gha --repo /path/to/your/checkout \
  --nightloop-repo OWNER/agentlab \
  --nightloop-sha <40-hex commit of agentlab to install nightloop from> \
  --claude-version 2.1.330 \
  --credential oauth-token \
  --timezone America/Chicago \
  --minute 17 \
  [--hour H] [--config <toml inside the repo>] [--timeout-s 3600] \
  [--job-timeout-minutes 360] [--workflow-name nightloop] [--dry-run]
```

It writes `<repo>/.github/workflows/<name>.yml`. Run it again with the same
inputs and it says "nothing to do" and leaves the file alone. `--dry-run`
prints the YAML on stdout and the manual steps on stderr, and writes nothing.
[`examples/nightloop.workflow.yml`](../examples/nightloop.workflow.yml) is the
output for `examples/plugin.nightloop.toml`. A test keeps it byte for byte
equal to what the generator renders.

Every input is checked before anything is written. If a check fails, the
command exits 2 with one line naming the input:

- the hour must be inside the config's window (default: the window's start).
  The minute must be 1-59, because GitHub names `:00` as its busiest minute.
- the night's worst case must fit the job: cycles x 3 phases x
  (`--timeout-s` + 10 s kill grace), plus 30 minutes of slack for setup and
  shipping. `--job-timeout-minutes` may not exceed GitHub's 6-hour hosted
  limit. At the default 3600 s, one cycle fits (211 min) and two do not
  (391 min).
- the SHA must be a full 40-hex commit and the CLI version an exact `X.Y.Z`:
  an unattended job must not pick up a moved ref.
- the zone must exist in this machine's tz database. On the runner, an
  unknown `TZ` silently reads as UTC, and the window check would then use
  the wrong hour.
- the config must be inside `--repo`, because the runner only sees the repo.
- every text value must match a strict pattern. Values with newlines, `: `,
  `#`, quotes, `$(` and the like are refused, never escaped.

## What the workflow does

- Triggers on `schedule` (`cron: "<minute> <hour> * * *"` with
  `timezone:`) and on `workflow_dispatch`, for manual runs.
- Sets `TZ` to the same zone, so `nightloop run`'s window check reads the
  hour in that zone. A test proves `TZ` moves the hour on this machine's
  libc. That the runner's glibc does the same is expected but not verified.
- `permissions` is exactly `contents: write` (to push the cycle branch) and
  `pull-requests: write` (for `gh pr create`). Concurrency never cancels a
  running night.
- Checks your repo out to `target/` with full history (claim tracking needs
  `merge-base`). It checks nightloop out to `nightloop-src/` *beside* it,
  never inside it, at the pinned SHA. Metrics go to `nightloop-metrics/`,
  also outside `target/`. Anything untracked inside `target/` would make
  every cycle's tree dirty, and recovery would snapshot it.
- Installs Claude Code `@<version>` with npm (`DISABLE_AUTOUPDATER=1`) and
  nightloop with pip, on Python 3.13 and Node 22.
- Exports exactly one Claude credential, from `secrets.*`, and `GH_TOKEN`,
  both on the run step only, so the install steps never see them.
  `ANTHROPIC_API_KEY` outranks `CLAUDE_CODE_OAUTH_TOKEN` in `claude -p`, so
  exporting both would bill the API.
- Runs `nightloop run --repo target --config target/<config> --metrics
  nightloop-metrics/metrics.jsonl --timeout-s <N>`, never with
  `--ignore-window`.
- Uploads `nightloop-metrics/` as an artifact with `if: always()`, because
  the VM and everything on it is gone after the job.
- Every action is pinned to a full commit SHA, with the tag in a comment:
  checkout v7.0.1, setup-python v7.1.0, setup-node v7.1.0,
  upload-artifact v7.0.2. The tags were resolved on 2026-11-23.

## Manual steps nobody can automate

1. Add the repository secret. `CLAUDE_CODE_OAUTH_TOKEN` comes from `claude
   setup-token` and needs a Pro/Max/Team/Enterprise plan. It ties every night
   to that one person's subscription. `ANTHROPIC_API_KEY` bills per token
   instead.
2. Settings > Actions > General > Workflow permissions: enable "Allow GitHub
   Actions to create and approve pull requests". Without it `gh pr create`
   fails, and the shipper reports `NotShipped`. This is a correct, loud
   failure, but it looks like a model failure.
3. Make the config's agents resolve on the runner, for example by committing
   them under `.claude/agents/`. The workflow does not install the nightloop
   plugin, and the phase runner does not pass `--plugin-dir` (an m8 limit).
4. Commit the workflow to the **default branch**. Schedules run only from
   there.
5. If CI must run on nightloop's PRs, use a PAT or GitHub App token. PRs
   opened with `GITHUB_TOKEN` do not trigger other workflows.
6. If nightloop's source repo is private, the second checkout needs a token
   that can read it. The workflow uses the default token, which cannot.

## Expected behaviour that is not a fault

- GitHub can delay or drop a scheduled run under load. A late start is
  refused by `nightloop run` (exit 3, "refusing to start"). The job then
  shows as failed and nothing in the repo changes.
- On a public repo, GitHub disables the schedule after 60 days without repo
  activity.
- Edit the config's window or cycles, and you must regenerate. The file's
  header says what it was checked against.

## The three-step human check

1. Actions > nightloop > **Run workflow** (`workflow_dispatch`), during the
   window. Outside the window, expect exit 3.
2. Read the log: the CLI version line, `nightloop run`'s per-cycle lines,
   and the PR it opened (or the `NotShipped` reason).
3. Download the `nightloop-metrics` artifact and run `nightloop report
   --metrics metrics.jsonl` on it.

## Tests

`cd projects/nightloop && python3 -I -m unittest discover -v`. The m10
modules are `tests/test_gha_*.py`. `NIGHTLOOP_LIVE=1` with `actionlint` on
PATH also lints the golden file.
