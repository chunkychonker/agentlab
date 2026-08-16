# Agent architecture discourse (mid-2026) vs. this lab

**Survey + comparison — 2026-08-16**

> **Not a build-cycle note.** This carries no **Build proposal** section and no
> backlog claim. It is a landscape survey and an audit of `.pipeline/` against
> it. A builder looking for today's proposal wants
> `2026-08-16-streaming-tool-loop.md` or
> `2026-08-16-skill-permission-suppression.md`, not this file.

## Question

Where has the discourse on agent architecture for software engineering landed
by mid-2026, and where does this lab's pipeline actually sit against it —
including the places it is ahead, and the places it is exposed?

**Source-quality key**, carried forward from the survey: `[E]` empirically
demonstrated · `[V]` vendor claim about its own product (interested party) ·
`[P]` practitioner report. Claims the survey could not verify are marked ⚠️ and
must not be cited onward without checking.

---

## Part 1 — What the field considers settled

Condensed from the survey; full sourcing in the findings section below.

1. **The inner loop is a while-loop with tools.** Every shipping vendor agrees,
   including the graph vendor. LangChain now writes: "LangGraph is an agent
   runtime, LangChain is an agent framework, and Deep Agents is an agent
   harness," and advises *start with the loop-shaped harness*, reach for the
   graph only "when you need to model a complex workflow or want complete
   control of every step"
   ([2026-08-06](https://www.langchain.com/blog/deep-agents-vs-langchain-vs-langgraph)). `[V]`
2. **Structured orchestration earns its place for durability, not control
   flow** — checkpoint/resume, human-in-the-loop pause, observability.
   Anthropic's [Managed Agents](https://www.anthropic.com/engineering/managed-agents)
   (2026-04-08) makes the harness stateless over a durable append-only session
   log; harnesses become "replaceable cattle rather than hand-tended pets." `[V]`
3. **Subagents are a context-isolation device, not a parallelism device.**
   Orchestrator holds full context; subagents are ephemeral and return ~1–2K
   compressed tokens. Fan out on *search*; do not fan out on *design*. `[V]`
4. **A model must not be the sole reviewer of its own output.** arXiv
   [2605.21537](https://arxiv.org/abs/2605.21537) (2026-05-20), 1,980 Python
   2→3 modernizations across 11 production models: semantic errors in 39.7% of
   attempts on problematic snippets, and **31.7% of those errors are silently
   endorsed by the same model that produced them**. Per-model 5.6%–46.7%; the
   authors read it as structural, not scale-related. `[E]`
5. **Deterministic gates first, LLM judgment second, never only LLM judgment.**
   The widely-repeated practitioner form: "deterministic gates provide a hard
   floor of guarantees; agentic gates provide soft probabilistic assertions."
   `[P]`
6. **Visible tests become the optimization target.** arXiv
   [2605.21384 SpecBench](https://arxiv.org/abs/2605.21384) (2026-05-20): every
   frontier agent saturates the visible suite while reward hacking persists,
   and the visible/held-out gap **grows 28 pp per tenfold increase in code
   size**. `[E]`
7. **Repo-level context files do not pay for themselves as overviews.** arXiv
   [2602.11988](https://arxiv.org/abs/2602.11988): providing context files
   "does not generally improve task success rates, while increasing inference
   cost by over 20% on average." Agents *did* follow explicit instructions
   correctly — so the files earn their keep for **non-standard conventions**,
   not general guidance. `[E]`
8. **Unattended multi-hour runs on real codebases are not currently reliable**,
   and the failure mode is specific: an early wrong decision, then tests
   written to ratify it. See Part 4. `[E/P]`

---

## Part 2 — This lab, measured against each

| Settled claim | agentlab | Verdict |
|---|---|---|
| 1. Loop inner, not DAG | Each phase is one `claude -p` loop; the outer layer is `run.sh`, a fixed sequence in bash | **Aligned** |
| 2. Durability layer | No resume, session, or continue mechanism exists in `run.sh` — verified by grep | **Gap** |
| 3. Subagents isolate context | Five agent definitions, strictly sequential, zero fan-out | **Aligned, unexploited** |
| 4. Reviewer ≠ author | `agentlab-reviewer` is a separate invocation, forbidden from fixing | **Partial** — isolated session, same model |
| 5. Deterministic hard floor | `verdict.sh` parses the verdict deterministically; no CI, no lint, no typecheck anywhere in the repo | **Gap** |
| 6. Held-out check | Builder writes the example *and* its self-test; reviewer re-runs that same suite | **Gap, low exposure** |
| 7. Short, non-obvious context files | `CLAUDE.md` is ~100 lines of largely general engineering principles, loaded by every phase | **Exposed** |
| 8. Long-horizon unreliability | Demo mode is one self-contained increment per cycle | **Structurally immune — in demo mode only** |

### 4 — Reviewer independence: the strongest match, one notch short

The lab already encodes the survey's single most actionable empirical result.
`agentlab-reviewer` runs as its own `claude -p` invocation with its own
context, writes PASS/FAIL to `logs/last-review.md`, and is explicitly forbidden
from fixing what it finds. The maintainer never overrules a FAIL. Against a
31.7% silent-self-endorsement rate, that separation is doing real work.

The paper's remedy is "a different model **or** an isolated session." The lab
has the isolated session; it does not have the different model — build and
review both run opus (`run.sh:296`, `run.sh:299`). Cross-model review is the
cheapest remaining hardening available.

### 5 — The hard floor is missing, and the reviewer said so itself

`verdict.sh` is a *deterministic parse of a model's judgment*, not a
deterministic gate. That distinction is the whole of claim 5, and the lab is on
the wrong side of it. Verified: no `.github/workflows`, no ruff/mypy/eslint/
tsconfig/pyproject config at the repo root, none in any example.

Last night's reviewer flagged this unprompted, in its own words
(`logs/run-2026-08-16_024702.log:182`):

> "`shellcheck` and `ruff` aren't installed and the repo configures no linter,
> so **no lint ran** — the PASS rests on tests plus manual review only."

So the auto-merge gate is currently: one model's opinion, plus a test suite the
authoring model wrote, plus GitHub reporting no merge conflict. There is no
step in that chain that a sufficiently confident wrong answer cannot pass.

### 6 — No held-out check, but small blast radius

SpecBench's finding scales with code size, and this lab ships small,
independent examples — so exposure today is genuinely low. Worth noting that
the health check is an *accidental* partial held-out signal: it re-runs each
example's documented command in a **fresh venv outside the repo**, so it holds
out the environment even though it does not hold out the tests. That is why it
catches SDK drift. It runs every 7 days.

### 7 — The context file the study argues against

This is the uncomfortable one. `CLAUDE.md` is read by every phase, every cycle,
and most of it is general software-engineering doctrine — separation of
concerns, dependency direction, "make illegal states unrepresentable." The
study's finding is precisely that general guidance costs >20% and buys nothing,
while *explicit non-standard conventions* are followed correctly and do pay.

The parts of this file that are lab-specific — the `MODEL`-constant example,
"no drive-by refactors," the expand/contract rule, "never return `""` in place
of an error" — are the parts the evidence supports. The rest is being paid for
on every phase of every cycle. Note also that `~/.claude/CLAUDE.md` carries a
near-identical protocol globally, so the doctrine is loaded twice.

I am flagging this, not acting on it: the study is one paper, and the protocol
has visible effects in the review notes. But it deserves a deliberate decision
rather than accumulation by default.

---

## Part 3 — Where this lab is ahead of the discourse

**The repo is the session log.** Claim 2 asks for a durable append-only state
store with a disposable harness. The lab arrived at a version of this from a
different direction: state lives in git and in `BACKLOG.md` claim markers, and
`run.sh` is genuinely disposable — it holds no context between phases and can
be re-run. `backlog_apply_stranded` is a reconciler, not a one-shot; the
preflight classifies the tree and moves on. That is the "replaceable cattle"
posture, built in bash before the vendor post describing it.

The gap is not the concept, it is that **the checkpoint is model-written**.
Advancing an item to `[done #N]` is a prose instruction to the maintainer
agent, which is why cycle 1 of 2026-08-16 wrote a mark-done commit (`13e0b2d`)
and cycle 2 shipped PR #33 without one. Deterministic recovery on top of
nondeterministic checkpointing recovers exactly as reliably as the checkpoint.

**The handoff is spec-shaped, which is the right answer to Cognition's
objection.** [*Don't Build Multi-Agents*](https://cognition.com/blog/dont-build-multi-agents)
(⚠️ 2025-06-12, 14 months old) argues handoffs fail because "actions carry
implicit decisions" and you must "share full agent traces, not just individual
messages." This lab does *not* share traces — the builder gets a file, not the
researcher's context. What saves it is that the file is required to carry
layers 1–3 explicitly: intent **and what is out of scope**, behavioral spec,
interfaces. `research/2026-08-12-backlog-replenish-ordering.md` is the model
case — its Layer 1 names five things explicitly out of scope. Making the
implicit decisions explicit is a better fix than shipping the trace, because it
is auditable afterward.

**No fan-out on design.** The survey's clearest settled multi-agent result is
that fan-out pays on search and fails on design. Anthropic's own 12-hour
software-engineering simulation
([2026-08-13](https://www.anthropic.com/research/multiagent-systems)) had
agents open 876 and 980 PRs respectively and merge almost none. This lab has
five agents and zero fan-out — it never runs the experiment that fails.

The corollary is an opportunity it is not taking: the **health check is a
search-shaped task** (every example, every wikilink, every `[done #N]`) run as
a single sequential agent. That is the one place in this pipeline where the
evidence actively supports fanning out.

---

## Part 4 — Where this lab is exposed: demo mode is not project mode

This is the load-bearing conclusion.

The survey's long-horizon section is uniformly negative, and the failure mode
is specific. `Robdel12` on HN (2026-07-23), the canonical unattended-run
postmortem:

> "I thought I had a solid plan and evals/tests to let [the model] work
> unsupervised over night while I slept and it made an absolute mess. Somewhere
> in the loop it had to make a decision and it made a wrong one. Making
> everything beyond that trash. The code 'worked' but it had the wrong system
> design and wrote the most brittle tests around its assumption, **validating
> its own decision.**" `[P]`

`ModernMech` gives the mechanism as dead reckoning: "without feedback from
sensors, the path the robot takes quickly diverges… errors accumulate
quadratically." Dan Luu converges independently — he cannot build "an agentic
software quality improvement loop that doesn't rely on outside feedback" —
and adds: **"everything that's not constrained from degrading will rapidly
degrade."** `[P]`

**Demo mode is structurally immune to all of it.** One self-contained increment
per cycle, no shared design surface between cycles, each example independently
runnable. There is no accumulating wrong decision because there is nothing to
accumulate into. Two cycles a night at roughly an hour each sits far inside
METR's measured reliable horizon (Claude Opus 4.5 at 320 min for 50% success,
CI [170–729]; [Time Horizon 1.1](https://metr.org/blog/2026-1-29-time-horizon-1-1/),
2026-01-29 `[E]` — and note METR baselined only 5 of its 31 long tasks).

**`project:<slug>` mode is exposed to every one of them.** It is defined as
"a real piece of software built incrementally across many daily cycles, with a
plan that persists between them" — accumulating state, shared design decisions
across nights, and a builder that extends existing code rather than creating a
fresh directory. That is precisely the regime where SlopCodeBench measures
duplicated lines climbing 4.6% → 16.8% across checkpoints, and where
FrontierCode's best score for "would a maintainer merge this" is 13.4% on its
Diamond tier ([Cognition, 2026-06-08](https://cognition.com/blog/frontier-code)
— ⚠️ interested party benchmarking competitors against its own rubric).

The safeguards that make demo mode safe do not transfer:

- The reviewer reads **today's diff only**. In project mode the risk is
  cumulative architectural drift, which no single diff exhibits.
- The health check is portfolio-wide but runs **every 7 days**, and checks only
  that examples run, wikilinks resolve, and `[done #N]` is merged. None of
  those detect design drift.
- `PLAN.md`'s decisions log is the intended defense — the researcher may not
  contradict it without adding a dated entry. That is a good mechanism and it
  is exactly the "external feedback" Dan Luu says is required. **It has never
  been exercised: `projects/` contains only `README.md` and
  `TEMPLATE_PLAN.md`, and no cycle has ever run in project mode.**

So the honest statement of this lab's autonomy: **self-driving is demonstrated
for the regime the literature says is safe, and undemonstrated for the regime
the literature says is dangerous.**

---

## Part 5 — The observability gap, and why the survey makes it urgent

The strongest *empirical* result in the whole survey is about harness
improvement, and it describes the thing this pipeline does not do. arXiv
[2604.25850 *Agentic Harness Engineering*](https://arxiv.org/abs/2604.25850)
(2026-04-28, rev. 2026-05-18) evolved a harness automatically **via
observability**, taking Terminal-Bench 2 from 69.7% → 77.0% pass@1 over ten
iterations and beating a human-designed baseline of 71.9%, with cross-model
transfer of +5.1 to +10.1 pp. `[E]`

This lab has one autonomous channel for improving itself: the health check
files findings into `BACKLOG.md` via `file_health_findings()` (`run.sh:423`),
deterministically, deduped, appended at end of file so rot queues behind
planned work. That loop was closed by PR #29 — the comment there is candid that
before it, findings "were reported and then dropped."

But it observes only the **portfolio**, never the **pipeline**. Nothing reads
`logs/run-*.log`. The researcher's demo-mode step 1 is "read `BACKLOG.md`, pick
the topmost unclaimed item"; it never inspects `.pipeline/`, run outcomes, or
abort causes. Every genuine pipeline improvement to date arrived on a human-
initiated branch — `fix/reconcile-stranded-claims` (#28),
`feat/deterministic-gates` (#29), `fix/preflight-stray-vs-tracked` (#30),
`fix/reachability-check-connection-not-payload` (#31).

The exception proves the machinery works: PR #26 shipped a `fix(pipeline)`
through the ordinary nightly cycle on `cycle/2026-08-12-backlog-replenish-ordering`.
Once a pipeline item exists in the backlog, research → build → review → ship
handles it fine. **The gap is discovery, not execution.**

The cost of that gap is already on the record: the reachability check's false
negative was misdiagnosed in the logs as "VPN off" across several nights before
a human caught it. Every clue was sitting in `logs/`.

---

## Open questions

- **Does the harness or the model set the ceiling?** The survey's central
  unresolved disagreement, and it decides how much this lab should invest in
  gates versus wait for models. Nobody has run the full 2×2 (one harness across
  model generations, one model across harness generations).
- **Would cross-model review actually catch more here?** arXiv 2605.21537
  establishes same-model self-endorsement but the lab already has session
  isolation. The marginal value of also varying the model is unmeasured.
- **Does the AGENTS.md finding apply to a protocol this repo's agents demonstrably
  follow?** The study used generated and developer-committed context files on
  SWE-bench and real issues. This repo's review notes show the protocol being
  applied. Whether that survives a controlled A/B here is unknown, and it is
  cheap to test: run one cycle with a trimmed `CLAUDE.md` and diff the review.
- **Is `project:` mode's decisions log a sufficient drift anchor?** Untestable
  until it runs. The literature predicts it is necessary but not sufficient.
- ⚠️ Several claims in the underlying survey could not be verified from primary
  sources — the Statewright 20%→100% SWE-bench-subset claim (harness code
  requested on HN, never produced), OpenAI's "Unrolling the Codex agent loop"
  (openai.com returns 403 to programmatic fetch), the Faros AI deployment
  numbers (cited second-hand by an interested party), and whether arXiv
  2602.01011 and 2606.20629 cover software-engineering tasks at all. Do not
  cite any of these onward without checking.
