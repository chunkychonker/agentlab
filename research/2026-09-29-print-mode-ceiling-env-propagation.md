# The 600s print-mode ceiling: the fix is one line, keeping it effective is the hard part

Date: 2026-09-29. Backlog item: `fix (health 2026-09-02): "Background tasks still running after 600s; terminating..."` (topmost unclaimed `[ ]`; no open PRs at research time).

## Question

`.pipeline/run.sh` already exports `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` (commit 832134b, 2026-09-20). What remains to build so this class of silent truncation cannot come back unnoticed, in a form that generalizes beyond this repo?

## Findings

- **State of the pipeline item is stale, not open.** The last run logs containing the kill message are `logs/run-2026-09-18_*` and `run-2026-09-19_020002.log` (both researchers died on it). The fix landed 2026-09-20; runs 09-21, 09-28, 09-29 contain no occurrence (09-28 shipped 1/1, PR #50). The only remaining mention is the health finding being re-filed from older logs. `test_gates.sh` R15/R16 already assert statically that the `export` line exists and precedes `run_phase`. What no test asserts: that the value reaches the actual `claude` process.
- **Official semantics** ([Claude Code env-vars docs](https://code.claude.com/docs/en/env-vars), fetched 2026-09-29): "Ceiling in milliseconds on *idle* waiting for background subagents and workflows after the final turn" with `-p`. Idle wait restarts each time Claude takes a turn to handle a background result. Default `600000`. At the ceiling Claude Code "stops waiting for the remaining background tasks and exits". `0` waits indefinitely. Separate from the 5-second grace for plain background shells. Requires Claude Code v2.1.182+ (local: 2.1.284). Related knobs: `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` (removes `run_in_background` entirely), `CLAUDE_AUTO_BACKGROUND_TASKS`.
- **The failure is silent by design: exit 0.** [laconic PR #235](https://github.com/JordanMPDS/laconic/pull/235) (2026-09-05): a truncated round "reported success", so the supervisor's retry logic (keyed on non-zero) never fired and the next iteration started on a half-done tree. Their fix added a selftest that the variable is exported, because "the variable is invisible in the log when it goes missing".
- **Same fix, second failure mode: launchers that scrub the environment.** [veriloom/cli #72](https://github.com/veriloom/cli/issues/72) (2026-09-11): the review launcher used `env -i` with an allowlist of HOME/PATH/TERM/LANG/CLAUDE_PID, so exporting the variable upstream had no effect on the reviewer. The documented workaround "cannot take effect". Fix: add the variable to the allowlist. A grep for the `export` line (what our R16 does) would pass in that repo and still be wrong.
- **Detection side** ([special-circumstances #1062](https://github.com/ctoforaday/special-circumstances/issues/1062), 2026-09-19): every launcher sharing the ceiling truncates silently; their fix sets 0 and adds a completion check on the artifact (opened spans == closed spans, outcome row present). Consistent with our `postcondition.sh` approach: judge by artifact, not exit code.
- Practitioner pattern across [drafto #637](https://github.com/JakubAnderwald/drafto/pull/637), [rjskene/pipeline #1306](https://github.com/rjskene/pipeline/issues/1306), grace-board #4: all converge on `export ...=0` plus, in some, an outer wall-clock `timeout` so "wait indefinitely" cannot hang forever. (Our run.sh explicitly chose no cap; a run-window gate covers it.)
- Not confirmed: whether `settings.json` `env` block is honored for this variable (docs say env vars can be set there generally; no source specific to this one). Treat as unverified.

## Build proposal

**Increment: `examples/print-mode-ceiling-probe/`** — an offline, key-free harness that proves the ceiling override actually reaches the `claude -p` process through a launcher, and detects the exit-0 truncation signature in captured output.

**Intent.** Turn "the export line exists" (grep) into "the child process sees `=0`" (behavioral), and give a reusable classifier for the silent-truncation log signature. Out of scope: calling the real `claude`/API, editing `.pipeline/run.sh` or its tests, timeouts/wall-clock caps, fixing the health-finding backlog lines.

**Files (Python 3 stdlib only, no deps).**
- `probe.py` — edge shell: `probe(launcher_cmd: list[str], workdir) -> ProbeResult`. Creates a temp dir holding an executable stub named `claude` that writes `{"argv": [...], "env": {...}}` as JSON to a path given by `PROBE_OUT` and exits 0; prepends that dir to `PATH`, runs the launcher, reads the JSON.
- `verdict.py` — pure core, no I/O: `check_ceiling(env: Mapping[str,str]) -> CeilingVerdict` (enum `OFF` = "0", `DEFAULT_600S` = unset, `BOUNDED` = positive integer, `INVALID` = anything else e.g. `""`, `"off"`, `"-1"`); `find_truncation(output: str) -> TruncationFinding | None` matching the exact line `Background tasks still running after 600s; terminating` (the number varies with a non-default ceiling; regex `after (\d+)s; terminating`), returning the seconds.
- `fixtures/launchers/` — small shell launchers: `good_export.sh` (exports then calls `claude -p ...`), `missing.sh` (never sets it), `env_i_allowlist_bad.sh` (`env -i HOME PATH ... claude -p`, the veriloom #72 shape), `env_i_allowlist_good.sh` (same plus `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`), `set_after_subshell.sh` (sets it in a subshell only, so it does not propagate), `empty_value.sh` (`=""`).
- `fixtures/outputs/` — `truncated.txt` (verbatim message from the real logs, plus text implying success), `clean.txt`.
- `test_verdict.py`, `test_probe.py` (stdlib `unittest`), `README.md` (status, how to run, link to this note; note the stub must be first on PATH and launchers must call bare `claude`).

**Interfaces.**
```python
class Ceiling(Enum): OFF; DEFAULT_600S; BOUNDED; INVALID
@dataclass(frozen=True) class CeilingVerdict: ceiling: Ceiling; raw: str | None
@dataclass(frozen=True) class TruncationFinding: waited_seconds: int
def check_ceiling(env: Mapping[str, str]) -> CeilingVerdict
def find_truncation(output: str) -> TruncationFinding | None
def probe(launcher: Sequence[str], workdir: Path) -> Mapping[str, str]   # env seen by the stub; raises ProbeError if stub never invoked or launcher exits non-zero
```

**Invariants / failure modes.** `check_ceiling` is total and pure; unset is distinguishable from `""`. `probe` fails loudly (`ProbeError`) if the stub was never called (launcher used an absolute path or scrubbed PATH), rather than reporting "unset". `find_truncation` returns `None` on clean output, never a default finding. Stub output is written to a file, not stdout, so launchers that redirect stdout cannot hide it. Idempotent; temp dir cleaned on exit.

**Acceptance criteria (self-test, `python3 -m unittest` passes offline, no key).**
1. `good_export.sh` and `env_i_allowlist_good.sh` -> `OFF`.
2. `missing.sh` and `env_i_allowlist_bad.sh` -> `DEFAULT_600S` (the second is the case a grep-based test wrongly passes; the test also asserts the bad launcher's text does contain the string `export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`, demonstrating grep would be fooled). 
3. `set_after_subshell.sh` -> `DEFAULT_600S`; `empty_value.sh` -> `INVALID`.
4. `check_ceiling({"...": "1200000"})` -> `BOUNDED`.
5. `find_truncation(truncated.txt).waited_seconds == 600`; `find_truncation(clean.txt) is None`.
6. A launcher that never invokes bare `claude` -> `ProbeError`.
7. Optional CLI `python3 probe.py <launcher...>` prints the verdict and exits 1 unless `OFF`. README includes one sentence on the optional follow-up (not in this increment): pointing the probe at a real `.pipeline/run.sh` phase invocation.

**Knowledge note to add with the build:** `knowledge/print-mode-background-ceiling.md` (drafted by researcher, see below).

## Open questions

- Does `settings.json` `env` apply to this variable in `-p`? Unverified; could be a follow-up probe.
- Does an exit-0 truncated run set any distinct exit code or stream-json event in newer versions? Docs say only "stops waiting ... and exits"; not confirmed.
- The remaining health-finding backlog lines about this ceiling (09-02, 09-15 x2) are stale after the fix; a human or the reconcile pass should close them. Left alone here.
