# Typed tools: `@beta_tool` + `client.beta.messages.tool_runner`

The SDK's own answer to "define a tool from a typed function instead of a
hand-written JSON Schema dict." Complements [[tool-use-loop]] (the manual
version) — reach for this once the mechanics are understood.

## The pattern

```python
from anthropic import beta_tool

@beta_tool
def word_count(text: str) -> str:
    """Count words in a string.

    Args:
        text: The text to count words in.
    """
    return str(len(text.split()))

runner = client.beta.messages.tool_runner(
    model="claude-haiku-4-5",
    max_tokens=1024,
    tools=[word_count],                # any list of @beta_tool functions
    messages=[{"role": "user", "content": "..."}],
    max_iterations=8,                  # unbounded if omitted — always set it
)
final_message = runner.until_done()    # or `for message in runner: ...`
```

- `name` = function name, `description` = docstring, `input_schema` = real
  JSON Schema generated from the type hints via `pydantic.TypeAdapter` (Google-
  style `Args:` lines become per-field `description`). **Requires Pydantic v2**
  (raises `RuntimeError` under v1).
- `.call(input_dict)` validates via `pydantic.validate_call` first — bad type
  or missing required key raises `ValueError`, *before* your function body
  runs. Verified locally (2026-07-28): `word_count.call({"text": 123})` and
  `word_count.call({})` both raise `ValueError`.
- A registry is just `{t.name: t for t in tools}` — no special API needed; the
  runner's own internal indexer (`tool_registry()` in
  `anthropic/lib/tools/_tool_dispatch.py`) is private, not exported.
- Unknown tool name or a tool raising inside the loop → caught, turned into a
  `tool_result` with `is_error: true`. The loop itself never crashes on a bad
  tool call. Raise `anthropic.ToolError` from inside a tool to control exactly
  what content comes back instead of `repr(exc)`. (An unknown name also emits a
  `UserWarning`; a generic exception is additionally `log.exception`-ed. `repr`
  is used deliberately over `str` because it keeps the exception type.) Because
  nothing ever stops the loop on a failing tool, a permanently-broken tool spins
  until `max_iterations` — see [[tool-failure-taxonomy]].
- Async variant: `@beta_async_tool` for `async def` tools.
- `@beta_tool(strict=True)` (kwarg, verified in `anthropic==1.3.0`) makes
  `.to_dict()` emit top-level `"strict": true`; the generated schema already
  carries `additionalProperties: false` + all-args-`required`, so it is
  strict-subset-compatible unmodified. See [[strict-tool-use]].

## Gotchas

- Lives under the **beta** namespace: `client.beta.messages.tool_runner`, not
  `client.messages`. Still beta as of SDK 0.120.0 (2026-07-28).
- `max_iterations` defaults to `None` = **unbounded**. Same discipline as the
  hand-rolled loop's `max_turns` — always pass it explicitly.
- **Silent truncation.** Reaching `max_iterations` makes `_should_stop()` return
  `True` and the loop exit *normally* — `until_done()` returns the last message
  with no exception and no flag. A cut-off run is indistinguishable from a
  finished one unless you check the returned message yourself: if
  `stop_reason == "tool_use"`, the agent never finished and its "answer" isn't
  one. (By contrast `stop_reason == "refusal"` *is* handled explicitly.)
  Verified in `_beta_runner.py` on `main`, 2026-08-10.
- If you intercept results: `generate_tool_call_response()` **caches** its result
  for the iteration, and calling `append_messages()` inside the loop flags state
  as modified so the runner **skips its own append** — you then own keeping the
  conversation valid.
- Constructing the runner does no network call; only iterating / `.until_done()`
  does. Safe to build and unit-test the tool objects themselves fully offline.
- Schema generation + Pydantic validation are pure local code — testable
  without a fake client or network, unlike the hand-rolled loop (which needs a
  scripted fake `.messages.create`, see [[tool-use-loop]]).

Source: read directly from installed `anthropic==0.120.0` source
(`anthropic/lib/tools/_beta_functions.py`, `_beta_runner.py`,
`_tool_dispatch.py`) and exercised locally, 2026-07-28. Background/history:
[SDK discussion #1036](https://github.com/anthropics/anthropic-sdk-python/discussions/1036)
(2025-09-18, when `@beta_tool`/`tool_runner` first shipped in 0.68.0) — some
of its "missing features" list (e.g. `max_iterations`) has since shipped;
don't trust it for current capability without re-checking source.

Related: [[tool-use-loop]], [[anthropic-python-sdk]], [[anthropic-models]]
