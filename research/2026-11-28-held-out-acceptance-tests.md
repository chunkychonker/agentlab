# Held-out acceptance tests for the nightly build

Date: 2026-11-28. Mode: demo. Backlog item: "Held-out acceptance tests".
Skipped the topmost `[ ]` item ("Cross-model review"): `BACKLOG.md` shows it
claimed by `[stranded cycle/2026-11-27-unshipped-022347-1]`, which already holds
`research/2026-11-27-cross-model-review.md`. Re-researching it would repeat the
PR #5/#6 mistake. No PRs are open.

## Question

Can the pipeline run researcher-written tests that the builder cannot see, as a
gate between build and review? Which parts of that can be enforced, and which
can't?

## Findings

1. **The motivation is cited and the lab's exposure is small.**
   `research/2026-08-16-agent-architecture-discourse-vs-agentlab.md` claim 6
   cites [SpecBench, arXiv 2605.21384](https://arxiv.org/abs/2605.21384)
   (2026-05-20). Agents saturate the visible suite while the visible/held-out gap
   grows about 28 pp per tenfold increase in code size. I did not re-fetch the
   paper this cycle, so treat that figure as second-hand. Today the builder writes
   both the code and the tests, and the reviewer re-runs the builder's suite. The
   same note observes that the health check holds out the *environment* (a fresh
   venv) but not the tests.

2. **The researcher cannot write the held-out file where the backlog says,
   today.** `.pipeline/phase_tools.sh` (read this cycle) gives the research phase
   Edit/Write only on `research/**`, `knowledge/**`, `BACKLOG.md` and
   `projects/*/PLAN.md`. A new path, for example `heldout/<slug>/`, needs a new
   rule in `phase_file_rules` for `research`. That is an allow-list widening,
   which belongs in the same change as the gate.

3. **A deny rule alone will not hide the file from the builder.** The Claude Code
   [permissions docs](https://code.claude.com/docs/en/permissions) (fetched
   2026-11-28) say:
   - `Read(path)` deny rules apply to the built-in file tools. Claude makes only a
     "best-effort attempt" to apply them to Grep and Glob.
   - `Write(path)`, `Glob(path)` and `Grep(path)` rules don't match. Use `Edit(...)`
     and `Read(...)` instead.
   - `//path` anchors to the filesystem root, so `Read(//<repo>/heldout/**)` is the
     right anchored form.
   - A Bash deny rule matches command text and "doesn't match ... inside `sh -c`".
   - Sandboxing is the documented answer for enforcement that doesn't depend on
     command text.

   The build phase has `Bash` allowed (`phase_tools.sh`: build gets
   `Read Glob Grep Edit Write Bash`). `cat heldout/x.py` or `python3 -c` therefore
   goes around any Read deny. The file's own header already says as much for
   publish rules: "NOT a security boundary for phases that have Bash."

4. **So the real mechanism is absence, not denial.** The pipeline should move the
   held-out directory out of the worktree before the build phase and put it back
   before verify. Used together with the Read deny, the builder would have to
   guess an unrelated temp path, not just run `cat`. This is friction, not a
   boundary, and the README should say so. A full boundary needs the sandbox or a
   separate OS user, which is out of scope.

5. **The verify gate is the right hook, with one wrinkle.** `verify.sh` plans from
   `.pipeline/verify.conf` by path prefix over `git status` changed paths. A
   held-out run must not depend on the builder's changed paths. If the stash is
   restored into the worktree as untracked files, `verify_increment` sees
   `heldout/...` as changed and a prefix rule `heldout/ | held-out tests | ...`
   would fire. That is incidental, so the gate should be explicit: a separate
   `heldout_run` step called from `run.sh` right after `verify_increment` (line
   ~847), with its own one-word result. A FAIL there feeds the existing single fix
   round. The fix round must not hand the builder the test source. It gets only
   the failing test names and the assertion message, in the style of
   `VERIFY_REPORT_TAIL_LINES`, because showing source would defeat the hold-out
   after one round.

6. **The research note leaks too.** The builder reads `research/<date>-*.md`. The
   researcher must therefore not paste held-out test bodies or exact expected
   values into the note. The note carries the layer-2 criteria, and the held-out
   file encodes extra cases for those same criteria. If a case can't be stated as
   a criterion, the builder can't be blamed for missing it.

7. **Git-tracking decision.** `heldout/` must be git-ignored. Otherwise the
   maintainer's `git add` ships the tests into the public PR, and later cycles
   read them. Keeping them local-only means they are not preserved after the
   night. Put the stash outside the repo and optionally archive passed cases under
   `logs/` (already git-ignored).

## Build proposal

Directory: `.pipeline/` (pipeline increment, like `verify.sh`). Name the module
`heldout.sh` with `test_heldout.sh`. No collision: `grep heldout` hits only
BACKLOG.md and the 2026-08-16 note, and there are no open PRs or matching
branches.

**Intent.** Let a researcher-written test file be hidden from the build phase and
run as a gate after it. Out of scope: the sandbox or OS-user boundary, the
researcher prompt wording, and cross-model review.

**Interfaces (bash 3.2, sourceable, runs nothing on source):**

```
HELDOUT_DIR="heldout"                 # in the worktree, git-ignored
heldout_stash   <stash-root>          # mv $HELDOUT_DIR -> <stash-root>/<random>; prints that path; rc 0 (also when no dir: prints NONE)
heldout_restore <stashed-path>        # mv it back; rc 0; rc 2 if the worktree already has $HELDOUT_DIR
heldout_run     <log>                 # runs $HELDOUT_DIR/*/run.sh in order; prints PASS|FAIL|NONE|ERROR
heldout_report  <report-file>         # failing test names + assertion lines only, no source
```

`phase_tools.sh` changes: research gets `Edit/Write(//root/heldout/**)`; build
gets `Read(//root/heldout/**)` and `Edit(//root/heldout/**)` deny rules.
`test_phase_tools.sh` is extended for both.

**Behaviour and invariants.**
- Stash then restore is the identity on the directory's contents, byte for byte.
- `heldout_run` never runs unless the directory is present and restored, so a
  forgotten restore is `ERROR`, never `PASS`.
- A missing directory is `NONE` (ungated), not `PASS`. The log says so.
- The report contains no line of test source. It is built from the runner's
  output filtered to `FAIL:`/assertion lines.
- A timeout (reuse `VERIFY_TIMEOUT_S` via `verify_run`) is a FAIL, as in
  `verify.sh`.
- Wire-up in `run.sh`: stash before build, restore after build (also on build
  failure, via an explicit step, not a trap, because bash 3.2 defers traps; see
  `knowledge/pipeline-run-log-shapes.md`), `heldout_run` after `verify_increment`.
  FAIL triggers the existing one fix round.

**Acceptance criteria (offline, no key, no network):**
1. After `heldout_stash`, `$HELDOUT_DIR` does not exist in the worktree and the
   stashed path holds identical files. After `heldout_restore` it is identical
   again.
2. `heldout_restore` onto an existing `heldout/` returns rc 2 and overwrites
   nothing.
3. With a fixture directory whose test fails, `heldout_run` prints `FAIL`, and
   `heldout_report` output contains the test name and assertion text but not a
   fixture-source sentinel string planted in the test file.
4. Fixture that passes gives `PASS`; no directory gives `NONE`; unreadable
   directory or a stash not yet restored gives `ERROR`.
5. `phase_tool_args build <root>` output contains the anchored Read and Edit deny
   for `heldout/**`, and `phase_tool_args research <root>` contains the matching
   allow, in the format of the existing assertions.
6. A build that passes its own suite but fails the held-out one yields a combined
   outcome that does not reach review, using the injected-side-effect style of
   `test_runner.sh`.

README (in the module header) must say: this hides, it does not enforce, because
the build phase has Bash.

## Open questions

- Whether `claude -p` under `dontAsk` really refuses `Read(//root/heldout/**)` for
  Grep/Glob. The docs say "best-effort", so verify with one live probe or treat
  it as unverified.
- Not confirmed: SpecBench's numbers, since I only read them through the 2026-08
  survey note.
- Where the researcher should write the held-out file when it has no Bash. It may
  only write `heldout/<slug>/...`, so who writes `run.sh` and the `.gitignore`
  line? Probably the pipeline owns both and the researcher writes only test
  files.
- Whether the held-out set should persist across nights for regression value
  (currently dropped, point 7).
