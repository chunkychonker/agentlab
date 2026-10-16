# Token-budget guard for the hand-written loop

Date: 2026-10-16. Backlog item: "Token-budget guard for the hand-written loop". No open PRs at pick time; `examples/token-budget-guard/` is unused.

## Question

Can the hand-written tool loop (`examples/minimal-agent-loop/`) refuse to send a request that would exceed an input-token budget, using the token-counting endpoint, and what are the exact endpoint and response facts?

## Findings

Primary source: [Token counting docs](https://platform.claude.com/docs/en/build-with-claude/token-counting), fetched 2026-10-16.

- Endpoint: `POST /v1/messages/count_tokens`. Python: `client.messages.count_tokens(model=..., system=..., tools=..., messages=...)`. It takes the same structured inputs as `messages.create` (system, tools, images, PDFs, thinking).
- Response shape: `{"input_tokens": N}` (SDK type `MessageTokensCount`, attribute `.input_tokens`). The docs example prints `response.json()`. I could not install the SDK here to introspect the type, so the builder should confirm `.input_tokens` against the installed `anthropic`.
- It is an **estimate**. Actual usage may differ by a small amount. Anthropic may add system-optimization tokens that are not billed. So a guard is a budget fence, not an exact meter.
- Free to use. Rate limits are separate from message creation: 5,000 / 10,000 / 20,000 RPM on Start / Build / Scale tiers. One extra call per turn is nowhere near these.
- Counts under the tokenizer of the `model` you pass. Claude 4.7+ models (including the 5-series) use a tokenizer that yields about 30% more tokens than earlier models. Count against the model the loop actually runs (here `claude-haiku-4-5`, pre-4.7 tokenizer) and never reuse a number measured on another model.
- No caching logic: `cache_control` is accepted but ignored, so the count is raw prefix size and ignores cache discounts. A budget on it is conservative relative to billing.
- Errors: `invalid_request_error` for server tools (web search, code execution, etc.), the MCP connector, and `url`/`file` image or document sources. The lab loop uses only client tools, so unaffected. Thinking blocks from earlier turns count only on models that retain them.
- Counts input only. Output is bounded separately by `max_tokens`; a guard that wants a worst case can compare `counted + max_tokens` to the limit (design choice, stated below).
- Related existing notes: [[context-editing]] (free count_tokens measurement path and the tokenizer gotcha), [[tool-use-loop]], [[anthropic-models]]. This is the first lab use of count_tokens as a *gate* rather than a measurement.

Design observation: the loop's history grows monotonically (each turn appends the assistant turn and tool results), so counted tokens rise every turn. A pure decision function over (counted, limit, turn) is enough; no state beyond the message list.

## Build proposal

Directory: `examples/token-budget-guard/` (checked `ls examples/` and open PRs: free).

Intent: stop the tool loop before sending a request whose counted input tokens exceed a limit, returning a typed result rather than raising or silently truncating. Out of scope: truncating or compacting history (see [[context-editing]], [[compaction]]), cumulative billing across turns, output-token accounting beyond an optional reserve, streaming.

Files (small, one concern each):
- `budget.py` (pure core, no I/O, no SDK import):
  - `@dataclass(frozen=True) class BudgetExceeded: turn: int; counted_tokens: int; limit: int`
  - `def check_budget(counted_tokens: int, limit: int, turn: int, reserve: int = 0) -> BudgetExceeded | None` returns `BudgetExceeded` iff `counted_tokens + reserve > limit`. Raises `ValueError` for negative counted/reserve, non-positive limit, or turn < 1. Boundary: `counted + reserve == limit` is allowed.
- `agent.py` (imperative shell): `run_guarded_agent(client, user_message, *, token_limit, max_turns=5) -> str | BudgetExceeded`. Each turn: build the request kwargs once, call `client.messages.count_tokens(**kwargs)` (no `max_tokens`; confirm the SDK accepts the same kwargs minus `max_tokens`), call `check_budget(resp.input_tokens, token_limit, turn, reserve=MAX_TOKENS)`, return the `BudgetExceeded` if set, else `client.messages.create(**kwargs, max_tokens=MAX_TOKENS)`. Reuse the calculator tool and loop shape from `examples/minimal-agent-loop/` by importing or copying minimally (prefer import via a documented sys.path note, or copy with a comment; builder's call, state it in README). `MODEL = "claude-haiku-4-5"` constant defined once. Key read only in `main`.
- `test_guard.py`, `README.md`, `requirements.txt` (pin `anthropic`).

Invariants and failure modes:
- A request that fails the check is never passed to `messages.create` (assert the fake's create-call count).
- `count_tokens` API errors propagate loudly (no fallback to "assume under budget"). Document this fail-closed choice.
- Exhausting `max_turns` still raises `RuntimeError` as in the original loop.
- Failure domain: one extra free API call per turn; if count_tokens is down the agent stops rather than running unmetered.

Acceptance criteria (offline, no key, `python test_guard.py`):
1. `check_budget` table tests: under, exactly at limit (None), one over (returns `BudgetExceeded` with the given turn/counted/limit), reserve pushes it over, invalid args raise `ValueError`.
2. Fake client whose `count_tokens` returns climbing values (e.g. 100, 400, 900) with limit 500 and a tool-using script: the loop stops on turn 3, returns `BudgetExceeded(turn=3, counted_tokens=900, limit=500)`, and `create` was called exactly twice.
3. With a generous limit the fake scenario returns the final text string and `create` is called once per turn, `count_tokens` once per turn before each create.
4. A `count_tokens` that raises propagates the exception and `create` is never called.
5. Optional live mode (`python agent.py`, needs `ANTHROPIC_API_KEY`) prints the counted tokens per turn and a tiny limit demonstrates the stop; documented, not part of the self-test.

## Open questions

- Exact SDK attribute name/type of the response (`.input_tokens` on `MessageTokensCount`) was taken from the docs JSON and not introspected; builder verifies against the pinned `anthropic`.
- Whether `count_tokens` rejects `max_tokens` in the SDK signature is unconfirmed; the proposal omits it.
- Estimate-vs-actual drift size is documented only as "small"; not measured live.
- hn-search MCP server failed to connect this session (missing venv python); no HN reception was gathered. This topic is plain API behavior so little was lost.
