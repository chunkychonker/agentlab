# nightloop m9: launchd scheduler (macOS)

Date: 2026-11-22. Mode: project:nightloop, milestone 9.

**Milestone choice.** The topmost `not-started` milestone is 7b (claim wiring),
but it is already built and open as PR #104. Researching it again would
duplicate that PR (the same call the 2026-11-21 decisions entry made for m8).
Milestone 8 is done (#105). So this cycle takes milestone 9. It does not touch
claim code or `cli/main.py`'s night path.

## Question

How should nightloop generate and install a launchd job so that it is
idempotent, honours the config's run window, and is testable with no launchd?

## Findings

1. **agentlab already has a working bash reference.** `.pipeline/runner.sh`
   (`render_runner_plist`) and `.pipeline/setup_runner.sh` step 5 render a
   LaunchAgent plist and reconcile it: compare desired text with the installed
   file and with `launchctl print gui/<uid>/<label>`; do nothing if both match;
   refuse while the job is `state = running`; otherwise `bootout`, write,
   `plutil -lint`, `enable`, `bootstrap`. It saves the old plist once. It
   rejects a directory containing `& < > " '` instead of escaping it. The
   Python port can do better with stdlib `plistlib`, which escapes by
   construction (`plistlib.dumps` is stdlib; no new dependency).
2. **StartCalendarInterval keys** ([launchd.info](https://www.launchd.info/),
   a community reference, undated; consistent with `launchd.plist(5)`): `Hour`
   0-23, `Minute` 0-59, `Day` 1-31, `Month` 1-12, `Weekday` 0-7. An omitted
   key is a wildcard, so omitting `Minute` fires every minute of the hour. The
   generated dict must always set both `Hour` and `Minute`.
3. **A missed calendar job runs at the next wake, and missed runs coalesce into
   one** (same source; Apple's older
   [Scheduling Timed Jobs](https://developer.apple.com/library/prerelease/content/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html),
   2016, possibly stale). This is why the run window matters: the slot is a
   request, not a guarantee. `nightloop run` already checks the hour itself
   (`cli/window.py`), so a job fired at 10:00 after a late wake is refused
   there. The scheduler must not weaken this: it only picks the slot, it does
   not replace the check.
4. **Newer evidence that the power state is not guaranteed.** An
   [Apple Developer Forums thread (Feb 2026)](https://developer.apple.com/forums/thread/815034)
   reports a job can run during Dark Wake with the lid open, and not at all
   with the lid closed. Apple DTS says launchd guarantees no particular power
   state for a `StartCalendarInterval` start. launchd cannot wake a Mac; that
   needs `pmset` / `IOPMSchedulePowerEvent` (launchd-dev list, 2012,
   possibly stale; agentlab's own plist comment says the same). Consequence:
   nightloop should document a wake schedule as a human step and never run
   `pmset` itself (needs sudo, out of scope), but `install` can print the
   `pmset repeat wakeorpoweron` line as advice.
5. **Domain commands** (launchd.info): agents use `gui/<uid>`; `bootstrap`
   replaces `load` (10.10+); `bootout` on an unloaded job fails with
   "Bootout failed: 5"; `bootstrap` fails with "Bootstrap failed: 5" if the job
   is already loaded or disabled. So a re-runnable install must read state
   first (`launchctl print gui/<uid>/<label>` exit code) rather than ignore
   errors, which is what `setup_runner.sh` does.
6. **Existing nightloop pieces to reuse.** `Window(start_hour, end_hour)` (end
   exclusive, no midnight wrap) from `model.py`; `Config.window`;
   `window_disposition`. The CLI is argparse in `cli/main.py` with an exit-code
   `IntEnum` (m7 decisions entry (d)).
7. I did not verify live that `launchctl print` exit codes are 0 loaded / non-0
   not loaded on this macOS (Darwin 25.2); `setup_runner.sh` relies on it and it
   has run nightly since August, which is weak but real evidence.

## Build proposal

Layers 1-3 for the builder, per CLAUDE.md's Engineering Protocol.

### 1. Intent

Add `nightloop schedule install|uninstall|status`, which writes and loads one
LaunchAgent plist that runs `nightloop run` daily inside the config's window,
and does nothing when the desired state already holds. Out of scope: GitHub
Actions (m10); `pmset` wake schedules (printed as advice only); `system/`
daemons; Linux cron/systemd; replacing agentlab's own `setup_runner.sh` job;
a window that wraps midnight (m1 cannot represent it); multiple jobs per repo.

### 2. Behavioural spec

Package `projects/nightloop/nightloop/schedule/` (stdlib only).

Inputs: a loaded `Config`, an absolute `repo` path, an absolute path to the
`nightloop` command (or `[python3, -m, nightloop]`), `config_path`, `uid`,
`home`, a `PATH` string, and a `slot_hour`/`slot_minute` (default: the
window's `start_hour`, minute 0, so the slot is always inside the window).

Pure core:
- `LaunchdJob(label, argv, workdir, path_env, hour, minute, out_log, err_log)`
  is a frozen value. Construction raises `ScheduleError` naming the field when:
  the label is not `[A-Za-z0-9._-]+`; any path is relative; argv is empty or
  holds an empty or NUL-containing item; hour not 0-23 or minute not 0-59;
  bool is not accepted for ints.
- `slot_for(window, hour=None, minute=0)`: returns `(hour, minute)`. Raises
  if `hour` is outside `[start_hour, end_hour)`. The default is `start_hour`.
- `render_plist(job) -> bytes` via `plistlib.dumps(..., sort_keys=True)`.
  Keys: `Label`, `ProgramArguments`, `WorkingDirectory`,
  `EnvironmentVariables.PATH`, `StartCalendarInterval {Hour, Minute}` (a dict,
  both keys always present), `RunAtLoad` false, `StandardOutPath`,
  `StandardErrorPath`. Deterministic: same job, same bytes.
- `plan_install(desired: bytes, installed: bytes | None, state: JobState) ->
  tuple[Step, ...]`, where `JobState` is `NOT_LOADED | LOADED_IDLE | RUNNING`.
  Steps are an enum-typed sum: `Nothing`, `WritePlist`, `Bootout`,
  `Bootstrap`, `RefuseRunning`. Table:
  - installed == desired and LOADED_IDLE: `(Nothing,)`
  - installed == desired and NOT_LOADED: `(Bootstrap,)`
  - installed differs or is absent, NOT_LOADED: `(WritePlist, Bootstrap)`
  - installed differs, LOADED_IDLE: `(Bootout, WritePlist, Bootstrap)`
  - any state RUNNING: `(RefuseRunning,)`, nothing else
- `plan_uninstall(installed, state)`: absent and NOT_LOADED gives `(Nothing,)`;
  LOADED_IDLE gives `(Bootout, RemovePlist)`; RUNNING gives `(RefuseRunning,)`.

Edge (`schedule/launchctl.py`, the only module that runs a process or writes
the plist): `Launchctl` port with `state(domain, label) -> JobState`,
`bootstrap`, `bootout`, `lint(path)`. A failing command raises
`ScheduleError` quoting the command and its last output line. The plist is
written to a temp file in the same directory and `os.replace`d, and is linted
(`plutil -lint`) before `bootstrap`. If the write or lint fails after a
`bootout`, the error says the job is UNLOADED and gives the restore command,
as `setup_runner.sh` does. The old plist is copied once to
`<label>.plist.bak` if no backup exists.

CLI: `nightloop schedule install [--dry-run] [--hour H] [--minute M]`,
`uninstall [--dry-run]`, `status`. `--dry-run` prints the plan and the
rendered plist and calls no launchctl mutating command. Exit codes extend the
existing `IntEnum`; usage/config is 2, a refused (RUNNING) install is a new
named code, a `ScheduleError` is 1. `install` prints the
`sudo pmset repeat wakeorpoweron` advice line for the slot minus 10 minutes
and states that it ran nothing.

Invariants:
- I1 idempotence: after a successful `install`, a second `install` plans
  `(Nothing,)` and writes nothing (file bytes and mtime unchanged).
- I2 the rendered slot is inside the window: for every valid `Window` and
  default slot, `window_disposition(slot_hour, window, ignore_window=False)`
  is `Run`.
- I3 render is deterministic and `plistlib.loads(render_plist(job))` round-trips
  to the job's fields.
- I4 a path with `& < > " '` or a space survives a round trip unchanged (no
  rejection, unlike the bash version).
- I5 `RUNNING` never causes a mutation.
- I6 the argv contains `--config <config_path>` and `--repo <repo>`, and never
  `--ignore-window` (the schedule must not defeat the check in finding 3).

Failure modes: not macOS (`sys.platform != "darwin"`): refuse with exit 2 before
anything else; `launchctl`/`plutil` cannot start: `OSError`, propagates;
unreadable installed plist that is not bytes-equal: treated as "differs";
failure between `bootout` and `bootstrap` leaves the job unloaded and says so.

### 3. Interfaces

```python
# nightloop/schedule/job.py   (pure)
@dataclass(frozen=True)
class LaunchdJob: label: str; argv: tuple[str, ...]; workdir: Path
    path_env: str; hour: int; minute: int; out_log: Path; err_log: Path
def slot_for(window: Window, hour: int | None = None, minute: int = 0) -> tuple[int, int]: ...
# nightloop/schedule/plist.py (pure)
def render_plist(job: LaunchdJob) -> bytes: ...
# nightloop/schedule/plan.py  (pure)
class JobState(Enum): NOT_LOADED; LOADED_IDLE; RUNNING
Step = Nothing | WritePlist | Bootout | Bootstrap | RemovePlist | RefuseRunning
def plan_install(desired: bytes, installed: bytes | None, state: JobState) -> tuple[Step, ...]: ...
def plan_uninstall(installed: bytes | None, state: JobState) -> tuple[Step, ...]: ...
# nightloop/schedule/launchctl.py (edge)
class Launchctl(Protocol): state / bootstrap / bootout / lint
class SubprocessLaunchctl: ...
def apply(steps, *, plist_path: Path, desired: bytes, launchctl: Launchctl, domain: str, label: str) -> None: ...
# nightloop/cli/schedule_cmd.py (edge): add_subparser(sub), run_schedule(args, *, launchctl, home, uid, platform)
```

### Acceptance criteria (checkable, all offline)

1. **Plan table**: each of the 5+3 rows above, asserted on `plan_*` directly.
2. **I1**: a fake `Launchctl` that keeps state in a dict and a temp
   `~/Library/LaunchAgents`: install twice; the second run issues no mutating
   call and leaves bytes and mtime unchanged.
3. **I2**: loop over every valid `Window` (start 0-23, end start+1..24); default
   slot is `Run`; an explicit hour outside the window raises.
4. **I3/I4**: round-trip through `plistlib.loads`; label/argv/paths with `& < >
   " '` and spaces survive. `Hour` and `Minute` are both present.
5. **I5/I6**: RUNNING gives `RefuseRunning` and the fake records zero
   mutating calls; argv never holds `--ignore-window`.
6. **Failure ordering**: a fake whose `bootstrap` fails after `bootout` raises
   `ScheduleError` whose message says UNLOADED and contains the restore
   command; a lint failure writes no file at the final path.
7. **Dry run**: a tree digest of the temp home is unchanged and the fake
   records no mutating call.
8. **Purity**: `job.py`, `plist.py`, `plan.py` import no I/O module
   (`plistlib` and `dataclasses`/`enum`/`pathlib.PurePath` are allowed), as in
   `tests/test_*_purity.py`.
9. **Non-mac**: `platform="linux"` exits 2 and builds no `Launchctl`.
10. Opt-in live test (`NIGHTLOOP_LIVE=1`, macOS only): `plutil -lint` accepts
    the rendered bytes for a real job; no `bootstrap`.
11. Mutation checks, each failing a test: omit `Minute`; `Hour` taken from the
    window end; ignore RUNNING; skip the `differs` check; add `--ignore-window`.

## Open questions

- Whether `launchctl print gui/<uid>/<label>` exit code and `state = running`
  text are stable across macOS versions; the bash job relies on both. Make the
  parse one small pure function with fixture text.
- Launching `nightloop` from launchd needs an absolute interpreter or command
  path. For a venv or `pip install -e .` the builder should default to
  `sys.executable -m nightloop`, resolved at install time. This gets baked
  into the plist, so a moved venv breaks it silently (document it; `status`
  should flag a missing argv[0]).
- The wake schedule is advice only; whether a Dark Wake run is acceptable on a
  laptop is the owner's call (finding 4).
- No page told me to run anything; nothing was ignored.
