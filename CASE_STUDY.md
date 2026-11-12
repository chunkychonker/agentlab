# Case study: a nightly agent pipeline that is allowed to ship code

agentlab is built by a pipeline of headless Claude Code agents that runs every
night on one Mac. It researches a topic, builds one runnable increment, reviews
it, opens a pull request under a real name, and merges it with no human click.
This page explains how that pipeline is built, why it can be trusted to merge,
and what broke along the way.

Numbers are as of 2026-11-11.

## At a glance

| | |
|---|---|
| Running since | 2026-07-27 |
| Merged PRs | 90: 52 shipped by nightly cycles, 38 changes to the pipeline itself made outside the nightly run |
| Output | 40 runnable examples under `examples/`, 67 dated research notes, 47 notes in the `knowledge/` base |
| Long-running projects | `projects/nightloop/` (the pipeline itself, ported to an installable Python tool; 4 of 13 milestones done) and an external hackathon project |
| Review gate | Since `run.sh` began logging its gate (2026-08-16): 38 PASS, 11 FAIL, 3 MISSING. The 14 non-PASS cycles shipped nothing. |
| Cost | About $4 per cycle (median of the last two nights; metrics recording is new, so the sample is small) |

## The shape

```
BACKLOG.md / PLAN.md
   └─▶ researcher (Sonnet) ── research note: sources + spec
         └─▶ builder (Opus) ── working tree diff
               └─▶ reviewer (Opus) ── PASS/FAIL + criterion-by-criterion check
                     └─▶ [gate: run.sh parses the verdict and the spec check]
                           └─▶ maintainer (Sonnet) ── branch, commit, PR
                                 └─▶ [gate: GitHub says MERGEABLE] ── merge
```

Two observers run every seventh night: a health check that re-runs every
example's tests in a fresh environment, and an observer that reads the
pipeline's own run logs for failures. Both only report; neither can change
code. Details are in [`PIPELINE.md`](PIPELINE.md).

## The design decisions that matter

**1. An instruction is not a gate.** Every decision about whether code ships is
made by deterministic bash, not by a model:
- The maintainer is *told* to ship only on PASS. But `run.sh` doesn't even
  start the maintainer unless its own parser reads exactly one
  `VERDICT: PASS` line.
- That parser fails closed. A missing, malformed or self-contradicting verdict
  ships nothing.
- Merging is a question for GitHub (`mergeable == MERGEABLE`), not a model.

Each gate is a small pure shell function with its own offline test suite. 14
suites cover the deterministic parts, and none of them calls a model.

**2. The reviewer is independent and checks the spec.**
- The researcher writes each increment's spec: intent, behaviour, interfaces
  and acceptance criteria.
- A different model run, which didn't write the code, reviews the diff. It
  runs the tests, then marks every acceptance criterion MET (naming the test
  that proves it), CUT (with the builder's written reason) or UNMET.
- `run.sh` parses that list too. A PASS that lists an unmet criterion still
  doesn't ship.
- The reviewer has its own regression eval (`eval/run_reviewer_eval.sh`).
  Fixtures replay bugs it has caught before, to prove a prompt edit didn't
  make it weaker.

**3. Least privilege per phase.** Each phase gets only the tools it needs:
- **Researcher:** the only phase that reads the open web, and it has no
  shell. A malicious page can't get it to run a command, push or open a PR.
- **Builder:** can't commit, push or call `gh`.
- **Reviewer:** the same, and its Write tool reaches only its verdict file.
- **Maintainer:** can run a fixed list of `git` commands and can't merge.

**4. Phases talk through files, not to each other.** Research notes, the
working tree, `logs/last-review.md` and `logs/last-pr.txt` are the whole
interface. Every handoff is something you can open and read after the fact.
The verdict and PR files are cleared at the start of each cycle, so a phase
that dies can't leave the last cycle's answer behind.

**5. Models by job, not by default.** The two phases that hold judgment, the
builder and the reviewer (the gate on code going out under a real name), run
on Opus. Research, git bookkeeping and reporting run on Sonnet. A one-word
auth check runs on Haiku. The model is passed per phase, so changing the
interactive default can't silently re-price the nightly job.

## What broke, and what changed because of it

Each of these happened on a real night. Each fix is in the repo, and the
fixes to gates come with offline tests.

- **A phase "succeeded" having done nothing (2026-09-01).**
  - What happened: a background-task time limit killed the researcher
    mid-run, but the CLI still exited 0. The builder then ran against the
    previous night's note.
  - The fix: every phase now has a *postcondition*, a check that it actually
    produced its artifact *after the phase started*. "A file exists" would
    have passed, because `research/` was full of old notes.
- **A stale PASS could have approved unreviewed work.**
  - The risk: if the review phase died, last cycle's `PASS` was still sitting
    in the verdict file.
  - The fix: the file is deleted before every review, so a dead reviewer
    reads as MISSING, not PASS.
- **Two cycles built the same directory (PRs #5 and #6).**
  - What happened: an open-but-unmerged PR made a topic look unclaimed.
  - The fix: the researcher and builder both check merged examples, open PRs
    and remote branches before naming a directory.
- **Same-file conflicts (#19/#20, 2026-08-10).**
  - What happened: two PRs edited the same file.
  - The fix: auto-merge only acts when GitHub reports a clean merge. A
    conflict is left open for a human, because resolving one needs judgment
    about intent.
- **Shipped work never marked done (until 2026-09-10).**
  - What happened: nothing rewrote a backlog claim from `[building]` to
    `[done #N]`, so it was corrected by hand.
  - The fix: the merge step now does it from the merge commit's diff.
- **Health findings were written and then dropped.**
  - What happened: the health check reported problems, but nothing filed
    them anywhere.
  - The fix: a deterministic step saves each finding to a private findings
    file, with when it was first and last seen.
- **The reviewer never read the spec (fixed 2026-11-11).**
  - What happened: the researcher wrote acceptance criteria and the reviewer
    judged the code without them.
  - The fix: the spec check described in decision 2 above.

The pattern repeats: a model is trusted to do the work, and the decisions
*about* the work are moved into code that can be tested without a model.

## What it doesn't do (yet)

- It has no human in the loop between spec and build. The spec check moves
  that review after the build instead.
- The reviewer can still claim a criterion is MET when it isn't. The gate
  makes it name the test, so the claim can be checked, but it can't verify
  the reviewer's judgment.
- It runs on one machine under launchd. `nightloop` is the effort to make it
  installable on any repo, with a GitHub Actions scheduler.
