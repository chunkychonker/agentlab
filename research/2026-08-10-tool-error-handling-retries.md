# Tool-use error handling and retries done well

*Researched 2026-08-10. Backlog item: "Tool-use error handling and retries done well"
(the last unclaimed item; no open PRs and no non-`main` branches at pick time, so
nothing to collide with).*

## Question

When a tool call fails inside an agent loop, what are the genuinely distinct
failure classes, and which layer is responsible for handling each — the HTTP
client, my loop, or the model?

## Findings

### 1. There are two separate "retry" layers and they are constantly conflated

Almost every blog post on "agent retries" blurs these. They have different
owners, different triggers, and different correct policies.

**Layer A — the API call to Anthropic.** Already solved by the SDK; you mostly
configure it. Verified by reading `src/anthropic/_base_client.py` and
`_constants.py` on `main` (read 2026-08-10):

- `DEFAULT_MAX_RETRIES = 2`, `INITIAL_RETRY_DELAY = 0.5`, `MAX_RETRY_DELAY = 8.0`
  ([`_constants.py`](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_constants.py)).
- `_should_retry()` retries on 408, 409, 429, and any `>= 500`; a non-standard
  `x-should-retry: true|false` response header overrides that decision either way.
- `_calculate_retry_timeout()` honours `retry-after-ms`, then `retry-after`
  (seconds, then HTTP-date) — but **only if the parsed value is in `0 < v <= 60`**;
  otherwise it falls back to `min(0.5 * 2**n, 8.0)`.
- The jitter is `1 - 0.25 * random()`, i.e. a *multiplicative* 0.75–1.0 factor.
  The code comment immediately above it says "plus-or-minus half a second," which
  is wrong. Don't trust the comment; the sleep is only ever ≤ the computed delay.
- Configure with `Anthropic(max_retries=0)` or per-request
  `client.with_options(max_retries=5).messages.create(...)`. Default timeout is
  10 minutes and `APITimeoutError` is itself retried
  ([Python SDK docs](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python), fetched 2026-08-10).

The [errors reference](https://platform.claude.com/docs/en/api/errors) (fetched
2026-08-10) gives the status/type table: 400 `invalid_request_error`, 401
`authentication_error`, 402 `billing_error`, 403 `permission_error`, 404
`not_found_error`, 409 `conflict_error`, 413 `request_too_large`, 429
`rate_limit_error`, 500 `api_error`, 504 `timeout_error`, 529 `overloaded_error`.
Python exception classes: `BadRequestError`, `AuthenticationError`,
`PermissionDeniedError`, `NotFoundError`, `ConflictError`,
`UnprocessableEntityError`, `RateLimitError`, `InternalServerError` (>=500),
`APIConnectionError` (no status).

**Layer B — the tool function itself.** *Nothing retries this for you.* This is
the actual gap, and it is where the interesting design lives.

### 2. What the SDK's tool runner actually does with a failing tool

Read directly from `main` on 2026-08-10 —
[`_beta_runner.py`](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/lib/tools/_beta_runner.py),
[`_tool_dispatch.py`](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/lib/tools/_tool_dispatch.py),
[`_beta_functions.py`](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/lib/tools/_beta_functions.py):

- Unknown tool name → emits a `UserWarning` *and* a tool_result
  `{"content": "Error: Tool '<name>' not found", "is_error": True}`. The loop
  continues.
- `ToolError` → the exception's `.content` is used verbatim as tool_result
  content (it may be structured blocks, e.g. text + image), `is_error: True`.
