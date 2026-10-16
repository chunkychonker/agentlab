# Token counting as a budget gate

Verified 2026-10-16 from the [token counting docs](https://platform.claude.com/docs/en/build-with-claude/token-counting).

- `client.messages.count_tokens(model, system, tools, messages, thinking...)` -> `{"input_tokens": N}`. Same inputs as `messages.create`.
- An **estimate** (small drift; unbilled system tokens may be included). Treat a limit as a fence, not a meter.
- Free; RPM limits (5k/10k/20k by tier) are independent of message creation.
- Counts under the tokenizer of the `model` passed. 4.7+/5-series models count ~30% more than older ones: count against the model you will run.
- Ignores caching (`cache_control` accepted, unused): counts raw prefix, conservative vs billing.
- Errors for server tools, MCP connector, and url/file image/document sources. Client-tool loops are fine.
- Input only: add `max_tokens` as a reserve for a worst case.
- Pattern: pure `check_budget(counted, limit, turn, reserve)` returning a typed `BudgetExceeded | None` under a thin shell; fail closed if the count call errors. Planned example: `examples/token-budget-guard/` (see research/2026-10-16-token-budget-guard.md).

Related: [[tool-use-loop]], [[context-editing]], [[anthropic-models]]
