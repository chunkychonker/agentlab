# nightloop m10: GitHub Actions scheduler

Date: 2026-11-23. Mode: project:nightloop, milestone 10 (m7b skipped: built and open as PR #104; m9 merged as #106).

## Question

What does a workflow file need so that `nightloop run` does one night on a GitHub-hosted runner on a cron, and what can a pure generator check before it writes that file?

## Findings

1. **`schedule` semantics** ([GitHub events docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), fetched 2026-11-23).
   - POSIX 5-field cron; `@daily`-style shorthands are not supported; shortest interval 5 minutes.
   - UTC by default; an optional `timezone:` IANA string exists (workflow-syntax docs, same date).
   - Runs only from the latest commit on the **default branch**, and the file must be on it.
   - The run "can be delayed during high load" (the start of every hour is named as high-load), and "some queued jobs may be dropped". Advice: avoid `:00`.
   - Public repos: the schedule is auto-disabled after 60 days without repo activity. nightloop's own PRs/merges count as activity only if they land.
   - **Consequence:** a late or dropped run is normal. Do not use `--ignore-window`; the run's own window check (m7) refuses a late start. Same rule as m9's `FORBIDDEN_ARG`.

2. **Clock/window mismatch.** nightloop's window hour comes from the machine's local time (m7 decision, `time.localtime`). A runner is UTC, so a config window of 1-6 would be read as 01:00-06:00 UTC. Fix without code change: set `TZ` in the job env to the same IANA zone as the cron's `timezone:`. Whether the CLI's `time.localtime` honours `TZ` on the runner image is standard Linux libc behaviour but **not verified here**; the builder's test can run `main` with `TZ` set in a subprocess to prove it.

3. **Auth in CI** ([authentication docs](https://code.claude.com/docs/en/authentication), 2026-11-23).
   - `claude setup-token` makes a one-year OAuth token for `CLAUDE_CODE_OAUTH_TOKEN`; it needs a Pro/Max/Ultra/Team/Enterprise plan and only makes model requests.
   - **Precedence trap:** `ANTHROPIC_API_KEY` outranks `CLAUDE_CODE_OAUTH_TOKEN`, and in `-p` mode the API key is always used when present. So the generated workflow must export exactly one credential variable, never both, or the "subscription" run bills the API.
   - `--bare` does not read the OAuth token. nightloop's `build_argv` never emits `--bare` (m3), so the token works.
   - The token is per-person: it ties the unattended job to one subscription. Say so in the output notes.

4. **Installing the CLI** ([setup docs](https://code.claude.com/docs/en/setup), 2026-11-23). `npm install -g @anthropic-ai/claude-code@<version>` is documented (Node 22+; the binary is native). The native installer also takes a version: `curl -fsSL https://claude.ai/install.sh | bash -s 2.1.89`. Pin a version either way; native installs auto-update, so set `DISABLE_AUTOUPDATER=1` in the job env. The native path is `~/.local/bin`, which needs adding to `$GITHUB_PATH`. The npm path avoids that on `ubuntu-latest`, which ships Node (version on the image not checked).

5. **Anthropic's own action is not the right fit.** [`anthropics/claude-code-action@v1`](https://code.claude.com/docs/en/github-actions) with a `prompt` runs one `claude` session. nightloop needs its orchestration (gates, recovery, shipper) around three separate `claude -p` phases, so the workflow installs the CLI and runs `nightloop run`. The docs still give three facts that apply:
   - a scheduled run is attributed to a repo user, usually whoever last edited the cron, and the action rejects bot actors unless listed in `allowed_bots`. Irrelevant to us (we do not use the action), but it means "who is the actor" is not fixed;
   - workflow-created commits/PRs made with the default `GITHUB_TOKEN` do not trigger other workflows ("CI not running on Claude's commits"), so CI will not run on nightloop's PR unless a PAT or App token is used;
   - `--max-turns`, workflow timeouts and concurrency controls are the documented cost caps.

6. **Token and repo setting.** Creating a PR with `GITHUB_TOKEN` also needs the repo/org setting "Allow GitHub Actions to create and approve pull requests" (community and third-party sources, not official docs; verify in the repo UI). Without it `gh pr create` fails and nightloop's shipper reports `NotShipped` (correct, loud, but the run looks like a model failure). The generator cannot see the setting, so the README must name it as a manual step.

7. **Permissions** ([workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)): listing any permission sets all unlisted ones to `none`. The job needs `contents: write` (push the cycle branch) and `pull-requests: write` (`gh pr create`). Nothing else. `gh` reads `GH_TOKEN`; git push uses the credential `actions/checkout` persists.

8. **Concurrency** (same page): one run per group; a new queued run cancels the *pending* one by default; `cancel-in-progress: true` also kills the running one. For a night loop, never cancel a running night (it would leave a half-built tree), so the template sets `cancel-in-progress: false`. Group names must be unique across workflows.

9. **Ephemeral runner.** `logs/metrics.jsonl` (the `report` input) and any unshipped recovery state die with the VM. Recovery branches are pushed (m4/m5) so work survives; metrics need `actions/upload-artifact` with `if: always()`. Not verified: the action's current major version; the builder must check the pins (see open questions).

10. **Checkout depth.** m6's `GitClaimSource` uses `merge-base`, `for-each-ref` over `origin/cycle/*`, and recovery reads `origin/main`. A shallow clone (the checkout default, depth 1) would break the first two. Use `fetch-depth: 0`. Git identity must be configured for the shipper's commit (`git config user.name/email`), as the runner has none.

11. **Getting nightloop onto the runner.** It is not on PyPI. The template does a second `actions/checkout` of the agentlab repo at a **pinned 40-hex SHA** into a side directory, then `pip install` of `projects/nightloop` from it. A floating ref would let the tool change under an unattended job. (m1 stays stdlib-only, so `pip install` pulls nothing else.)

12. **Hosted-job time limit.** Commonly documented as 6 hours per job (default `timeout-minutes` 360); I could not confirm it from the fetched pages (truncated). Treat the number as a named constant the builder double-checks. nightloop's worst case is `cycles x 3 phases x timeout_s` (default 3600s), i.e. 3 h per cycle, so a 2-cycle night already exceeds 6 h at defaults. The generator should refuse that combination rather than let the platform kill the job mid-cycle with no recovery.

HN reception was not searched; this is a platform-docs question and the claims above are from primary docs.

## Build proposal

### Layer 1: Intent

Add `nightloop schedule-gha` (name: `nightloop actions`), a generator that renders one `.github/workflows/nightloop.yml` for a repo from its `nightloop.toml`, honouring the run window, with every unattended-job hazard from the findings checked before a byte is written. **Out of scope:** running or calling GitHub, managing secrets, editing repo settings, a marketplace action, Windows/macOS runners, matrix runs, installing from PyPI, a YAML parser dependency.

### Layer 2: Behavioural spec

Inputs: a loaded `Config` (cycles, window, phases), a `WorkflowSpec`, and a target path.

`WorkflowSpec` fields (all explicit, no hidden defaults beyond named constants):
- `nightloop_sha`: 40 lowercase hex; `nightloop_repo`: `owner/name`.
- `claude_version`: `\d+\.\d+\.\d+`.
- `credential`: enum `OAUTH_TOKEN | API_KEY`.
- `timezone`: IANA-looking string (`Area/City` or `UTC`).
- `minute`: 0-59 and not 0; hour: optional, defaults like `slot_for`.
- `timeout_s`: the same value passed to `nightloop run --timeout-s`.
- `job_timeout_minutes`: int.

Output: workflow text (pure) and, at the edge, a file written atomically; plus a `plan`/`--dry-run` that prints the text and writes nothing.

Invariants (each becomes a test):
- I1 **Slot in window.** The cron's hour comes from `slot_for(cfg.window, hour, minute)` (reuse m9's pure function); an hour outside the window raises. The cron is `"<minute> <hour> * * *"` and `timezone:` is set; `TZ` env equals `timezone`.
- I2 **Never `--ignore-window`.** The rendered text does not contain it anywhere (by construction: the run command is built from a constant argv list).
- I3 **One credential.** Exactly one of `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` appears, from `secrets.*`; never a literal value. (Finding 3.)
- I4 **No cascade-kill.** `concurrency` is present, with `cancel-in-progress: false`; `permissions` lists exactly `contents: write` and `pull-requests: write`. (Findings 7, 8.)
- I5 **Time budget.** If `cfg.cycles * len(phases) * timeout_s` seconds (rounded up to minutes) plus a named slack exceeds `job_timeout_minutes`, or `job_timeout_minutes` exceeds `MAX_JOB_MINUTES`, generation raises `SpecError` naming both numbers. (Finding 12.)
- I6 **Pinned and deep.** `fetch-depth: 0`; second checkout `ref:` is the 40-hex SHA; the CLI install line carries the exact version; `DISABLE_AUTOUPDATER=1` is set. (Findings 4, 10, 11.)
- I7 **Metrics survive.** An upload-artifact step runs with `if: always()`. (Finding 9.)
- I8 **Deterministic and quote-safe.** Same input gives identical bytes; any user string (repo name, timezone) is validated against a strict pattern before it is placed, so no value can inject YAML or shell. Strings that do not match are `SpecError`, never escaped-and-hoped.
- I9 **Idempotent write.** Writing twice leaves the file's bytes and mtime unchanged the second time; a differing existing file is replaced via temp file + `os.replace`, with the temp removed on failure.
- I10 **No writes on dry run.** Tree digest unchanged.

Failure modes: `SpecError(field, reason)` for every bad input; `OSError` from the write propagates. No default is substituted for a bad value.

### Layer 3: Interfaces (no bodies)

Under `projects/nightloop/nightloop/schedule_gha/`:
- `spec.py`: `class Credential(Enum)`, `@dataclass(frozen=True) class WorkflowSpec`, `class SpecError(ValueError)`, `validate_spec(cfg, spec) -> ValidSpec` (the only way to get a `ValidSpec`; `render` accepts only that type).
- `budget.py` (pure): `worst_case_minutes(cycles:int, phases:int, timeout_s:int) -> int`, constants `JOB_SLACK_MINUTES`, `MAX_JOB_MINUTES`.
- `render.py` (pure): `render_workflow(v: ValidSpec) -> str`; builds from a list of lines and the shared argv constant, no templating library.
- `write.py` (edge, the only file writer): `install_workflow(path: Path, text: str) -> Written | Unchanged`.
- `cli/gha_args.py`, `cli/gha_cmd.py`: argparse and the edge for `schedule-gha --dry-run`; `main` imports them only for this subcommand (m9's lazy-import rule).
- Default output path `<repo>/.github/workflows/nightloop.yml`; `--workflow-name`-style file name is a flag validated by pattern.

Reuse, do not copy: `nightloop.schedule.job.slot_for`. But m9's `schedule` package imports `plistlib` lazily; check `slot_for` can be imported without pulling `plistlib` (the 3.12 Homebrew interpreter cannot import it). If it cannot, move `slot_for` to a neutral module and re-export from `job.py` (expand/contract, one commit adding the new home).

### Acceptance criteria ("it works")

1. Golden file: rendering the plugin example config (`examples/plugin.nightloop.toml`) with a fixed spec equals a checked-in `examples/nightloop.workflow.yml` byte for byte; a second run is identical.
2. I1: for every hour in a window, cron hour == hour and an hour outside the window raises; `timezone`/`TZ` agree.
3. I2/I3/I4/I6/I7 as string-structure assertions on the rendered lines (indentation-aware line checks; no YAML parser).
4. I5: table of (cycles, phases, timeout_s, job minutes) with the exact boundary, one accepted and one rejected with a message naming both numbers.
5. I8: spec fields containing newline, `:`-space, `#`, `$(`, quotes, or leading `-` are rejected; property-style loop over a short hostile list.
6. I9/I10 on a temp tree (digest, mtime).
7. Purity: `render.py`, `budget.py`, `spec.py` import only `dataclasses`, `enum`, `re`, `typing` and `nightloop` pure modules (allowlist test like m9's); only `write.py` writes.
8. Mutation checks, each must fail a test: drop the `if: always()`; set `cancel-in-progress: true`; add `--ignore-window`; render both credentials; take the cron hour from `window.end_hour`; skip the budget check; floating `ref: main`.
9. Opt-in (`NIGHTLOOP_LIVE=1`, skipped by default): run `actionlint` on the golden file if it is on PATH and report; otherwise skip with a reason. Not required for the offline suite. No test calls GitHub or a model.
10. Existing 566+ tests still pass.

### README must state

Manual steps nobody can automate: add the secret; enable "Allow GitHub Actions to create and approve pull requests"; use a PAT/App token if CI must run on the PR; the OAuth token is one person's subscription; schedule disables after 60 idle days on public repos; a late/dropped cron is expected and not an error. And plainly: **the generated workflow has never run on GitHub** (same honesty as m5/m9), with the 3-step human check (`workflow_dispatch` run, read the log, check the artifact). Add `workflow_dispatch:` to the template so that check is possible.

## Open questions

- Current major versions of `actions/checkout`, `actions/setup-python` and `actions/upload-artifact`: not fetched. The builder must look them up and pin them (a SHA is better than a tag for an unattended job) rather than copy from memory. The Claude docs page shows `actions/checkout@v6`.
- Whether `ubuntu-latest` has a Python >= 3.11 and Node >= 22 by default; the template should use `setup-python` with an explicit 3.11+ version and `setup-node`, or the builder should confirm.
- Whether `TZ` makes `time.localtime` report the zone on the runner (expected, unverified).
- The exact hosted-job max time and the default `timeout-minutes` (I recall 360; the fetched page was truncated before that section).
- Whether a persisted `actions/checkout` credential lets `git push` of a new branch work with the permissions above (believed yes; unverified), and whether `gh` needs `GH_TOKEN: ${{ github.token }}` set explicitly (yes per `gh` docs I recall; not re-fetched).
- nightloop does not pass `--plugin-dir`, so the plugin's agents are only used if the plugin is installed on the runner. The template needs a step for that, or documentation. m8 left this as a human check; unresolved here.
- Pages fetched contained no instructions to run commands.