- Any other `Exception` → `log.exception(...)` then `repr(exc)` as the content
  (`tool_error_content()` uses `repr`, not `str`, deliberately "which, unlike
  ``str``, keeps the exception type"), `is_error: True`.
- **So a tool that always fails does not stop the runner.** It hands the failure
  to the model and loops again. Combined with `max_iterations` defaulting to
  `None` (unbounded — already recorded in `knowledge/typed-tool-registry.md`),
  that is a runaway-cost shape straight out of the box.

**The silent-truncation gotcha (new, source-verified).** `_should_stop()` is:

```python
def _should_stop(self) -> bool:
    if self._max_iterations is not None and self._iteration_count >= self._max_iterations:
        return True
    return False
```

Hitting `max_iterations` makes `__run__` exit the `while` loop *normally*.
`until_done()` then returns the last message with **no exception and no flag**.
A truncated, unfinished run is therefore indistinguishable from a completed one
unless you inspect the returned message yourself — if `stop_reason == "tool_use"`,
the agent was cut off mid-task and its "answer" is not an answer. (For contrast,
`stop_reason == "refusal"` *is* handled explicitly and returns early.)

Two other runner facts worth knowing if you intercept results:
`generate_tool_call_response()` **caches** its result for the iteration (repeat
calls return the same object), and calling `append_messages()` inside the loop
flags state as modified so the runner **skips its own append** for that
iteration — you then own conversation validity
([Tool runner docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-runner), fetched 2026-08-10).

### 3. The wire contract, and what a good error message looks like

From [Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)
(fetched 2026-08-10):

```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
      "content": "ConnectionError: the weather service API is not available (HTTP 500)",
      "is_error": true
    }
  ]
}
```

- `is_error` is *optional* and defaults to absent/false; `content` is optional too.
- tool_result blocks must come **first** in the user message's content array;
  text before them is a 400.
- Doc tip, verbatim: *"Write instructive error messages. Instead of generic
  errors like `"failed"`, include what went wrong and what Claude should try
  next, e.g., `"Rate limit exceeded. Retry after 60 seconds."`"*
- Doc claim on model-side recovery: *"If a tool request is invalid or missing
  parameters, Claude will retry 2-3 times with corrections before apologizing to
  the user."* (Doc-only; I made no billed call this cycle to confirm the count.)
- Server tools (web_search etc.) handle their own errors — you never emit
  `is_error` for them.
- Security note from the same page: tool results are untrusted input; an error
  string you interpolate from a third-party response is an indirect
  prompt-injection surface.

Anthropic's [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
(2025-09-11 — ~11 months old, treat the surrounding advice as possibly stale, but
this point is echoed in the current docs above): *"if a tool call raises an error
(for example, during input validation), you can prompt-engineer your error
responses to clearly communicate specific and actionable improvements, rather
than opaque error codes or tracebacks."*

### 4. Prevention beats recovery for one whole error class

[Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)
(fetched 2026-08-10): `"strict": true` on a tool definition grammar-constrains
sampling so that "Tool `input` strictly follows the `input_schema`" and "Tool
`name` is always valid." That deletes the entire missing-parameter / wrong-type /
unknown-tool-name error class rather than handling it. Caveats: only a
[JSON Schema subset](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#json-schema-limitations)
is supported, compiled schemas are cached up to 24h, and no PHI may appear in
schema property names / enums / patterns. Every doc example uses
`claude-opus-5`; the page does not publish a per-model support matrix, so I
can't confirm it works on `claude-haiku-4-5` without a live call.

### 5. The failure taxonomy this all points at

Three dispositions, and the whole point is that they are *different*:

| Class | Example | Who handles it | Why |
|---|---|---|---|
| **Transient** | connection reset, 503 from the upstream service, upstream 429 | your loop, locally, with bounded backoff | The model can't help. Burning a model turn to say "try again" costs tokens and latency for a decision that needs neither. |
| **Recoverable-by-model** | bad argument, resource not found, ambiguous query, unknown tool | the model, via `is_error: true` + an actionable message | Only the model can pick different arguments or a different tool. |
| **Terminal** | auth/permission failure, budget exhausted, the model repeating an identical call that has already failed | your loop, by aborting loudly | Neither retrying nor re-prompting can succeed; continuing only spends money. |

The third row is the one people skip, and it is the documented real-world
failure. Secondary sources (low rigor, but consistent, all 2026): an incident
where a support agent's order-lookup tool timed out and the agent *"retried four
hundred times in five minutes"*
([ODSC, 2026-07](https://opendatascience.com/the-3-loops-that-break-ai-agents-in-production/)),
and converging advice to cap iterations plus a spend ceiling
([niteagent, 2026-07-14](https://niteagent.com/blog/2026-07-14-building-reliable-agent-error-handling-guide/),
[explainx, 2026](https://www.explainx.ai/blog/ai-agent-loop-architecture-triggers-retries-checkpoints-2026)).
Treat these as anecdote, not measurement — but note the SDK's own defaults
(unbounded `max_iterations`, every tool exception fed straight back to the model)
make exactly this shape easy to reach.

I searched HN via the Algolia API for practitioner discussion
(`agent retry loop tool error`, `agent error handling retries`,
`circuit breaker LLM agent tool`) and did **not** find a canonical high-signal
thread — results were overwhelmingly product launches. Honest answer: no strong
independent practitioner corroboration found this cycle beyond the blog posts
above.

### 6. Gap in this repo

`examples/minimal-agent-loop/agent.py` today does the naive thing on both counts:
`calculator()` catches every exception and returns an `"Error: ..."` **string**,
and the loop's unknown-tool branch does the same — neither sets `is_error`, and
nothing distinguishes "retry this" from "give up." That was correct scope for
that increment (it was about the loop mechanics). This increment is the honest
version, and deliberately does not touch that file.

## Build proposal

### Layer 1 — Intent

Build `examples/tool-error-policy/`: a hand-written tool loop whose response to a
failing tool call is decided by a **pure policy function** returning one of three
dispositions — retry locally with bounded backoff, report to the model with
`is_error: true`, or abort the run — so that the decision is auditable and fully
testable with no network and no sleeping.

Explicitly **out of scope**: transport-level retry (the SDK already does it; the
example only sets `max_retries` at the edge and says so in the README);
`strict: true`; streaming; async; the SDK tool runner; cross-run circuit
breakers; persistence; touching `examples/minimal-agent-loop/`.

Directory name checked 2026-08-10: `ls examples/` has no `tool-error-*`,
`gh pr list --state open` is empty, `git branch -a` shows only `main`.

### Layer 2 — Behavioral spec

**Inputs.** A user message; a client exposing `.messages.create(...)`; injected
`sleep(seconds)` and `jitter() -> float` callables.

**Outputs.** Claude's final text answer, or a raised `AgentAborted`.

**Invariants.**
1. Every `tool_use` block in an assistant turn is answered by exactly one
   `tool_result` with a matching `tool_use_id`, in the immediately following user
   turn, with all tool_result blocks first in the content array.
2. `is_error: true` is set **iff** the disposition is REPORT. Never on success.
3. Local retries for one tool call never exceed `MAX_ATTEMPTS`; per-attempt delay
   is `min(base * 2**(attempt-1), cap) * jitter_factor` and never exceeds `cap`.
4. No wall-clock sleeping and no network in the self-test — `sleep` and `jitter`
   are parameters, defaulted to `time.sleep` / `random.random` only in `main()`.
5. Termination is loud: exhausting `MAX_TURNS` raises, it does not return the
   last text. (This is the inverse of the SDK's silent-truncation gotcha above,
   and the README should say that's why.)

**Failure modes.**
- `TransientToolError` → RETRY, up to `MAX_ATTEMPTS`; on exhaustion degrade to
  REPORT whose message names the attempt count.
- `FatalToolError` → ABORT immediately, zero retries, raise `AgentAborted`.
- Any other exception from a tool → REPORT (conservative: unknown ≠ safe to retry).
- Unknown tool name → REPORT, message lists the available tool names.
- Model re-issues an identical `(name, input)` call that has already been
  REPORTed `REPEAT_LIMIT` times → ABORT.
- `MAX_TURNS` exceeded → raise `AgentAborted`.

**Acceptance criteria** (each an assertion in `test_agent.py`):
1. Tool fails transiently twice then succeeds → exactly 3 invocations, run
   completes, the emitted tool_result has no `is_error` key set true.
2. Tool always raises `TransientToolError` → exactly `MAX_ATTEMPTS` invocations,
   one tool_result with `is_error is True` whose content contains the attempt count.
3. `FatalToolError` → exactly 1 invocation and `AgentAborted` raised.
4. Unknown tool name → tool_result with `is_error is True` whose content contains
   every registered tool name; the loop continues to the next turn.
5. `backoff_delay` for attempts 1..5 with `jitter=1.0` equals the documented
   sequence and is capped at `cap`; with `jitter=0.75` every value is 0.75×.
6. Over the whole recorded transcript, the set of `tool_use_id`s in each
   tool_result turn equals the set of `tool_use.id`s in the preceding assistant
   turn (invariant 1, asserted structurally, not per-case).
7. A fake client that loops the same failing call forever raises `AgentAborted`
   via the repeat guard **before** `MAX_TURNS` is reached.
8. `python test_agent.py` exits 0 with `ANTHROPIC_API_KEY` unset, offline, in
   under a second (proves invariant 4).

### Layer 3 — Interfaces

`policy.py` — pure core, imports nothing from `anthropic`, does no I/O:

```python
class TransientToolError(Exception): ...
class FatalToolError(Exception): ...

class Disposition(enum.Enum):
    RETRY = "retry"
    REPORT = "report"
    ABORT = "abort"

@dataclasses.dataclass(frozen=True)
class Decision:
    disposition: Disposition
    message: str  # tool_result content; empty string only when RETRY

def classify(exc: BaseException, *, attempt: int, max_attempts: int) -> Decision: ...
def backoff_delay(attempt: int, *, base: float, cap: float, jitter: float) -> float: ...
def unknown_tool_message(name: str, available: Sequence[str]) -> str: ...
```

`agent.py` — imperative shell:

```python
MODEL = "claude-haiku-4-5"   # see knowledge/anthropic-models.md
MAX_ATTEMPTS = 3
MAX_TURNS = 6
REPEAT_LIMIT = 2
BACKOFF_BASE = 0.5           # mirrors the SDK's own INITIAL_RETRY_DELAY
BACKOFF_CAP = 8.0            # mirrors the SDK's own MAX_RETRY_DELAY

class AgentAborted(RuntimeError): ...

def fetch_metric(name: str) -> str: ...
    # one tool, three behaviours, deterministic:
    #   name == "flaky"     -> TransientToolError on the first two calls, then ok
    #   name == "forbidden" -> FatalToolError
    #   otherwise           -> a value string

def call_tool_with_retry(fn, tool_input, *, max_attempts, sleep, jitter) -> str: ...
    # returns content on success; raises AgentAborted on ABORT;
    # raises a private _Reportable carrying the REPORT message otherwise

def run_agent(client, user_message, *, max_turns=MAX_TURNS,
              sleep=time.sleep, jitter=random.random) -> str: ...

def main() -> int: ...   # reads ANTHROPIC_API_KEY here and only here
```

Files: `policy.py`, `agent.py`, `test_agent.py`, `README.md`,
`requirements.txt` (`anthropic>=0.120.0`, needed only for the live run — the
self-test imports nothing external, same arrangement as `minimal-agent-loop`).

**"It works" =** `python examples/tool-error-policy/test_agent.py` exits 0 with
no API key and no network, asserting all eight criteria; and with a key,
`python agent.py` makes a real call in which the `flaky` metric visibly recovers
after local retries and the `forbidden` metric aborts the run.

## Open questions

- **Is `strict: true` supported on `claude-haiku-4-5`?** Every doc example uses
  `claude-opus-5` and no per-model matrix is published. Unverified — which is
  part of why the proposal doesn't use it.
- **"Claude will retry 2-3 times with corrections"** is a documentation claim; I
  made no billed call to measure it. Don't restate it as measured.
- I did not read the Python SDK's `_should_retry_exception` body in full, so I
  can't say exactly which `httpx` exception types map to `APIConnectionError`
  retries — only that connection errors are retried per the docs and that the
  function exists.
- `generate_tool_call_response()` is sync on `BaseSyncToolRunner` and `async def`
  on `BaseAsyncToolRunner` (both live in `_beta_runner.py`); the docs show only
  the sync form. Not used by this proposal, but worth care if a later cycle
  builds on the runner.
- No independent practitioner corroboration found on HN for the retry-storm
  failure mode; the supporting citations are 2026 blog posts, not measurements.
