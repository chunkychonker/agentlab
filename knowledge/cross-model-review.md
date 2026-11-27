# Cross-model review of agent output

Durable points from `research/2026-11-27-cross-model-review.md`:

- Evidence that a reviewer should not be the author's model is real but mixed (2026 preprints; see [[nightloop-reviewer-benchmark]] for how to measure instead of assume). Session isolation is the other half of the remedy and the lab already has it.
- Inside `claude -p` every model is Anthropic's, so switching opus to sonnet or fable varies the model, not the family. The "different family" advice cannot be tested in the lab.
- `claude --model` takes aliases (`opus`, `sonnet`, `fable`, ...) or a full name. Aliases move; pin with a full name or `ANTHROPIC_DEFAULT_OPUS_MODEL`. Some models need a minimum Claude Code version. Source: [model config docs](https://code.claude.com/docs/en/model-config), 2026-11-27.
- `run.sh`'s `run_phase` guard accepts only `opus|sonnet` on purpose, so a typo aborts rather than silently reverting to the default. A new setting must keep that fail-loud property.
- Model ids in [[anthropic-models]] and the model-config page disagree (`claude-opus-5` vs `claude-opus-5-5`); unresolved, so do not hardcode ids.

Status: researched 2026-11-27, not built yet. Related: [[claude-p-phase-contract]].
