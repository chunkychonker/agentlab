# Cross-model review: make the reviewer's model its own setting

Date: 2026-11-27. Mode: demo. Backlog item: "Cross-model review" (Pipeline & repo hygiene).

## Question

How should the pipeline's review phase get its own model setting, separate from the build phase's, and what does the evidence say the alternative model should be?

## Findings

**Current state (read from the repo, not memory).**
- `.pipeline/run.sh:831` runs `run_phase opus build ...`. `run.sh:871` runs `run_phase opus review ...`. Both models are string literals at the call site.
- `run_phase` (`run.sh:487-493`) accepts only `opus|sonnet` and aborts the phase on anything else. That deliberate typo guard is the only validation. It rejects full model ids and `fable`.
- `projects/nightloop` already has the measuring tool, `nightloop bench` (PR 108). It takes the review model from the `--config` file's review phase (`nightloop/cli/bench_args.py` has `--fixtures/--template/--config/--results/--trials/--timeout-s`, and no model flag). Comparing two models today means keeping two config files.
- `eval/run_reviewer_eval.sh` is the older bash harness. It dirties the live checkout, and `knowledge/nightloop-reviewer-benchmark.md` says not to extend it.

**What the CLI accepts** ([Claude Code model config](https://code.claude.com/docs/en/model-config), fetched 2026-11-27):
- `--model` takes the aliases `default, best, fable, sonnet, opus, haiku, sonnet[1m], opus[1m], opusplan`, or a full model name such as `claude-opus-5-5`.
- Aliases move: `opus` currently resolves to Opus 5.5 and `sonnet` to Sonnet 5.5 on the Anthropic API. A full name pins. `ANTHROPIC_DEFAULT_OPUS_MODEL` and its siblings repoint an alias.
- `fable` resolves to Fable 5.2 and needs Claude Code v2.1.321 or later. Opus 5.5 needs v2.1.280 or later. Check the runner's installed version before allowing either.
- `knowledge/anthropic-models.md` (verified 2026-07-27) lists different ids, such as `claude-opus-5` and `claude-fable-5`. The model-config page names `claude-opus-5-5` and `claude-fable-5-1`. I could not reconcile the two. The builder must not hardcode an id from either. Take the alias, or an id passed in by the human.

**Evidence that the reviewer's model matters** (all 2026 preprints or practitioner posts, not settled):
- arXiv 2605.21537 (2026-05-20), cited in `research/2026-08-16-agent-architecture-discourse-vs-agentlab.md`: 31.7% of a model's semantic errors are silently endorsed by the model that made them. The remedy it names is "a different model or an isolated session".
- [Self-attribution bias](https://arxiv.org/html/2603.04582v1): across 10 frontier models, a model rates an action more favourably when it is implicitly framed as its own. The effect is largest on incorrect code and on PR approval. The search summary said that labelling the source explicitly did not produce it by itself. I did not read the paper, so treat the detail as unconfirmed.
- [Cross-model review in LLM verification](https://arxiv.org/pdf/2610.01471) compares same-conversation, fresh-context and different-model review. I did not read it. Its conclusions are unverified.
- [A small practitioner study](https://clementzach.substack.com/p/improving-automated-code-review-performance) found that same-model reviewers caught fewer true bugs than cross-model ones. It is a single small study.
- Counter-evidence: [arXiv 2606.20093](https://arxiv.org/abs/2606.20093) found no detectable self-preference on verifiable instruction-following. An [EMNLP 2026 Findings paper](https://aclanthology.org/2026.findings-emnlp.1571.pdf) argues that self/other labels alone can shift judge scores, and that apparent self-preference may be confounded with style.

**Implication for this lab.**
- Every model reachable through `claude -p` is an Anthropic model. Switching the reviewer from opus to sonnet or fable varies the model, not the family. The "different family" advice does not apply here, and the lab cannot test it.
- The backlog item is right to say "measure, don't assume". The cheap, reversible increment is a setting plus a way to measure it. Changing the default is a human decision made from the bench numbers.

## Build proposal

Directory: `.pipeline/` plus a one-flag change under `projects/nightloop/`. This is not an `examples/` increment. Nothing in `examples/` or the open PRs collides: there are no open PRs, and the branches `cycle/2026-11-2x-nightloop-*` touch nightloop docs and claim wiring, not `bench_args.py`.

**1. Intent.** Let the review phase's model differ from the build phase's without editing `run.sh`, and let `nightloop bench` run the reviewer on a chosen model. Out of scope: changing the default (it stays opus for both), running the billed bench, fixing the two loose-pattern fixtures, a compare-two-runs report, and any non-Anthropic reviewer.

**2. Behavioral spec.**
- Input: `$REPO/.pipeline/models.conf`, lines of the form `build=opus` and `review=opus`. A missing file means both are `opus`. Env vars `AGENTLAB_BUILD_MODEL` and `AGENTLAB_REVIEW_MODEL` override the file. They are read once, at the top of `run.sh`.
- Output: `resolve_phase_model <phase> <conf-path>` prints the model for that phase. It accepts only `build` and `review`.
- Accepted values: `opus`, `sonnet`, `fable`, or a full name matching `^claude-[a-z0-9.-]+$`. Anything else, including an empty value, an unknown phase, a duplicate key or an unknown key, exits non-zero with a message naming the file and line. There is no silent fallback to opus, because a typo would restore the default and hide itself.
- `run_phase`'s case guard widens to the same validator. This is expand-only: `opus` and `sonnet` behave as before.
- The review phase's `--- phase: ... (model: X) ---` log line already shows the model, so no new logging is needed. `run_log.sh` parses that line, and its tests must still pass.
- `nightloop bench --model NAME` overrides the review phase's model from `--config` for that run. NAME is validated by the existing `_MODEL_NAME` rule in `model.py`. The results file records the model used, so two runs can be told apart. The template SHA freshness check is unchanged.
- Invariants: with no conf file and no env vars, the argv for every phase is byte-identical to today's. The `fix` round stays on the build model.
- Failure modes: unreadable conf, bad value, or unknown key makes `run.sh` abort before any phase runs, with the file and line in the message. A model that the installed CLI does not support fails in the CLI. `run_phase` already surfaces that as a non-zero phase, and the new setting adds no handling for it.

**3. Interfaces.**
- `.pipeline/models.sh` (sourceable, bash 3.2, no I/O except reading the conf): `is_valid_model <name>` returns 0 or 1. `resolve_phase_model <phase> <conf-path>` prints the model or returns 1.
- `.pipeline/test_models.sh`, in the style of `test_phase_tools.sh`: no network, no model.
- `projects/nightloop/nightloop/cli/bench_args.py`: add `--model` (`type=str`, `default=None`). `bench_cmd.py` applies it with `dataclasses.replace` on the review `Phase`. The builder should check whether `Config` and `Phase` are frozen dataclasses first.

**4. Acceptance criteria** (each checkable):
1. No conf file and no env vars: `resolve_phase_model build` and `resolve_phase_model review` both print `opus`.
2. Conf `review=sonnet` prints `sonnet` for review and `opus` for build.
3. `AGENTLAB_REVIEW_MODEL=claude-opus-5-5` beats the conf file.
4. These each return non-zero and name the offending file and line: `review=opsu`, `review=`, `review=opus` twice, an unknown key `lint=opus`, and `resolve_phase_model research`.
5. `is_valid_model` accepts `opus`, `sonnet`, `fable` and `claude-opus-5-5`, and rejects `Opus`, `claude opus`, `claude-`, and a string containing `;` or `$(`.
6. A `run.sh` dry segment (the way `test_runner.sh` drives it) shows the review phase header `(model: sonnet)` when configured, and `(model: opus)` for build.
7. `nightloop bench --dry-run --model sonnet` prints the review model `sonnet`. Without the flag it prints the config's model. A model with a space exits non-zero.
8. The existing `.pipeline/test_*.sh` suites and the nightloop test suite still pass.
9. The README or PIPELINE.md gets two lines: how to set the model, and that opus for both is still the default.

Note for the builder: `postcondition.sh` flags builds that land only under `.pipeline/` as STALE (the documented blind spot in PIPELINE.md). Putting the `bench --model` change under `projects/nightloop/` in the same increment keeps `increment_built` satisfied.

## Open questions

- I could not reconcile `knowledge/anthropic-models.md` ids (`claude-opus-5`, `claude-fable-5`) with the model-config page ids (`claude-opus-5-5`, `claude-fable-5-1`). The `claude-api` skill was not consulted this cycle because no API code is proposed. The builder should not hardcode ids.
- The runner's installed Claude Code version is unknown. `fable` and Opus 5.5 have minimum versions.
- I read only a search summary of the self-attribution and cross-model-review papers. Their effect sizes are unconfirmed.
- Whether varying the model inside one family helps at all is exactly what the bench would measure, with 21 billed calls per arm. The two loose fixtures should be fixed before that run, or the comparison measures the fixtures. That is a separate item.
