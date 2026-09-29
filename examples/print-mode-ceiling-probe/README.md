# Print-mode ceiling probe: prove the override reaches `claude`, don't grep for it

`claude -p` stops waiting for background subagents after 600s of idle wait,
prints `Background tasks still running after 600s; terminating.`, and **exits
0**. Retry logic keyed on the exit status never fires, and the half-done run looks
like a success. The fix is one line, `export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`,
and this repo's `.pipeline/run.sh` has had it since 2026-09-20.

The hard part is keeping that line effective. A test that greps for the `export`
line passes on a launcher that exports the variable and then runs the phase under
`env -i` with an allowlist that leaves it out (veriloom/cli #72). The same goes
for a launcher that exports it only inside a subshell. This example checks what
actually happens: it runs the launcher against a stub `claude` that records the
environment it received. It also includes a classifier for the exit-0 kill line
in captured output.

From the research note:
[`research/2026-09-29-print-mode-ceiling-env-propagation.md`](../../research/2026-09-29-print-mode-ceiling-env-propagation.md).
Background: [`knowledge/print-mode-background-ceiling.md`](../../knowledge/print-mode-background-ceiling.md).

## What's here

| File | What it is |
|------|-----------|
| `verdict.py` | The pure core. `check_ceiling(env)` classifies the ceiling a process will apply. `find_truncation(output)` finds the kill line. No I/O, no env reads. |
| `probe.py` | The launcher probe (imperative shell). `probe(launcher, workdir, base_env)` runs a launcher with a stub `claude` first on `PATH` and returns every recorded invocation. It also works as a CLI. |
| `scan_output.py` | CLI over `find_truncation` for captured log files. |
| `fixtures/launchers/` | Nine `/bin/sh` launchers, one per propagation shape (good export, missing, `env -i` bad/good, subshell, empty value, two phases, never calls `claude`, scrubbed `PATH`). |
| `fixtures/outputs/` | `truncated.txt` (verbatim from `logs/run-2026-09-19_020002.log`, kill line followed by success-sounding text) and `clean.txt`. |
| `test_verdict.py`, `test_probe.py` | The two suites. Each runs on its own with `python3 <file>`. |
| `selftest.py` | Runs both suites as one transcript. |

Stdlib only (Python 3.10+). No `requirements.txt`, no key, no network, no real
Claude Code.

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/print-mode-ceiling-probe
python3 selftest.py
```

Expected output:

```
ok  "0" is OFF, unset is DEFAULT_600S, a positive integer is BOUNDED
ok  an empty value is INVALID, never confused with unset
ok  non-canonical values (off, -1, ' 0', 00, 600s) are INVALID
ok  the verbatim 600s kill line is found, with its line number
ok  clean output yields None, not a default finding
ok  a non-default ceiling's seconds are read from the line
ok  export and allowlisted env -i both deliver OFF to claude
ok  missing export and env -i without it both leave the 600s default
ok  the bad env -i launcher passes a grep yet claude never sees =0
ok  a subshell-only export is DEFAULT_600S, an empty value INVALID
ok  each claude call is recorded in order; one bad phase is visible
ok  a launcher that never reaches bare claude raises ProbeError
ok  probe CLI exits 0 only if every call saw OFF; 1 if not; 2 if unobserved
ok  scan CLI: clean 0, truncated 1, unreadable 2 (never 'clean')
ok  a launcher-set finite ceiling arrives as BOUNDED

All 15 self-tests passed.
```

The ninth line matters most. `env_i_allowlist_bad.sh` does contain
`export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`, so a grep passes, and the stub
still sees no variable at all.

## Use it on your own launcher

```bash
python3 probe.py fixtures/launchers/two_phases_one_missing.sh
```

```text
OFF          Off()                        claude -p research phase
DEFAULT_600S DefaultCeiling()             claude -p review phase
```

This exits 1. It exits 0 only if **every** `claude` invocation saw `OFF`, and 2
if the probe observed nothing (see below). To check captured output:

```bash
python3 scan_output.py fixtures/outputs/*.txt
```

```text
CLEAN      fixtures/outputs/clean.txt
TRUNCATED  fixtures/outputs/truncated.txt:2  killed after 600s idle wait
```

This exits 1 if any file carries the kill line, and 2 if a file can't be read.
An unreadable file is never reported as clean.

## The verdicts

| Verdict | Variable in the child's env | Meaning |
|---|---|---|
| `Off()` | exactly `"0"` | waits for background tasks indefinitely |
| `DefaultCeiling()` | absent | the 600s default applies |
| `Bounded(ms)` | canonical positive integer | a finite, non-default ceiling |
| `Invalid(raw)` | anything else: `""`, `" 0"`, `"00"`, `"-1"`, `"off"` | we don't know how Claude Code parses it, so it is not treated as proven |

These are a tagged union, not one `(enum, raw)` pair as the note sketched.
`Bounded` always carries its number and `Invalid` always carries its raw string,
so a `BOUNDED` verdict with no value can't be represented.

## Design choices worth knowing

- **The probe strips the variable from the base env.** Our pipeline already
  exports `=0`. If the probe inherited that value, `missing.sh` would read `OFF`
  whenever the probe ran inside the pipeline. The tests deliberately poison the
  base env with `=0` to catch this. With the strip removed, the suite fails on
  the `missing.sh` assertion.
- **The stub's output path is baked into the stub, not passed through an env
  var.** The note proposed a `PROBE_OUT` variable, but `env -i` launchers, the
  exact thing under test, would scrub it. The stub writes one file per
  invocation, created with `O_EXCL`, to a temp directory that is removed on every
  exit path.
- **"Never reached the stub" is an error, not "unset".** If the launcher runs a
  hard-coded binary path and exits 0, or scrubs `PATH` so `claude` isn't found
  (exit 127), `probe` raises `ProbeError`. It does not report
  `DefaultCeiling`, because in that case it observed nothing.
- **Launcher requirements:** launchers must call bare `claude` so that `PATH`
  lookup finds the stub, and each probe run has a 30s timeout.

## Not covered

- Whether a `settings.json` `env` block is honored for this variable in `-p`
  mode. The note leaves this unverified, and the probe only sees process
  environment.
- Whether newer Claude Code versions emit a distinct exit code or stream-json
  event on a ceiling kill.
- The real `.pipeline/run.sh`. It calls `claude` many times, needs a network
  preflight, and writes to the repo, so it is out of scope here. The natural
  follow-up is to point `probe()` at one real `run.sh` phase invocation, replacing
  the static R15/R16 grep in `test_gates.sh` with a behavioral check.
